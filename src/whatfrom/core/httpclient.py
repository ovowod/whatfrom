# src/whatfrom/core/httpclient.py
import random
import time
from collections.abc import Callable, Mapping

import httpx2

# 429와 5xx만 재시도한다. 4xx는 같은 요청이면 같은 답이 온다 (스펙 §8).
# 529는 Anthropic의 overloaded_error다(spec F15). 다른 공급자는 보내지 않는다.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})

# 전송 오류 중에서는 연결 단계 실패만 재시도한다. 아직 아무것도 보내지 못했고
# connect 타임아웃이 3초라 재시도가 싸다.
#
# 읽기/쓰기 타임아웃은 재시도하지 않는다. 이미 타임아웃 예산을 다 쓴 뒤라서
# 한 번 더 시도하면 대기 시간이 그만큼 배로 늘어난다. LLM 타임아웃이 120초인데
# 3회 시도하면 요청 하나가 6분을 넘긴다 — 저하 사다리가 빨리 답한다는 전제를
# 스스로 깨는 셈이다.
RETRYABLE_TRANSPORT_ERRORS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.PoolTimeout,
)

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
    on_attempt: Callable[[int], None] | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict:
    """on_attempt는 요청을 보낼 때마다 몇 번째 시도인지(1부터) 받는다.

    반환값이 아니라 callback으로 알리는 이유는 실패한 호출도 시도 횟수를 남겨야 해서다.
    """
    # headers는 Bearer가 아닌 방식으로 인증하는 공급자(Anthropic의 x-api-key)가 넘긴다.
    sent = {"Content-Type": "application/json", **(headers or {})}
    if api_key:
        sent["Authorization"] = f"Bearer {api_key}"

    last_error = "no attempt was made"
    for attempt in range(max_retries + 1):
        if on_attempt is not None:
            on_attempt(attempt + 1)
        try:
            response = client.post(url, json=payload, headers=sent)
        except RETRYABLE_TRANSPORT_ERRORS as exc:
            last_error = f"connection failed: {exc}"
        except httpx2.HTTPError as exc:
            raise RemoteCallError(f"request failed: {exc}") from exc
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
