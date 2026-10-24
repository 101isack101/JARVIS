from loguru import logger

from telemetry.tracker import TokenTracker


def test_listener_failure_is_logged_not_printed(capsys):
    warnings = []
    sink_id = logger.add(lambda message: warnings.append(message.record["message"]), level="WARNING")
    tracker = TokenTracker()

    def boom(_snap):
        raise RuntimeError("ui gone")

    tracker.add_listener(boom)
    try:
        tracker.record("gemini-test", input_tokens=1, output_tokens=1)
    finally:
        logger.remove(sink_id)

    captured = capsys.readouterr()
    assert "[tracker] listener fallo" not in captured.out
    assert any("ui gone" in item for item in warnings)
