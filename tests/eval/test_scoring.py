# tests/eval/test_scoring.py
from datetime import UTC, datetime

from whatfrom.core.contracts import (
    Candidate,
    Platform,
    Recommendation,
    RecommendedImage,
    RecommendResponse,
    SearchPlan,
)
from whatfrom.eval.goldenset import Conditions, GoldenCase, Rationale
from whatfrom.eval.scoring import conditions_satisfied, score_full, score_retrieval

NOW = datetime(2026, 9, 10, tzinfo=UTC)


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


def make_candidate(tag: str, platforms: list[Platform] | None = None) -> Candidate:
    return Candidate(
        image=f"python:{tag}",
        repository="python",
        tag=tag,
        digest="sha256:aaa",
        platforms=platforms or [],
        last_pushed_at=NOW,
        source_url="https://hub.docker.com/_/python",
        collected_at=NOW,
    )


def test_candidate_hit_when_an_accepted_image_is_offered() -> None:
    score = score_retrieval(make_case(), [make_candidate("3.13-slim")], [])

    assert score.candidate_hit is True


def test_candidate_miss_when_no_accepted_image_is_offered() -> None:
    score = score_retrieval(make_case(), [make_candidate("3.13-alpine")], [])

    assert score.candidate_hit is False


def test_hit_is_not_declared_without_expected_sections() -> None:
    """expected_sections가 없는 문항은 Hit@5 분모에서 빠진다."""
    score = score_retrieval(make_case(), [], [("python", "Image Variants")])

    assert score.hit_declared is False
    assert score.hit_at5 is False


def test_hit_on_a_substring_match() -> None:
    """섹션 제목이 버전을 포함해 바뀌어도 깨지지 않게 부분문자열로 본다."""
    case = make_case(expected_sections=["slim"])

    score = score_retrieval(case, [], [("python", "python:3.14-slim"), ("python", "License")])

    assert score.hit_declared is True
    assert score.hit_at5 is True


def test_hit_when_any_one_expected_section_matches() -> None:
    """기대 섹션 하나만 나와도 히트다 — 지표 이름이 Recall이 아니라 Hit@5인 이유다."""
    case = make_case(expected_sections=["Image Variants", "없는 섹션"])

    score = score_retrieval(case, [], [("python", "Image Variants")])

    assert score.hit_at5 is True


def test_miss_when_no_expected_section_matches() -> None:
    case = make_case(expected_sections=["Image Variants"])

    score = score_retrieval(case, [], [("python", "License"), ("python", "Supported tags")])

    assert score.hit_at5 is False


def test_miss_when_the_section_comes_from_another_repository() -> None:
    """다른 제품의 문서를 찾은 것은 성공이 아니다.

    "Image Variants"는 열 개 리포에 전부 있고 40문항 중 25문항이 이 제목을
    기대한다. 리포를 보지 않으면 python 문항이 node README를 끌어와도 히트가 된다.
    """
    case = make_case(requires_repositories=["python"], expected_sections=["Image Variants"])

    score = score_retrieval(case, [], [("node", "Image Variants")])

    assert score.hit_declared is True
    assert score.hit_at5 is False


def test_hit_when_the_section_comes_from_a_repository_the_case_asks_about() -> None:
    """리포까지 보더라도 묻는 리포의 섹션은 그대로 히트여야 한다."""
    case = make_case(requires_repositories=["python"], expected_sections=["Image Variants"])

    score = score_retrieval(case, [], [("node", "Image Variants"), ("python", "Image Variants")])

    assert score.hit_at5 is True


def linux(architecture: str, size_bytes: int = 100_000_000) -> Platform:
    return Platform(
        os="linux",
        architecture=architecture,
        arch_variant="",
        os_version="",
        size_bytes=size_bytes,
        digest="sha256:bbb",
    )


def make_response(
    image: str | None,
    candidates: list[Candidate],
    notes: list[str] | None = None,
) -> RecommendResponse:
    recommendation = (
        None
        if image is None
        else Recommendation(image=image, reason="이유", dockerfile=f"FROM {image}")
    )
    return RecommendResponse(
        question="질문",
        recommendation=recommendation,
        candidates=candidates,
        degraded=recommendation is None,
        notes=notes or [],
    )


def test_accurate_when_the_recommendation_is_in_the_accept_set() -> None:
    candidates = [make_candidate("3.13-slim")]

    score = score_full(make_case(), make_response("python:3.13-slim", candidates), [], True)

    assert score.accurate is True
    assert score.rejected_pick is False


def test_rejected_pick_is_flagged_but_still_just_inaccurate() -> None:
    """명시적 오답은 지표가 아니라 경보다. 정확도 계산은 accept로만 한다."""
    case = make_case(reject=["python:3.13-alpine"])
    candidates = [make_candidate("3.13-alpine")]

    score = score_full(case, make_response("python:3.13-alpine", candidates), [], True)

    assert score.accurate is False
    assert score.rejected_pick is True


def test_degraded_counts_as_inaccurate() -> None:
    """응답을 받지 못한 것도 사용자에게는 실패다."""
    response = make_response(None, [make_candidate("3.13-slim")], notes=["LLM 실패"])

    score = score_full(make_case(), response, [], None)

    assert score.accurate is False
    assert score.degraded_note == "LLM 실패"


def test_degraded_note_is_the_last_note_when_there_is_no_recommendation() -> None:
    """검색 단계의 알림이 앞에 오고, 추천을 못 한 사유는 API가 맨 뒤에 붙인다."""
    response = make_response(
        None,
        [make_candidate("3.13-slim")],
        notes=["조건에 맞는 태그가 없어 버전 조건(stable)을 풀었습니다.", "LLM 실패"],
    )

    score = score_full(make_case(), response, [], None)

    assert score.degraded_note == "LLM 실패"


def test_degraded_leaves_tag_reality_undecided() -> None:
    """답하지 않은 것을 '환각 안 함'으로 세면 저하가 잦을수록 점수가 오른다."""
    score = score_full(make_case(), make_response(None, []), [], None)

    assert score.tag_real is None


def test_degraded_ignores_caller_supplied_recommended_image_exists() -> None:
    """추천이 없으면 호출자가 True를 넘겨도 tag_real은 None이어야 한다."""
    score = score_full(make_case(), make_response(None, []), [], True)

    assert score.tag_real is None


def test_tag_reality_follows_the_runner_lookup() -> None:
    candidates = [make_candidate("3.13-slim")]

    score = score_full(make_case(), make_response("python:3.13-slim", candidates), [], False)

    assert score.tag_real is False


def test_conditions_are_not_declared_when_empty() -> None:
    score = score_full(make_case(), make_response("python:3.13-slim", []), [], True)

    assert score.conditions_declared is False
    assert score.conditions_met is False


def test_conditions_met_when_every_condition_passes() -> None:
    case = make_case(
        conditions=Conditions(architectures=["arm64"], exclude_distributions=["alpine"])
    )
    candidates = [make_candidate("3.13-slim", [linux("arm64"), linux("amd64")])]

    score = score_full(case, make_response("python:3.13-slim", candidates), [], True)

    assert score.conditions_declared is True
    assert score.conditions_met is True


def test_degraded_fails_the_conditions_but_stays_in_the_denominator() -> None:
    case = make_case(conditions=Conditions(architectures=["arm64"]))

    score = score_full(case, make_response(None, []), [], None)

    assert score.conditions_declared is True
    assert score.conditions_met is False


def test_sources_follow_the_recommended_candidate() -> None:
    candidates = [make_candidate("3.13-slim")]

    score = score_full(make_case(), make_response("python:3.13-slim", candidates), [], True)

    assert score.sources_ok is True


def test_sources_fall_back_to_the_candidate_table_when_degraded() -> None:
    """저하되어 후보 표만 받은 사용자도 출처는 받은 것이다."""
    score = score_full(make_case(), make_response(None, [make_candidate("3.13-slim")]), [], None)

    assert score.sources_ok is True


def test_sources_miss_when_there_are_no_candidates_at_all() -> None:
    score = score_full(make_case(), make_response(None, []), [], None)

    assert score.sources_ok is False


def test_architecture_condition_ignores_windows_platforms() -> None:
    """python:3.13의 amd64는 linux 하나와 windows 둘로 세 번 나온다."""
    windows_only = Platform(
        os="windows",
        architecture="arm64",
        arch_variant="",
        os_version="10.0.20348.2402",
        size_bytes=2_400_000_000,
        digest="sha256:ccc",
    )
    candidate = make_candidate("3.13-slim", [windows_only])

    assert conditions_satisfied(Conditions(architectures=["arm64"]), candidate) is False


def test_version_prefix_condition_rejects_latest() -> None:
    candidate = make_candidate("latest")

    assert conditions_satisfied(Conditions(version_prefix="3.13"), candidate) is False


def test_version_prefix_condition_accepts_a_patch_tag() -> None:
    candidate = make_candidate("3.13.2-slim")

    assert conditions_satisfied(Conditions(version_prefix="3.13"), candidate) is True


def test_version_prefix_condition_is_anchored_on_a_separator() -> None:
    """version_prefix="3.1"이 "3.13-slim"을 부분문자열로 잘못 통과시키면 안 된다."""
    unanchored = make_candidate("3.13-slim")

    assert conditions_satisfied(Conditions(version_prefix="3.1"), unanchored) is False

    for tag in ("3.13", "3.13-slim", "3.13.2-slim"):
        candidate = make_candidate(tag)

        assert conditions_satisfied(Conditions(version_prefix="3.13"), candidate) is True


def test_max_size_condition_uses_the_requested_architecture() -> None:
    candidate = make_candidate(
        "3.13-slim", [linux("amd64", 500_000_000), linux("arm64", 100_000_000)]
    )
    conditions = Conditions(architectures=["arm64"], max_size_mb=200)

    assert conditions_satisfied(conditions, candidate) is True


def test_max_size_condition_defaults_to_amd64() -> None:
    candidate = make_candidate(
        "3.13-slim", [linux("amd64", 500_000_000), linux("arm64", 100_000_000)]
    )

    assert conditions_satisfied(Conditions(max_size_mb=200), candidate) is False


def test_score_full_propagates_retrieval_fields() -> None:
    """score_full이 score_retrieval의 candidate_hit/hit_declared/hit_at5를 옮기는지 본다.

    세 값이 전부 같으면 뒤섞여도(예: candidate_hit을 hit_at5에서 가져오거나
    hit_declared/hit_at5를 맞바꿔도) 테스트가 통과한다. 후보에 정답이 없어
    candidate_hit은 False이지만 Hit@5는 여전히 맞는 경우로 세 값을 갈라놓는다.
    """
    case = make_case(expected_sections=["Image Variants"])
    candidates = [make_candidate("3.13-alpine")]

    score = score_full(case, make_response(None, candidates), [("python", "Image Variants")], None)

    assert score.candidate_hit is False
    assert score.hit_declared is True
    assert score.hit_at5 is True


def test_score_full_ignores_sections_from_another_repository() -> None:
    """리포를 보는 규칙은 전체 모드에도 똑같이 걸려야 한다."""
    case = make_case(expected_sections=["Image Variants"])
    candidates = [make_candidate("3.13-slim")]

    score = score_full(
        case, make_response("python:3.13-slim", candidates), [("node", "Image Variants")], True
    )

    assert score.hit_declared is True
    assert score.hit_at5 is False


def test_candidate_hit_needs_only_one_of_several_accepted_images() -> None:
    """accept가 여럿일 때 하나만 후보에 있어도 히트다.

    전부 있어야 한다고 보면(any 대신 all) accept를 넓게 적은 문항일수록 후보
    포함률이 떨어진다 — 라벨을 넓게 적은 것이 검색 실패로 둔갑한다. 실제
    골든셋의 accept는 최대 아홉 개다.
    """
    case = make_case(
        accept=["python:3.14-slim", "python:3.13-slim", "python:3.13-bookworm"],
    )

    score = score_retrieval(case, [make_candidate("3.13-slim")], [])

    assert score.candidate_hit is True


def test_candidate_miss_when_there_are_no_candidates_at_all() -> None:
    """후보가 하나도 없으면 정답이 제시된 적이 없다."""
    score = score_retrieval(make_case(), [], [])

    assert score.candidate_hit is False


def test_a_recommendation_in_neither_list_is_inaccurate() -> None:
    """accept에도 reject에도 없는 답 — 가장 흔한 오답이다.

    정확도를 "accept에 있는가"가 아니라 "reject에 없는가"로 판정하면 이 답이
    정답으로 세어진다. reject는 대부분 문항에서 비어 있으므로 그 뒤집기 하나로
    바닥값이 만점이 된다.
    """
    case = make_case(accept=["python:3.13-slim"], reject=["python:3.13-alpine"])
    candidates = [make_candidate("3.12-bookworm")]

    score = score_full(case, make_response("python:3.12-bookworm", candidates), [], True)

    assert score.accurate is False
    assert score.rejected_pick is False


def test_excluded_distribution_in_the_tag_fails() -> None:
    """exclude_distributions가 실제로 태그를 거르는지 본다."""
    candidate = make_candidate("3.13-alpine")

    assert conditions_satisfied(Conditions(exclude_distributions=["alpine"]), candidate) is False


def test_max_size_condition_uses_the_smallest_matching_platform() -> None:
    """같은 아키텍처의 플랫폼 중 최소 크기를 사용하는 현재 평가 정책을 확인한다.

    실제로 내려받을 플랫폼 변형까지 판별하는 테스트는 아니다.
    """
    candidate = make_candidate(
        "3.13-slim", [linux("amd64", 500_000_000), linux("amd64", 100_000_000)]
    )

    assert conditions_satisfied(Conditions(max_size_mb=200), candidate) is True


def test_score_retrieval_records_the_offered_candidates() -> None:
    """후보 목록과 그중 정답 수를 남겨야 무작위 선택 기대값을 다시 구할 수 있다."""
    case = make_case(accept=["python:3.13-slim", "python:3.13"])
    candidates = [make_candidate("3.13-slim"), make_candidate("3.13"), make_candidate("3.14")]

    score = score_retrieval(case, candidates, [])

    assert score.candidate_count == 3
    assert score.accepted_count == 2
    assert score.candidate_images == ["python:3.13-slim", "python:3.13", "python:3.14"]


def test_score_retrieval_records_zero_when_there_are_no_candidates() -> None:
    score = score_retrieval(make_case(), [], [])

    assert score.candidate_count == 0
    assert score.accepted_count == 0
    assert score.candidate_images == []


def test_score_full_propagates_the_candidate_record() -> None:
    """전체 모드 JSON은 CaseScore를 저장한다. 여기에 옮기지 않으면 기록이 사라진다."""
    case = make_case(accept=["python:3.13-slim"])
    candidates = [make_candidate("3.13-slim"), make_candidate("3.14")]

    score = score_full(case, make_response("python:3.13-slim", candidates), [], True)

    assert score.candidate_count == 2
    assert score.accepted_count == 1
    assert score.candidate_images == ["python:3.13-slim", "python:3.14"]


def digest_candidate(tag: str, digest: str | None) -> Candidate:
    return make_candidate(tag).model_copy(update={"digest": digest})


def test_a_recommendation_with_an_accepted_digest_is_accurate() -> None:
    """eclipse-temurin:25-noble과 25-jdk-noble은 이름만 다르고 같은 이미지다."""
    case = make_case(accept=["python:3.13-slim"])
    candidates = [digest_candidate("3.13-slim-trixie", "sha256:same")]

    score = score_full(
        case,
        make_response("python:3.13-slim-trixie", candidates),
        [],
        True,
        accepted_digests=frozenset({"sha256:same"}),
    )

    assert score.accurate is True
    assert score.accurate_by_digest is True


def test_a_name_match_is_not_reported_as_a_digest_match() -> None:
    case = make_case(accept=["python:3.13-slim"])
    candidates = [digest_candidate("3.13-slim", "sha256:same")]

    score = score_full(
        case,
        make_response("python:3.13-slim", candidates),
        [],
        True,
        accepted_digests=frozenset({"sha256:same"}),
    )

    assert score.accurate is True
    assert score.accurate_by_digest is False


def test_an_explicitly_rejected_name_stays_wrong_even_with_an_accepted_digest() -> None:
    """postgres:latest는 지금 18과 같은 이미지지만, 메이저가 바뀔 수 있어 오답으로 지정했다."""
    case = make_case(accept=["python:3.13-slim"], reject=["python:latest"])
    candidates = [digest_candidate("latest", "sha256:same")]

    score = score_full(
        case,
        make_response("python:latest", candidates),
        [],
        True,
        accepted_digests=frozenset({"sha256:same"}),
    )

    assert score.accurate is False
    assert score.accurate_by_digest is False
    assert score.rejected_pick is True


def test_a_candidate_without_a_digest_is_judged_by_name_only() -> None:
    case = make_case(accept=["python:3.13-slim"])
    candidates = [digest_candidate("3.13-slim-trixie", None)]

    score = score_full(
        case,
        make_response("python:3.13-slim-trixie", candidates),
        [],
        True,
        accepted_digests=frozenset({"sha256:same"}),
    )

    assert score.accurate is False


def test_a_digest_match_counts_toward_candidate_hit_and_accepted_count() -> None:
    """정확도에만 적용하면 상한이어야 할 후보 포함률보다 정확도가 높아질 수 있다."""
    case = make_case(accept=["python:3.13-slim"], reject=["python:latest"])
    candidates = [
        digest_candidate("3.13-slim-trixie", "sha256:same"),
        digest_candidate("latest", "sha256:same"),
        digest_candidate("3.14", "sha256:other"),
    ]

    score = score_retrieval(case, candidates, [], accepted_digests=frozenset({"sha256:same"}))

    assert score.candidate_hit is True
    assert score.accepted_count == 1


def derived_candidate(tag: str, **derived: str | None) -> Candidate:
    return make_candidate(tag).model_copy(update=derived)


def test_a_derived_codename_violates_the_exclusion_even_when_the_tag_hides_it() -> None:
    """python:3.14는 이름에 배포판이 없지만 3.14-trixie와 같은 이미지다."""
    candidate = derived_candidate("3.14", distribution="debian", distro_codename="trixie")

    assert conditions_satisfied(Conditions(exclude_distributions=["trixie"]), candidate) is False
    assert conditions_satisfied(Conditions(exclude_distributions=["alpine"]), candidate) is True


def test_a_derived_distribution_violates_the_exclusion() -> None:
    candidate = derived_candidate("3.14-alpine", distribution="alpine", distro_codename="3.24")

    assert conditions_satisfied(Conditions(exclude_distributions=["alpine"]), candidate) is False


def test_derived_values_override_a_word_that_only_looks_like_a_distribution() -> None:
    """파생 값이 있으면 태그 문자열은 보지 않는다. 판정 근거가 하나여야 한다."""
    candidate = derived_candidate(
        "alpine-lookalike", distribution="debian", distro_codename="trixie"
    )

    assert conditions_satisfied(Conditions(exclude_distributions=["alpine"]), candidate) is True


def test_the_tag_name_decides_the_exclusion_when_nothing_was_derived() -> None:
    candidate = derived_candidate("3.14-alpine")

    assert conditions_satisfied(Conditions(exclude_distributions=["alpine"]), candidate) is False


def test_a_derived_version_satisfies_a_prefix_the_alias_name_does_not_show() -> None:
    """nginx:stable은 이름에 버전이 없지만 1.30이다."""
    candidate = derived_candidate("stable", version="1.30")

    assert conditions_satisfied(Conditions(version_prefix="1.30"), candidate) is True


def test_a_derived_version_keeps_the_separator_boundary() -> None:
    candidate = derived_candidate("latest", version="3.14.7")

    assert conditions_satisfied(Conditions(version_prefix="3.1"), candidate) is False
    assert conditions_satisfied(Conditions(version_prefix="3.14"), candidate) is True


def test_a_java8_version_prefix_holds_before_and_after_derivation() -> None:
    """8-jdk는 태그 이름으로 "8"을 통과한다. 파생 버전 8u502-b07로 바뀌어도 통과해야 한다."""
    before = derived_candidate("8-jdk")
    after = derived_candidate("8-jdk", version="8u502-b07")

    assert conditions_satisfied(Conditions(version_prefix="8"), before) is True
    assert conditions_satisfied(Conditions(version_prefix="8"), after) is True
    assert conditions_satisfied(Conditions(version_prefix="80"), after) is False


def test_a_temurin_build_number_extends_the_version_prefix() -> None:
    candidate = derived_candidate("21", version="21.0.12_8")

    assert conditions_satisfied(Conditions(version_prefix="21.0.12"), candidate) is True


def planned_response(plan: SearchPlan | None, notes: list[str] | None = None) -> RecommendResponse:
    return make_response("python:3.13-slim", [make_candidate("3.13-slim")], notes).model_copy(
        update={"plan": plan}
    )


def test_a_plan_matching_the_expected_conditions_passes():
    case = make_case(expected_plan={"architectures": ["arm64"], "distributions": ["bookworm"]})
    plan = SearchPlan(repository="python", architectures=["arm64"], distributions=["bookworm"])

    score = score_full(case, planned_response(plan), [], True)

    assert score.repository_extracted is True
    assert score.plan_matched is True
    assert score.plan == plan.model_dump()


def test_list_fields_are_compared_as_sets():
    case = make_case(expected_plan={"exclude_distributions": ["alpine", "trixie"]})
    plan = SearchPlan(repository="python", exclude_distributions=["trixie", "alpine"])

    assert score_full(case, planned_response(plan), [], True).plan_matched is True


def test_an_invented_condition_fails_extraction():
    """질문에 없는 조건을 만들면 정답 후보가 지워진다. 조건 없는 문항에서도 실패다."""
    case = make_case(expected_plan={})
    plan = SearchPlan(repository="python", exclude_distributions=["alpine"])

    score = score_full(case, planned_response(plan), [], True)

    assert score.plan_matched is False
    assert score.plan_fields["exclude_distributions"] is False
    assert score.plan_fields["architectures"] is True


def test_an_invented_size_limit_fails_extraction():
    """크기는 골든셋에 적지 않았어도 비어 있어야 한다는 뜻으로 비교한다."""
    case = make_case(expected_plan={})
    plan = SearchPlan(repository="python", max_size_mb=100)

    score = score_full(case, planned_response(plan), [], True)

    assert score.plan_matched is False
    assert score.plan_fields["max_size_mb"] is False


def test_a_missing_condition_fails_extraction():
    case = make_case(expected_plan={"version_prefix": "3.12"})

    score = score_full(case, planned_response(SearchPlan(repository="python")), [], True)

    assert score.plan_matched is False
    assert score.plan_fields["version_prefix"] is False


def test_a_wrong_repository_fails_repository_extraction_only():
    case = make_case(expected_plan={})

    score = score_full(case, planned_response(SearchPlan(repository="node")), [], True)

    assert score.repository_extracted is False
    assert score.plan_matched is True


def test_a_failed_extraction_fails_both_and_keeps_the_notes():
    case = make_case(expected_plan={})
    notes = ["검색 조건 추출에 실패해 벡터 검색만 사용했습니다: boom"]

    score = score_full(case, planned_response(None, notes), [], True)

    assert (score.plan, score.plan_fields) == (None, None)
    assert (score.repository_extracted, score.plan_matched) == (False, False)
    assert score.notes == notes


def test_score_full_keeps_the_recommendation_provenance_and_final_dockerfile() -> None:
    """완료 판정(digest 부착, FROM 고정)을 결과 JSON만으로 확인할 수 있어야 한다."""
    response = make_response("python:3.13-slim", [make_candidate("3.13-slim")])
    response = response.model_copy(
        update={
            "recommended": RecommendedImage(
                image="python:3.13-slim",
                digest="sha256:aaa",
                source_url="https://hub.docker.com/_/python",
                collected_at=NOW,
            ),
            "recommendation": response.recommendation.model_copy(
                update={"dockerfile": "FROM python:3.13-slim@sha256:aaa"}
            ),
        }
    )

    score = score_full(make_case(), response, [], True)

    assert score.recommended == {
        "image": "python:3.13-slim",
        "digest": "sha256:aaa",
        "source_url": "https://hub.docker.com/_/python",
        "collected_at": NOW.isoformat().replace("+00:00", "Z"),
    }
    assert score.dockerfile == "FROM python:3.13-slim@sha256:aaa"


def test_score_full_leaves_provenance_empty_without_a_recommendation() -> None:
    score = score_full(make_case(), make_response(None, [make_candidate("3.13-slim")]), [], None)

    assert (score.recommended, score.dockerfile) == (None, None)
