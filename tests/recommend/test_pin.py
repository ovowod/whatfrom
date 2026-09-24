"""검증을 통과한 Dockerfile의 FROM을 수집 시점 digest로 고정한다."""

from whatfrom.recommend.pin import pin_dockerfile

DIGESTS = {"python:3.13-slim": "sha256:aaa", "nginx:1.30": "sha256:bbb"}


def test_a_from_line_is_pinned_to_the_digest_and_keeps_the_tag():
    pinned, missing = pin_dockerfile("FROM python:3.13-slim\nWORKDIR /app\n", DIGESTS)

    assert pinned == "FROM python:3.13-slim@sha256:aaa\nWORKDIR /app\n"
    assert missing == []


def test_a_lowercase_from_line_is_pinned_too():
    pinned, _ = pin_dockerfile("from python:3.13-slim\n", DIGESTS)

    assert pinned == "from python:3.13-slim@sha256:aaa\n"


def test_multistage_pins_every_image_and_leaves_stage_names_and_scratch():
    dockerfile = (
        "FROM python:3.13-slim AS build\n"
        "RUN pip wheel .\n"
        "FROM build AS test\n"
        "FROM scratch AS empty\n"
        "FROM nginx:1.30\n"
    )

    pinned, missing = pin_dockerfile(dockerfile, DIGESTS)

    assert pinned == (
        "FROM python:3.13-slim@sha256:aaa AS build\n"
        "RUN pip wheel .\n"
        "FROM build AS test\n"
        "FROM scratch AS empty\n"
        "FROM nginx:1.30@sha256:bbb\n"
    )
    assert missing == []


def test_platform_flag_and_stage_name_are_kept():
    pinned, _ = pin_dockerfile("FROM --platform=linux/arm64 python:3.13-slim as build\n", DIGESTS)

    assert pinned == "FROM --platform=linux/arm64 python:3.13-slim@sha256:aaa as build\n"


def test_an_image_without_a_digest_is_left_as_is_and_reported():
    pinned, missing = pin_dockerfile(
        "FROM python:3.13-slim\nFROM python:3.13-slim\n", {"python:3.13-slim": None}
    )

    assert pinned == "FROM python:3.13-slim\nFROM python:3.13-slim\n"
    assert missing == ["python:3.13-slim"]


def test_an_already_pinned_reference_is_not_pinned_twice():
    dockerfile = "FROM python:3.13-slim@sha256:zzz\n"

    pinned, missing = pin_dockerfile(dockerfile, DIGESTS)

    assert pinned == dockerfile
    assert missing == []
