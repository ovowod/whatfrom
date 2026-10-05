"""후보들의 근거에 요청 안에서만 쓰는 번호를 붙인다(ADR 0003).

API가 한 번 번호를 매기고, 같은 근거를 응답과 두 번째 LLM 호출에 함께 넘긴다. 그래야
LLM이 인용한 번호와 응답의 번호가 같은 section을 가리킨다.
"""

from whatfrom.core.contracts import Candidate, NumberedEvidence


def number_evidence(candidates: list[Candidate]) -> tuple[list[NumberedEvidence], list[Candidate]]:
    """(번호 붙은 근거, 근거 번호를 채운 후보). 순서는 후보 순서, 그 안에서 근거 순서다.

    repository까지 봐야 한다. 제목만으로 합치면 python과 node의 "Image Variants" 중
    하나가 사라진다.
    """
    numbers: dict[tuple[str, str], int] = {}
    evidence: list[NumberedEvidence] = []
    numbered: list[Candidate] = []
    for candidate in candidates:
        own: list[int] = []
        for item in candidate.evidence:
            key = (item.repository, item.section_title)
            if key not in numbers:
                numbers[key] = len(evidence) + 1
                evidence.append(NumberedEvidence(number=numbers[key], **item.model_dump()))
            if numbers[key] not in own:
                own.append(numbers[key])
        numbered.append(candidate.model_copy(update={"evidence_numbers": own}))
    return evidence, numbered
