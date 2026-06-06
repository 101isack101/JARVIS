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
    ui.drain()
    assert out == ["a", "b"]
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
