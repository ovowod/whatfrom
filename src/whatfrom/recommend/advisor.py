from collections.abc import Sequence

from whatfrom.core.contracts import Candidate, NumberedEvidence, Platform, Recommendation
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.llm import LLMProvider

# 검토 단계의 두 안(단일 선택, 판정 단계)이 같은 문구를 쓴다. M3는 인용 지시가 같아야
# 인용 뒷받침률의 차이가 출력 구조에서 왔다고 말할 수 있다.
CITATION_RULES = """\
- For every documented fact a statement relies on, cite the sentence in the evidence \
that states it. If a fact needs more than one sentence, cite each of them.
- A citation gives the evidence number shown in brackets and a `quote` copied character \
for character from that evidence. Never paraphrase, translate, or shorten a quote into \
something the evidence does not contain. Leave `citations` empty rather than invent one."""

SYSTEM_PROMPT = f"""You compare container base images and explain the trade-offs.

Hard rules:
- Pick `image` EXACTLY as written in one of the candidate lines. Never invent, \
complete, or modify a tag. Never combine parts of two candidates.
- Every entry in `alternatives` must also be copied verbatim from the candidate list.
- Explain the choice as `claims`, one statement per claim. Base every claim only on the \
evidence provided. If the evidence does not settle the question, say so in a claim \
instead of guessing.
{CITATION_RULES}
- Write claims in Korean, except for image names and tags. Keep quotes in the \
evidence's original language."""


def _format_platforms(platforms: list[Platform]) -> str:
    """후보 한 줄에 들어갈 만큼 압축한다.

    태그 하나에 플랫폼이 10개까지 붙어서 전부 적으면 프롬프트가 후보 목록이
    아니라 표가 된다. Phase 0에는 아키텍처 필터가 없으므로 여기서 좁힐 근거도
    없다 — 그래서 크기 범위와 플랫폼 이름만 넘긴다. F7에서 SearchPlan이
    아키텍처를 지정하면 그때 해당 플랫폼만 골라 보여준다.
    """
    if not platforms:
        return "platforms unknown"
    sizes = [p.size_bytes for p in platforms]
    names = sorted({f"{p.os}/{p.architecture}" for p in platforms})
    low, high = min(sizes) / 1_000_000, max(sizes) / 1_000_000
    span = f"{low:.0f} MB" if low == high else f"{low:.0f}-{high:.0f} MB"
    return f"{span} across {', '.join(names)}"


def format_requirements(requirements: Sequence[str]) -> list[str]:
    """판단 요구에 1부터 번호를 붙인다. 판정 단계도 이 번호로 요구를 가리킨다.

    빈 목록은 없다고 적는다. 줄을 아예 빼면 LLM이 요구를 빠뜨린 것으로 읽을 수 있다.
    """
    if not requirements:
        return ["Judgment requirements: none"]
    lines = ["Judgment requirements:"]
    lines += [f"{number}. {text}" for number, text in enumerate(requirements, start=1)]
    return lines


def build_prompt(
    question: str,
    candidates: list[Candidate],
    evidence: list[NumberedEvidence],
    requirements: Sequence[str] = (),
    candidates_heading: str = "Candidates (choose exactly one):",
) -> str:
    """evidence는 응답에 싣는 그 근거다. 같은 목록을 보여 줘야 인용 번호가 응답과 맞는다.

    질문과 후보 줄의 형식은 인용 표본 도구가 결과 파일의 prompt에서 다시 읽는다. 판단 요구는
    그 형식을 건드리지 않도록 후보 목록 뒤에 둔다. 판정 단계는 후보 목록의 제목만 바꿔 쓴다.
    """
    lines = [f"Requirement: {question}", "", candidates_heading]
    for candidate in candidates:
        pushed = candidate.last_pushed_at.date() if candidate.last_pushed_at else "unknown"
        lines.append(
            f"- {candidate.image} | {_format_platforms(candidate.platforms)} | pushed: {pushed}"
        )

    lines += ["", *format_requirements(requirements)]
    lines += ["", "Evidence from the official README:"]
    for item in evidence:
        lines += [f"[{item.number}] {item.repository} — {item.section_title}", item.content, ""]
    return "\n".join(lines)


def advise(
    provider: LLMProvider,
    question: str,
    candidates: list[Candidate],
    evidence: list[NumberedEvidence],
    requirements: Sequence[str] = (),
) -> Recommendation:
    if not candidates:
        raise RemoteCallError("no candidates to choose from")
    return provider.recommend(
        SYSTEM_PROMPT, build_prompt(question, candidates, evidence, requirements)
    )
