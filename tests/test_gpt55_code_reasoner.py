from types import SimpleNamespace

from openai_code.reasoner import TerraCodeReasoner


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


def test_terra_code_reasoner_uses_responses_api_and_high_reasoning():
    client = FakeClient()
    reasoner = TerraCodeReasoner(
        model="gpt-5.6-terra",
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
    assert call["model"] == "gpt-5.6-terra"
    assert call["max_output_tokens"] == 900
    assert call["reasoning"] == {"effort": "high"}
    assert call["instructions"]
    assert "Implementa una skill" in call["input"]
    assert "repo=JARVIS" in call["input"]
    assert result.text == "plan de codigo"
    assert result.input_tokens == 12
    assert result.output_tokens == 34


def test_terra_warmup_accepts_injected_client_without_api_key():
    client = FakeClient()
    reasoner = TerraCodeReasoner(
        model="gpt-5.6-terra",
        client=client,
        api_key=None,
        tracker=None,
    )

    reasoner.warmup()

    assert reasoner.client is client
