"""HTTP load test: fire N analyses at a running API with bounded concurrency and report latency / throughput.

Run the API with LIVE_DATA=false so the test does not hammer government servers:
    LIVE_DATA=false uvicorn app.main:app --port 8000
    python scripts/load_test.py --total 40 --concurrency 8
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx

QUERIES = [
    "Analyse employment trends in the technology sector from 2020-2024",
    "Compare wage growth in finance and the technology sector since 2019",
    "Forecast employment in the manufacturing sector for the next two years",
    "Is there a link between job vacancies and retrenchments in the health sector?",
    "job vacancy rate by sector 2021 to 2025",
]


async def one(client: httpx.AsyncClient, i: int, timeout: float) -> tuple[str, float, float]:
    t0 = time.perf_counter()
    r = await client.post("/api/analyses", json={"query": QUERIES[i % len(QUERIES)]})
    if r.status_code != 202:
        return f"http_{r.status_code}", time.perf_counter() - t0, 0.0
    aid = r.json()["id"]
    submit = time.perf_counter() - t0
    while time.perf_counter() - t0 < timeout:
        rec = (await client.get(f"/api/analyses/{aid}")).json()
        if rec["status"] not in ("queued", "running"):
            return rec["status"], time.perf_counter() - t0, submit
        await asyncio.sleep(0.2)
    return "timeout", time.perf_counter() - t0, submit


async def main(a: argparse.Namespace) -> int:
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(base_url=a.base_url, timeout=30) as client:
        h = (await client.get("/api/health")).json()
        print(f"target {a.base_url}  live_data={h['live_data']}  providers={h['llm_providers_configured']}")

        async def guarded(i: int):
            async with sem:
                return await one(client, i, a.timeout)

        t0 = time.perf_counter()
        results = await asyncio.gather(*(guarded(i) for i in range(a.total)))
        wall = time.perf_counter() - t0
    lat = sorted(r[1] for r in results)
    sub = sorted(r[2] for r in results)
    ok = sum(1 for r in results if r[0] in ("completed", "degraded"))
    p = lambda xs, q: xs[min(len(xs) - 1, int(len(xs) * q))]  # noqa: E731
    print(
        f"{a.total} analyses, concurrency {a.concurrency}: {ok} succeeded, {a.total - ok} failed, wall {wall:.1f}s, throughput {a.total / wall:.2f}/s"
    )
    print(f"end-to-end latency  p50 {statistics.median(lat):.2f}s  p95 {p(lat, 0.95):.2f}s  max {lat[-1]:.2f}s")
    print(f"POST /analyses (accept)  p50 {statistics.median(sub) * 1000:.0f}ms  p95 {p(sub, 0.95) * 1000:.0f}ms")
    statuses: dict[str, int] = {}
    for r in results:
        statuses[r[0]] = statuses.get(r[0], 0) + 1
    print("statuses:", statuses)
    return 0 if ok == a.total else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--total", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--timeout", type=float, default=120)
    raise SystemExit(asyncio.run(main(ap.parse_args())))
