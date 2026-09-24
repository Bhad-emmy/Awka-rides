import json
import time
from collections import deque

from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send


RATE_LIMIT_RULES = {
    ("POST", "/api/customer/auth/signup"): (5, 60 * 60),
    ("POST", "/api/customer/auth/login"): (10, 10 * 60),
    ("POST", "/api/auth/login"): (10, 10 * 60),
    ("POST", "/api/rides"): (5, 10 * 60),
}

PUBLIC_STATUS_RULE = ("GET", 30, 60)


def _client_ip(scope: Scope) -> str:
    headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }

    forwarded_for = headers.get("x-forwarded-for", "")
    if forwarded_for:
        first = forwarded_for.split(",", 1)[0].strip()
        if first:
            return first

    real_ip = headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip

    client = scope.get("client")
    if client and client[0]:
        return str(client[0])

    return "unknown"


def _match_rule(method: str, path: str):
    direct_rule = RATE_LIMIT_RULES.get((method, path))
    if direct_rule:
        return direct_rule

    if method == PUBLIC_STATUS_RULE[0] and path.startswith("/api/rides/public/"):
        return PUBLIC_STATUS_RULE[1:]

    return None


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app
        self.buckets: dict[tuple[str, str], deque[float]] = {}

    def _check_limit(self, client_ip: str, rule_key: str, limit: int, window: int):
        now = time.monotonic()
        key = (client_ip, rule_key)
        bucket = self.buckets.setdefault(key, deque())

        cutoff = now - window
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= limit:
            retry_after = max(1, int(window - (now - bucket[0])) + 1)
            return False, retry_after

        bucket.append(now)

        if len(self.buckets) > 5000:
            stale_keys = [
                bucket_key
                for bucket_key, timestamps in self.buckets.items()
                if not timestamps or timestamps[-1] <= cutoff
            ]
            for stale_key in stale_keys[:1000]:
                self.buckets.pop(stale_key, None)

        return True, 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        rule = _match_rule(method, path)

        if not rule:
            await self.app(scope, receive, send)
            return

        limit, window = rule
        client_ip = _client_ip(scope)
        rule_key = f"{method}:{path.split('/public/', 1)[0]}/public" if path.startswith("/api/rides/public/") else f"{method}:{path}"

        allowed, retry_after = self._check_limit(
            client_ip,
            rule_key,
            limit,
            window,
        )

        if allowed:
            await self.app(scope, receive, send)
            return

        payload = json.dumps(
            {
                "detail": "Too many requests. Please try again later.",
                "retryAfter": retry_after,
            }
        ).encode("utf-8")

        headers = [
            (b"content-type", b"application/json"),
            (b"retry-after", str(retry_after).encode("ascii")),
            (b"x-ratelimit-limit", str(limit).encode("ascii")),
        ]

        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": payload,
            }
        )


def install_rate_limit(app: FastAPI) -> None:
    app.add_middleware(RateLimitMiddleware)
