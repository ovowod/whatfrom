import random
import re
from collections import Counter
from datetime import UTC, datetime

import pytest

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


def test_dockerfile_image_refs_reads_from_in_any_letter_case():
    """Dockerfile 명령은 대소문자를 구분하지 않는다. 소문자 from도 이미지를 가져온다."""
    dockerfile = (
        "FROM python:3.13-slim AS build\nfrom unknown:tag\nFrom Other:Tag as runtime\nfRoM build\n"
    )

    assert dockerfile_image_refs(dockerfile) == ["python:3.13-slim", "unknown:tag", "Other:Tag"]


def test_verify_catches_an_unverified_image_in_a_lowercase_from(session):
    """소문자 from 줄이 검증을 빠져나가면 없는 이미지가 Dockerfile로 사용자에게 보인다."""
    _seed(session)
    rec = Recommendation(
        image="python:3.13-slim",
        reason="ok",
        dockerfile="FROM python:3.13-slim AS build\nfrom unknown:tag\n",
    )

    result = verify_recommendation(session, rec, [_candidate()])

    assert result.ok is True
    assert result.unverifiable_dockerfile_refs == ("unknown:tag",)


def test_a_line_continuation_after_from_fails_verification():
    """FROM \\ 다음 줄에 이미지를 쓰면 참조를 \\로 읽는다. 검증에 실패해 Dockerfile이 지워진다."""
    assert dockerfile_image_refs("FROM \\\n  python:3.13-slim\n") == ["\\"]


def test_lowercase_python_import_lines_are_not_images():
    """소문자 from 줄도 `FROM 문법(참조 뒤에 AS만 올 수 있음)`에 안 맞으면 이미지가 아니다."""
    heredoc_body = "FROM python:3.13-slim\nCOPY <<EOF app.py\nfrom flask import Flask\nEOF\n"
    assert dockerfile_image_refs(heredoc_body) == ["python:3.13-slim"]

    after_continuation = 'FROM python:3.13-slim\nRUN python -c "\\\nfrom os import path"\n'
    assert dockerfile_image_refs(after_continuation) == ["python:3.13-slim"]


def test_a_grammar_valid_lowercase_from_is_read_anywhere_including_inside_a_heredoc():
    """소문자라도 FROM 문법에 맞으면 어디 있든(heredoc 안이라도) 읽는다 — 지우는 쪽이라 안전하다."""
    after_heredoc = "FROM python:3.13-slim\nRUN <<-'EOT'\necho hi\nEOT\nfrom other:tag\n"
    assert dockerfile_image_refs(after_heredoc) == ["python:3.13-slim", "other:tag"]

    inside_heredoc = "FROM python:3.13-slim\nRUN <<EOF\nfrom other:tag\nEOF\n"
    assert dockerfile_image_refs(inside_heredoc) == ["python:3.13-slim", "other:tag"]


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM a:1\nRUN cat <<< hi\nfrom evil:1\n",
        "FROM a:1\nRUN echo $((1<<2))\nFROM evil:1\n",
        "FROM a:1\nRUN cat <<EOF\nhi\nFROM evil:1\n",
        "FROM a:1\nRUN a \\\n   \nFROM evil:1\n",
        "FROM a:1\nRUN echo '<<EOF'\nFROM evil:1\nRUN <<EOF\necho\nEOF\n",
        "FROM a:1\n# note \\\nFROM evil:1\n",
        "# escape=`\nFROM a:1\nRUN echo \\\nFROM evil:1\n",
        "FROM a:1\nRUN a \\ \nFROM evil:1\n",
        "FROM a:1\n# n\x0bRUN cat <<EOF\nFROM evil:1\nRUN <<EOF\nEOF\n",
        "FROM a:1\nRUN <<EOF\n  EOF\nRUN cat <<X\nEOF\nFROM evil:1\nRUN <<X\nx\nX\n",
        "FROM a:1\nfrom\x0cevil:1\n",
        "FROM a:1\nfrom evil:1\xa0\n",
        "FROM a:1\nRUN x \\\nfrom\nFROM evil:1\n",
        "FROM a:1\nRUN x \\\n  from\n\nFROM evil:1\n",
        "FROM a:1\nfrom b:2\nas\nFROM evil:1\n",
    ],
)
def test_a_real_from_is_never_hidden_by_heredoc_or_continuation_shaped_text(dockerfile):
    """heredoc·이음 줄을 흉내 내지 않는다. 대문자 FROM은 어디 있든 항상 읽는다."""
    assert "evil:1" in dockerfile_image_refs(dockerfile)


def test_uppercase_from_keeps_mains_lenient_read():
    """대문자 FROM은 main과 같이 문법 검사 없이 위치만으로 읽는다."""
    dockerfile = "FROM a:1\nFROM evil:1 extra\n"
    assert dockerfile_image_refs(dockerfile) == ["a:1", "evil:1"]


def test_a_trailing_continuation_backslash_on_a_lowercase_from_is_still_read():
    """소문자 from 줄 끝의 이음 `\\`은 FROM 문법이 허용하므로 참조는 그대로 읽는다."""
    assert dockerfile_image_refs("from a:1 \\") == ["a:1"]


# main의 파서(대문자 FROM만, 문법 검사 없음)를 그대로 재현한다. 아래 프로퍼티 테스트 전용.
_MAIN_FROM = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+[Aa][Ss]\s+(\S+))?", re.MULTILINE)


def _main_dockerfile_image_refs(dockerfile: str) -> list[str]:
    stages: set[str] = set()
    refs: list[str] = []
    for match in _MAIN_FROM.finditer(dockerfile):
        ref, alias = match.group(1), match.group(2)
        if ref.lower() != "scratch" and ref not in stages:
            refs.append(ref)
        if alias:
            stages.add(alias)
    return refs


_FUZZ_TOKENS = [
    "FROM",
    "from",
    "From",
    "evil:1",
    "a:1",
    "b",
    "AS",
    "as",
    "--platform=x",
    "\\",
    "<<EOF",
    "EOF",
    "<<<",
    "RUN",
    "#",
    "scratch",
    "import",
    "flask",
    " ",
    "\t",
    "\x0b",
    "\x0c",
    "\r",
    " ",
    "\xa0",
    "\n",
    "\n",
    "\n",
]
_FUZZ_SEPARATORS = ["", " ", "\n"]


def test_a_lowercase_aware_parser_never_drops_a_ref_that_the_uppercase_only_parser_finds():
    """소문자까지 읽더라도, main(대문자만 보는 파서)이 찾는 참조를 놓치면 안 된다."""
    rng = random.Random(0)
    for _ in range(25_000):
        tokens = [
            rng.choice(_FUZZ_TOKENS) + rng.choice(_FUZZ_SEPARATORS)
            for _ in range(rng.randint(1, 14))
        ]
        dockerfile = "".join(tokens)
        missing = Counter(_main_dockerfile_image_refs(dockerfile)) - Counter(
            dockerfile_image_refs(dockerfile)
        )
        assert not missing, dockerfile
