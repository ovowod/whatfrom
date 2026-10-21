# src/whatfrom/eval/citations.py
"""인용이 진술에 맞게 붙었는지 표본으로 검토하는 도구 (design §10).

eval 결과 파일에서 검증을 통과한 인용이 붙은 진술(단일 선택의 주장, 판정 단계의 판정 칸)을
뽑아, 어느 설정의 결과인지 가린 YAML 표본과 원래 위치를 담은 key를 만든다. 표본의 verdict와
candidate_ok를 채우면 key와 대조해 안마다 인용 뒷받침률과 후보 정보 오류를 낸다.
"""

import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass, field

import yaml

PER_ARM = 50
PER_CASE = 2
# 합격선. 표본 50개면 45/50이다.
PASS_RATIO = 0.9
# unclear가 이보다 많으면 주장이나 인용 단위에 문제가 있다고 보고 합격 여부를 보류한다.
MAX_UNCLEAR_RATIO = 0.1
# no_doc_fact는 인용으로 확인할 문서 사실이 없는 주장이다. 분모에서도 unclear 한도에서도 뺀다.
VERDICTS = ("supported", "unsupported", "unclear", "no_doc_fact")
CANDIDATE_CHECKS = ("match", "mismatch", "unclear", "not_applicable")
# 검토하는 사람이 채우는 칸. hash에서 뺀다.
FILLED = ("verdict", "candidate_ok")

GUIDE = """\
# 인용 검토 표본: 채점 방법
#
# 항목마다 verdict와 candidate_ok 두 칸을 채운다. 아래 값만 쓰고 따옴표는 붙이지 않는다.
# 판단에는 그 항목의 question, statement, citations, candidates만 쓴다.
# README를 따로 찾아보거나, 다른 항목의 인용을 빌려 오거나, 아는 지식으로 채우지 않는다.
#
# ── 1단계. statement를 사실 단위로 나눈다 ─────────────────────────────
# 문장 하나에 사실이 여러 개 들어 있을 수 있다.
# 쉼표, "~이므로", "~지만"으로 이어진 부분을 따로 본다.
#
# ── 2단계. 사실마다 출처를 하나로 정한다 ───────────────────────────────
#   조건   question에 사용자가 적은 내용.
#          예: "3.12로 고정해야 한다", "arm64에서만 돈다", "확장은 안 쓴다"
#   후보   candidates 줄로 알 수 있는 내용. 크기(MB), 아키텍처, pushed 날짜, 후보끼리의 비교
#          ("가장 작다", "모든 후보가 arm64를 지원한다").
#          태그 이름이 후보에 있다는 것만으로는 후보 사실이 아니다. 다만 "후보 목록에 X가 있다",
#          "패치 버전 태그는 후보에 없다"처럼 후보 목록에 무엇이 있거나 없다고 명시한 부분은
#          후보 사실이다. 같은 문장의 그 태그에 관한 다른 내용(예: 1.30은 stable 브랜치다)은
#          문서 사실로 따로 본다.
#   문서   그 밖에 이미지나 소프트웨어에 관한 사실. 예: "slim에는 컴파일 도구가 없다",
#          "alpine은 musl libc를 쓴다", "18부터 볼륨 경로가 바뀌었다",
#          "24.04와 noble은 같은 태그다".
#          statement가 "README는 ~라고 한다", "공식 문서에 따르면"이라고 하면
#          그 내용은 문서 사실이다.
#   결론   무엇을 고르라는, 권한다는, 적합하다는 판단. 예: "node:24를 쓰세요", "slim이 적절하다"
#   모름   "근거만으로는 알 수 없다", "확인할 수 없다"처럼 모른다고 말하는 부분
#
# ── 3단계. verdict를 정한다 ───────────────────────────────────────────
# 문서 사실이 하나도 없으면                         → no_doc_fact
# 문서 사실이 있으면 하나씩 citations와 대조한다.
#   확인됨      citations의 quote가 그 사실을 직접 말한다. 표현이 달라도 뜻이 같으면 된다.
#               quote 여러 개를 합쳐서 확인돼도 된다.
#   확인 안 됨  quote가 주제만 같다 / 다른 변형이나 다른 버전에 관한 문장이다
#               (예: 사실은 alpine-slim에 관한 것인데 quote는 slim 절) / 사실의 일부만 담는다
#               (예: 사실은 "X이고 Y다"인데 quote에는 X만 있다)
#               / quote가 태그 목록뿐이라 그 사실을 말하지 않는다
# 태그 목록 quote는 이렇게 본다.
#   - 태그 이름에 적힌 표시는 그 태그에 대한 사실로 확인된다.
#     예: `3.15-rc`는 RC다. `stable`과 같은 줄의 `1.30`은 stable 브랜치다.
#   - 표시가 없다는 것으로 상태를 추론한 사실은 확인 안 됨이다.
#     예: rc가 붙지 않았으니 정식 릴리스다.
#   - "최신", "가장 높은 버전" 같은 비교는 인용된 줄만으로는 확인 안 됨이다.
#     인용 밖에 더 높은 버전이 없다는 것을 그 줄들이 말하지 않는다.
#   - 지원 태그 목록에 있다는 것은 "지원된다"까지만 확인된다. "안정"은 따로 확인한다.
# 모든 문서 사실이 확인됨                           → supported
# 하나라도 확인 안 됨                               → unsupported
# quote가 잘려 뜻이 둘로 읽히는 등, 확인됨인지 아닌지 정말 정할 수 없을 때만 → unclear
#   (unclear가 표본의 10%를 넘으면 합격 여부가 보류된다. 망설여지면 unsupported 쪽으로 정한다.)
# 조건, 후보, 결론, 모름은 verdict에서 보지 않는다. 다만:
#   - 결론이 statement에 없는 사실에 기대면 그 사실도 문서 사실로 보고 확인한다.
#     결론이나 다른 사실이 성립하려면 반드시 참이어야 하는 사실만 이렇게 본다.
#     있으면 더 설득력 있는 배경 지식은 채점하지 않는다.
#     예: "빌드 안정성을 우선하면 alpine보다 slim이 적절하다"는 slim에 그 문제가 없다는
#     사실이 있어야 성립하므로, 그것도 확인한다.
#     예: "기본 이미지를 쓰세요"의 이유로 "기본 이미지에는 빌드 도구가 있다"가 필요하다면
#     그것도 확인한다.
#   - 결론이 확인된 사실, 조건, 후보 정보로 이어지면 결론 자체는 문서에 쓰여 있지 않아도 된다.
#   - "README는 X라고 하지만 Y는 알 수 없다"면 X만 확인한다.
#
# ── 4단계. candidate_ok를 정한다 ──────────────────────────────────────
# 후보 사실이 하나도 없으면                         → not_applicable
# 후보 사실을 candidates 줄과 하나씩 대조한다. 지금 DB나 Docker Hub와 대조하지 않는다.
# 모두 맞으면                                       → match
# 하나라도 틀리면 (숫자가 다르다, 비교가 틀렸다)     → mismatch
# candidates로 확인할 수 없으면 (목록에 없는 태그의 크기 등) → unclear
#
# ── 예시 ──────────────────────────────────────────────────────────────
# (1) statement: PostGIS처럼 postgres-contrib에 없는 확장은 Alpine에서 직접 컴파일해야 하므로,
#                PostGIS에는 기본 Debian 변형이 더 간단하다.
#     quote: When using the Alpine variants, any postgres extension not listed in postgres-contrib
#            will need to be compiled in your own image
#     문서 사실: ① PostGIS는 contrib에 없다  ② contrib에 없는 확장은 Alpine에서 컴파일해야 한다
#                ③ Debian 변형에서는 설치가 간단하다
#     ②만 확인됨 → verdict: unsupported, candidate_ok: not_applicable
# (2) statement: node:24-alpine은 59 MB로 세 후보 중 가장 작지만, musl libc를 써서 호환성 문제가
#                생길 수 있다.
#     quote: ... it does use musl libc instead of glibc ...,
#            so software will often run into issues ...
#     문서 사실: musl을 써서 호환성 문제 → 확인됨 → verdict: supported
#     후보 사실: 59 MB, 셋 중 가장 작다 → candidates와 맞으면 match, 아니면 mismatch
# (3) statement: 태그 7이 패치 버전에 고정되는지는 제공된 근거만으로 확인할 수 없다.
#     문서 사실 없음(모름만 있음) → verdict: no_doc_fact, candidate_ok: not_applicable
# (4) statement: nginx:1.31-alpine-slim은 공간 제약이 있을 때 쓰는 변형이다.
#     quote: (nginx:<version>-slim 절의 문장)
#     사실은 alpine-slim에 관한 것인데 quote는 slim 절이다 → 확인 안 됨 → verdict: unsupported
#
# 값 정리
#   verdict:      supported | unsupported | unclear | no_doc_fact
#   candidate_ok: match | mismatch | unclear | not_applicable
#
"""


# advisor.build_prompt가 후보 목록 앞에 다는 제목. 단일 선택과 판정 단계가 다르다.
# eval은 recommend를 import하지 않아(계층 규칙) 문구를 여기 다시 적는다.
CANDIDATE_HEADINGS = ("Candidates (choose exactly one):", "Candidates (judge every one):")


def _prompt_parts(case: dict) -> tuple[str, list[str]]:
    """LLM에게 넘긴 prompt에서 질문과 후보 줄을 꺼낸다.

    후보 정보는 지금 DB가 아니라 그 실행에서 실제로 넘긴 값과 대조해야 한다. 결과 파일에는
    그 값이 advise_prompt로만 남아 있어 advisor.build_prompt의 형식을 읽는다.
    """
    prompt = case.get("advise_prompt") or ""
    for heading in CANDIDATE_HEADINGS:
        head, marker, rest = prompt.partition(f"\n\n{heading}\n")
        if marker:
            break
    if not head.startswith("Requirement: ") or not marker:
        raise ValueError(f"{case['case_id']}: 결과 파일에 LLM에게 넘긴 prompt가 없다")
    candidates = rest.split("\n\n", 1)[0].splitlines()
    # 질문의 줄바꿈은 YAML에서 빈 줄로 보여 읽기 어려워 공백으로 합친다.
    question = " ".join(head.removeprefix("Requirement: ").split())
    return question, [line.removeprefix("- ") for line in candidates]


def _verified_citations(case: dict, citations: list[dict]) -> list[dict]:
    """검증을 통과한 인용만, 근거 번호를 repository와 section 제목으로 풀어 돌려준다.

    글자 그대로 검증에 실패한 인용은 인용 검증 통과율이 이미 센다.
    """
    evidence = {e["number"]: e for e in case.get("evidence") or []}
    verified = []
    for citation in citations:
        if not citation["verified"]:
            continue
        source = evidence.get(citation["evidence"])
        # 검증을 통과한 인용은 그 요청의 근거에서 나왔다. 없으면 결과 파일이 깨졌다.
        if source is None:
            raise ValueError(
                f"{case['case_id']}: 검증을 통과한 인용의 근거 {citation['evidence']}번이 "
                "결과 파일에 없다"
            )
        verified.append(
            {
                "quote": citation["quote"],
                "evidence": f"{source['repository']} — {source['section_title']}",
            }
        )
    return verified


def _statements(document: dict) -> list[dict]:
    """결과 파일 하나에서 검증을 통과한 인용이 하나 이상 붙은 진술을 모은다.

    진술은 단일 선택의 주장이나 판정 단계의 판정 칸이다. 한 진술의 인용은 함께 본다.
    인용 두 개를 합쳐야 성립하는 진술이 있기 때문이다. locator는 key에 남길 진술의 위치다.
    """
    statements = []
    for case in document.get("cases", []):
        for locator, image, text, citations in _case_statements(case):
            verified = _verified_citations(case, citations)
            if not verified:
                continue
            question, candidates = _prompt_parts(case)
            statements.append(
                {
                    "case_id": case["case_id"],
                    "locator": locator,
                    "item": {
                        "question": question,
                        "image": image,
                        "statement": text,
                        "citations": verified,
                        "candidates": candidates,
                    },
                }
            )
    return statements


VERDICT_WORDS = {"satisfied": "만족한다", "violated": "어긴다"}


def _case_statements(case: dict) -> list[tuple[dict, str, str, list[dict]]]:
    """문항 하나의 (위치, 이미지, 진술 문장, 인용) 목록.

    주장이 가리키는 이미지는 추천 이미지다. 판정 칸은 만족과 불만족만 진술이 되고, 그 칸의
    후보를 가리킨다. 알 수 없음 칸은 판정하지 않은 것이고, 버린 판정은 비교표에 없다.
    """
    found = []
    recommendation = case.get("recommendation")
    if recommendation is not None:
        for index, claim in enumerate(recommendation["claims"]):
            found.append(
                ({"claim": index}, recommendation["image"], claim["text"], claim["citations"])
            )
    judgments = case.get("judgments")
    if judgments is not None:
        for cell in judgments["judgments"]:
            if cell["verdict"] not in VERDICT_WORDS:
                continue
            requirement = judgments["requirements"][cell["requirement"] - 1]
            text = f"{cell['candidate']}가 요구 '{requirement}'를 {VERDICT_WORDS[cell['verdict']]}"
            locator = {"candidate": cell["candidate"], "requirement": cell["requirement"]}
            found.append((locator, cell["candidate"], text, cell["citations"]))
    return found


def _choose(
    statements: list[dict],
    rng: random.Random,
    per_case: int,
    limit: int,
    taken: Counter[str],
) -> list[dict]:
    """문항당 최대 per_case개를 고른 뒤, 그중 최대 limit개를 고른다.

    한 문항이 진술을 많이 내도 표본을 독차지하지 못하게 한다. taken은 같은 안의 앞 결과
    파일에서 이미 고른 문항별 수다. 문항당 상한은 묶은 안 전체에 적용한다.
    """
    by_case: dict[str, list[dict]] = {}
    for statement in statements:
        by_case.setdefault(statement["case_id"], []).append(statement)
    chosen = []
    for case_id in sorted(by_case):
        room = max(per_case - taken[case_id], 0)
        chosen += rng.sample(by_case[case_id], min(room, len(by_case[case_id])))
    return rng.sample(chosen, min(limit, len(chosen)))


def _digest(item: dict) -> str:
    """검토하는 칸 말고 다른 칸이 바뀌었는지 확인하는 hash."""
    content = {k: v for k, v in item.items() if k not in FILLED}
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def sample_citations(
    results: list[tuple[str, dict]],
    seed: int,
    per_case: int = PER_CASE,
    cases: set[str] | None = None,
    arm_of: dict[str, str] | None = None,
) -> tuple[list[dict], dict]:
    """(결과 파일 이름, 결과 문서) 목록에서 표본 항목과 key를 만든다. 같은 seed면 같은 표본이다.

    항목에는 결과 파일 이름이 들어가지 않는다. 순서를 섞고 익명 id를 붙여 검토하는 쪽이
    어느 안의 진술인지 모르게 한다. 원래 위치는 key에만 남긴다.

    cases를 주면 그 문항의 진술만 뽑는다. arm_of는 결과 파일을 안으로 묶는다. 같은 안의
    파일은 results 순서대로, 앞 파일에서 먼저 뽑고 PER_ARM개가 안 되면 다음 파일에서 채운다.
    주지 않으면 결과 파일 하나가 한 안이다.
    """
    names = [name for name, _ in results]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        # 이름이 겹치면 채점 집계에서 두 안이 하나로 합쳐진다.
        raise ValueError(f"결과 파일 이름이 겹친다: {', '.join(repeated)}")
    arm_of = arm_of or {}
    arms: dict[str, list[tuple[str, dict]]] = {}
    for name, document in results:
        arms.setdefault(arm_of.get(name, name), []).append((name, document))

    rng = random.Random(seed)
    drawn = []
    for arm, sources in arms.items():
        taken: Counter[str] = Counter()
        for source, document in sources:
            room = PER_ARM - sum(taken.values())
            if room == 0:
                break
            statements = [
                st for st in _statements(document) if cases is None or st["case_id"] in cases
            ]
            chosen = _choose(statements, rng, per_case, room, taken)
            taken.update(st["case_id"] for st in chosen)
            drawn += [(arm, source, st) for st in chosen]
    rng.shuffle(drawn)

    items: list[dict] = []
    entries: dict[str, dict] = {}
    for index, (arm, source, statement) in enumerate(drawn, start=1):
        item = {"id": f"c{index:03d}", **statement["item"], "verdict": None, "candidate_ok": None}
        items.append(item)
        # 묶은 파일들에서 같은 문항, 같은 위치가 나와도 source로 가른다.
        entries[item["id"]] = {
            "arm": arm,
            "source": source,
            "case_id": statement["case_id"],
            **statement["locator"],
            "hash": _digest(item),
        }
    return items, {"seed": seed, "arms": list(arms), "items": entries}


def dump_sample(items: list[dict]) -> str:
    """검토 기준을 머리 주석으로 붙인다. YAML로 읽을 때는 무시된다."""
    return GUIDE + yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=1000)


@dataclass
class ArmScore:
    arm: str
    sampled: int = 0
    supported: int = 0
    unsupported: int = 0
    unclear: int = 0
    no_doc_fact: int = 0
    blank: int = 0
    # candidate_ok 집계. 인용 뒷받침률과 합치지 않는 보조 항목이다.
    candidate_match: int = 0
    candidate_mismatch: int = 0
    candidate_unclear: int = 0
    candidate_not_applicable: int = 0
    # 표본 전체에 손상이나 빈 칸이 있으면 None이다.
    outcome: str | None = None

    @property
    def scorable(self) -> int:
        return self.supported + self.unsupported

    @property
    def ratio(self) -> float | None:
        """unclear는 분모에서 뺀다. 판정할 수 있는 주장이 없으면 미측정이다."""
        return self.supported / self.scorable if self.scorable else None

    @property
    def candidate_checked(self) -> int:
        return self.candidate_match + self.candidate_mismatch

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

    빈 칸, 정한 어휘 밖의 값, id 중복, 빠지거나 key에 없는 행, 검토 칸 밖의 내용 변경이
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
            problems.append(f"검토 칸 밖의 내용이 바뀌었다: {item_id}")
        arm = arms.get(entry["arm"])
        if arm is None:
            problems.append(f"key의 안 목록에 없는 안 {entry['arm']}: {item_id}")
            continue
        arm.sampled += 1
        verdict = item.get("verdict")
        check = item.get("candidate_ok")
        # 해당 없음도 not_applicable로 적는다. 빈 칸은 검토하지 않은 것과 구분되지 않는다.
        if verdict is None or check is None:
            arm.blank += 1
        # 따옴표 없는 yes, no, true는 YAML이 bool로 읽는다. 그래서 어휘에 쓰지 않고,
        # 문자열이 아니면 모두 잘못된 값으로 본다.
        if verdict is not None and verdict not in VERDICTS:
            problems.append(f"알 수 없는 verdict {verdict!r}: {item_id}")
        elif verdict is not None:
            setattr(arm, verdict, getattr(arm, verdict) + 1)
        if check is not None and check not in CANDIDATE_CHECKS:
            problems.append(f"알 수 없는 candidate_ok {check!r}: {item_id}")
        elif check is not None:
            setattr(arm, f"candidate_{check}", getattr(arm, f"candidate_{check}") + 1)

    blank = sum(arm.blank for arm in arms.values())
    if blank:
        problems.append(f"다 채우지 않은 항목 {blank}개")
    # 빠진 행은 검토하지 않은 것으로 센다. 지우면 빈 칸 검사를 피할 수 있기 때문이다.
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
        short = " · 50개 미만" if arm.short else ""
        lines.append(
            f"  {arm.arm}: {arm.supported}/{arm.scorable} {ratio} {outcome}"
            f" (표본 {arm.sampled}, unclear {arm.unclear}, 문서 사실 없음 {arm.no_doc_fact},"
            f" 빈 칸 {arm.blank}{short})"
        )
    lines.append("후보 정보 오류 (보조 항목, 합격선 없음)")
    for arm in score.arms:
        lines.append(
            f"  {arm.arm}: 불일치 {arm.candidate_mismatch}/{arm.candidate_checked}"
            f" (unclear {arm.candidate_unclear}, 해당 없음 {arm.candidate_not_applicable})"
        )
    return "\n".join(lines)
