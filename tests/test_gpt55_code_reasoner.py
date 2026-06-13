from types import SimpleNamespace

from openai_code.reasoner import GPT55CodeReasoner


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_text="plan de codigo",
            usage=SimpleNamespace(input_tokens=12, output_tokens=34),
        )


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_gpt55_code_reasoner_uses_responses_api_and_model():
    client = FakeClient()
    reasoner = GPT55CodeReasoner(
        model="gpt-5.5",
        client=client,
        api_key=None,
        tracker=None,
    )

    result = reasoner.ask(
        "Implementa una skill",
        context_extra="repo=JARVIS",
        max_output_tokens=900,
    )

    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.5"
    assert call["max_output_tokens"] == 900
    assert call["instructions"]
    assert "Implementa una skill" in call["input"]
    assert "repo=JARVIS" in call["input"]
    assert result.text == "plan de codigo"
    assert result.input_tokens == 12
    assert result.output_tokens == 34
