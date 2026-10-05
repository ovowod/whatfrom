# src/whatfrom/eval/citations.py
"""인용 뒷받침률을 사람이 표본으로 재는 도구 (design §10).

eval 결과 파일에서 검증을 통과한 (진술, 인용) 쌍을 뽑아, 어느 설정의 결과인지 가린
YAML 표본과 원래 위치를 담은 key를 만든다. 사람이 표본의 verdict를 채우면 key와
대조해 안(결과 파일)마다 뒷받침률과 합격 여부를 낸다. LLM judge는 쓰지 않는다.
"""

import hashlib
import json
import random
from dataclasses import dataclass, field

import yaml

PER_ARM = 50
PER_CASE = 2
# 합격선. 표본 50쌍이면 45/50이다.
PASS_RATIO = 0.9
# unclear가 이보다 많으면 진술이나 인용 단위에 문제가 있다고 보고 합격 여부를 보류한다.
MAX_UNCLEAR_RATIO = 0.1
VERDICTS = ("supported", "unsupported", "unclear")


def _pairs(document: dict) -> list[dict]:
    """결과 파일 하나에서 검증을 통과한 (진술, 인용) 쌍을 모은다.

    지금의 진술은 단일 선택의 주장 문장이고, 그 주장이 가리키는 이미지는 추천 이미지다.
    """
    pairs = []
    for case in document.get("cases", []):
        recommendation = case.get("recommendation")
        if recommendation is None:
            continue
        evidence = {e["number"]: e for e in case.get("evidence") or []}
        for claim_index, claim in enumerate(recommendation["claims"]):
            for citation_index, citation in enumerate(claim["citations"]):
                # 글자 그대로 검증에 실패한 인용은 인용 검증 통과율이 이미 센다.
                if not citation["verified"]:
                    continue
                source = evidence.get(citation["evidence"])
                # 검증을 통과한 인용은 그 요청의 근거에서 나왔다. 없으면 결과 파일이 깨졌다.
                if source is None:
                    raise ValueError(
                        f"{case['case_id']}: 검증을 통과한 인용의 근거 {citation['evidence']}번이 "
                        "결과 파일에 없다"
                    )
                pairs.append(
                    {
                        "case_id": case["case_id"],
                        "claim": claim_index,
                        "citation": citation_index,
                        "item": {
                            "image": recommendation["image"],
                            "statement": claim["text"],
                            "quote": citation["quote"],
                            "evidence": f"{source['repository']} — {source['section_title']}",
                        },
                    }
                )
    return pairs


def _choose(pairs: list[dict], rng: random.Random) -> list[dict]:
    """문항당 최대 PER_CASE쌍을 고른 뒤, 그중 최대 PER_ARM쌍을 고른다.

    한 문항이 인용을 많이 달아도 표본을 독차지하지 못하게 한다. 문항 안에서는 서로 다른
    주장의 인용을 먼저 고른다. 같은 주장의 인용 두 개보다 문항의 주장을 더 넓게 본다.
    """
    by_case: dict[str, list[dict]] = {}
    for pair in pairs:
        by_case.setdefault(pair["case_id"], []).append(pair)
    chosen = []
    for case_id in sorted(by_case):
        by_claim: dict[int, list[dict]] = {}
        for pair in by_case[case_id]:
            by_claim.setdefault(pair["claim"], []).append(pair)
        # 주장마다 인용 순서를 섞고, 주장 순서도 섞은 뒤 주장을 번갈아 돌며 하나씩 꺼낸다.
        queues = [rng.sample(group, len(group)) for _, group in sorted(by_claim.items())]
        rng.shuffle(queues)
        picked: list[dict] = []
        while len(picked) < PER_CASE and any(queues):
            for queue in queues:
                if queue and len(picked) < PER_CASE:
                    picked.append(queue.pop())
        chosen += picked
    return rng.sample(chosen, min(PER_ARM, len(chosen)))


def _digest(item: dict) -> str:
    """verdict 말고 다른 칸이 바뀌었는지 확인하는 hash."""
    content = {k: v for k, v in item.items() if k != "verdict"}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def sample_citations(results: list[tuple[str, dict]], seed: int) -> tuple[list[dict], dict]:
    """(결과 파일 이름, 결과 문서) 목록에서 표본 항목과 key를 만든다. 같은 seed면 같은 표본이다.

    항목에는 결과 파일 이름이 들어가지 않는다. 순서를 섞고 익명 id를 붙여 채점하는 사람이
    어느 안의 인용인지 모르게 한다. 원래 위치는 key에만 남긴다.
    """
    names = [name for name, _ in results]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        # 이름이 겹치면 채점 집계에서 두 안이 하나로 합쳐진다.
        raise ValueError(f"안 이름이 겹친다: {', '.join(repeated)}")
    rng = random.Random(seed)
    drawn = [(name, pair) for name, document in results for pair in _choose(_pairs(document), rng)]
    rng.shuffle(drawn)

    items: list[dict] = []
    entries: dict[str, dict] = {}
    for index, (name, pair) in enumerate(drawn, start=1):
        item = {"id": f"c{index:03d}", **pair["item"], "verdict": None}
        items.append(item)
        entries[item["id"]] = {
            "arm": name,
            "case_id": pair["case_id"],
            "claim": pair["claim"],
            "citation": pair["citation"],
            "hash": _digest(item),
        }
    return items, {"seed": seed, "arms": names, "items": entries}


def dump_sample(items: list[dict]) -> str:
    return yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=1000)


@dataclass
class ArmScore:
    arm: str
    sampled: int = 0
    supported: int = 0
    unsupported: int = 0
    unclear: int = 0
    blank: int = 0
    # 표본 전체에 손상이나 빈 칸이 있으면 None이다.
    outcome: str | None = None

    @property
    def scorable(self) -> int:
        return self.supported + self.unsupported

    @property
    def ratio(self) -> float | None:
        """unclear는 분모에서 뺀다. 채점할 수 있는 쌍이 없으면 미측정이다."""
        return self.supported / self.scorable if self.scorable else None

    @property
    def short(self) -> bool:
        return self.sampled < PER_ARM

    def judge(self) -> str:
        if self.ratio is None:
            return "미측정"
        if self.unclear > self.sampled * MAX_UNCLEAR_RATIO:
            return "보류"
        return "합격" if self.ratio >= PASS_RATIO else "불합격"


@dataclass
class CitationScore:
    arms: list[ArmScore]
    # 하나라도 있으면 어느 안의 합격 여부도 내지 않는다.
    problems: list[str] = field(default_factory=list)


def score_citations(sample_text: str, key: dict) -> CitationScore:
    """채운 표본을 key와 대조해 안마다 집계한다.

    빈 칸, 세 어휘 밖의 값, id 중복, 빠지거나 key에 없는 행, verdict 밖의 내용 변경이
    하나라도 있으면 합격 여부를 내지 않는다. 덜 채우거나 손상된 표본으로 합격을
    선언하지 않기 위해서다.
    """
    items = yaml.safe_load(sample_text) or []
    entries = key["items"]
    arms = {name: ArmScore(arm=name) for name in key["arms"]}
    problems: list[str] = []

    seen: set[str] = set()
    for item in items:
        item_id = item.get("id")
        if item_id in seen:
            problems.append(f"중복된 id: {item_id}")
            continue
        seen.add(item_id)
        entry = entries.get(item_id)
        if entry is None:
            problems.append(f"key에 없는 id: {item_id}")
            continue
        if _digest(item) != entry["hash"]:
            problems.append(f"verdict 밖의 내용이 바뀌었다: {item_id}")
        arm = arms.get(entry["arm"])
        if arm is None:
            problems.append(f"key의 안 목록에 없는 안 {entry['arm']}: {item_id}")
            continue
        arm.sampled += 1
        verdict = item.get("verdict")
        if verdict is None:
            arm.blank += 1
        elif verdict not in VERDICTS:
            # 따옴표 없는 yes, no는 YAML이 bool로 읽는다. 문자열이 아니면 모두 잘못된 값이다.
            problems.append(f"알 수 없는 verdict {verdict!r}: {item_id}")
        else:
            setattr(arm, verdict, getattr(arm, verdict) + 1)

    blank = sum(arm.blank for arm in arms.values())
    if blank:
        problems.append(f"채점하지 않은 항목 {blank}개")
    # 빠진 행은 채점하지 않은 것으로 센다. 지우면 빈 칸 검사를 피할 수 있기 때문이다.
    missing = sorted(set(entries) - seen)
    if missing:
        problems.append(f"빠진 행 {len(missing)}개: {', '.join(missing)}")
        for item_id in missing:
            arm = arms.get(entries[item_id]["arm"])
            if arm is not None:
                arm.sampled += 1
                arm.blank += 1

    for arm in arms.values():
        arm.outcome = None if problems else arm.judge()
    return CitationScore(arms=list(arms.values()), problems=problems)


def render_citation_score(score: CitationScore) -> str:
    """안마다 집계를 보여 준다. 문제가 있으면 합격 여부만 비우고 집계는 그대로 낸다."""
    lines = [f"인용 뒷받침률 (합격선 {PASS_RATIO:.0%}, unclear는 분모에서 뺀다)"]
    for arm in score.arms:
        ratio = "—" if arm.ratio is None else f"{arm.ratio:.1%}"
        outcome = arm.outcome or "판정 안 함"
        short = " · 50쌍 미만" if arm.short else ""
        lines.append(
            f"  {arm.arm}: {arm.supported}/{arm.scorable} {ratio} {outcome}"
            f" (표본 {arm.sampled}, unclear {arm.unclear}, 빈 칸 {arm.blank}{short})"
        )
    return "\n".join(lines)
