from datetime import UTC, datetime

from whatfrom.collect.hub import RepositoryRow, TagRow, VariantRow
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.contracts import Candidate, Recommendation
from whatfrom.recommend.verify import dockerfile_image_refs, verify_recommendation

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _seed(session):
    upsert_repository(
        session,
        RepositoryRow(
            name="python",
            is_official=True,
            description=None,
            source_url="https://hub.docker.com/_/python",
            readme="",
        ),
        NOW,
    )
    session.flush()
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.13-slim",
                manifest_digest="sha256:aaa",
                last_pushed_at=NOW,
                variants=(VariantRow("linux", "amd64", "", "", "sha256:bbb", 1),),
            )
        ],
        NOW,
    )
    session.flush()


def _candidate() -> Candidate:
    return Candidate(
        image="python:3.13-slim",
        repository="python",
        tag="3.13-slim",
        source_url="https://hub.docker.com/_/python",
        collected_at=NOW,
    )


def test_verify_accepts_a_tag_that_exists_and_was_offered(session):
    _seed(session)
    rec = Recommendation(image="python:3.13-slim", reason="ok", dockerfile="FROM python:3.13-slim")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.reason is None


def test_verify_rejects_a_hallucinated_tag_absent_from_the_database(session):
    """LLM이 그럴듯하지만 존재하지 않는 태그를 만들어낸 경우."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim-bookworm-arm64",
        reason="sounds plausible",
        dockerfile="FROM python:3.13-slim-bookworm-arm64",
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_rejects_a_real_tag_that_was_never_offered_as_a_candidate(session):
    """DB에는 있지만 후보로 주지 않은 태그를 골랐다면 불변식 위반이다."""
    _seed(session)
    upsert_tags(
        session,
        "python",
        [
            TagRow(
                tag="3.12-alpine",
                manifest_digest="sha256:ccc",
                last_pushed_at=NOW,
                variants=(VariantRow("linux", "amd64", "", "", "sha256:ddd", 1),),
            )
        ],
        NOW,
    )
    session.flush()
    rec = Recommendation(image="python:3.12-alpine", reason="", dockerfile="")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_rejects_a_malformed_image_reference(session):
    _seed(session)
    rec = Recommendation(image="python", reason="", dockerfile="")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_rejects_when_the_candidate_set_is_stale_relative_to_the_database(session):
    """후보에는 있지만 DB에서 사라진 태그 — 수집 이후 삭제된 경우."""
    rec = Recommendation(image="python:3.13-slim", reason="", dockerfile="")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_drops_a_bad_alternative_without_discarding_a_valid_recommendation(session):
    """실측: 모델이 대안 이름에 주석을 덧붙인다. 주 추천은 멀쩡하므로 살린다."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        reason="ok",
        dockerfile="FROM python:3.13-slim",
        alternatives=["python:3.13-alpine(호환성 문제 가능성)"],
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.dropped_alternatives == ("python:3.13-alpine(호환성 문제 가능성)",)


def test_verify_keeps_alternatives_drawn_from_the_candidate_set(session):
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        reason="ok",
        dockerfile="FROM python:3.13-slim",
        alternatives=["python:3.13-slim"],
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.dropped_alternatives == ()


def test_dockerfile_image_refs_reads_every_from_line():
    refs = dockerfile_image_refs("FROM python:3.13-slim\nRUN pip install x\nFROM nginx:1.27\n")
    assert refs == ["python:3.13-slim", "nginx:1.27"]


def test_dockerfile_image_refs_ignores_stage_names_and_scratch():
    """멀티스테이지의 앞 단계 참조와 scratch는 이미지가 아니다."""
    refs = dockerfile_image_refs(
        "FROM --platform=linux/amd64 python:3.13-slim AS builder\n"
        "RUN pip install x\n"
        "FROM scratch\n"
        "COPY --from=builder /app /app\n"
        "FROM builder\n"
    )
    assert refs == ["python:3.13-slim"]


def test_verify_flags_a_dockerfile_that_pulls_an_invented_image(session):
    """image는 실재하는데 FROM만 지어낸 경우 — 사용자가 복사하는 쪽이 위험하다."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        reason="ok",
        dockerfile='FROM python:3.13-slim-bookworm-arm64-INVENTED\nCMD ["python"]',
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.unverifiable_dockerfile_refs == ("python:3.13-slim-bookworm-arm64-INVENTED",)


def test_verify_flags_a_dockerfile_that_contradicts_its_own_recommendation(session):
    """실재하더라도 추천하지 않은 이미지를 쓰면 답변이 자기모순이다."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim", reason="ok", dockerfile="FROM python:3.12-alpine"
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.unverifiable_dockerfile_refs == ("python:3.12-alpine",)


def test_verify_accepts_a_dockerfile_that_matches_the_recommendation(session):
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        reason="ok",
        dockerfile="FROM python:3.13-slim\nWORKDIR /app\n",
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.unverifiable_dockerfile_refs == ()
