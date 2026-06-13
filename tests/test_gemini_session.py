import asyncio
from types import SimpleNamespace

from gemini.session import JarvisSession, SessionCallbacks, SessionConfig, _call_compatible


def _turn_complete_response():
    return SimpleNamespace(
        usage_metadata=None,
        go_away=None,
        session_resumption_update=None,
        tool_call=None,
        server_content=SimpleNamespace(
            interrupted=False,
            input_transcription=None,
            model_turn=None,
            turn_complete=True,
        ),
    )


def _go_away_response():
    return SimpleNamespace(
        usage_metadata=None,
        go_away=SimpleNamespace(time_left="50s"),
        session_resumption_update=SimpleNamespace(
            resumable=True,
            new_handle="resume-handle",
        ),
        tool_call=None,
        server_content=None,
    )


class FakeLiveSession:
    def __init__(self):
        self.receive_calls = 0

    def receive(self):
        self.receive_calls += 1

        async def gen():
            if self.receive_calls == 1:
                yield _turn_complete_response()

        return gen()


class FakeFailingLiveSession:
    def receive(self):
        async def gen():
            raise RuntimeError("APIError: 1007 None. Request contains an invalid argument.")
            yield  # pragma: no cover

        return gen()


def test_receive_loop_keeps_connection_alive_after_turn_complete():
    async def run():
        logs = []
        session = JarvisSession(
            SessionConfig(api_key="test-key"),
            SessionCallbacks(on_log=logs.append),
        )
        fake = FakeLiveSession()
        session._session = fake
        session._stop_event = asyncio.Event()

        await session._receive_loop()

        assert fake.receive_calls == 2
        assert not any("sesion cerrada por servidor" in line for line in logs)

    asyncio.run(run())


def test_receive_loop_records_invalid_argument_error():
    async def run():
        logs = []
        errors = []
        session = JarvisSession(
            SessionConfig(api_key="test-key"),
            SessionCallbacks(on_log=logs.append, on_error=errors.append),
        )
        session._session = FakeFailingLiveSession()
        session._stop_event = asyncio.Event()

        await session._receive_loop()

        assert session._last_receive_error is not None
        assert session._is_invalid_resumption_error(session._last_receive_error)
        assert errors
        assert any("receive_loop excepcion" in line for line in logs)

    asyncio.run(run())


def test_invalid_resumption_error_classifier_matches_gemini_1007():
    session = JarvisSession(
        SessionConfig(api_key="test-key"),
        SessionCallbacks(),
    )

    assert session._is_invalid_resumption_error(
        RuntimeError("APIError: 1007 None. Precondition check failed.")
    )
    assert session._is_invalid_resumption_error(
        RuntimeError("ConnectionClosedError: invalid frame payload data")
    )
    assert not session._is_invalid_resumption_error(RuntimeError("temporary network timeout"))


def test_receive_loop_reconnects_cleanly_on_go_away():
    async def run():
        logs = []
        statuses = []
        session = JarvisSession(
            SessionConfig(api_key="test-key"),
            SessionCallbacks(
                on_log=logs.append,
                on_connection_status=lambda status, detail: statuses.append((status, detail)),
            ),
        )
        session._stop_event = asyncio.Event()

        action = await session._handle_response(_go_away_response(), 1)

        assert action == "reconnect"
        assert session._resumption_handle == "resume-handle"
        assert any(status == "reconnecting" for status, _ in statuses)
        assert any("go_away recibido" in line for line in logs)

    asyncio.run(run())


def test_submit_closes_coroutine_when_loop_not_ready():
    session = JarvisSession(
        SessionConfig(api_key="test-key"),
        SessionCallbacks(),
    )

    async def noop():
        return None

    coro = noop()
    session._submit(coro)

    assert coro.cr_frame is None


def test_tool_callbacks_remain_backward_compatible():
    calls = []

    def old_start(name):
        calls.append(("start", name))

    def old_end(name, elapsed_ms, ok):
        calls.append(("end", name, elapsed_ms, ok))

    _call_compatible(old_start, "jarvis_recall", {"query": "secret-ish content"})
    _call_compatible(old_end, "jarvis_recall", 12.5, True, {"found": 2})

    assert calls == [
        ("start", "jarvis_recall"),
        ("end", "jarvis_recall", 12.5, True),
    ]
