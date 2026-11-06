from __future__ import annotations

import httpx

from ..config import Settings
from .providers.azure import AzureOpenAIProvider
from .providers.base import LLMProvider
from .providers.bedrock import BedrockProvider
from .providers.gemini import GeminiProvider
from .providers.offline import OfflineProvider
from .providers.openai import OpenAIProvider


def build_providers(s: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> list[LLMProvider]:
    """All known providers ordered by ``LLM_PROVIDER_ORDER``; unconfigured ones stay in the list but are skipped at call time."""
    available: dict[str, LLMProvider] = {
        "openai": OpenAIProvider(s.openai_api_key, s.openai_model, s.openai_base_url, transport),
        "gemini": GeminiProvider(s.gemini_api_key, s.gemini_model, s.gemini_base_url, transport),
        "bedrock": BedrockProvider(s.bedrock_enabled, s.bedrock_model_id, s.aws_region),
        "azure": AzureOpenAIProvider(s.azure_openai_endpoint, s.azure_openai_api_key, s.azure_openai_deployment, s.azure_openai_api_version, transport),
        "offline": OfflineProvider(),
    }
    ordered = [available[n] for n in s.provider_order if n in available]
    if not any(isinstance(p, OfflineProvider) for p in ordered):
        ordered.append(available["offline"])
    return ordered
