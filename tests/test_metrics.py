"""앱 지표. 지표는 프로세스 전역이라 값은 전후 차이로 확인한다."""

import threading

import anyio.to_thread
import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from whatfrom.api import create_app
from whatfrom.core.contracts import Recommendation, RecommendResponse
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.metrics import record_outcome, stage_timer
from whatfrom.recommend.llm import FakeLLMProvider


def value(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_stage_timer_records_the_time_even_when_the_stage_fails():
    before = value("whatfrom_stage_seconds_count", {"stage": "advise"})

    with pytest.raises(RemoteCallError), stage_timer("advise"):
        raise RemoteCallError("read timed out")

    assert value("whatfrom_stage_seconds_count", {"stage": "advise"}) == before + 1


REC = Recommendation(image="python:3.13-slim", reason="ok", dockerfile="")


@pytest.mark.parametrize(
    ("recommendation", "degraded", "outcome"),
    [(REC, False, "ok"), (REC, True, "degraded"), (None, True, "no_recommendation")],
)
def test_record_outcome_separates_failures_hidden_behind_200(recommendation, degraded, outcome):
    """실패해도 200으로 답하므로 HTTP 상태로는 셀 수 없다."""
    before = value("whatfrom_recommend_outcomes_total", {"outcome": outcome})
    response = RecommendResponse(
        question="q", recommendation=recommendation, candidates=[], degraded=degraded
    )

    record_outcome(response)

    assert value("whatfrom_recommend_outcomes_total", {"outcome": outcome}) == before + 1


def _app():
    return create_app(embedder=FakeEmbedder(), provider=FakeLLMProvider())


def test_middleware_records_the_status_and_releases_the_in_progress_gauge():
    labels = {"path": "/health", "status": "200"}
    before = value("whatfrom_http_request_seconds_count", labels)

    TestClient(_app()).get("/health")

    assert value("whatfrom_http_request_seconds_count", labels) == before + 1
    assert value("whatfrom_requests_in_progress", {"path": "/health"}) == 0


def test_the_metrics_endpoint_itself_is_not_recorded():
    """수집 요청까지 세면 5초마다 들어오는 수집이 요청 지표를 오염시킨다."""
    TestClient(_app()).get("/metrics")

    assert (
        REGISTRY.get_sample_value(
            "whatfrom_http_request_seconds_count", {"path": "/metrics", "status": "200"}
        )
        is None
    )


class BlockingEmbedder(FakeEmbedder):
    """풀려날 때까지 스레드를 붙잡는다. 풀리면 실패해 DB까지 가지 않는다."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.entered.set()
        self.release.wait(10)
        raise RemoteCallError("released")


def test_metrics_answers_while_every_worker_thread_is_busy():
    """스레드가 다 찬 과부하 순간에 수집이 끊기면 가장 중요한 구간의 지표를 잃는다."""
    embedder = BlockingEmbedder()
    app = create_app(embedder=embedder, provider=FakeLLMProvider())

    async def one_worker_thread() -> None:
        anyio.to_thread.current_default_thread_limiter().total_tokens = 1

    with TestClient(app) as client:
        client.portal.call(one_worker_thread)
        blocked = threading.Thread(target=lambda: client.post("/recommend", json={"question": "q"}))
        blocked.start()
        try:
            assert embedder.entered.wait(5)
            scraped: dict = {}
            scraper = threading.Thread(target=lambda: scraped.update(r=client.get("/metrics")))
            scraper.start()
            scraper.join(5)

            assert not scraper.is_alive(), "/metrics가 스레드 풀을 기다렸다"
            assert scraped["r"].status_code == 200
        finally:
            embedder.release.set()
            blocked.join(10)
