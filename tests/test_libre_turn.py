from audio.libre_turn import LibreSpeakGate


def test_underflow_before_turn_complete_does_not_resume_mic():
    gate = LibreSpeakGate()
    gate.on_audio()
    assert gate.on_underflow() is False
    assert gate.speaking is True
    assert gate.waiting_for_server is True


def test_underflow_after_turn_complete_resumes_mic_once():
    gate = LibreSpeakGate()
    gate.on_audio()
    gate.on_turn_complete()
    assert gate.on_underflow() is True
    assert gate.speaking is False
    assert gate.on_underflow() is False


def test_interrupt_clears_speaking_without_waiting_drain():
    gate = LibreSpeakGate()
    gate.on_audio()
    gate.on_interrupt()
    assert gate.speaking is False
    assert gate.on_underflow() is False


def test_reset_clears_waiting_for_server():
    gate = LibreSpeakGate()
    gate.on_audio()
    gate.reset()
    assert gate.waiting_for_server is False
    assert gate.speaking is False
