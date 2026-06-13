import threading

from overlay.ui_thread import UiThread


def _boom():
    raise RuntimeError("boom")


def test_submit_does_not_execute_immediately():
    ran = []
    ui = UiThread()
    ui.submit(lambda: ran.append(1))
    assert ran == []  # solo encolado, no ejecutado
    assert ui.pending() == 1


def test_drain_executes_in_fifo_order():
    out = []
    ui = UiThread()
    ui.submit(lambda: out.append("a"))
    ui.submit(lambda: out.append("b"))
    drained = ui.drain()
    assert out == ["a", "b"]
    assert drained == 2
    assert ui.pending() == 0


def test_submit_from_worker_thread_is_safe():
    out = []
    ui = UiThread()

    def worker():
        ui.submit(lambda: out.append("worker"))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert out == []  # NO se ejecutó en el worker (sin tocar Tcl ahí)
    ui.drain()        # el "main thread" lo ejecuta
    assert out == ["worker"]


def test_drain_continues_after_callback_raises():
    out = []
    ui = UiThread()
    ui.submit(_boom)              # lanza
    ui.submit(lambda: out.append("ok"))
    ui.drain()                   # no propaga; sigue con el resto
    assert out == ["ok"]


def test_submit_none_is_ignored():
    ui = UiThread()
    ui.submit(None)
    assert ui.pending() == 0


def test_submit_latest_coalesces_same_key():
    out = []
    ui = UiThread()
    ui.submit_latest("audio", lambda: out.append("old"))
    ui.submit_latest("audio", lambda: out.append("new"))

    assert ui.pending() == 1
    ui.drain()

    assert out == ["new"]


def test_drain_can_limit_callbacks_per_pump():
    out = []
    ui = UiThread()
    ui.submit(lambda: out.append(1))
    ui.submit(lambda: out.append(2))

    drained = ui.drain(max_callbacks=1)

    assert out == [1]
    assert drained == 1
    assert ui.pending() == 1


def test_submit_latest_recovers_when_queue_was_full():
    out = []
    ui = UiThread(max_pending=1)
    ui.submit(lambda: out.append("fills queue"))

    ui.submit_latest("audio", lambda: out.append("dropped"))
    ui.drain()
    ui.submit_latest("audio", lambda: out.append("accepted"))
    ui.drain()

    assert out == ["fills queue", "accepted"]


def test_submit_force_discards_old_work_when_queue_is_full():
    out = []
    ui = UiThread(max_pending=1)
    ui.submit(lambda: out.append("old"))

    ui.submit_force(lambda: out.append("critical"))
    ui.drain()

    assert out == ["critical"]
