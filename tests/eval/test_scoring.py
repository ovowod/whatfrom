# tests/eval/test_scoring.py
from datetime import UTC, datetime

from whatfrom.core.contracts import Candidate, Platform, Recommendation, RecommendResponse
from whatfrom.eval.goldenset import Conditions, GoldenCase, Rationale
from whatfrom.eval.scoring import conditions_satisfied, score_full, score_retrieval

NOW = datetime(2026, 9, 10, tzinfo=UTC)


def make_case(**overrides: object) -> GoldenCase:
    base: dict[str, object] = {
        "id": "case",
        "question": "질문",
        "requires_repositories": ["python"],
        "accept": ["python:3.13-slim"],
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
