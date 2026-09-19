# tests/eval/test_goldenset_file.py
"""실제 골든셋 파일이 스키마를 통과하는지 CI에서 확인한다.

파일만 읽으므로 네트워크도 DB도 필요 없다. 오타로 깨진 골든셋을 eval을 40분
돌린 뒤에 알게 되는 상황을 막는 것이 목적이다.
"""

from datetime import UTC, date, datetime
from pathlib import Path

from whatfrom.collect.tagparse import parse_tag
from whatfrom.core.contracts import Candidate
from whatfrom.eval.goldenset import Conditions, load_goldenset
from whatfrom.eval.scoring import conditions_satisfied

GOLDENSET = Path(__file__).parents[2] / "eval" / "goldenset.yaml"


def test_the_real_goldenset_loads() -> None:
    goldenset = load_goldenset(GOLDENSET)

    assert len(goldenset.cases) == 40


def test_the_real_goldenset_says_when_it_was_last_verified() -> None:
    """latest·LTS·최신 패치 주장은 시간이 지나면 낡는다. 언제 기준인지 남아야 한다."""
    assert load_goldenset(GOLDENSET).verified_on == date(2026, 9, 12)


def test_every_label_carries_a_source() -> None:
    """스펙 §13. 근거 없는 라벨은 작성자 취향이다."""
    for case in load_goldenset(GOLDENSET).cases:
        assert case.rationale.sources, case.id


def test_accept_and_reject_do_not_overlap() -> None:
    for case in load_goldenset(GOLDENSET).cases:
        assert not set(case.accept) & set(case.reject), case.id


def test_every_accepted_tag_satisfies_its_tag_string_conditions() -> None:
    """정답이라고 적어 둔 답이 같은 문항의 요구를 어긴다면 답안지가 자기모순이다.

    한 문항이 accept에 넣은 태그는 "이렇게 답하면 틀렸다고 하지 않겠다"는
    선언이고, conditions는 "이 요구를 만족해야 한다"는 선언이다. 둘이 부딪치면
    채점기는 같은 답을 정확하다고 세면서 조건 불일치로도 센다. 라벨이 틀린
    것인지 조건이 틀린 것인지 지표만 보고는 알 수 없으므로 파일에서 막는다.

    아키텍처와 크기는 여기서 보지 않는다. 그 둘은 태그 문자열이 아니라 DB의
    플랫폼 정보로 판정되는데 이 테스트는 파일만 읽기 때문이다. 그래서 태그
    문자열만으로 판정되는 version_prefix와 exclude_distributions만 떼어
    Conditions를 새로 만들어 넘긴다. 비교 자체를 여기서 다시 구현하지 않고
    채점기의 conditions_satisfied를 그대로 부르는 이유는, 베껴 쓰면 감시하려던
    그 함수와 따로 놀게 되기 때문이다. 접두 앵커링 규칙이 바뀌면 이 테스트도
    같이 바뀌어야 한다.

    이름에 버전이 없는 별칭(nginx:stable)은 같은 이미지의 다른 태그에서 버전을
    물려받아 채점된다. 그 값은 DB에만 있어 파일로는 판정할 수 없으므로 이런
    태그는 버전 조건을 보지 않는다.
    """
    for case in load_goldenset(GOLDENSET).cases:
        judgeable = Conditions(
            version_prefix=case.conditions.version_prefix,
            exclude_distributions=case.conditions.exclude_distributions,
        )
        if not judgeable.declared:
            continue
        for image in case.accept:
            repository, _, tag = image.partition(":")
            checked = judgeable
            if parse_tag(tag, repository).version is None:
                checked = judgeable.model_copy(update={"version_prefix": None})
            candidate = Candidate(
                image=image,
                repository=repository,
                tag=tag,
                source_url="https://example.invalid/",
                collected_at=datetime(2026, 9, 12, tzinfo=UTC),
            )
            assert conditions_satisfied(checked, candidate), (
                f"{case.id}: accept의 {image}가 같은 문항의 조건을 만족하지 않는다"
            )


def test_every_image_is_a_repo_tag_pair() -> None:
    for case in load_goldenset(GOLDENSET).cases:
        for image in [*case.accept, *case.reject]:
            assert image.count(":") == 1, f"{case.id}: {image}"
            repository, _, tag = image.partition(":")
            assert repository and tag, f"{case.id}: {image}"
            assert repository in case.requires_repositories, f"{case.id}: {image}"
