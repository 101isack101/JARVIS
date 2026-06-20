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


from memory.self_improvement.write_critique import critique, refine


class FakeReasoner:
    """Devuelve una respuesta con `.text` fija y cuenta las llamadas."""

    def __init__(self, text):
        self._text = text
        self.calls = 0

    def ask(self, instructions, *, context_extra="", max_tokens=300):
        self.calls += 1
        return type("Resp", (), {"text": self._text})()


class BoomReasoner:
    def ask(self, *a, **k):
        raise RuntimeError("reasoner caído")


def test_refine_rewrites_and_reads_doubt():
    r = FakeReasoner('{"text": "Isaac prefiere notas granulares por proyecto", "doubt": false}')
    out = refine(r, "Isaac quiere algo más granular")
    assert out.text == "Isaac prefiere notas granulares por proyecto"
    assert out.doubt is False
    assert r.calls == 1


def test_refine_sets_doubt_true():
    r = FakeReasoner('{"text": "Isaac mencionó un cambio sin especificar cuál", "doubt": true}')
    out = refine(r, "hay que cambiar algo")
    assert out.doubt is True


def test_refine_self_heals_json_with_prose():
    r = FakeReasoner('Claro:\n{"text": "texto preciso", "doubt": false}\n¿algo más?')
    out = refine(r, "algo vago")
    assert out.text == "texto preciso"


def test_refine_corrupt_json_returns_original():
    r = FakeReasoner("no es json en absoluto")
    out = refine(r, "texto original vago")
    assert out.text == "texto original vago"
    assert out.doubt is False


def test_refine_empty_refined_returns_original():
    r = FakeReasoner('{"text": "   ", "doubt": false}')
    out = refine(r, "texto original")
    assert out.text == "texto original"


def test_refine_none_reasoner_returns_original():
    out = refine(None, "texto")
    assert out.text == "texto"


def test_critique_disabled_returns_original_without_calling_reasoner():
    r = FakeReasoner('{"text": "no debería usarse", "doubt": false}')
    out = critique(r, "esto es algo vago", enabled=False)
    assert out.text == "esto es algo vago"
    assert r.calls == 0


def test_critique_not_vague_skips_reasoner():
    r = FakeReasoner('{"text": "no debería usarse", "doubt": false}')
    out = critique(r, "Mergeado a main en el commit 84a15c6", enabled=True)
    assert out.text == "Mergeado a main en el commit 84a15c6"
    assert r.calls == 0


def test_critique_vague_refines():
    r = FakeReasoner('{"text": "texto preciso final", "doubt": false}')
    out = critique(r, "Isaac quiere algo más simple, no estoy seguro", enabled=True)
    assert out.text == "texto preciso final"
    assert r.calls == 1


def test_critique_reasoner_exception_returns_original():
    out = critique(BoomReasoner(), "Isaac quiere algo, no estoy seguro", enabled=True)
    assert out.text == "Isaac quiere algo, no estoy seguro"
    assert out.doubt is False
