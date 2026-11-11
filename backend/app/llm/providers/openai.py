from __future__ import annotations

import httpx

from ..types import Completion, LLMError, LLMRequest
from .base import LLMProvider, raise_for_http


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.api_key, self.model, self.base_url, self._transport = api_key, model, base_url.rstrip("/"), transport

    def configured(self) -> bool:
        return bool(self.api_key)

    def _body(self, req: LLMRequest) -> dict:
        body = {
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": req.prompt}],
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    def _request(self) -> tuple[str, dict]:
        return f"{self.base_url}/chat/completions", {"Authorization": f"Bearer {self.api_key}"}

    async def complete(self, req: LLMRequest, *, timeout_s: float) -> Completion:
        url, headers = self._request()
        body = {"model": self.model, **self._body(req)}
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=timeout_s) as client:
                resp = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError(f"{self.name}: timeout", transient=True, provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.name}: network error {exc.__class__.__name__}", transient=True, provider=self.name) from exc
        raise_for_http(self.name, resp)
        data = resp.json()
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name}: unexpected response shape", provider=self.name) from exc
        usage = data.get("usage") or {}
        return Completion(text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))
