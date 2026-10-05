from whatfrom.core.contracts import Candidate, Platform, Recommendation
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.evidence import number_evidence
from whatfrom.recommend.llm import LLMProvider

SYSTEM_PROMPT = """You compare container base images and explain the trade-offs.

Hard rules:
- Pick `image` EXACTLY as written in one of the candidate lines. Never invent, \
complete, or modify a tag. Never combine parts of two candidates.
- Every entry in `alternatives` must also be copied verbatim from the candidate list.
- Explain the choice as `claims`, one statement per claim. Base every claim only on the \
evidence provided. If the evidence does not settle the question, say so in a claim \
instead of guessing.
- Support each claim with `citations`. A citation gives the evidence number shown in \
brackets and a `quote` copied character for character from that evidence. Never \
paraphrase, translate, or shorten a quote into something the evidence does not contain.
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


def build_prompt(question: str, candidates: list[Candidate]) -> str:
    lines = [f"Requirement: {question}", "", "Candidates (choose exactly one):"]
    for candidate in candidates:
        pushed = candidate.last_pushed_at.date() if candidate.last_pushed_at else "unknown"
        lines.append(
            f"- {candidate.image} | {_format_platforms(candidate.platforms)} | pushed: {pushed}"
        )

    # 응답과 같은 번호로 보여 준다. LLM이 인용한 번호가 응답의 근거를 가리킨다.
    evidence, _ = number_evidence(candidates)
    lines += ["", "Evidence from the official README:"]
    for item in evidence:
        lines += [f"[{item.number}] {item.repository} — {item.section_title}", item.content, ""]
    return "\n".join(lines)


def advise(provider: LLMProvider, question: str, candidates: list[Candidate]) -> Recommendation:
    if not candidates:
        raise RemoteCallError("no candidates to choose from")
    return provider.recommend(SYSTEM_PROMPT, build_prompt(question, candidates))
