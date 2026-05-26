import asyncio
import time
from types import SimpleNamespace

from gemini.session import JarvisSession, SessionCallbacks, SessionConfig


class SlowDispatcher:
    def call(self, name, args):
        time.sleep(0.2)
        return {"ok": True}


class FakeSession:
    def __init__(self):
        self.responses = []

    async def send_tool_response(self, function_responses):
        self.responses.extend(function_responses)


def test_tool_call_times_out_and_sends_response():
    async def run():
        session = JarvisSession(
            SessionConfig(
                api_key="test",
                tool_dispatcher=SlowDispatcher(),
                tool_timeout_s=0.01,
            ),
            SessionCallbacks(),
        )
        session._session = FakeSession()
        tool_call = SimpleNamespace(
            function_calls=[
                SimpleNamespace(id="1", name="slow_tool", args={}),
            ]
        )

        await session._handle_tool_call(tool_call)

        assert session._session.responses
        response = session._session.responses[0].response
        assert response["ok"] is False
        assert "tardo mas" in response["error"]

    asyncio.run(run())
