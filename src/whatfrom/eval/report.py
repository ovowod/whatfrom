# src/whatfrom/eval/report.py
from collections import Counter
from dataclasses import asdict, dataclass
from unicodedata import east_asian_width

from whatfrom.eval.goldenset import GoldenCase
from whatfrom.eval.scoring import CaseScore, RetrievalScore


@dataclass(frozen=True)
class Metric:
    label: str
    hits: int
    total: int

    @property
    def ratio(self) -> float | None:
        """분모가 0이면 None. 0%가 아니라 측정되지 않았다는 뜻이다."""
        return self.hits / self.total if self.total else None


@dataclass(frozen=True)
class ConstantBaseline:
    """질문을 무시하고 늘 같은 이미지 하나만 답하는 대조군의 점수.

    여러 문항이 같은 정답을 허용하면 질문을 분석하지 않아도 일부를 맞힐 수 있다.
    실제 추천 정확도가 이런 고정답 방식보다 높은지 비교하기 위한 기준선이다.
    """

    # 측정 문항이 없거나 accept가 하나도 없으면 고를 이미지가 없다.
    image: str | None
    hits: int
    total: int

    @property
    def ratio(self) -> float | None:
        """Metric.ratio와 같은 규칙. 분모가 0이면 0%가 아니라 미측정이다."""
        return self.hits / self.total if self.total else None


def constant_baseline(cases: list[GoldenCase]) -> ConstantBaseline:
    """측정 문항에서 가장 많이 정답으로 인정되는 이미지와 그 정확도를 구한다.

    accept 목록만 사용하며 DB 조회나 모델 호출은 없다. 같은 측정 문항으로
    고정답을 선택하고 평가하므로, 이 문항 집합에서 가능한 고정답의 최고 점수다.
    """
    # accept 안에 같은 이미지가 두 번 적혀도 한 문항은 한 표다.
    counts = Counter(image for case in cases for image in set(case.accept))
    if not counts:
        return ConstantBaseline(image=None, hits=0, total=len(cases))

    # 동점은 이미지 이름 오름차순으로 깬다. Counter.most_common은 동점일 때
    # 입력 순서를 따르므로 골든셋 문항 순서만 바뀌어도 답이 달라진다.
    image = min(counts, key=lambda candidate: (-counts[candidate], candidate))
    return ConstantBaseline(image=image, hits=counts[image], total=len(cases))


@dataclass(frozen=True)
class RandomBaseline:
    """후보 중 하나를 무작위로 고르는 대조군의 기대 정확도.

    후보에 정답이 많으면 질문을 읽지 않고 골라도 자주 맞는다. 추천 정확도가
    후보 구성 덕인지 판단하려면 이 값과 나란히 봐야 한다.
    """

    # 측정 문항이 없으면 None. 0%가 아니라 미측정이다.
    expected: float | None
    total: int


def random_baseline(scores: list[CaseScore] | list[RetrievalScore]) -> RandomBaseline:
    """문항마다 "후보 중 정답 수 ÷ 후보 수"를 구해 평균한다.

    후보를 모두 합쳐 나누지 않는다. 무작위 선택은 문항마다 따로 일어나므로, 합치면
    후보가 많은 문항이 결과를 좌우한다. 후보가 없는 문항은 맞힐 수 없으니 0으로 센다.
    분모는 전체 후보다. 정답 리포지토리의 후보로 좁히지 않는다.
    """
    if not scores:
        return RandomBaseline(expected=None, total=0)
    ratios = [s.accepted_count / s.candidate_count if s.candidate_count else 0.0 for s in scores]
    return RandomBaseline(expected=sum(ratios) / len(ratios), total=len(ratios))


@dataclass(frozen=True)
class Skipped:
    case_id: str
    missing: list[str]


def aggregate_full(scores: list[CaseScore]) -> list[Metric]:
    return [
        Metric("추천 정확도", sum(s.accurate for s in scores), len(scores)),
        Metric("후보 포함률", sum(s.candidate_hit for s in scores), len(scores)),
        Metric(
            "태그 실재율",
            sum(1 for s in scores if s.tag_real),
            sum(1 for s in scores if s.tag_real is not None),
        ),
        Metric(
            "조건 일치율",
            # 조건이 선언된 문항만 분자와 분모에 포함한다. 외부에서 모순된
            # CaseScore를 만들어도 조건 없는 문항의 성공을 더하지 않는다.
            sum(s.conditions_met for s in scores if s.conditions_declared),
            sum(s.conditions_declared for s in scores),
        ),
        Metric("출처 제공률", sum(s.sources_ok for s in scores), len(scores)),
        Metric(
            # Recall이 아니라 Hit이다. 기대 섹션 중 하나만 상위 5청크에서 나와도
            # 히트로 세므로, 관련 문서 전체 중 회수한 비율(Recall)이 아니다.
            "문서 Hit@5",
            # 위와 같은 이유로 hit_at5도 hit_declared로 걸러서 센다.
            sum(s.hit_at5 for s in scores if s.hit_declared),
            sum(s.hit_declared for s in scores),
        ),
    ]


def aggregate_retrieval(scores: list[RetrievalScore]) -> list[Metric]:
    return [
        Metric("후보 포함률", sum(s.candidate_hit for s in scores), len(scores)),
        Metric(
            "문서 Hit@5",
            sum(s.hit_at5 for s in scores),
            sum(s.hit_declared for s in scores),
        ),
    ]


def _display_width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글처럼 전각(W)·전각형(F)인 글자는 두 칸이다."""
    return sum(2 if east_asian_width(character) in "WF" else 1 for character in text)


def _pad(text: str, width: int) -> str:
    """글자 수가 아니라 칸 수로 채운다. 한글 라벨을 :<14로 채우면 열이 어긋난다."""
    return text + " " * max(width - _display_width(text), 0)


# 지표 라벨 중 가장 넓은 "추천 정확도"가 11칸이다. 여유 한 칸을 더 둔다.
_LABEL_WIDTH = 12


def _format_metric(metric: Metric) -> str:
    if metric.ratio is None:
        return f"  {_pad(metric.label, _LABEL_WIDTH)} {'0/0':>7}       —"
    fraction = f"{metric.hits}/{metric.total}"
    return f"  {_pad(metric.label, _LABEL_WIDTH)} {fraction:>7}   {metric.ratio:6.1%}"


def _format_baseline(baseline: ConstantBaseline) -> str:
    """여섯 지표와 섞이지 않게 들여쓰기와 머리표를 다르게 준다."""
    if baseline.image is None or baseline.ratio is None:
        return "※ 고정답 대조군: 측정 문항이 없어 계산할 수 없다 (지표 아님)"
    fraction = f"{baseline.hits}/{baseline.total}"
    return (
        f"※ 고정답 대조군 — 질문을 무시하고 늘 {baseline.image}만 답하면 "
        f"{fraction} {baseline.ratio:.1%} (지표 아님)"
    )


def _format_random_baseline(baseline: RandomBaseline) -> str:
    if baseline.expected is None:
        return "※ 무작위 선택 대조군: 측정 문항이 없어 계산할 수 없다 (지표 아님)"
    return (
        f"※ 무작위 선택 대조군 — 후보 중 하나를 무작위로 고르면 기대 정확도 "
        f"{baseline.expected:.1%} (측정 {baseline.total}문항, 지표 아님)"
    )


def _failure_line(score: CaseScore, case: GoldenCase | None, width: int) -> str:
    """실패 문항을 출력한다. width는 목록에서 가장 긴 문항 ID의 표시 너비다."""
    case_id = _pad(score.case_id, width)
    if score.recommended_image is None:
        note = score.degraded_note or "사유 없음"
        return f"  {case_id} 추천 없음 ({note})"

    accept = case.accept if case is not None else []
    expected = accept[0] if accept else "?"
    more = f" 외 {len(accept) - 1}" if len(accept) > 1 else ""
    flag = "  [명시적 오답]" if score.rejected_pick else ""
    return f"  {case_id} 기대 {expected}{more} → 실제 {score.recommended_image}{flag}"


def render_summary(
    metrics: list[Metric],
    scores: list[CaseScore],
    cases: list[GoldenCase],
    skipped: list[Skipped],
    total_cases: int,
    meta: dict,
    baseline: ConstantBaseline | None = None,
    random: RandomBaseline | None = None,
) -> str:
    by_id = {case.id: case for case in cases}
    missing_repos = sorted({repo for s in skipped for repo in s.missing})

    # llm_model은 fake 프로바이더일 때 일부러 None이다. 그대로 찍으면 LLM이 아예
    # 안 돈 것처럼 보이니, 모델명이 없으면 프로바이더 이름으로 대신한다.
    # retrieval-only에서는 llm_provider도 None이라 결국 '-'로 떨어진다.
    llm_label = meta.get("llm_model") or meta.get("llm_provider") or "-"
    lines = [
        f"whatfrom eval — {meta.get('started_at', '')}  "
        f"(llm={llm_label}, embedder={meta.get('embedder', '-')})",
    ]
    # 측정 문항 수는 cases에서 센다. scores로 세면 --retrieval-only가 빈 scores를
    # 넘기는 탓에 '0문항 측정'으로 찍힌다.
    headline = f"골든셋 {total_cases}문항 중 {len(cases)}문항 측정"
    if skipped:
        headline += f" · {len(skipped)}문항 미측정 ({', '.join(missing_repos)} 미색인)"
    # --tags로 걸러진 문항은 measured에도 skipped에도 없어 그냥 사라진 것처럼 보인다.
    # total_cases에서 측정·미측정을 빼면 태그 필터로 빠진 수가 나온다.
    tag_filtered = total_cases - len(cases) - len(skipped)
    if tag_filtered > 0:
        headline += f" · {tag_filtered}문항 태그 필터 제외"
    lines += [headline, ""]
    lines += [_format_metric(m) for m in metrics]
    # 후보만 있으면 계산되므로 고정답 대조군과 달리 검색 전용 모드에도 나온다.
    if random is not None:
        lines += ["", _format_random_baseline(random)]
    # 추천 정확도를 읽기 위한 기준선이므로 그것이 없는 실행(--retrieval-only)에는
    # 놓일 자리가 없다. 호출자가 넘겨도 여기서 걸러 둘이 어긋나지 않게 한다.
    if baseline is not None and any(m.label == "추천 정확도" for m in metrics):
        lines += ["", _format_baseline(baseline)]

    failures = [s for s in scores if not s.accurate]
    if failures:
        width = max(_display_width(s.case_id) for s in failures)
        lines += ["", f"실패 {len(failures)}문항:"]
        lines += [_failure_line(s, by_id.get(s.case_id), width) for s in failures]

    return "\n".join(lines)


def result_document(
    metrics: list[Metric],
    scores: list[CaseScore] | list[RetrievalScore],
    skipped: list[Skipped],
    meta: dict,
    baseline: ConstantBaseline | None = None,
    random: RandomBaseline | None = None,
) -> dict:
    """저장용 JSON. 실행 메타가 없으면 나중에 점수를 비교할 수 없다."""
    document: dict = {
        "meta": meta,
        "metrics": [asdict(m) | {"ratio": m.ratio} for m in metrics],
        "cases": [asdict(s) for s in scores],
        "skipped": [asdict(s) for s in skipped],
    }
    # 전달받은 대조군을 저장한다. 검색 전용 실행에서는 호출자가 None을 넘긴다.
    if baseline is not None:
        document["constant_baseline"] = asdict(baseline) | {"ratio": baseline.ratio}
    if random is not None:
        document["random_baseline"] = asdict(random)
    return document
