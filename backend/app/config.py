"""Runtime configuration, read from environment variables (see ``.env.example``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- storage -----------------------------------------------------------
    data_dir: Path = BACKEND_DIR / "data"  # bundled snapshots + internal mock data (read-only)
    runtime_dir: Path = BACKEND_DIR / "var"  # sqlite db + http cache (read-write, a volume in Docker)

    # --- government data sources ------------------------------------------
    live_data: bool = True  # False -> never call the network, use cache/snapshots only
    http_timeout_s: float = 20.0
    http_retries: int = 3
    http_backoff_s: float = 1.0
    cache_ttl_hours: float = 24.0
    datagov_base_url: str = "https://api-open.data.gov.sg/v1/public/api"
    singstat_base_url: str = "https://tablebuilder.singstat.gov.sg/api/table"
    user_agent: str = "policy-analytics-platform/1.0 (research prototype)"

    # --- LLM providers -----------------------------------------------------
    # Tried in order; providers without credentials are skipped. "offline" is always last.
    llm_provider_order: str = "anthropic,openai,gemini,bedrock,azure,offline"
    llm_timeout_s: float = 45.0
    llm_max_retries: int = 1
    llm_breaker_threshold: int = 3  # consecutive failures before a provider is skipped...
    llm_breaker_cooldown_s: float = 60.0  # ...for this long

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "low"  # low | medium | high | xhigh | max - each agent step is a small, structured decision

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_deployment: str = ""
    azure_openai_api_version: str = "2024-10-21"

    bedrock_enabled: bool = False  # set true when AWS credentials are available to boto3
    bedrock_model_id: str = "anthropic.claude-3-5-haiku-20241022-v1:0"
    aws_region: str = "ap-southeast-1"

    # --- agents / service --------------------------------------------------
    agent_max_steps: int = 14
    max_concurrent_analyses: int = 4
    analysis_timeout_s: float = 240.0
    cors_origins: str = "http://localhost:3000"
    # Comma separated fault injections for demos/tests, e.g. "source:mom,llm:openai".
    fault_injection: str = ""
    allow_fault_injection: bool = True  # lets API clients request simulated failures (demo feature) - disable in production
    rate_limit_per_min: int = 30  # POST /api/analyses per client IP
    log_level: str = "INFO"

    llm_budget_usd: float = Field(default=0.50, description="Soft per-analysis LLM spend cap")

    @property
    def provider_order(self) -> list[str]:
        order = [p.strip().lower() for p in self.llm_provider_order.split(",") if p.strip()]
        if "offline" not in order:
            order.append("offline")
        return order

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "platform.db"

    @property
    def cache_dir(self) -> Path:
        return self.runtime_dir / "http-cache"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def internal_dir(self) -> Path:
        return self.data_dir / "internal"


@lru_cache
def get_settings() -> Settings:
    return Settings()
