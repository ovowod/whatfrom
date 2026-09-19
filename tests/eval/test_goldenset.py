# tests/eval/test_goldenset.py
from datetime import date
from pathlib import Path

import pytest

from whatfrom.eval.goldenset import Conditions, GoldenSetError, load_goldenset

VALID = """
version: 1
cases:
  - id: python-numpy-arm64
    question: |
      numpy를 쓰고 ARM64에서 돌아야 해.
    requires_repositories: [python]
    tags: [arch, native-deps]
    accept: [python:3.13-slim]
    reject: [python:3.13-alpine]
    conditions:
      architectures: [arm64]
      exclude_distributions: [alpine]
    expected_plan:
      architectures: [arm64]
    expected_sections: [Image Variants]
    rationale:
      note: musl에는 manylinux 휠이 없다
      sources:
        - https://pythonspeed.com/articles/alpine-docker-python/
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "goldenset.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_a_valid_case(tmp_path: Path) -> None:
    goldenset = load_goldenset(write(tmp_path, VALID))

    assert goldenset.version == 1
    case = goldenset.cases[0]
    assert case.id == "python-numpy-arm64"
    assert case.accept == ["python:3.13-slim"]
    assert case.conditions.architectures == ["arm64"]
    assert case.conditions.declared is True
    assert case.expected_plan.architectures == ["arm64"]
    assert case.expected_plan.exclude_distributions == []


def test_optional_fields_default_to_empty(tmp_path: Path) -> None:
    minimal = """
version: 1
cases:
  - id: minimal
    question: 뭐 쓰지
    requires_repositories: [python]
    accept: [python:3.13-slim]
    expected_plan: {}
    rationale:
      note: 근거
      sources: [https://hub.docker.com/_/python]
"""
    case = load_goldenset(write(tmp_path, minimal)).cases[0]

    assert case.tags == []
    assert case.reject == []
    assert case.expected_sections == []
    assert case.conditions.declared is False


NO_SOURCES = """
version: 1
cases:
  - id: no-sources
    question: 근거가 없다
    requires_repositories: [python]
    accept: [python:3.13-slim]
    expected_plan: {}
    rationale:
      note: 근거를 못 찾았다
      sources: []
"""


def test_missing_sources_is_rejected(tmp_path: Path) -> None:
    """스펙 §13의 순환논리 완화책. 근거 없는 라벨은 골든셋에 들어가지 못한다."""
    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, NO_SOURCES))


@pytest.mark.parametrize("source", ["", "   ", "not a url", "ftp://example.com/doc", "https://"])
def test_a_source_that_is_not_an_http_url_is_rejected(tmp_path: Path, source: str) -> None:
    """빈 목록만 막으면 sources: [""]로 근거 요건을 우회할 수 있다."""
    bad = NO_SOURCES.replace("sources: []", f"sources: [{source!r}]")

    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, bad))


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    """YAML 키 오타를 외부 모델 호출 전에 발견한다.

    오타를 내는 키는 선택 키여야 한다. 필수 키를 오타내면 "키가 없다"로도
    실패해서 extra="forbid"가 사라져도 테스트가 통과한다 — 그러면 오타난 라벨이
    조용히 무시되는 것을 막지 못한다.
    """
    typo = VALID.replace("    expected_sections:", "    expected_section:")

    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, typo))


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    doubled = VALID + VALID.split("cases:", 1)[1]

    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, doubled))


def test_empty_accept_is_rejected(tmp_path: Path) -> None:
    """허용 집합이 비면 그 문항은 무엇을 답해도 오답이다. 라벨이 아니라 실수다."""
    empty = VALID.replace("    accept: [python:3.13-slim]", "    accept: []")

    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, empty))


def test_verified_on_is_read_as_a_date(tmp_path: Path) -> None:
    """라벨을 마지막으로 확인한 날. 실행 메타에 실려 라벨이 언제 기준인지 남긴다."""
    dated = VALID.replace("version: 1", "version: 1\nverified_on: 2026-09-12", 1)

    goldenset = load_goldenset(write(tmp_path, dated))

    assert goldenset.verified_on == date(2026, 9, 12)


def test_verified_on_is_optional(tmp_path: Path) -> None:
    assert load_goldenset(write(tmp_path, VALID)).verified_on is None


def test_version_prefix_alone_declares_conditions() -> None:
    """declared는 조건 일치율의 분모를 가른다.

    네 필드 중 하나라도 빠뜨리면 그 필드만 가진 문항이 통째로 분모에서 사라져
    조건 일치율이 실제보다 좁은 표본에서 계산된다. 실제 골든셋에서 조건을 가진
    18문항 중 7문항이 version_prefix나 max_size_mb 하나에만 걸려 있다.
    """
    assert Conditions(version_prefix="3.13").declared is True


def test_max_size_alone_declares_conditions() -> None:
    assert Conditions(max_size_mb=200).declared is True


def test_a_case_without_expected_plan_is_rejected(tmp_path: Path) -> None:
    """빠뜨린 문항이 조용히 "조건 없음"으로 채점되면 안 된다."""
    missing = VALID.replace("    expected_plan:\n      architectures: [arm64]\n", "")

    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, missing))


def test_expected_plan_rejects_an_unknown_key(tmp_path: Path) -> None:
    typo = VALID.replace(
        "      architectures: [arm64]\n    expected_sections",
        "      architecture: [arm64]\n    expected_sections",
    )

    with pytest.raises(GoldenSetError):
        load_goldenset(write(tmp_path, typo))
