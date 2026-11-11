"""HTTP fetching with retry/backoff for flaky government APIs.

Data.gov.sg rate-limits aggressively (HTTP 429 after a handful of requests), so retries honour
``Retry-After`` and fall back to exponential backoff.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from ..config import Settings
from .base import DataSourceError

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


async def get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    settings: Settings,
    *,
    source: str,
    params: dict[str, Any] | None = None,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    """GET ``url``; retry 429/5xx/timeouts; raise :class:`DataSourceError` with a precise ``code``."""
    attempts = max(1, settings.http_retries)
    last: DataSourceError | None = None
    for attempt in range(attempts):
        try:
            resp = await client.get(
                url, params=params, timeout=settings.http_timeout_s, headers={"User-Agent": settings.user_agent}
            )
        except httpx.TimeoutException as exc:
            last = DataSourceError(
                f"{source}: request timed out ({exc.__class__.__name__})", code="timeout", source=source, retryable=True
            )
        except httpx.HTTPError as exc:
            last = DataSourceError(
                f"{source}: network error ({exc.__class__.__name__}: {exc})", code="unavailable", source=source, retryable=True
            )
        else:
            if resp.status_code == 429:
                last = DataSourceError(f"{source}: rate limited (HTTP 429)", code="rate_limited", source=source, retryable=True)
                retry_after = _retry_after(resp)
                if attempt < attempts - 1:
                    await sleep(min(retry_after if retry_after is not None else _backoff(settings, attempt), 30.0))
                    continue
            elif resp.status_code >= 500:
                last = DataSourceError(
                    f"{source}: server error (HTTP {resp.status_code})", code="unavailable", source=source, retryable=True
                )
            elif resp.status_code == 404:
                raise DataSourceError(f"{source}: not found (HTTP 404) for {url}", code="not_found", source=source)
            elif resp.status_code >= 400:
                raise DataSourceError(f"{source}: request rejected (HTTP {resp.status_code})", code="unavailable", source=source)
            else:
                return resp
        if attempt < attempts - 1:
            delay = _backoff(settings, attempt)
            log.info("retrying %s in %.1fs (%s)", source, delay, last)
            await sleep(delay)
    assert last is not None
    raise last


def _backoff(settings: Settings, attempt: int) -> float:
    return settings.http_backoff_s * (2**attempt)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None
