from datetime import UTC, datetime

import pytest

from whatfrom.core.contracts import Candidate, Citation, Evidence, Judgment, JudgmentTable
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.evidence import number_evidence
from whatfrom.recommend.judge import judge
from whatfrom.recommend.llm import FakeLLMProvider

NOW = datetime(2026, 10, 17, tzinfo=UTC)
NODE_GYP = "node-gyp must compile native addons without extra packages"
MUSL = "musl-based images must not be used"


def _candidate(tag: str) -> Candidate:
    return Candidate(
        image=f"node:{tag}",
        repository="node",
        tag=tag,
        source_url="https://hub.docker.com/_/node",
        collected_at=NOW,
        evidence=[
            Evidence(
                repository="node",
                section_title="Image Variants",
                content="node:<version> includes build tools. node:<version>-alpine uses musl.",
                source_url="https://github.com/docker-library/docs/blob/master/node/README.md",
            )
        ],
    )


CANDIDATES = [_candidate("24"), _candidate("24-slim"), _candidate("24-alpine"), _candidate("22")]
GOOD = [Citation(evidence=1, quote="node:<version> includes build tools.")]


def _cell(candidate: str, requirement: int, verdict: str, citations=GOOD) -> Judgment:
    return Judgment(
        candidate=candidate, requirement=requirement, verdict=verdict, citations=citations
    )


def _judge(provider: FakeLLMProvider, requirements: list[str]):
    evidence, candidates = number_evidence(CANDIDATES)
    return judge(provider, "node-gyp가 도는 이미지", candidates, evidence, requirements)


def test_a_question_without_requirements_recommends_the_top_search_result_without_the_llm():
    provider = FakeLLMProvider(judge_error=RemoteCallError("judge must not be called"))

    judged = _judge(provider, [])

    assert provider.judge_calls == []
    assert judged.recommended == "node:24"
    assert [c.grade for c in judged.ranking] == ["confirmed"] * 4
    assert judged.judgments == []


def test_candidates_rank_by_grade_then_satisfied_count_then_search_order():
    """확인됨, 미확인, 위반 순이다. 미확인끼리 만족 수가 같으면 검색 순위를 따른다."""
    table = JudgmentTable(
        judgments=[
            _cell("node:24", 1, "satisfied"),
            _cell("node:24", 2, "unknown", []),
            _cell("node:24-slim", 1, "satisfied"),
            _cell("node:24-slim", 2, "satisfied"),
            _cell("node:24-alpine", 1, "violated"),
            _cell("node:24-alpine", 2, "satisfied"),
            _cell("node:22", 1, "satisfied"),
            _cell("node:22", 2, "unknown", []),
        ]
    )

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP, MUSL])

    assert [(c.image, c.grade, c.satisfied) for c in judged.ranking] == [
        ("node:24-slim", "confirmed", 2),
        ("node:24", "unconfirmed", 1),
        ("node:22", "unconfirmed", 1),
        ("node:24-alpine", "violated", 1),
    ]
    assert judged.recommended == "node:24-slim"


def test_without_a_confirmed_candidate_there_is_no_recommendation():
    table = JudgmentTable(judgments=[_cell(c.image, 1, "unknown", []) for c in CANDIDATES])

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP])

    assert judged.recommended is None
    assert {c.grade for c in judged.ranking} == {"unconfirmed"}


@pytest.mark.parametrize(
    ("citations", "problem", "verified"),
    [
        ([], "no citation", []),
        ([Citation(evidence=1, quote="node ships every compiler.")], "citation failed", [False]),
        (
            [GOOD[0], Citation(evidence=1, quote="node ships every compiler.")],
            "citation failed",
            [True, False],
        ),
    ],
    ids=["no-citation", "unverified-quote", "one-of-two-unverified"],
)
def test_a_verdict_whose_citations_do_not_hold_falls_to_unknown(citations, problem, verified):
    """근거를 지어낸 판정을 내보내느니 모른다고 한다(설계 문서 §8)."""
    table = JudgmentTable(
        judgments=[
            _cell("node:24", 1, "violated", citations),
            *[_cell(c.image, 1, "satisfied") for c in CANDIDATES[1:]],
        ]
    )

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP])

    [cell] = [j for j in judged.judgments if j.candidate == "node:24"]
    assert (cell.claimed, cell.verdict, cell.problem) == ("violated", "unknown", problem)
    assert [c.verified for c in cell.citations] == verified
    grades = {c.image: c.grade for c in judged.ranking}
    assert grades["node:24"] == "unconfirmed"


def test_the_prompt_carries_the_question_numbered_requirements_and_every_candidate():
    table = JudgmentTable(judgments=[_cell(c.image, 1, "satisfied") for c in CANDIDATES])
    provider = FakeLLMProvider(judgments=table)

    _judge(provider, [NODE_GYP, MUSL])

    [(_system, prompt)] = provider.judge_calls
    assert "node-gyp가 도는 이미지" in prompt
    assert f"1. {NODE_GYP}" in prompt and f"2. {MUSL}" in prompt
    assert all(c.image in prompt for c in CANDIDATES)
    assert "node:<version> includes build tools." in prompt
