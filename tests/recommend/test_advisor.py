from datetime import UTC, datetime

import pytest

from whatfrom.core.contracts import Candidate, Evidence, Platform, Recommendation
from whatfrom.core.httpclient import RemoteCallError
from whatfrom.recommend.advisor import advise, build_prompt
from whatfrom.recommend.llm import FakeLLMProvider

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _candidate(tag: str, size: int, repository: str = "python") -> Candidate:
    return Candidate(
        image=f"{repository}:{tag}",
        repository=repository,
        tag=tag,
        digest="sha256:aaa",
        platforms=[
            Platform(
                os="linux",
                architecture="amd64",
                arch_variant="",
                os_version="",
                size_bytes=size,
                digest="sha256:a",
            ),
            Platform(
                os="linux",
                architecture="arm64",
                arch_variant="v8",
                os_version="",
                size_bytes=size + 600_000,
                digest="sha256:b",
            ),
        ],
        last_pushed_at=NOW,
        source_url="https://hub.docker.com/_/python",
        collected_at=NOW,
        evidence=[
            Evidence(
                repository=repository,
                section_title="Image Variants",
                content=f"{repository}: musl libc instead of glibc",
                source_url=f"https://github.com/docker-library/docs/blob/master/{repository}/README.md",
            )
        ],
    )


def test_build_prompt_lists_every_candidate_image():
    prompt = build_prompt(
        "numpy 쓰는데 arm64", [_candidate("3.13-slim", 1), _candidate("3.13-alpine", 2)]
    )

    assert "python:3.13-slim" in prompt
    assert "python:3.13-alpine" in prompt
    assert "numpy 쓰는데 arm64" in prompt


def test_build_prompt_includes_platforms_and_evidence():
    prompt = build_prompt("q", [_candidate("3.13-slim", 46992930)])

    assert "linux/amd64" in prompt and "linux/arm64" in prompt
    assert "musl libc instead of glibc" in prompt
    assert "Image Variants" in prompt


def test_advise_returns_the_providers_recommendation():
    expected = Recommendation(
        image="python:3.13-slim", reason="numpy needs glibc", dockerfile="FROM python:3.13-slim\n"
    )
    provider = FakeLLMProvider(recommendation=expected)

    assert advise(provider, "q", [_candidate("3.13-slim", 1)]) == expected


def test_advise_raises_llm_error_when_there_are_no_candidates():
    with pytest.raises(RemoteCallError):
        advise(
            FakeLLMProvider(recommendation=Recommendation(image="x", reason="", dockerfile="")),
            "q",
            [],
        )


def test_advise_propagates_provider_failure_as_llm_error():
    provider = FakeLLMProvider(error=RemoteCallError("upstream 500"))

    with pytest.raises(RemoteCallError):
        advise(provider, "q", [_candidate("3.13-slim", 1)])


def test_build_prompt_keeps_the_same_section_from_different_repositories():
    """리포지토리가 다르면 제목이 같아도 다른 문서다. 제목만으로 지우면 한쪽이 사라진다."""
    prompt = build_prompt("q", [_candidate("3.13-slim", 1), _candidate("24-slim", 2, "node")])

    assert "python: musl libc instead of glibc" in prompt
    assert "node: musl libc instead of glibc" in prompt
    assert "## python — Image Variants" in prompt
    assert "## node — Image Variants" in prompt


def test_build_prompt_lists_a_shared_section_once():
    """같은 리포지토리의 같은 섹션은 후보마다 붙어 있어도 한 번만 넘긴다."""
    prompt = build_prompt("q", [_candidate("3.13-slim", 1), _candidate("3.13-alpine", 2)])

    assert prompt.count("## python — Image Variants") == 1
