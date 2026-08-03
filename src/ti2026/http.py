"""Shared HTTP client: per-bucket rate limiting, retries with backoff, custom UAs."""

from __future__ import annotations

import time

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from . import config


class RetryableHTTP(Exception):
    """Raised on 429/5xx so tenacity retries the request."""


class _RateLimiter:
    def __init__(self, buckets: dict[str, float]):
        self._buckets = buckets
        self._last: dict[str, float] = {}

    def wait(self, bucket: str) -> None:
        interval = self._buckets.get(bucket, 1.0)
        last = self._last.get(bucket)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self._last[bucket] = time.monotonic()


_limiter = _RateLimiter(config.RATE_BUCKETS)
_client = httpx.Client(
    timeout=httpx.Timeout(90, connect=20),
    follow_redirects=True,
    headers={"Accept-Encoding": "gzip"},
)


def _honor_retry_after(resp: httpx.Response) -> None:
    ra = resp.headers.get("Retry-After")
    if ra and ra.isdigit():
        time.sleep(min(int(ra), 120))


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=2, max=60),
    retry=retry_if_exception_type((RetryableHTTP, httpx.TransportError)),
    reraise=True,
)
def _request(method: str, url: str, *, bucket: str, params, headers, json_body):
    _limiter.wait(bucket)
    resp = _client.request(method, url, params=params, headers=headers, json=json_body)
    if resp.status_code == 429 or resp.status_code >= 500:
        _honor_retry_after(resp)
        raise RetryableHTTP(f"HTTP {resp.status_code} for {url}")
    return resp


def get(
    url: str,
    *,
    bucket: str,
    params: dict | None = None,
    headers: dict | None = None,
    ua: str = "pipeline",
) -> httpx.Response:
    hdrs = {"User-Agent": config.PIPELINE_UA if ua == "pipeline" else config.BROWSER_UA}
    if headers:
        hdrs.update(headers)
    resp = _request("GET", url, bucket=bucket, params=params, headers=hdrs, json_body=None)
    resp.raise_for_status()
    return resp


def post(
    url: str,
    *,
    bucket: str,
    json_body: dict | None = None,
    headers: dict | None = None,
    ua: str = "pipeline",
) -> httpx.Response:
    hdrs = {"User-Agent": config.PIPELINE_UA if ua == "pipeline" else config.BROWSER_UA}
    if headers:
        hdrs.update(headers)
    resp = _request("POST", url, bucket=bucket, params=None, headers=hdrs, json_body=json_body)
    resp.raise_for_status()
    return resp
