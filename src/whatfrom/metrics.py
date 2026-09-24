# src/whatfrom/metrics.py
"""앱 지표. `/metrics`로 내보내 Prometheus가 수집한다(스펙 F10).

실패해도 `/recommend`는 200으로 답하므로 HTTP 상태만으로는 실패를 셀 수 없다.
그래서 응답 결과 종류와 외부 호출 실패를 따로 센다.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import Counter, Gauge, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from whatfrom.core.contracts import RecommendResponse

# 기본 구간은 10초까지라 LLM 시간(수십~120초)을 담지 못한다.
BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300)

# 경로를 그대로 레이블로 쓰면 아무 경로나 레이블이 늘어난다. 잴 경로만 정해 둔다.
# /metrics는 빼야 5초마다 들어오는 수집이 요청 지표를 오염시키지 않는다.
TRACKED_PATHS = frozenset({"/recommend", "/health"})

HTTP_REQUEST_SECONDS = Histogram(
    "whatfrom_http_request_seconds",
    "서버가 본 요청 처리 시간",
    ["path", "status"],
    buckets=BUCKETS,
)
REQUESTS_IN_PROGRESS = Gauge("whatfrom_requests_in_progress", "지금 처리 중인 요청 수", ["path"])
THREADPOOL_WAIT_SECONDS = Histogram(
    "whatfrom_threadpool_wait_seconds",
    "요청이 도착한 뒤 /recommend 핸들러가 스레드에서 시작하기까지 기다린 시간",
    buckets=BUCKETS,
)
STAGE_SECONDS = Histogram(
    "whatfrom_stage_seconds", "추천 경로의 단계별 시간", ["stage"], buckets=BUCKETS
)
RECOMMEND_OUTCOMES = Counter("whatfrom_recommend_outcomes", "추천 응답의 결과 종류", ["outcome"])
STAGE_ERRORS = Counter("whatfrom_stage_errors", "외부 호출 실패(시간 초과 포함)", ["stage"])


@contextmanager
def stage_timer(stage: str) -> Iterator[None]:
    """단계 시간을 잰다. 실패하거나 시간이 초과된 호출도 걸린 시간을 남긴다."""
    start = time.perf_counter()
    try:
        yield
    finally:
        STAGE_SECONDS.labels(stage).observe(time.perf_counter() - start)


def record_outcome(response: RecommendResponse) -> None:
    if response.recommendation is None:
        outcome = "no_recommendation"
    elif response.degraded:
        outcome = "degraded"
    else:
        outcome = "ok"
    RECOMMEND_OUTCOMES.labels(outcome).inc()


class MetricsMiddleware:
    """요청 시간과 처리 중인 요청 수를 잰다.

    순수 ASGI 미들웨어라 이벤트 루프에서 돈다. 스레드 풀이 다 찼을 때도 기록이 밀리지 않는다.
    도착 시각을 scope의 state에 적어, /recommend 핸들러가 스레드를 얻기까지 기다린 시간을 잰다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] not in TRACKED_PATHS:
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        start = time.perf_counter()
        scope.setdefault("state", {})["arrived_at"] = start
        status = 500

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        REQUESTS_IN_PROGRESS.labels(path).inc()
        try:
            await self.app(scope, receive, send_with_status)
        finally:
            REQUESTS_IN_PROGRESS.labels(path).dec()
            HTTP_REQUEST_SECONDS.labels(path, str(status)).observe(time.perf_counter() - start)
