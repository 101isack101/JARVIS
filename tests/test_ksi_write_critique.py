from memory.self_improvement.write_critique import CritiqueResult, detect_vague


def test_critique_result_is_frozen_dataclass():
    r = CritiqueResult(text="hola", doubt=True)
    assert r.text == "hola"
    assert r.doubt is True


def test_detect_vague_flags_vague_spanish():
    assert detect_vague("Isaac quiere algo más sencillo, no estoy seguro de qué") is True


def test_detect_vague_flags_vague_english():
    assert detect_vague("we should add some stuff, not sure exactly what") is True


def test_detect_vague_ignores_precise_with_numbers():
    assert detect_vague("Decidimos usar Sonnet 4.6 por costo y latencia") is False


def test_detect_vague_ignores_precise_with_identifiers():
    assert detect_vague("El bug estaba en memory/tools.py por el import de numpy") is False


def test_detect_vague_precise_text_without_filler_is_false():
    assert detect_vague("La build v1.1.0 se mergeó a main el commit 84a15c6") is False


def test_detect_vague_empty_is_false():
    assert detect_vague("") is False
    assert detect_vague(None) is False  # type: ignore[arg-type]


def test_detect_vague_filler_but_concrete_acronym_is_false():
    # "varios" es muletilla, pero "AEC" (acrónimo) y "31dB" dan concreción.
    assert detect_vague("varios fixes al AEC subieron el ERLE a 31dB") is False
