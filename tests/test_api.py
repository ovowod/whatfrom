# tests/test_api.py
import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from sqlalchemy import update
from sqlalchemy.orm import Session

from whatfrom.admission import REJECTED_DETAIL
from whatfrom.api import create_app
from whatfrom.collect.hub import TagRow, VariantRow, parse_repository
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.contracts import Citation, Claim, Recommendation, SearchPlan
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.core.models import ImageTag
from whatfrom.index.indexer import index_readme
from whatfrom.recommend.llm import FakeLLMProvider

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
QUESTION = "FastAPI에 numpy를 쓰는데 ARM64에서도 돌아야 해"


def _seed(session):
    payload = json.loads((FIXTURES / "hub_repository.json").read_text())
    row = parse_repository(payload)
    upsert_repository(session, row, NOW)
    session.flush()
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-slim",
                manifest_digest="sha256:aaa",
                last_pushed_at=datetime(2026, 9, 2, tzinfo=UTC),
                variants=(
                    VariantRow("linux", "amd64", "", "", "sha256:bbb", 46992930),
                    VariantRow("linux", "arm64", "v8", "", "sha256:ccc", 47609438),
                ),
            )
        ],
        NOW,
    )
    index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()


def _client(session, provider, embedder=None) -> TestClient:
    """conftest의 롤백 세션을 계속 내주는 팩토리로 갈아끼운다.

    실제 팩토리는 호출할 때마다 새 세션을 열지만, 테스트에서는 매번 같은
    롤백 세션을 돌려줘야 테스트가 끝날 때 통째로 되감긴다.
    """

    @contextmanager
    def open_fixed() -> Generator[Session]:
        yield session

    app = create_app(embedder=embedder or FakeEmbedder(), provider=provider)
    app.state.open_session = open_fixed
    return TestClient(app)


def test_health_returns_ok():
    client = TestClient(create_app(embedder=FakeEmbedder(), provider=FakeLLMProvider()))
    assert client.get("/health").json() == {"status": "ok"}


def test_recommend_returns_a_real_tag_with_evidence_and_provenance(session):
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(
            image="python:3.13-slim",
            alternatives=[],
        )
    )

    response = _client(session, provider).post("/recommend", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert body["degraded"] is False
    assert body["candidates"]
    assert body["candidates"][0]["source_url"].startswith("https://hub.docker.com/_/")
    assert body["candidates"][0]["collected_at"]


def test_recommend_returns_no_dockerfile_draft(session):
    """Dockerfile은 프로젝트를 보는 쪽이 쓴다(ADR 0003). 응답에 초안을 싣지 않는다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(image="python:3.13-slim", alternatives=[])
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert "dockerfile" not in body["recommendation"]
    # 인용이 없는 추천이라 그 알림만 남는다.
    assert body["notes"] == ["검증된 근거 인용이 없습니다."]


def test_recommend_numbers_each_evidence_section_once_and_candidates_point_at_them(session):
    """근거는 응답 최상위에 한 번씩 번호를 붙여 담고, 후보는 번호로 가리킨다."""
    _seed_with_alpine(session)
    provider = FakeLLMProvider(recommendation=SLIM)

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    evidence = body["evidence"]
    numbers = [item["number"] for item in evidence]
    assert numbers == list(range(1, len(evidence) + 1))
    sections = [(item["repository"], item["section_title"]) for item in evidence]
    assert len(sections) == len(set(sections))
    assert all(item["content"] and item["source_url"] for item in evidence)
    assert len(body["candidates"]) == 2
    for candidate in body["candidates"]:
        assert "evidence" not in candidate
        assert candidate["evidence_numbers"]
        assert set(candidate["evidence_numbers"]) <= set(numbers)


def test_recommend_gives_each_candidate_a_pinned_reference(session):
    _seed_with_alpine(session)
    session.execute(
        update(ImageTag).where(ImageTag.tag == "3.13-alpine").values(manifest_digest=None)
    )
    provider = FakeLLMProvider(recommendation=SLIM)

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    references = {c["image"]: c["reference"] for c in body["candidates"]}
    assert references == {
        "python:3.13-slim": "python:3.13-slim@sha256:aaa",
        "python:3.13-alpine": None,
    }


def test_recommend_keeps_the_evidence_when_the_llm_call_fails(session):
    _seed(session)
    provider = FakeLLMProvider(error=RemoteCallError("upstream 500"))

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"] is None
    assert body["evidence"]
    assert body["candidates"][0]["evidence_numbers"]


def test_recommend_returns_no_evidence_without_candidates(session):
    """임베딩이 실패하면 후보도 근거도 없다."""
    _seed(session)

    class Broken(FakeEmbedder):
        def embed(self, texts):
            raise RemoteCallError("embedding down")

    body = (
        _client(session, FakeLLMProvider(recommendation=SLIM), embedder=Broken())
        .post("/recommend", json={"question": QUESTION})
        .json()
    )

    assert (body["candidates"], body["evidence"]) == ([], [])


QUOTE = "it does use musl libc instead of glibc and friends"


def _cited(*citations: tuple[int, str]) -> Recommendation:
    return Recommendation(
        image="python:3.13-slim",
        claims=[
            Claim(
                text="alpine은 musl 기반이다.",
                citations=[Citation(evidence=n, quote=q) for n, q in citations],
            )
        ],
    )


def _python_variants(body: dict) -> int:
    return next(
        item["number"]
        for item in body["evidence"]
        if item["repository"] == "python" and QUOTE in item["content"]
    )


def test_recommend_verifies_a_quote_found_in_the_evidence(session):
    _seed(session)
    first = _client(session, FakeLLMProvider(recommendation=SLIM)).post(
        "/recommend", json={"question": QUESTION}
    )
    number = _python_variants(first.json())
    provider = FakeLLMProvider(recommendation=_cited((number, QUOTE)))

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    [claim] = body["recommendation"]["claims"]
    assert claim["text"] == "alpine은 musl 기반이다."
    assert claim["citations"] == [
        {"evidence": number, "quote": QUOTE, "verified": True, "problem": None}
    ]
    assert body["recommendation"]["verified_citations"] == 1
    assert body["notes"] == []


def test_recommend_marks_invented_citations_but_keeps_the_recommendation(session):
    """LLM은 인용을 지어내지 않는다는 불변식. 지어낸 인용은 실패로 표시하고 추천은 남긴다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=_cited((1, "alpine breaks every numpy build"), (999, QUOTE))
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    problems = [c["problem"] for c in body["recommendation"]["claims"][0]["citations"]]
    assert problems == ["quote not in evidence", "unknown evidence"]
    assert body["recommendation"]["verified_citations"] == 0
    assert body["degraded"] is False
    assert any("검증에 실패한 근거 인용이 2개" in note for note in body["notes"])
    assert any("검증된 근거 인용이 없습니다" in note for note in body["notes"])


@pytest.mark.parametrize(
    "recommendation",
    [
        Recommendation(image="python:3.13-slim"),
        Recommendation(image="python:3.13-slim", claims=[Claim(text="근거 없는 주장")]),
    ],
)
def test_recommend_warns_when_nothing_is_cited(session, recommendation):
    """인용을 생략해도 경고는 남는다. 경로의 저하가 아니라 degraded는 그대로다."""
    _seed(session)

    body = (
        _client(session, FakeLLMProvider(recommendation=recommendation))
        .post("/recommend", json={"question": QUESTION})
        .json()
    )

    assert body["recommendation"]["verified_citations"] == 0
    assert body["degraded"] is False
    assert body["notes"] == ["검증된 근거 인용이 없습니다."]


def test_recommend_attaches_the_digest(session):
    """digest는 코드가 붙인다. LLM은 digest를 보지도 쓰지도 않는다."""
    _seed(session)
    provider = FakeLLMProvider(recommendation=Recommendation(image="python:3.13-slim"))

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommended"] == {
        "image": "python:3.13-slim",
        "digest": "sha256:aaa",
        "source_url": "https://hub.docker.com/_/python",
        "collected_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    # 인용이 없는 추천이라 그 알림만 남는다.
    assert body["notes"] == ["검증된 근거 인용이 없습니다."]


def test_recommend_leaves_the_digest_empty_when_the_tag_has_none(session):
    _seed(session)
    session.execute(update(ImageTag).values(manifest_digest=None))
    provider = FakeLLMProvider(recommendation=Recommendation(image="python:3.13-slim"))

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommended"]["digest"] is None
    # 인용이 없는 추천이라 그 알림만 남는다.
    assert body["notes"] == ["검증된 근거 인용이 없습니다."]


def test_recommend_discards_a_hallucinated_answer_and_still_returns_candidates(session):
    """저하 사다리 3단계: verify가 거부하면 LLM 답변을 버리고 후보 표만 낸다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(image="python:3.13-slim-bookworm-arm64")
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"] is None
    assert body["recommended"] is None
    assert body["degraded"] is True
    assert body["candidates"]
    assert any("not a verifiable candidate image" in note for note in body["notes"])


def test_recommend_strips_unverifiable_alternatives_but_keeps_the_recommendation(session):
    """대안 하나가 지어낸 이름이라고 해서 멀쩡한 주 추천까지 버리지 않는다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(
            image="python:3.13-slim",
            alternatives=["python:3.13-alpine(호환성 문제 가능성)"],
        )
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert body["recommendation"]["alternatives"] == []
    assert body["degraded"] is False
    assert any("실재하지 않는 대안" in note for note in body["notes"])


def test_recommend_degrades_when_the_embedder_fails(session):
    """임베딩도 HTTP 호출이고 매 요청마다 부른다. LLM보다 먼저, 더 자주 실패한다."""

    class DeadEmbedder:
        dimension = 1024

        def embed(self, texts):
            raise RemoteCallError("embedding server down")

    _seed(session)
    client = _client(session, FakeLLMProvider(), embedder=DeadEmbedder())

    response = client.post("/recommend", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["recommendation"] is None
    assert body["degraded"] is True
    assert any("임베딩 생성에 실패" in note for note in body["notes"])


def test_recommend_degrades_when_the_llm_call_fails(session):
    _seed(session)
    provider = FakeLLMProvider(error=RemoteCallError("upstream 503"))

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"] is None
    assert body["degraded"] is True
    assert body["candidates"]
    assert any("upstream 503" in note for note in body["notes"])


def test_recommend_never_returns_502_when_nothing_is_indexed(session):
    """빈손으로 돌려보내는 경로가 없어야 한다 (스펙 §8)."""
    provider = FakeLLMProvider(recommendation=Recommendation(image="x:y"))

    response = _client(session, provider).post("/recommend", json={"question": QUESTION})

    assert response.status_code == 200
    body = response.json()
    assert body["candidates"] == []
    assert body["recommendation"] is None
    assert body["degraded"] is True
    # LLM 실패 분기가 아니라 후보 없음 분기를 탔는지 구분한다.
    assert any("검색된 후보가 없습니다" in note for note in body["notes"])


def test_recommend_rejects_an_empty_question(session):
    provider = FakeLLMProvider()

    response = _client(session, provider).post("/recommend", json={"question": "  "})

    assert response.status_code == 422


def _seed_with_alpine(session) -> None:
    """3.13-slim(debian trixie)과 3.13-alpine(alpine) 두 태그. 파생 값을 직접 채운다."""
    _seed(session)
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-alpine",
                manifest_digest="sha256:ddd",
                last_pushed_at=datetime(2026, 9, 2, tzinfo=UTC),
                variants=(VariantRow("linux", "amd64", "", "", "sha256:eee", 18500000),),
            )
        ],
        NOW,
    )
    for tag, distribution, codename in (
        ("3.13-slim", "debian", "trixie"),
        ("3.13-alpine", "alpine", "3.24"),
    ):
        session.execute(
            update(ImageTag)
            .where(ImageTag.tag == tag)
            .values(language_version="3.13.9", distribution=distribution, distro_codename=codename)
        )
    session.flush()


SLIM = Recommendation(image="python:3.13-slim")


def test_recommend_filters_candidates_by_the_extracted_plan(session):
    _seed_with_alpine(session)
    plan = SearchPlan(repository="python", exclude_distributions=["alpine"])
    provider = FakeLLMProvider(recommendation=SLIM, plan=plan)

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert [c["image"] for c in body["candidates"]] == ["python:3.13-slim"]
    assert body["plan"] == plan.model_dump()
    assert body["degraded"] is False
    assert body["recommendation"]["image"] == "python:3.13-slim"


def test_recommend_gives_the_plan_prompt_the_collected_repositories(session):
    _seed(session)
    provider = FakeLLMProvider(recommendation=SLIM)

    _client(session, provider).post("/recommend", json={"question": QUESTION})

    [(_system, prompt)] = provider.plan_calls
    assert QUESTION in prompt
    assert "python" in prompt


def test_recommend_keeps_answering_when_plan_extraction_fails(session):
    """스펙 §8의 1단계 저하. 벡터 검색 후보로 추천하되 저하로 표시한다."""
    _seed_with_alpine(session)
    # 근거 번호를 모르므로 앞 번호마다 같은 문장을 인용한다. 그 문장이 있는 근거 하나만 통과한다.
    provider = FakeLLMProvider(
        recommendation=_cited(*((n, QUOTE) for n in range(1, 10))),
        plan_error=RemoteCallError("planner 500"),
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert body["plan"] is None
    assert body["degraded"] is True
    assert {c["image"] for c in body["candidates"]} == {"python:3.13-slim", "python:3.13-alpine"}
    assert any("검색 조건 추출에 실패" in note and "planner 500" in note for note in body["notes"])
    # v1의 저하 동작 그대로 두 번째 LLM을 부르고, v2 응답의 근거와 인용 검증도 담긴다.
    assert body["evidence"]
    assert body["recommendation"]["verified_citations"] == 1


def test_recommend_marks_a_relaxed_answer_degraded_but_keeps_the_recommendation(session):
    """스펙 §8의 2단계 저하. 추천이 있어도 degraded는 True다."""
    _seed_with_alpine(session)
    provider = FakeLLMProvider(
        recommendation=SLIM, plan=SearchPlan(repository="python", distributions=["bookworm"])
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert body["degraded"] is True
    assert any("배포판 조건(bookworm)을 풀었습니다" in note for note in body["notes"])


def test_recommend_without_candidates_after_relaxing_mentions_only_the_empty_search(session):
    """조건을 모두 풀어도 후보가 없으면 "검색된 후보가 없습니다"만 남는다."""
    row = parse_repository(json.loads((FIXTURES / "hub_repository.json").read_text()))
    upsert_repository(session, row, NOW)
    session.flush()
    index_readme(session, "python", row.readme, row.source_url, FakeEmbedder(), NOW)
    session.flush()
    provider = FakeLLMProvider(recommendation=SLIM, plan=SearchPlan(distributions=["bookworm"]))

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"] is None
    assert body["degraded"] is True
    assert not any("풀었습니다" in note for note in body["notes"])
    assert any("검색된 후보가 없습니다" in note for note in body["notes"])


def test_recommend_does_not_extract_a_plan_when_embedding_fails(session):
    """싼 호출을 먼저 한다. 어차피 실패할 요청에 LLM을 쓰지 않는다."""
    _seed(session)
    provider = FakeLLMProvider(recommendation=SLIM)

    class DeadEmbedder:
        dimension = 1024

        def embed(self, texts):
            raise RemoteCallError("embedder down")

    _client(session, provider, embedder=DeadEmbedder()).post(
        "/recommend", json={"question": QUESTION}
    )

    assert provider.plan_calls == []


def _metric(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


STAGES = ("embedding", "plan", "search", "advise", "verify")


def _stage_counts() -> dict[str, float]:
    return {s: _metric("whatfrom_stage_seconds_count", {"stage": s}) for s in STAGES}


def test_metrics_endpoint_exposes_every_whatfrom_metric(session):
    _seed(session)
    client = _client(session, FakeLLMProvider(error=RemoteCallError("down")))
    client.post("/recommend", json={"question": QUESTION})

    text = client.get("/metrics").text

    for name in (
        "whatfrom_http_request_seconds",
        "whatfrom_requests_in_progress",
        "whatfrom_threadpool_wait_seconds",
        "whatfrom_stage_seconds",
        "whatfrom_recommend_outcomes_total",
        "whatfrom_stage_errors_total",
    ):
        assert name in text


def test_a_recommendation_records_every_stage_the_thread_wait_and_the_outcome(session):
    _seed(session)
    provider = FakeLLMProvider(recommendation=Recommendation(image="python:3.13-slim"))
    stages = _stage_counts()
    waits = _metric("whatfrom_threadpool_wait_seconds_count")
    ok = _metric("whatfrom_recommend_outcomes_total", {"outcome": "ok"})

    _client(session, provider).post("/recommend", json={"question": QUESTION})

    assert _stage_counts() == {s: count + 1 for s, count in stages.items()}
    assert _metric("whatfrom_threadpool_wait_seconds_count") == waits + 1
    assert _metric("whatfrom_recommend_outcomes_total", {"outcome": "ok"}) == ok + 1


def test_a_failed_advise_counts_an_advise_error_and_no_recommendation(session):
    _seed(session)
    errors = _metric("whatfrom_stage_errors_total", {"stage": "advise"})
    none = _metric("whatfrom_recommend_outcomes_total", {"outcome": "no_recommendation"})

    provider = FakeLLMProvider(error=RemoteCallError("read timed out"))
    _client(session, provider).post("/recommend", json={"question": QUESTION})

    assert _metric("whatfrom_stage_errors_total", {"stage": "advise"}) == errors + 1
    assert (
        _metric("whatfrom_recommend_outcomes_total", {"outcome": "no_recommendation"}) == none + 1
    )


class BrokenEmbedder(FakeEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise RemoteCallError("embedding down")


def test_a_failed_embedding_skips_the_later_stages(session):
    stages = _stage_counts()
    errors = _metric("whatfrom_stage_errors_total", {"stage": "embedding"})

    client = _client(session, FakeLLMProvider(), embedder=BrokenEmbedder())
    client.post("/recommend", json={"question": QUESTION})

    after = _stage_counts()
    assert after["embedding"] == stages["embedding"] + 1
    assert {s: after[s] for s in STAGES[1:]} == {s: stages[s] for s in STAGES[1:]}
    assert _metric("whatfrom_stage_errors_total", {"stage": "embedding"}) == errors + 1


class GateEmbedder(FakeEmbedder):
    """첫 호출부터 풀려날 때까지 스레드를 붙잡고 호출 수를 센다.

    풀리면 실패해 DB까지 가지 않는다.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.entered.set()
        self.release.wait(10)
        raise RemoteCallError("released")


def test_recommend_rejects_at_once_when_every_seat_is_taken():
    """자리가 없으면 추천 함수를 부르지 않고 503으로 거절한다. 다른 경로는 막히지 않는다."""
    embedder = GateEmbedder()
    provider = FakeLLMProvider()
    app = create_app(embedder=embedder, provider=provider, max_concurrent_recommendations=1)
    rejected = _metric("whatfrom_recommend_rejected_total")
    started = _metric("whatfrom_recommend_started_total")
    waits = _metric("whatfrom_threadpool_wait_seconds_count")

    with TestClient(app) as client:
        first = threading.Thread(
            target=lambda: client.post("/recommend", json={"question": QUESTION})
        )
        first.start()
        try:
            assert embedder.entered.wait(5)

            response = client.post("/recommend", json={"question": QUESTION})

            assert response.status_code == 503
            assert response.headers["Retry-After"] == "60"
            assert response.json() == {"detail": REJECTED_DETAIL}
            assert embedder.calls == 1
            assert (provider.plan_calls, provider.calls) == ([], [])
            assert _metric("whatfrom_recommend_rejected_total") == rejected + 1
            assert app.state.limiter.active == 1
            assert client.get("/health").status_code == 200
            assert client.get("/metrics").status_code == 200
        finally:
            embedder.release.set()
            first.join(10)

    assert app.state.limiter.active == 0
    assert _metric("whatfrom_recommend_started_total") == started + 1
    assert _metric("whatfrom_threadpool_wait_seconds_count") == waits + 1


class ExplodingEmbedder(FakeEmbedder):
    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ValueError("bug")


def test_an_unexpected_error_returns_the_seat_and_counts_the_start():
    """예상하지 못한 500도 시작한 작업으로 센다. 헛일 비교에서 빠지면 안 된다."""
    app = create_app(
        embedder=ExplodingEmbedder(), provider=FakeLLMProvider(), max_concurrent_recommendations=1
    )
    client = TestClient(app, raise_server_exceptions=False)
    started = _metric("whatfrom_recommend_started_total")

    assert client.post("/recommend", json={"question": QUESTION}).status_code == 500
    assert client.post("/recommend", json={"question": QUESTION}).status_code == 500

    assert app.state.limiter.active == 0
    assert _metric("whatfrom_recommend_started_total") == started + 2


def test_a_degraded_answer_returns_the_seat():
    app = create_app(
        embedder=BrokenEmbedder(), provider=FakeLLMProvider(), max_concurrent_recommendations=1
    )
    client = TestClient(app)

    assert client.post("/recommend", json={"question": QUESTION}).status_code == 200
    assert app.state.limiter.active == 0


def test_create_app_rejects_a_zero_limit_instead_of_using_the_default():
    with pytest.raises(ValueError):
        create_app(
            embedder=FakeEmbedder(), provider=FakeLLMProvider(), max_concurrent_recommendations=0
        )


def test_recommend_limit_and_active_gauges_reflect_the_current_app():
    """게이지는 모듈 전역이라 마지막으로 만든 app이 이긴다. 그래서 이 app을 마지막에 만들고
    바로 읽는다(운영에서는 프로세스마다 app이 하나뿐이라 문제가 없다)."""
    app = create_app(
        embedder=FakeEmbedder(), provider=FakeLLMProvider(), max_concurrent_recommendations=3
    )

    assert _metric("whatfrom_recommend_limit") == 3

    ticket = app.state.limiter.try_acquire()
    assert _metric("whatfrom_recommend_active") == 1

    ticket.start()
    ticket.finish()
    assert _metric("whatfrom_recommend_active") == 0


def test_openapi_documents_the_rejection():
    app = create_app(embedder=FakeEmbedder(), provider=FakeLLMProvider())

    responses = (
        TestClient(app).get("/openapi.json").json()["paths"]["/recommend"]["post"]["responses"]
    )

    assert "Retry-After" in responses["503"]["headers"]
    assert "200" in responses
