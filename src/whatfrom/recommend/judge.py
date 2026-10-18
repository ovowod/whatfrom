from collections.abc import Sequence

from whatfrom.core.contracts import (
    Candidate,
    CheckedJudgment,
    Grade,
    JudgedCandidates,
    Judgment,
    NumberedEvidence,
    RankedCandidate,
)
from whatfrom.recommend.advisor import CITATION_RULES, build_prompt
from whatfrom.recommend.llm import LLMProvider
from whatfrom.recommend.verify import check_citation

SYSTEM_PROMPT = f"""You judge container base images against requirements, one cell at a time.

Hard rules:
- Return one judgment for every candidate and every numbered judgment requirement. Copy \
`candidate` EXACTLY as written in the candidate lines and use the requirement's number.
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
    if requirements:
        prompt = build_prompt(question, candidates, evidence, requirements, CANDIDATES_HEADING)
        table = provider.judge(SYSTEM_PROMPT, prompt)
        by_number = {item.number: item for item in evidence}
        repository = {c.image: c.repository for c in candidates}
        judgments = [_check(j, by_number, repository.get(j.candidate)) for j in table.judgments]

    ranking = _rank(candidates, len(requirements), judgments)
    confirmed = [c.image for c in ranking if c.grade == "confirmed"]
    return JudgedCandidates(
        requirements=list(requirements),
        judgments=judgments,
        ranking=ranking,
        recommended=confirmed[0] if confirmed else None,
    )


def _check(
    judgment: Judgment, evidence: dict[int, NumberedEvidence], repository: str | None
) -> CheckedJudgment:
    """만족과 불만족은 인용이 있고 모두 검증을 통과할 때만 유지한다. 아니면 알 수 없음이다.

    인용은 그 후보의 repository 문서여야 한다.
    """
    repositories = {repository} if repository else set()
    citations = [check_citation(c, evidence, repositories) for c in judgment.citations]
    problem = None
    if judgment.verdict != "unknown":
        if not citations:
            problem = "no citation"
        elif not all(c.verified for c in citations):
            problem = "citation failed"
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
    """칸이 없는 판단 요구는 알 수 없음으로 본다."""
    verdicts = {(j.candidate, j.requirement): j.verdict for j in judgments}
    ranked = []
    for candidate in candidates:
        row = [
            verdicts.get((candidate.image, number), "unknown")
            for number in range(1, requirement_count + 1)
        ]
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
