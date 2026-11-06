"""Each cloud adapter is exercised against a mocked transport: correct URL/auth/body in, correct parsing and error mapping out."""

import json

import httpx
import pytest

from app.config import Settings
from app.llm.factory import build_providers
from app.llm.providers.azure import AzureOpenAIProvider
from app.llm.providers.bedrock import BedrockProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.offline import OfflineProvider
from app.llm.providers.openai import OpenAIProvider
from app.llm.types import LLMError, LLMRequest

REQ = LLMRequest(task="t", system="SYS", prompt="USER", max_tokens=123, temperature=0.2)


def capture(response: httpx.Response):
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(url=str(req.url), headers=dict(req.headers), body=json.loads(req.content))
        return response

    return httpx.MockTransport(handler), seen


async def test_openai_request_shape_and_parsing():
    t, seen = capture(httpx.Response(200, json={"choices": [{"message": {"content": '{"a":1}'}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}}))
    comp = await OpenAIProvider("sk-test", "gpt-4o-mini", "https://api.openai.com/v1", t).complete(REQ, timeout_s=5)
    assert seen["url"] == "https://api.openai.com/v1/chat/completions" and seen["headers"]["authorization"] == "Bearer sk-test"
    b = seen["body"]
    assert b["model"] == "gpt-4o-mini" and b["response_format"] == {"type": "json_object"} and b["max_tokens"] == 123
    assert b["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    assert (comp.text, comp.input_tokens, comp.output_tokens) == ('{"a":1}', 11, 7)


async def test_azure_uses_deployment_url_and_api_key_header():
    t, seen = capture(httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}}))
    p = AzureOpenAIProvider("https://res.openai.azure.com", "az-key", "my-deploy", "2024-10-21", t)
    assert p.configured()
    await p.complete(REQ, timeout_s=5)
    assert seen["url"] == "https://res.openai.azure.com/openai/deployments/my-deploy/chat/completions?api-version=2024-10-21"
    assert seen["headers"]["api-key"] == "az-key" and "authorization" not in seen["headers"]


async def test_gemini_request_shape_and_parsing():
    t, seen = capture(httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"b":'}, {"text": "2}"}]}}],
                                               "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}}))
    comp = await GeminiProvider("g-key", "gemini-2.0-flash", "https://generativelanguage.googleapis.com/v1beta", t).complete(REQ, timeout_s=5)
    assert seen["url"].endswith("/models/gemini-2.0-flash:generateContent") and seen["headers"]["x-goog-api-key"] == "g-key"
    assert seen["body"]["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json" and seen["body"]["generationConfig"]["maxOutputTokens"] == 123
    assert (comp.text, comp.input_tokens, comp.output_tokens) == ('{"b":2}', 5, 3)


async def test_gemini_safety_block_is_a_clear_error():
    t, _ = capture(httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMError, match="SAFETY"):
        await GeminiProvider("k", "m", "https://x.test", t).complete(REQ, timeout_s=5)


class FakeBedrock:
    def __init__(self, out=None, exc=None):
        self.out, self.exc, self.kwargs = out, exc, None

    def converse(self, **kwargs):
        self.kwargs = kwargs
        if self.exc:
            raise self.exc
        return self.out


async def test_bedrock_converse_shape_and_parsing():
    fake = FakeBedrock({"output": {"message": {"content": [{"text": "hello"}]}}, "usage": {"inputTokens": 9, "outputTokens": 4}})
    comp = await BedrockProvider(True, "anthropic.model", "ap-southeast-1", client=fake).complete(REQ, timeout_s=5)
    assert fake.kwargs["modelId"] == "anthropic.model" and fake.kwargs["system"] == [{"text": "SYS"}]
    assert fake.kwargs["inferenceConfig"] == {"maxTokens": 123, "temperature": 0.2}
    assert (comp.text, comp.input_tokens, comp.output_tokens) == ("hello", 9, 4)


async def test_bedrock_throttling_is_transient():
    p = BedrockProvider(True, "m", "r", client=FakeBedrock(exc=RuntimeError("ThrottlingException: slow down")))
    with pytest.raises(LLMError) as e:
        await p.complete(REQ, timeout_s=5)
    assert e.value.transient


@pytest.mark.parametrize("status,transient", [(429, True), (500, True), (503, True), (400, False), (401, False)])
async def test_http_status_to_transient_mapping(status, transient):
    t, _ = capture(httpx.Response(status, text="err"))
    with pytest.raises(LLMError) as e:
        await OpenAIProvider("k", "m", "https://x.test", t).complete(REQ, timeout_s=5)
    assert e.value.transient is transient


async def test_timeout_and_malformed_bodies_are_llm_errors():
    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LLMError) as e:
        await OpenAIProvider("k", "m", "https://x.test", httpx.MockTransport(boom)).complete(REQ, timeout_s=1)
    assert e.value.transient
    t, _ = capture(httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(LLMError, match="unexpected response shape"):
        await OpenAIProvider("k", "m", "https://x.test", t).complete(REQ, timeout_s=1)


def test_factory_honours_order_and_credentials():
    s = Settings(llm_provider_order="gemini,openai", openai_api_key="x", gemini_api_key="", azure_openai_api_key="")
    providers = build_providers(s)
    assert [p.name for p in providers] == ["gemini", "openai", "offline"]  # offline always appended last
    assert [p.name for p in providers if p.configured()] == ["openai", "offline"]
    full = build_providers(Settings(azure_openai_endpoint="https://a", azure_openai_api_key="k", azure_openai_deployment="d", bedrock_enabled=True))
    assert {p.name for p in full if p.configured()} == {"azure", "bedrock", "offline"}


async def test_offline_provider_rejects_unknown_tasks():
    with pytest.raises(LLMError):
        await OfflineProvider().complete(LLMRequest(task="nope", system="", prompt=""), timeout_s=1)
