# tests/test_api.py
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from whatfrom.api import create_app
from whatfrom.collect.hub import TagRow, VariantRow, parse_repository
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.contracts import Recommendation
from whatfrom.core.embed import FakeEmbedder
from whatfrom.core.httpclient import RemoteCallError
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
    def open_fixed() -> Iterator[Session]:
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
            reason="numpy는 glibc 기반이 빌드가 안정적입니다.",
            dockerfile="FROM python:3.13-slim\n",
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
    assert body["candidates"][0]["evidence"]


def test_recommend_discards_a_hallucinated_answer_and_still_returns_candidates(session):
    """저하 사다리 3단계: verify가 거부하면 LLM 답변을 버리고 후보 표만 낸다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(
            image="python:3.13-slim-bookworm-arm64", reason="plausible", dockerfile=""
        )
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"] is None
    assert body["degraded"] is True
    assert body["candidates"]
    assert any("not a verifiable candidate image" in note for note in body["notes"])


def test_recommend_strips_unverifiable_alternatives_but_keeps_the_recommendation(session):
    """대안 하나가 지어낸 이름이라고 해서 멀쩡한 주 추천까지 버리지 않는다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(
            image="python:3.13-slim",
            reason="numpy는 glibc 기반이 안정적입니다.",
            dockerfile="FROM python:3.13-slim\n",
            alternatives=["python:3.13-alpine(호환성 문제 가능성)"],
        )
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert body["recommendation"]["alternatives"] == []
    assert body["degraded"] is False
    assert any("실재하지 않는 대안" in note for note in body["notes"])


def test_recommend_strips_a_dockerfile_that_pulls_an_unverified_image(session):
    """Dockerfile은 사용자가 복사해 쓰는 산출물이라 image 필드보다 위험하다."""
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(
            image="python:3.13-slim",
            reason="numpy는 glibc 기반이 안정적입니다.",
            dockerfile='FROM python:3.13-slim-bookworm-arm64-INVENTED\nCMD ["python"]',
        )
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["image"] == "python:3.13-slim"
    assert body["recommendation"]["dockerfile"] == ""
    # 추천 객체에는 남으면 안 된다. 사용자가 복사해 쓰는 건 이쪽이다.
    assert "INVENTED" not in json.dumps(body["recommendation"], ensure_ascii=False)
    # 알림에는 남아야 한다. 무엇을 왜 지웠는지 말하지 않으면 진단이 안 된다.
    assert any("INVENTED" in note for note in body["notes"])


def test_recommend_keeps_a_dockerfile_that_matches_the_recommendation(session):
    _seed(session)
    provider = FakeLLMProvider(
        recommendation=Recommendation(
            image="python:3.13-slim",
            reason="ok",
            dockerfile="FROM python:3.13-slim\nWORKDIR /app\n",
        )
    )

    body = _client(session, provider).post("/recommend", json={"question": QUESTION}).json()

    assert body["recommendation"]["dockerfile"].startswith("FROM python:3.13-slim")
    assert body["degraded"] is False
    assert body["notes"] == []


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
    provider = FakeLLMProvider(recommendation=Recommendation(image="x:y", reason="", dockerfile=""))

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
