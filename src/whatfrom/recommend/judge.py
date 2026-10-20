from collections.abc import Sequence

from whatfrom.core.contracts import (
    Candidate,
    CheckedJudgment,
    Grade,
    JudgedCandidates,
    Judgment,
    JudgmentProblem,
    NumberedEvidence,
    RankedCandidate,
)
from whatfrom.recommend.advisor import CITATION_RULES, build_prompt
from whatfrom.recommend.llm import LLMProvider
from whatfrom.recommend.verify import OTHER_REPOSITORY, check_citation

SYSTEM_PROMPT = f"""You judge container base images against requirements, one cell at a time.

Hard rules:
- Return one judgment for every candidate and every numbered judgment requirement, and \
use the requirement's number.
- `candidate` is the image name alone: the text before the first ` | ` in a candidate \
line, such as `python:3.13-slim`. Copy it EXACTLY and never add the sizes, platforms, or \
push date that follow it.
- `verdict` is "satisfied" or "violated" only when the evidence says so for that \
candidate. If the evidence does not settle it, use "unknown" instead of guessing.
- Judge each candidate on its own. Do not rank or compare candidates.
{CITATION_RULES}
- Keep quotes in the evidence's original language."""

CANDIDATES_HEADING = "Candidates (judge every one):"

# 등급이 정렬의 첫째 기준이다(설계 문서 §7).
GRADE_ORDER: dict[Grade, int] = {"confirmed": 0, "unconfirmed": 1, "violated": 2}


def judge(
    provider: LLMProvider,
    question: str,
    candidates: list[Candidate],
    evidence: list[NumberedEvidence],
    requirements: Sequence[str],
) -> JudgedCandidates:
    """후보마다 판단 요구를 판정하고, 인용을 검증해 순위를 정한다.

    판단 요구가 없으면 LLM을 부르지 않는다. 모든 후보가 확인됨이라 검색 1위가 추천이다.
    candidates의 순서가 검색 순위다.
    """
    judgments: list[CheckedJudgment] = []
    dropped: list[CheckedJudgment] = []
    if requirements:
        prompt = build_prompt(question, candidates, evidence, requirements, CANDIDATES_HEADING)
        table = provider.judge(SYSTEM_PROMPT, prompt)
        judgments, dropped = _normalize(table.judgments, candidates, evidence, len(requirements))

    ranking = _rank(candidates, len(requirements), judgments)
    confirmed = [c.image for c in ranking if c.grade == "confirmed"]
    return JudgedCandidates(
        requirements=list(requirements),
        judgments=judgments,
        dropped=dropped,
        ranking=ranking,
        recommended=confirmed[0] if confirmed else None,
    )


def _normalize(
    raw: list[Judgment],
    candidates: list[Candidate],
    evidence: list[NumberedEvidence],
    requirement_count: int,
) -> tuple[list[CheckedJudgment], list[CheckedJudgment]]:
    """LLM의 판정 목록을 후보 × 판단 요구의 칸마다 정확히 하나로 맞춘다(설계 문서 §7).

    (칸 목록, 버린 판정)을 돌려준다. 칸은 후보 순서와 판단 요구 번호 순이다.
    """
    by_number = {item.number: item for item in evidence}
    repository = {c.image: c.repository for c in candidates}
    received: dict[tuple[str, int], list[CheckedJudgment]] = {}
    dropped: list[CheckedJudgment] = []
    for judgment in raw:
        problem: JudgmentProblem | None = None
        if judgment.candidate not in repository:
            problem = "unknown candidate"
        elif not 1 <= judgment.requirement <= requirement_count:
            problem = "unknown requirement"
        if problem is not None:
            # 어느 칸에도 쓰지 않으므로 인용을 검증하지 않는다. 모르는 후보는 repository도 몰라
            # 검증 결과가 실패 이유를 잘못 가리킨다.
            dropped.append(
                CheckedJudgment(
                    candidate=judgment.candidate,
                    requirement=judgment.requirement,
                    claimed=judgment.verdict,
                    verdict=judgment.verdict,
                    problem=problem,
                )
            )
            continue
        checked = _check(judgment, by_number, repository[judgment.candidate])
        received.setdefault((judgment.candidate, judgment.requirement), []).append(checked)

    cells: list[CheckedJudgment] = []
    for candidate in candidates:
        for number in range(1, requirement_count + 1):
            found = received.get((candidate.image, number), [])
            if len(found) == 1:
                cells.append(found[0])
            else:
                # 빠졌거나 둘 이상 왔다. 어느 쪽이든 고를 근거가 없다.
                cells.append(
                    CheckedJudgment(
                        candidate=candidate.image,
                        requirement=number,
                        claimed=None,
                        verdict="unknown",
                        problem="duplicate" if found else "missing",
                        originals=found,
                    )
                )
    return cells, dropped


def _check(
    judgment: Judgment, evidence: dict[int, NumberedEvidence], repository: str
) -> CheckedJudgment:
    """만족과 불만족은 인용이 있고 모두 검증을 통과할 때만 유지한다. 아니면 알 수 없음이다.

    인용은 그 후보의 repository 문서여야 한다.
    """
    citations = [check_citation(c, evidence, {repository}) for c in judgment.citations]
    failures = {c.problem for c in citations if not c.verified}
    problem: JudgmentProblem | None = None
    if judgment.verdict != "unknown":
        if not citations:
            problem = "no citation"
        elif failures - {OTHER_REPOSITORY}:
            problem = "citation failed"
        elif failures:
            problem = "other repository"
    return CheckedJudgment(
        candidate=judgment.candidate,
        requirement=judgment.requirement,
        claimed=judgment.verdict,
        verdict="unknown" if problem else judgment.verdict,
        citations=citations,
        problem=problem,
    )


def _rank(
    candidates: list[Candidate], requirement_count: int, judgments: list[CheckedJudgment]
) -> list[RankedCandidate]:
    # _normalize가 칸을 모두 채웠다.
    verdicts = {(j.candidate, j.requirement): j.verdict for j in judgments}
    ranked = []
    for candidate in candidates:
        row = [verdicts[(candidate.image, number)] for number in range(1, requirement_count + 1)]
        if "violated" in row:
            grade: Grade = "violated"
        elif "unknown" in row:
            grade = "unconfirmed"
        else:
            grade = "confirmed"
        ranked.append(
            RankedCandidate(image=candidate.image, grade=grade, satisfied=row.count("satisfied"))
        )
    # sorted는 안정 정렬이라 같은 등급과 만족 수 안에서는 검색 순위가 유지된다.
    return sorted(ranked, key=lambda c: (GRADE_ORDER[c.grade], -c.satisfied))
