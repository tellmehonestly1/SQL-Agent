"""Measure an already-running API; results are not synthetic performance claims."""

import argparse
import asyncio
import json
import math
import time
from pathlib import Path
import httpx


async def benchmark(args):
    limit = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=120) as client:

        async def request(index):
            async with limit:
                started = time.perf_counter()
                try:
                    response = await client.post(
                        args.url.rstrip("/") + "/query",
                        json={"question": args.question},
                    )
                    status = response.status_code
                except httpx.HTTPError:
                    status = 0
                return {
                    "id": index,
                    "status": status,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }

        started = time.perf_counter()
        records = await asyncio.gather(*(request(i) for i in range(args.requests)))
        elapsed = time.perf_counter() - started
    successful = sorted(r["latency_ms"] for r in records if r["status"] == 200)
    return {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "successes": len(successful),
        "success_throughput_rps": len(successful) / elapsed,
        "successful_p95_ms": successful[math.ceil(len(successful) * 0.95) - 1]
        if successful
        else None,
        "elapsed_seconds": elapsed,
        "records": records,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--question", default="用户总数是多少")
    parser.add_argument("--output", default="benchmark-results.json")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests and concurrency must be positive")
    report = asyncio.run(benchmark(args))
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, indent=2))
    if report["successes"] != args.requests:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
