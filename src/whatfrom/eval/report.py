# src/whatfrom/eval/report.py
from collections import Counter
from dataclasses import asdict, dataclass, fields
from statistics import median
from unicodedata import east_asian_width

from whatfrom.eval.goldenset import GoldenCase
from whatfrom.eval.scoring import CaseScore, PlanScore, RetrievalScore
from whatfrom.eval.timing import RunTrace


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
    # 입력 순서를 따르므로 golden set 문항 순서만 바뀌어도 답이 달라진다.
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
    분모는 전체 후보다. 정답 repository의 후보로 좁히지 않는다.
    """
    if not scores:
        return RandomBaseline(expected=None, total=0)
    ratios = [s.accepted_count / s.candidate_count if s.candidate_count else 0.0 for s in scores]
    return RandomBaseline(expected=sum(ratios) / len(ratios), total=len(ratios))


@dataclass(frozen=True)
class TopCandidateBaseline:
    """판정 없이 검색 1위 후보를 추천으로 본 대조군의 정확도.

    추천 정확도가 이보다 높아야 LLM이 검색 순위보다 나은 판단을 했다고 말할 수 있다.
    """

    hits: int
    total: int

    @property
    def ratio(self) -> float | None:
        """Metric.ratio와 같은 규칙. 분모가 0이면 0%가 아니라 미측정이다."""
        return self.hits / self.total if self.total else None


def top_candidate_baseline(
    scores: list[CaseScore] | list[RetrievalScore],
) -> TopCandidateBaseline:
    return TopCandidateBaseline(
        hits=sum(s.top_candidate_accepted for s in scores), total=len(scores)
    )


@dataclass(frozen=True)
class Skipped:
    case_id: str
    missing: list[str]


def aggregate_full(scores: list[CaseScore]) -> list[Metric]:
    return aggregate_recommendation(scores) + aggregate_plan(scores)


def aggregate_recommendation(scores: list[CaseScore], judged: bool = False) -> list[Metric]:
    """추천 단계까지 돈 실행의 지표. 조건 추출 지표는 aggregate_plan이 맡는다.

    judged는 판정 단계로 돈 실행인가다. 그때만 참고 정확도를 낸다.
    """
    return [
        Metric("추천 정확도", sum(s.accurate for s in scores), len(scores)),
        *([_reference_accuracy(scores)] if judged else []),
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
        _citation_pass_rate(scores),
        *_judgment_metrics(scores),
        _rejected_cases(scores),
        Metric(
            # Recall이 아니라 Hit이다. 기대 섹션 중 하나만 상위 5청크에서 나와도
            # 히트로 세므로, 관련 문서 전체 중 회수한 비율(Recall)이 아니다.
            "문서 Hit@5",
            # 위와 같은 이유로 hit_at5도 hit_declared로 걸러서 센다.
            sum(s.hit_at5 for s in scores if s.hit_declared),
            sum(s.hit_declared for s in scores),
        ),
    ]


# 판단 요구 label이 있는 문항과 없는 문항. 판정 단계는 요구가 없으면 검색 1위를 추천한다.
_GROUPS = {"요구 있음": True, "요구 없음": False}


def metric_groups(scores: list[CaseScore], judged: bool) -> dict[str, list[Metric]]:
    """추천 정확도, 검색 1위 대조군, 인용 검증 통과율(판정 단계면 참고 정확도도)을 묶음별로 낸다.

    묶음에 문항이 없으면 0%가 아니라 미측정이다(--tags로 한쪽만 남긴 실행).
    """
    groups = {}
    for name, has_requirements in _GROUPS.items():
        subset = [s for s in scores if s.has_requirements is has_requirements]
        groups[name] = [
            Metric("추천 정확도", sum(s.accurate for s in subset), len(subset)),
            *([_reference_accuracy(subset)] if judged else []),
            Metric("검색 1위 대조군", sum(s.top_candidate_accepted for s in subset), len(subset)),
            _citation_pass_rate(subset),
        ]
    return groups


def _reference_accuracy(scores: list[CaseScore]) -> Metric:
    """추천이 없으면 미확인 1위를 추천으로 본 정확도. 판정 실패 문항은 오답이다."""
    return Metric("참고 정확도", sum(s.reference_accurate for s in scores), len(scores))


def _citation_pass_rate(scores: list[CaseScore]) -> Metric:
    """인용 전체 중 글자 그대로 검증을 통과한 비율. 인용이 없으면 미측정이다.

    단일 선택은 주장의 인용을, 판정 단계는 LLM이 낸 판정 중 버리지 않은 것의 인용을 센다.
    중복 칸 자체에는 인용이 없고 원래 판정들(originals)에 있다. 그래서 칸과 originals를 함께
    세도 같은 인용을 두 번 세지 않는다.
    """
    citations = [
        citation["verified"]
        for s in scores
        if s.recommendation is not None
        for claim in s.recommendation["claims"]
        for citation in claim["citations"]
    ]
    citations += [
        citation["verified"]
        for s in scores
        if s.judgments is not None
        for cell in s.judgments["judgments"]
        for judgment in [cell, *cell["originals"]]
        for citation in judgment["citations"]
    ]
    return Metric("인용 검증 통과율", sum(citations), len(citations))


def _judgment_metrics(scores: list[CaseScore]) -> list[Metric]:
    """오답 적발률과 오판정률은 후보 단위로 센다. 판정이 없는 문항은 두 지표의 분모에서
    빠지므로, 판정이 없는 실행에서는 둘 다 0%가 아니라 미측정이다.

    판정 호출이 실패한 문항도 분모에서 빠진다. 그 수를 옆에 내서 감춰지지 않게 한다.
    """
    detected = total_rejected = misjudged = total_accepted = 0
    for s in scores:
        if s.unsatisfied_candidates is None:
            continue
        unsatisfied = set(s.unsatisfied_candidates)
        detected += sum(image in unsatisfied for image in s.rejected_candidates)
        total_rejected += len(s.rejected_candidates)
        misjudged += sum(image in unsatisfied for image in s.accepted_candidates)
        total_accepted += len(s.accepted_candidates)
    return [
        Metric("오답 적발률", detected, total_rejected),
        Metric("오판정률", misjudged, total_accepted),
        Metric(
            "판정 실패 문항",
            sum(s.judge_failed is True for s in scores),
            sum(s.judge_failed is not None for s in scores),
        ),
    ]


def _rejected_cases(scores: list[CaseScore] | list[RetrievalScore]) -> Metric:
    """명시적 오답 후보 문항. 오답 적발률이 잴 기회가 있던 문항 수다."""
    return Metric(
        "명시적 오답 후보 문항",
        sum(bool(s.rejected_candidates) for s in scores),
        len(scores),
    )


def aggregate_plan(scores: list[CaseScore] | list[PlanScore]) -> list[Metric]:
    """검색 조건 추출(LLM #1)의 지표. 전체 모드와 --plan-only가 함께 쓴다."""
    return [
        # 추출에 실패한 문항은 실패로 센다.
        Metric("repository 추출 일치율", sum(s.repository_extracted for s in scores), len(scores)),
        # 분모가 전체다. 조건이 없는 문항에서 없는 조건을 만들어내는 것도 실패다.
        Metric("조건 추출 일치율", sum(s.plan_matched for s in scores), len(scores)),
    ]


def aggregate_retrieval(scores: list[RetrievalScore]) -> list[Metric]:
    return [
        Metric("후보 포함률", sum(s.candidate_hit for s in scores), len(scores)),
        Metric(
            "문서 Hit@5",
            sum(s.hit_at5 for s in scores),
            sum(s.hit_declared for s in scores),
        ),
        _rejected_cases(scores),
    ]


def _display_width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글처럼 전각(W)·전각형(F)인 글자는 두 칸이다."""
    return sum(2 if east_asian_width(character) in "WF" else 1 for character in text)


def _pad(text: str, width: int) -> str:
    """글자 수가 아니라 칸 수로 채운다. 한글 라벨을 :<14로 채우면 열이 어긋난다."""
    return text + " " * max(width - _display_width(text), 0)


# 지표 label 중 가장 넓은 "repository 추출 일치율"이 22칸이다. 여유 두 칸을 더 둔다.
_LABEL_WIDTH = 24


def _format_metric(metric: Metric) -> str:
    if metric.ratio is None:
        return f"  {_pad(metric.label, _LABEL_WIDTH)} {'0/0':>7}       —"
    fraction = f"{metric.hits}/{metric.total}"
    return f"  {_pad(metric.label, _LABEL_WIDTH)} {fraction:>7}   {metric.ratio:6.1%}"


def _format_group(name: str, metrics: list[Metric]) -> str:
    """묶음 하나를 한 줄로. 문항 수는 추천 정확도의 분모다."""
    parts = [
        f"{m.label} {m.hits}/{m.total} {m.ratio:.1%}" if m.ratio is not None else f"{m.label} 0/0 —"
        for m in metrics
    ]
    return f"  {name} {metrics[0].total}문항 — " + " · ".join(parts)


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


def _format_top_baseline(baseline: TopCandidateBaseline) -> str:
    if baseline.ratio is None:
        return "※ 검색 1위 대조군: 측정 문항이 없어 계산할 수 없다 (지표 아님)"
    return (
        f"※ 검색 1위 대조군 — 판정 없이 검색 1위 후보를 고르면 "
        f"{baseline.hits}/{baseline.total} {baseline.ratio:.1%} (지표 아님)"
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


def _llm_label(stages: dict | None) -> str | None:
    """두 단계가 같은 모델이면 그 이름 하나, 다르면 단계별로. 부르지 않은 단계는 뺀다."""
    models = {
        stage: config["model"] for stage, config in (stages or {}).items() if config is not None
    }
    if not models:
        return None
    if len(models) == 2 and len(set(models.values())) == 1:
        return next(iter(models.values()))
    return " ".join(f"{stage}:{model}" for stage, model in models.items())


def render_summary(
    metrics: list[Metric],
    scores: list[CaseScore],
    cases: list[GoldenCase],
    skipped: list[Skipped],
    total_cases: int,
    meta: dict,
    baseline: ConstantBaseline | None = None,
    random: RandomBaseline | None = None,
    top: TopCandidateBaseline | None = None,
    groups: dict[str, list[Metric]] | None = None,
) -> str:
    by_id = {case.id: case for case in cases}
    missing_repos = sorted({repo for s in skipped for repo in s.missing})

    # llm_stages는 fake provider일 때 일부러 None이다. 그대로 찍으면 LLM이 아예
    # 안 돈 것처럼 보이니, 모델명이 없으면 프로바이더 이름으로 대신한다.
    # retrieval-only에서는 llm_provider도 None이라 결국 '-'로 떨어진다.
    llm_label = _llm_label(meta.get("llm_stages")) or meta.get("llm_provider") or "-"
    lines = [
        f"whatfrom eval — {meta.get('started_at', '')}  "
        f"(llm={llm_label}, embedder={meta.get('embedder', '-')})",
    ]
    # 측정 문항 수는 cases에서 센다. scores로 세면 --retrieval-only가 빈 scores를
    # 넘기는 탓에 '0문항 측정'으로 찍힌다.
    headline = f"golden set {total_cases}문항 중 {len(cases)}문항 측정"
    if skipped:
        headline += f" · {len(skipped)}문항 미측정 ({', '.join(missing_repos)} 미색인)"
    # --tags로 걸러진 문항은 measured에도 skipped에도 없어 그냥 사라진 것처럼 보인다.
    # total_cases에서 측정·미측정을 빼면 태그 필터로 빠진 수가 나온다.
    tag_filtered = total_cases - len(cases) - len(skipped)
    if tag_filtered > 0:
        headline += f" · {tag_filtered}문항 태그 필터 제외"
    lines += [headline, ""]
    lines += [_format_metric(m) for m in metrics]
    if groups is not None:
        lines += ["", "판단 요구 묶음:"]
        lines += [_format_group(name, group) for name, group in groups.items()]
    # 후보만 있으면 계산되므로 고정답 대조군과 달리 검색 전용 모드에도 나온다.
    if random is not None:
        lines += ["", _format_random_baseline(random)]
    if top is not None:
        lines += ["", _format_top_baseline(top)]
    # 추천 정확도를 읽기 위한 기준선이므로 그것이 없는 실행(--retrieval-only)에는
    # 놓일 자리가 없다. 호출자가 넘겨도 여기서 걸러 둘이 어긋나지 않게 한다.
    if baseline is not None and any(m.label == "추천 정확도" for m in metrics):
        lines += ["", _format_baseline(baseline)]

    # 전체 모드에서 러너가 잰 시간. 검색 전용 모드와 시간을 재지 않은 실행에는 없다.
    timed = [(s.case_id, s.trace.seconds_total) for s in scores if s.trace is not None]
    if timed:
        slowest_id, slowest_seconds = max(timed, key=lambda item: item[1])
        middle = median(seconds for _, seconds in timed)
        lines += [
            "",
            f"문항당 소요 시간: 중앙값 {middle:.1f}초, 최대 {slowest_seconds:.1f}초 ({slowest_id})",
        ]

    by_digest = [s.case_id for s in scores if s.accurate_by_digest]
    if by_digest:
        lines += [
            "",
            f"※ 이름은 달라도 정답과 digest가 같아 정답으로 인정: {len(by_digest)}문항 "
            f"({', '.join(by_digest)})",
        ]

    failures = [s for s in scores if not s.accurate]
    if failures:
        width = max(_display_width(s.case_id) for s in failures)
        lines += ["", f"실패 {len(failures)}문항:"]
        lines += [_failure_line(s, by_id.get(s.case_id), width) for s in failures]

    return "\n".join(lines)


def _case_record(score: CaseScore | RetrievalScore | PlanScore) -> dict:
    """문항 하나의 JSON. 시간 기록은 trace로 묶지 않고 펼쳐 기존 결과 파일과 같은 key를 쓴다."""
    record = asdict(score)
    if isinstance(score, CaseScore | PlanScore):
        trace = record.pop("trace")
        record |= trace if trace is not None else dict.fromkeys(f.name for f in fields(RunTrace))
    return record


def result_document(
    metrics: list[Metric],
    scores: list[CaseScore] | list[RetrievalScore] | list[PlanScore],
    skipped: list[Skipped],
    meta: dict,
    baseline: ConstantBaseline | None = None,
    random: RandomBaseline | None = None,
    top: TopCandidateBaseline | None = None,
    groups: dict[str, list[Metric]] | None = None,
) -> dict:
    """저장용 JSON. 실행 메타가 없으면 나중에 점수를 비교할 수 없다."""
    document: dict = {
        "meta": meta,
        "metrics": [asdict(m) | {"ratio": m.ratio} for m in metrics],
        "cases": [_case_record(s) for s in scores],
        "skipped": [asdict(s) for s in skipped],
    }
    # 전달받은 대조군을 저장한다. 검색 전용 실행에서는 호출자가 None을 넘긴다.
    if baseline is not None:
        document["constant_baseline"] = asdict(baseline) | {"ratio": baseline.ratio}
    if random is not None:
        document["random_baseline"] = asdict(random)
    if top is not None:
        document["top_candidate_baseline"] = asdict(top) | {"ratio": top.ratio}
    if groups is not None:
        document["metric_groups"] = {
            name: [asdict(m) | {"ratio": m.ratio} for m in group] for name, group in groups.items()
        }
    return document
