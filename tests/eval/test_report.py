# tests/eval/test_report.py
from whatfrom.eval.goldenset import GoldenCase, Rationale
from whatfrom.eval.report import (
    ConstantBaseline,
    Metric,
    RandomBaseline,
    Skipped,
    _display_width,  # 열 맞춤 검증에만 쓴다
    aggregate_full,
    aggregate_retrieval,
    constant_baseline,
    random_baseline,
    render_summary,
    result_document,
)
from whatfrom.eval.scoring import CaseScore, RetrievalScore


def make_score(**overrides: object) -> CaseScore:
    base: dict[str, object] = {
        "case_id": "case",
        "recommended_image": "python:3.13-slim",
        "accurate": True,
        "accurate_by_digest": False,
        "rejected_pick": False,
        "candidate_hit": True,
        "candidate_count": 1,
        "accepted_count": 1,
        "candidate_images": ["python:3.13-slim"],
        "tag_real": True,
        "conditions_declared": True,
        "conditions_met": True,
        "sources_ok": True,
        "hit_declared": True,
        "hit_at5": True,
        "degraded_note": None,
    }
    base.update(overrides)
    return CaseScore(**base)  # type: ignore[arg-type]


def make_case(**overrides: object) -> GoldenCase:
    base: dict[str, object] = {
        "id": "case",
        "question": "질문",
        "requires_repositories": ["python"],
        "accept": ["python:3.13-slim"],
        "expected_plan": {},
        "rationale": Rationale(note="근거", sources=["https://example.invalid/doc"]),
    }
    base.update(overrides)
    return GoldenCase.model_validate(base)


def metric(metrics: list[Metric], label: str) -> Metric:
    return next(m for m in metrics if m.label == label)


def test_ratio_is_none_when_the_denominator_is_zero() -> None:
    """분모가 0인 지표는 0%가 아니라 측정되지 않은 것이다."""
    assert Metric("조건 일치율", 0, 0).ratio is None


def test_ratio_divides_hits_by_total_when_the_denominator_is_nonzero() -> None:
    assert Metric("x", 1, 2).ratio == 0.5


def test_accuracy_counts_every_measured_case() -> None:
    scores = [make_score(), make_score(case_id="b", accurate=False)]

    assert metric(aggregate_full(scores), "추천 정확도") == Metric("추천 정확도", 1, 2)


def test_tag_reality_drops_cases_without_a_recommendation() -> None:
    """True/False/None 세 값을 다 섞어야 False가 분모에서 빠지는 실수를 잡는다."""
    scores = [
        make_score(),
        make_score(case_id="b", tag_real=None),
        make_score(case_id="c", tag_real=False),
    ]

    assert metric(aggregate_full(scores), "태그 실재율") == Metric("태그 실재율", 1, 2)


def test_conditions_use_only_declared_cases() -> None:
    scores = [
        make_score(),
        make_score(case_id="b", conditions_declared=False, conditions_met=False),
        make_score(case_id="c", conditions_declared=True, conditions_met=False),
    ]

    assert metric(aggregate_full(scores), "조건 일치율") == Metric("조건 일치율", 1, 2)


def test_hit_uses_only_declared_cases() -> None:
    scores = [
        make_score(),
        make_score(case_id="b", hit_declared=False, hit_at5=False),
        make_score(case_id="c", hit_declared=True, hit_at5=False),
    ]

    assert metric(aggregate_full(scores), "문서 Hit@5") == Metric("문서 Hit@5", 1, 2)


def test_hit_numerators_ignore_records_declared_false_but_hit_true() -> None:
    """CaseScore는 검증 없는 평범한 dataclass라 declared=False인데 met/hit=True인
    레코드도 만들어질 수 있다. 분자가 declared로 걸러지지 않으면 이런 레코드까지
    히트로 세져 분모(1)보다 분자가 커진다."""
    scores = [
        make_score(),
        make_score(
            case_id="b",
            conditions_declared=False,
            conditions_met=True,
            hit_declared=False,
            hit_at5=True,
        ),
    ]
    metrics = aggregate_full(scores)

    conditions = metric(metrics, "조건 일치율")
    assert conditions == Metric("조건 일치율", 1, 1)
    assert conditions.ratio is not None
    assert conditions.ratio <= 1.0

    hit = metric(metrics, "문서 Hit@5")
    assert hit == Metric("문서 Hit@5", 1, 1)
    assert hit.ratio is not None
    assert hit.ratio <= 1.0


def test_candidate_hit_rate_counts_only_hits() -> None:
    scores = [make_score(), make_score(case_id="b", candidate_hit=False)]

    assert metric(aggregate_full(scores), "후보 포함률") == Metric("후보 포함률", 1, 2)


def test_source_availability_counts_only_sources_ok() -> None:
    scores = [make_score(), make_score(case_id="b", sources_ok=False)]

    assert metric(aggregate_full(scores), "출처 제공률") == Metric("출처 제공률", 1, 2)


def test_retrieval_and_full_aggregate_the_shared_metrics_identically() -> None:
    """검색 전용 모드와 전체 모드는 같은 입력에서 같은 검색 지표를 내야 한다."""
    values = [
        {"case_id": "a", "candidate_hit": True, "hit_declared": True, "hit_at5": True},
        {"case_id": "b", "candidate_hit": False, "hit_declared": True, "hit_at5": False},
        {"case_id": "c", "candidate_hit": True, "hit_declared": False, "hit_at5": False},
    ]
    record = {"candidate_count": 0, "accepted_count": 0, "candidate_images": []}
    case_scores = [make_score(**v) for v in values]
    retrieval_scores = [RetrievalScore(**v, **record) for v in values]  # type: ignore[arg-type]

    full = {m.label: m for m in aggregate_full(case_scores)}
    retrieval = {m.label: m for m in aggregate_retrieval(retrieval_scores)}

    assert full["후보 포함률"] == retrieval["후보 포함률"]
    assert full["문서 Hit@5"] == retrieval["문서 Hit@5"]


def test_result_document_records_the_run_metadata() -> None:
    """모델이 바뀌면 점수 비교가 무의미해진다. 메타가 없으면 나중에 알 수 없다."""
    meta = {"llm_model": "kimi-k3", "embedder": "bge-m3", "goldenset_version": 1}

    document = result_document(aggregate_full([make_score()]), [make_score()], [], meta)

    assert document["meta"] == meta
    assert document["cases"][0]["case_id"] == "case"


def test_result_document_records_every_metric_with_its_ratio() -> None:
    metrics = aggregate_full([make_score()])

    document = result_document(metrics, [make_score()], [], {})

    assert document["metrics"] == [
        {"label": m.label, "hits": m.hits, "total": m.total, "ratio": m.ratio} for m in metrics
    ]


def test_result_document_records_skipped_cases() -> None:
    skipped = [Skipped(case_id="node-alpine", missing=["node"])]

    document = result_document([], [], skipped, {})

    assert document["skipped"] == [{"case_id": "node-alpine", "missing": ["node"]}]


def test_render_summary_shows_a_dash_when_the_denominator_is_zero() -> None:
    """0/0은 0%가 아니다. 0.0%로 찍히면 실제로 측정된 0%와 구별이 안 된다."""
    output = render_summary([Metric("조건 일치율", 0, 0)], [], [], [], 0, {})

    assert "—" in output
    assert "0.0%" not in output


def test_render_summary_marks_explicit_rejections() -> None:
    case = make_case(id="x", accept=["python:3.13-slim"])
    score = make_score(
        case_id="x", accurate=False, rejected_pick=True, recommended_image="python:3.12-alpine"
    )

    output = render_summary([], [score], [case], [], 1, {})

    assert "[명시적 오답]" in output


def test_render_summary_shows_the_degraded_note_when_there_is_no_recommendation() -> None:
    score = make_score(
        case_id="y", accurate=False, recommended_image=None, degraded_note="검색 실패"
    )

    output = render_summary([], [score], [], [], 1, {})

    assert "추천 없음 (검색 실패)" in output


def test_render_summary_explains_skipped_repos_as_not_indexed() -> None:
    """collect와 index는 별도 단계라 리포가 수집됐어도 색인이 안 됐을 수 있다.
    '미수집'이라고 하면 이미 끝난 collect를 다시 하라고 잘못 안내하게 된다."""
    skipped = [Skipped(case_id="a", missing=["node"])]

    output = render_summary([], [], [], skipped, 1, {})

    assert "미색인" in output
    assert "미수집" not in output


def test_render_summary_falls_back_to_the_provider_name_when_llm_model_is_absent() -> None:
    """fake 프로바이더는 llm_model을 None으로 남긴다. llm=None으로 찍히면 LLM이
    아예 안 돈 것처럼 보인다 — 프로바이더 이름이라도 보여줘야 fake 실행임을 알 수 있다."""
    output = render_summary([], [], [], [], 0, {"llm_model": None, "llm_provider": "fake"})

    assert "llm=fake" in output
    assert "llm=None" not in output


def test_render_summary_shows_a_dash_for_llm_in_retrieval_only_mode() -> None:
    """retrieval-only는 llm_provider도 None이다. 그래도 llm=None을 찍으면 안 된다."""
    output = render_summary([], [], [], [], 0, {"llm_model": None, "llm_provider": None})

    assert "llm=None" not in output
    assert "llm=-" in output


def test_render_summary_accounts_for_cases_removed_by_the_tag_filter() -> None:
    """--tags로 걸러진 문항은 measured에도 skipped에도 안 잡혀 사라진 것처럼 보인다.
    total_cases에서 측정·미측정을 빼면 태그 필터로 빠진 수가 나와야 한다."""
    case = make_case(id="x")
    skipped = [Skipped(case_id="b", missing=["node"])]

    output = render_summary([], [], [case], skipped, 3, {})

    assert "1문항 태그 필터 제외" in output


def test_render_summary_omits_the_tag_filter_clause_when_nothing_was_filtered() -> None:
    case = make_case(id="x")

    output = render_summary([], [], [case], [], 1, {})

    assert "태그 필터" not in output


def test_constant_baseline_picks_the_image_in_the_most_accept_lists() -> None:
    """대조군은 가능한 고정답 중 가장 센 것이어야 한다. 그보다 약한 걸 고르면
    대조군이 실제보다 낮게 나와 추천 정확도가 좋아 보인다."""
    cases = [
        make_case(id="a", accept=["python:3.14-slim", "python:3.13-slim"]),
        make_case(id="b", accept=["python:3.14-slim"]),
        make_case(id="c", accept=["python:3.14-slim", "python:3.12-alpine"]),
    ]

    # 이름순으로만 고르면 python:3.12-alpine이 나온다. 횟수가 먼저다.
    assert constant_baseline(cases).image == "python:3.14-slim"


def test_constant_baseline_scores_the_cases_whose_accept_contains_the_image() -> None:
    cases = [
        make_case(id="a", accept=["python:3.14-slim", "python:3.13-slim"]),
        make_case(id="b", accept=["python:3.14-slim"]),
        make_case(id="c", accept=["python:3.12-alpine"]),
    ]

    assert constant_baseline(cases) == ConstantBaseline(image="python:3.14-slim", hits=2, total=3)


def test_constant_baseline_breaks_ties_by_image_name_not_by_case_order() -> None:
    """동점일 때 입력 순서를 따르면 골든셋 문항 순서만 바꿔도 대조군이 달라진다."""
    cases = [
        make_case(id="a", accept=["python:3.14-slim"]),
        make_case(id="b", accept=["python:3.13-slim"]),
    ]
    reversed_cases = list(reversed(cases))

    assert constant_baseline(cases).image == "python:3.13-slim"
    assert constant_baseline(cases) == constant_baseline(reversed_cases)


def test_constant_baseline_counts_a_duplicated_accept_entry_once() -> None:
    """같은 이미지를 두 번 적은 accept가 한 문항에서 두 표를 내면 분자가 분모를 넘는다."""
    cases = [make_case(id="a", accept=["python:3.14-slim", "python:3.14-slim"])]

    assert constant_baseline(cases) == ConstantBaseline(image="python:3.14-slim", hits=1, total=1)


def test_constant_baseline_is_empty_when_there_are_no_measured_cases() -> None:
    """측정 문항이 0이면 고를 이미지가 없다. 0%가 아니라 계산 불가다."""
    baseline = constant_baseline([])

    assert baseline == ConstantBaseline(image=None, hits=0, total=0)
    assert baseline.ratio is None


def test_constant_baseline_still_answers_when_no_image_repeats() -> None:
    cases = [
        make_case(id="a", accept=["python:3.14-slim"]),
        make_case(id="b", accept=["node:24-alpine"]),
    ]

    assert constant_baseline(cases) == ConstantBaseline(image="node:24-alpine", hits=1, total=2)


def test_render_summary_shows_the_constant_baseline_beside_accuracy() -> None:
    metrics = aggregate_full([make_score()])
    baseline = ConstantBaseline(image="python:3.14-slim", hits=10, total=14)

    output = render_summary(metrics, [], [], [], 1, {}, baseline)

    assert "대조군" in output
    assert "python:3.14-slim" in output
    assert "10/14" in output
    assert "71.4%" in output
    assert "지표 아님" in output


def test_render_summary_omits_the_constant_baseline_in_retrieval_only_mode() -> None:
    """--retrieval-only는 추천 정확도를 내지 않는다. 기준선만 홀로 서면
    무엇에 대한 기준선인지 알 수 없다."""
    metrics = aggregate_retrieval([])

    output = render_summary(metrics, [], [], [], 1, {}, None)

    assert "대조군" not in output


def test_render_summary_drops_the_constant_baseline_when_accuracy_is_absent() -> None:
    """호출자가 실수로 넘겨도 추천 정확도가 없는 리포트에는 붙으면 안 된다."""
    metrics = aggregate_retrieval([])
    baseline = ConstantBaseline(image="python:3.14-slim", hits=10, total=14)

    output = render_summary(metrics, [], [], [], 1, {}, baseline)

    assert "대조군" not in output


def test_render_summary_says_the_constant_baseline_is_uncomputable_when_empty() -> None:
    output = render_summary(aggregate_full([]), [], [], [], 0, {}, constant_baseline([]))

    assert "대조군" in output
    assert "계산할 수 없다" in output


def test_result_document_records_the_constant_baseline() -> None:
    baseline = ConstantBaseline(image="python:3.14-slim", hits=10, total=14)

    document = result_document(aggregate_full([make_score()]), [make_score()], [], {}, baseline)

    assert document["constant_baseline"] == {
        "image": "python:3.14-slim",
        "hits": 10,
        "total": 14,
        "ratio": 10 / 14,
    }


def test_result_document_omits_the_constant_baseline_in_retrieval_only_mode() -> None:
    document = result_document(aggregate_retrieval([]), [], [], {}, None)

    assert "constant_baseline" not in document


def test_ratio_is_zero_when_nothing_hit_but_cases_were_measured() -> None:
    """0/14는 미측정이 아니라 측정된 0%다. —로 찍히면 발표할 숫자가 사라진다."""
    assert Metric("추천 정확도", 0, 14).ratio == 0.0


def test_accuracy_keeps_cases_without_a_recommendation_in_the_denominator() -> None:
    """답하지 않은 문항을 분모에서 빼면 저하가 잦을수록 점수가 오른다(스펙 '저하 응답의 처리')."""
    scores = [
        make_score(),
        make_score(case_id="b", recommended_image=None, accurate=False),
    ]

    assert metric(aggregate_full(scores), "추천 정확도") == Metric("추천 정확도", 1, 2)


def test_source_availability_keeps_cases_without_a_recommendation_in_the_denominator() -> None:
    """저하된 문항도 출처 제공률의 분모에 남는다. 같은 이유다."""
    scores = [
        make_score(),
        make_score(case_id="b", recommended_image=None, sources_ok=False),
    ]

    assert metric(aggregate_full(scores), "출처 제공률") == Metric("출처 제공률", 1, 2)


def test_render_summary_aligns_the_metric_column_for_double_width_labels() -> None:
    """한글 라벨은 터미널에서 두 칸을 차지한다. 글자 수로 채우면 열이 어긋난다."""
    metrics = [Metric("추천 정확도", 1, 2), Metric("문서 Hit@5", 1, 2)]

    output = render_summary(metrics, [], [], [], 0, {})

    columns = [
        _display_width(line.split("1/2")[0]) for line in output.splitlines() if "1/2" in line
    ]
    assert len(columns) == 2
    assert columns[0] == columns[1]


def test_render_summary_aligns_the_failure_column_for_a_long_case_id() -> None:
    """고정 폭보다 긴 id가 있으면 그 줄만 뒤 열이 밀린다. 가장 긴 id에 칸을 맞춘다."""
    scores = [
        make_score(case_id="a", accurate=False, recommended_image="python:3.12-alpine"),
        make_score(
            case_id="python-version-pinned-312",
            accurate=False,
            recommended_image="python:3.12-alpine",
        ),
    ]

    output = render_summary([], scores, [], [], 2, {})

    columns = [
        _display_width(line.split("기대")[0]) for line in output.splitlines() if "기대" in line
    ]
    assert len(columns) == 2
    assert columns[0] == columns[1]


def make_retrieval(accepted: int, count: int) -> RetrievalScore:
    return RetrievalScore(
        case_id="case",
        candidate_hit=accepted > 0,
        candidate_count=count,
        accepted_count=accepted,
        candidate_images=[f"python:{i}" for i in range(count)],
        hit_declared=False,
        hit_at5=False,
    )


def test_random_baseline_averages_per_case_ratios_not_pooled_counts() -> None:
    """문항마다 1/2, 0/8이면 평균은 0.25다. 후보를 모두 합쳐 나누면 1/10 = 0.1이 된다.

    무작위 선택은 문항마다 따로 일어난다. 합쳐 나누면 후보가 많은 문항이 결과를 좌우한다.
    """
    baseline = random_baseline([make_retrieval(1, 2), make_retrieval(0, 8)])

    assert baseline.expected == 0.25
    assert baseline.total == 2


def test_random_baseline_counts_a_case_without_candidates_as_zero() -> None:
    """측정했는데 후보가 없으면 무작위로 골라도 맞힐 수 없다. 분모에서 빼면 기대값이 부푼다."""
    baseline = random_baseline([make_retrieval(1, 1), make_retrieval(0, 0)])

    assert baseline.expected == 0.5
    assert baseline.total == 2


def test_random_baseline_is_unmeasured_when_there_are_no_cases() -> None:
    assert random_baseline([]) == RandomBaseline(expected=None, total=0)


def test_random_baseline_reads_full_mode_scores_too() -> None:
    baseline = random_baseline([make_score(candidate_count=4, accepted_count=1)])

    assert baseline.expected == 0.25


def test_render_summary_shows_the_random_baseline_in_retrieval_only_mode() -> None:
    """고정답 대조군과 달리 후보만 있으면 계산되므로 검색 전용 모드에도 나온다."""
    output = render_summary(
        aggregate_retrieval([]), [], [], [], 1, {}, None, RandomBaseline(expected=0.083, total=40)
    )

    assert "무작위 선택 대조군" in output
    assert "8.3%" in output
    assert "40문항" in output


def test_render_summary_shows_the_random_baseline_in_full_mode() -> None:
    output = render_summary(
        aggregate_full([make_score()]),
        [],
        [],
        [],
        1,
        {},
        ConstantBaseline(image="python:3.14-slim", hits=10, total=14),
        RandomBaseline(expected=0.25, total=2),
    )

    assert "무작위 선택 대조군" in output
    assert "25.0%" in output
    assert "고정답 대조군" in output


def test_render_summary_says_the_random_baseline_is_uncomputable_when_empty() -> None:
    output = render_summary(
        aggregate_retrieval([]), [], [], [], 0, {}, None, RandomBaseline(expected=None, total=0)
    )

    assert "무작위 선택 대조군" in output
    assert "계산할 수 없다" in output


def test_result_document_records_the_random_baseline() -> None:
    document = result_document(
        aggregate_retrieval([]), [], [], {}, None, RandomBaseline(expected=0.25, total=2)
    )

    assert document["random_baseline"] == {"expected": 0.25, "total": 2}
    assert "constant_baseline" not in document


def test_result_document_records_the_candidates_of_each_case() -> None:
    document = result_document(aggregate_full([make_score()]), [make_score()], [], {})

    case = document["cases"][0]
    assert case["candidate_count"] == 1
    assert case["accepted_count"] == 1
    assert case["candidate_images"] == ["python:3.13-slim"]


def test_render_summary_lists_the_cases_accepted_by_digest() -> None:
    """이름이 맞은 것과 digest로 맞은 것을 구분해 보여 준다."""
    scores = [
        make_score(case_id="temurin-jdk", accurate_by_digest=True),
        make_score(case_id="plain"),
    ]

    output = render_summary(aggregate_full(scores), scores, [], [], 2, {})

    assert "digest" in output
    assert "1문항" in output
    assert "temurin-jdk" in output
    assert "plain" not in output


def test_render_summary_omits_the_digest_line_when_every_match_is_by_name() -> None:
    scores = [make_score()]

    output = render_summary(aggregate_full(scores), scores, [], [], 1, {})

    assert "digest" not in output


def test_the_extraction_metrics_count_every_measured_case():
    scores = [
        make_score(repository_extracted=True, plan_matched=True),
        make_score(case_id="b", repository_extracted=True, plan_matched=False),
        make_score(case_id="c"),  # 추출 실패: 기본값 False
    ]

    metrics = aggregate_full(scores)

    assert metric(metrics, "리포 추출 일치율") == Metric("리포 추출 일치율", 2, 3)
    assert metric(metrics, "조건 추출 일치율") == Metric("조건 추출 일치율", 1, 3)
    assert [m.label for m in metrics][-2:] == ["리포 추출 일치율", "조건 추출 일치율"]


def test_retrieval_only_has_no_extraction_metrics():
    labels = [m.label for m in aggregate_retrieval([])]

    assert "리포 추출 일치율" not in labels
    assert "조건 추출 일치율" not in labels


def test_render_summary_aligns_the_longest_extraction_label():
    metrics = [Metric("추천 정확도", 1, 2), Metric("조건 추출 일치율", 1, 2)]

    output = render_summary(metrics, [], [], [], 0, {})

    columns = [
        _display_width(line.split("1/2")[0]) for line in output.splitlines() if "1/2" in line
    ]
    assert columns[0] == columns[1]


def test_result_document_records_the_plan_and_notes_of_each_case():
    score = make_score(plan={"repository": "python"}, notes=["조건을 풀었습니다."])

    case = result_document(aggregate_full([score]), [score], [], {})["cases"][0]

    assert case["plan"] == {"repository": "python"}
    assert case["notes"] == ["조건을 풀었습니다."]


def test_render_summary_shows_the_median_and_slowest_case_time() -> None:
    scores = [
        make_score(case_id="fast", seconds_total=10.0),
        make_score(case_id="mid", seconds_total=20.0),
        make_score(case_id="slow", seconds_total=90.0),
    ]

    output = render_summary([], scores, [], [], 3, {})

    assert "문항당 소요 시간: 중앙값 20.0초, 최대 90.0초 (slow)" in output


def test_render_summary_omits_the_time_line_without_timings() -> None:
    """검색 전용 모드와 시간을 재지 않은 실행에는 시간 줄이 없다."""
    output = render_summary([], [make_score()], [], [], 1, {})

    assert "소요 시간" not in output


def test_result_document_records_the_stage_times_and_the_advise_input() -> None:
    score = make_score(
        seconds_total=3.0,
        seconds_embedding=0.5,
        seconds_plan=1.0,
        advise_prompt="Requirement: q",
        dockerfile="FROM python:3.13-slim@sha256:aaa",
    )

    document = result_document([], [score], [], {})

    case = document["cases"][0]
    assert (case["seconds_total"], case["seconds_embedding"]) == (3.0, 0.5)
    assert (case["seconds_plan"], case["seconds_advise"]) == (1.0, None)
    assert case["advise_prompt"] == "Requirement: q"
    assert case["dockerfile"] == "FROM python:3.13-slim@sha256:aaa"


def test_result_document_of_a_retrieval_run_has_no_time_fields() -> None:
    score = RetrievalScore(
        case_id="case",
        candidate_hit=True,
        candidate_count=1,
        accepted_count=1,
        candidate_images=["python:3.13-slim"],
        hit_declared=True,
        hit_at5=True,
    )

    document = result_document([], [score], [], {})

    assert not any(key.startswith("seconds_") for key in document["cases"][0])
