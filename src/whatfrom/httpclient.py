# src/whatfrom/httpclient.py
import random
import time
from collections.abc import Callable

import httpx2

# 429와 5xx만 재시도한다. 4xx는 같은 요청이면 같은 답이 온다 (스펙 §8).
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# 스펙 §8: connect 3s / read 15s 분리
DEFAULT_TIMEOUT = httpx2.Timeout(20.0, connect=3.0, read=15.0, write=10.0)


class RemoteCallError(Exception):
    """외부 HTTP 호출 실패 또는 응답 계약 위반. 호출자는 저하 사다리로 내려간다."""


def post_json(
    client: httpx2.Client,
    url: str,
    payload: dict,
    *,
    api_key: str = "",
    max_retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_error = "no attempt was made"
    for attempt in range(max_retries + 1):
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx2.HTTPError as exc:
            last_error = f"connection failed: {exc}"
        else:
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise RemoteCallError(f"response was not JSON: {exc}") from exc
            if response.status_code not in RETRY_STATUSES:
                raise RemoteCallError(f"api error {response.status_code}: {response.text[:200]}")
            last_error = f"api error {response.status_code}"

        if attempt < max_retries:
            sleep(2**attempt + random.random())  # 지수 백오프 + jitter

    raise RemoteCallError(f"{last_error} (after {max_retries + 1} attempts)")
