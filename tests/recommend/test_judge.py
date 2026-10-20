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


def _cells(judged) -> dict[tuple[str, int], object]:
    return {(j.candidate, j.requirement): j for j in judged.judgments}


def _rest_satisfied(skip: str) -> list[Judgment]:
    return [_cell(c.image, 1, "satisfied") for c in CANDIDATES if c.image != skip]


@pytest.mark.parametrize(
    ("stray", "problem"),
    [
        (_cell("node:99", 1, "satisfied"), "unknown candidate"),
        (_cell("node:24", 2, "violated"), "unknown requirement"),
    ],
    ids=["candidate-not-offered", "requirement-out-of-range"],
)
def test_a_cell_pointing_outside_the_table_is_dropped_with_its_reason(stray, problem):
    """LLM이 지어낸 참조는 비교표에 남지 않는다. 버린 칸은 이유와 함께 기록한다."""
    table = JudgmentTable(judgments=[*[_cell(c.image, 1, "satisfied") for c in CANDIDATES], stray])

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP])

    assert [(d.candidate, d.requirement, d.problem) for d in judged.dropped] == [
        (stray.candidate, stray.requirement, problem)
    ]
    assert len(judged.judgments) == 4
    assert {j.verdict for j in judged.judgments} == {"satisfied"}


def test_every_candidate_gets_one_cell_per_requirement_and_a_missing_one_is_unknown():
    table = JudgmentTable(judgments=_rest_satisfied("node:22"))

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP])

    assert [(j.candidate, j.requirement) for j in judged.judgments] == [
        ("node:24", 1),
        ("node:24-slim", 1),
        ("node:24-alpine", 1),
        ("node:22", 1),
    ]
    missing = _cells(judged)[("node:22", 1)]
    assert (missing.claimed, missing.verdict, missing.problem) == (None, "unknown", "missing")


@pytest.mark.parametrize(
    ("pair", "second_verified"),
    [
        ([_cell("node:24", 1, "satisfied"), _cell("node:24", 1, "violated")], True),
        (
            [
                _cell("node:24", 1, "satisfied"),
                _cell("node:24", 1, "satisfied", [Citation(evidence=99, quote="no such evidence")]),
            ],
            False,
        ),
    ],
    ids=["conflicting", "one-with-a-missing-evidence-number"],
)
def test_two_judgments_for_one_cell_make_it_unknown_and_keep_both(pair, second_verified):
    """어느 쪽이 맞는지 고를 근거가 없다.

    없는 근거 번호를 가리킨 판정도 버리지 않으므로 중복이 된다.
    """
    table = JudgmentTable(judgments=[*pair, *_rest_satisfied("node:24")])

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP])

    cell = _cells(judged)[("node:24", 1)]
    assert (cell.verdict, cell.problem) == ("unknown", "duplicate")
    assert [o.claimed for o in cell.originals] == [p.verdict for p in pair]
    assert cell.originals[1].citations[0].verified is second_verified


def test_a_missing_evidence_number_fails_the_citation():
    table = JudgmentTable(
        judgments=[
            _cell("node:24", 1, "satisfied", [Citation(evidence=99, quote="no such evidence")]),
            *_rest_satisfied("node:24"),
        ]
    )

    judged = _judge(FakeLLMProvider(judgments=table), [NODE_GYP])

    cell = _cells(judged)[("node:24", 1)]
    assert (cell.verdict, cell.problem) == ("unknown", "citation failed")


def test_a_citation_from_another_repository_makes_the_cell_unknown():
    """같은 근거 본문이라도 그 후보의 repository 문서가 아니면 판정을 받칠 수 없다."""
    python = _candidate("24").model_copy(
        update={
            "image": "python:3.13",
            "repository": "python",
            "tag": "3.13",
            "evidence": [
                Evidence(
                    repository="python",
                    section_title="Image Variants",
                    content="python:<version> includes build tools.",
                    source_url="https://github.com/docker-library/docs/blob/master/python/README.md",
                )
            ],
        }
    )
    evidence, candidates = number_evidence([CANDIDATES[0], python])
    python_number = next(e.number for e in evidence if e.repository == "python")
    table = JudgmentTable(
        judgments=[
            _cell(
                "node:24",
                1,
                "satisfied",
                [Citation(evidence=python_number, quote="python:<version> includes build tools.")],
            ),
            _cell("python:3.13", 1, "satisfied", GOOD),
        ]
    )

    judged = judge(FakeLLMProvider(judgments=table), "q", candidates, evidence, [NODE_GYP])

    cells = _cells(judged)
    assert (cells[("node:24", 1)].verdict, cells[("node:24", 1)].problem) == (
        "unknown",
        "other repository",
    )
    # 근거 1은 node 문서다. python 후보가 인용하면 다른 repository다.
    assert cells[("python:3.13", 1)].problem == "other repository"


def test_the_system_prompt_asks_for_the_image_name_alone_as_the_candidate():
    """M3 1회차에서 LLM이 후보 줄 전체를 candidate에 넣어 판정 167칸이 버려졌다."""
    table = JudgmentTable(judgments=[_cell(c.image, 1, "satisfied") for c in CANDIDATES])
    provider = FakeLLMProvider(judgments=table)

    _judge(provider, [NODE_GYP])

    [(system, prompt)] = provider.judge_calls
    assert "- node:24 | " in prompt
    assert "image name" in system and "before the first ` | `" in system
