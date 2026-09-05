"""Bounded HTTPS transport; credentials never follow redirects or enter error text."""
import json
from urllib.parse import urlsplit

import httpx


class IntegrationError(Exception):
    def __init__(self, code="provider_unavailable", retry_after=60):
        super().__init__(code)
        self.code, self.retry_after = code, retry_after


def https_url(value):
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("integration endpoints require HTTPS without credentials or fragments")
    return value


def request_json(method, url, *, headers=None, data=None, params=None):
    https_url(url)
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False) as client:
            with client.stream(method, url, headers=headers, data=data, params=params) as response:
                if response.status_code == 429:
                    wait = response.headers.get("Retry-After", "60")
                    raise IntegrationError("provider_rate_limited", min(max(int(wait) if wait.isdigit() else 60, 1), 3600))
                if response.status_code in {401, 403}:
                    raise IntegrationError("provider_access_refused")
                if response.status_code != 200:
                    raise IntegrationError("provider_unavailable")
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise IntegrationError("provider_response_too_large")
        return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (httpx.HTTPError, ValueError, RecursionError) as exc:
        raise IntegrationError("provider_invalid_response") from exc
