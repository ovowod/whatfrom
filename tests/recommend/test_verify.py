from datetime import UTC, datetime

import pytest

from whatfrom.collect.hub import RepositoryRow, TagRow, VariantRow
from whatfrom.collect.store import upsert_repository, upsert_tags
from whatfrom.core.contracts import Candidate, Citation, NumberedEvidence, Recommendation
from whatfrom.recommend.verify import image_exists, verify_citation, verify_recommendation

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
    rec = Recommendation(image="python:3.13-slim")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.reason is None


def test_verify_rejects_a_hallucinated_tag_absent_from_the_database(session):
    """LLM이 그럴듯하지만 존재하지 않는 태그를 만들어낸 경우."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim-bookworm-arm64",
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
    rec = Recommendation(image="python:3.12-alpine")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_rejects_a_malformed_image_reference(session):
    _seed(session)
    rec = Recommendation(image="python")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_rejects_when_the_candidate_set_is_stale_relative_to_the_database(session):
    """후보에는 있지만 DB에서 사라진 태그 — 수집 이후 삭제된 경우."""
    rec = Recommendation(image="python:3.13-slim")

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is False
    assert "not a verifiable candidate image" in result.reason


def test_verify_drops_a_bad_alternative_without_discarding_a_valid_recommendation(session):
    """실측: 모델이 대안 이름에 주석을 덧붙인다. 주 추천은 멀쩡하므로 살린다."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        alternatives=["python:3.13-alpine(호환성 문제 가능성)"],
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.dropped_alternatives == ("python:3.13-alpine(호환성 문제 가능성)",)


def test_verify_keeps_alternatives_drawn_from_the_candidate_set(session):
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        alternatives=["python:3.13-slim"],
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.dropped_alternatives == ()


def test_image_exists_checks_the_collected_tags(session):
    _seed(session)

    assert image_exists(session, "python:3.13-slim") is True
    assert image_exists(session, "python:3.99") is False
    assert image_exists(session, "python") is False


EVIDENCE = {
    1: NumberedEvidence(
        number=1,
        repository="python",
        section_title="Image Variants",
        content=(
            "The main caveat to note is that it does use musl libc\ninstead of glibc and friends."
        ),
        source_url="https://example.invalid/python",
    ),
    2: NumberedEvidence(
        number=2,
        repository="node",
        section_title="Image Variants",
        content="node:<version>-slim does not contain the common packages.",
        source_url="https://example.invalid/node",
    ),
}


@pytest.mark.parametrize(
    ("evidence", "quote", "repositories", "problem"),
    [
        (1, "it does use musl libc instead of glibc", {"python"}, None),
        # README의 줄바꿈과 연속 공백은 인용과 다르게 감길 수 있다.
        (1, "musl  libc instead\n of glibc", {"python"}, None),
        # 대안의 repository 문서도 인용할 수 있다.
        (2, "does not contain the common packages", {"python", "node"}, None),
        (3, "it does use musl libc", {"python"}, "unknown evidence"),
        (1, "", {"python"}, "empty quote"),
        (1, "   \n", {"python"}, "empty quote"),
        (1, "it does use MUSL libc", {"python"}, "quote not in evidence"),
        (1, "musl libc is always broken", {"python"}, "quote not in evidence"),
        (2, "does not contain the common packages", {"python"}, "evidence from another repository"),
    ],
)
def test_verify_citation(evidence, quote, repositories, problem):
    citation = Citation(evidence=evidence, quote=quote)

    assert verify_citation(citation, EVIDENCE, repositories) == problem
