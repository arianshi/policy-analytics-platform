from __future__ import annotations

import httpx

from .openai import OpenAIProvider


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI speaks the same chat-completions dialect; only the URL and auth header differ."""

    name = "azure"

    def __init__(
        self, endpoint: str, api_key: str, deployment: str, api_version: str, transport: httpx.AsyncBaseTransport | None = None
    ):
        super().__init__(api_key, deployment, endpoint, transport)
        self.deployment, self.api_version = deployment, api_version

    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.deployment)

    def _request(self) -> tuple[str, dict]:
        url = f"{self.base_url}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"
        return url, {"api-key": self.api_key}

    async def complete(self, req, *, timeout_s):  # model is implied by the deployment
        return await super().complete(req, timeout_s=timeout_s)
