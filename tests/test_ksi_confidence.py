from memory.self_improvement.confidence import decayed, legacy_to_float, reinforce


def test_legacy_mapping():
    assert legacy_to_float("high") == 0.85
    assert legacy_to_float("medium") == 0.6
    assert legacy_to_float("low") == 0.35
    assert legacy_to_float("desconocido") == 0.6  # default


def test_decay_is_monotonic_with_age():
    fresh = decayed(0.8, "2026-06-14", half_life_days=45, today="2026-06-14")
    old = decayed(0.8, "2026-04-30", half_life_days=45, today="2026-06-14")
    assert fresh == 0.8
    assert old < fresh
    assert old > 0.0


def test_decay_half_life():
    half = decayed(0.8, "2026-04-30", half_life_days=45, today="2026-06-14")
    assert abs(half - 0.4) < 0.01


def test_reinforce_increases_capped():
    assert reinforce(0.6, times=1) == 0.6
    assert reinforce(0.6, times=3) > 0.6
    assert reinforce(0.99, times=10) == 1.0


def test_decay_bad_date_returns_input():
    assert decayed(0.7, "no-es-fecha", half_life_days=45, today="2026-06-14") == 0.7
