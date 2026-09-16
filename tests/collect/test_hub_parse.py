import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from whatfrom.collect.hub import HubClient, parse_repository, parse_tag_page

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture
def tags_payload() -> dict:
    return json.loads((FIXTURES / "hub_tags_page.json").read_text())


@pytest.fixture
def repository_payload() -> dict:
    return json.loads((FIXTURES / "hub_repository.json").read_text())


def test_parse_tag_page_reads_tag_level_fields(tags_payload):
    rows = parse_tag_page(tags_payload)

    assert [r.tag for r in rows] == ["3.13-slim", "3.13-alpine"]
    assert rows[0].manifest_digest == (
        "sha256:5a2a26a2439c981f3fc53c6372f08bc1ba1e21d7e48e966cd83af9f829fa8882"
    )
    assert rows[0].last_pushed_at == datetime(2026, 9, 3, 2, 9, 16, 561972, tzinfo=UTC)


def test_parse_tag_page_drops_unknown_os_attestation_manifests(tags_payload):
    """os=unknown 항목은 실제 이미지가 아니라 provenance/SBOM manifest다."""
    rows = parse_tag_page(tags_payload)

    assert all(v.os != "unknown" for r in rows for v in r.variants)
    assert len(rows[0].variants) == 4  # linux/amd64, linux/arm64, windows x2


def test_parse_tag_page_keeps_arch_variant_and_size(tags_payload):
    rows = parse_tag_page(tags_payload)
    arm = next(v for v in rows[0].variants if v.architecture == "arm64")

    assert arm.arch_variant == "v8"
    assert arm.size_bytes == 47609438
    assert arm.os == "linux"


def test_parse_tag_page_keeps_windows_manifests_that_differ_only_by_os_version(tags_payload):
    """Windows는 호스트 커널 버전마다 별개 매니페스트를 낸다 — digest도 크기도 다르다."""
    rows = parse_tag_page(tags_payload)
    windows = [v for v in rows[0].variants if v.os == "windows"]

    assert len(windows) == 2
    assert {v.os_version for v in windows} == {"10.0.20348.5499", "10.0.26100.33296"}
    assert len({v.digest for v in windows}) == 2


def test_parse_tag_page_normalizes_null_os_version_to_empty_string(tags_payload):
    """Linux는 os_version이 null로 온다. UNIQUE가 NULL을 구분값으로 보므로 ""로 정규화한다."""
    rows = parse_tag_page(tags_payload)
    linux = [v for v in rows[0].variants if v.os == "linux"]

    assert linux
    assert all(v.os_version == "" for v in linux)


def test_parse_tag_page_normalizes_null_variant_to_empty_string(tags_payload):
    """UNIQUE 제약이 NULL을 서로 다른 값으로 취급하므로 빈 문자열로 정규화한다."""
    rows = parse_tag_page(tags_payload)
    amd = next(v for v in rows[0].variants if v.architecture == "amd64")

    assert amd.arch_variant == ""


def test_parse_tag_page_skips_images_without_a_digest():
    """오래된 비활성 매니페스트는 digest가 없다. NOT NULL 컬럼이라 저장할 수 없다."""
    payload = {
        "results": [
            {
                "name": "4.0.13-alpine3.9",
                "digest": "sha256:tagdigest",
                "tag_last_pushed": None,
                "images": [
                    {
                        "architecture": "amd64",
                        "variant": None,
                        "digest": "sha256:normal",
                        "os": "linux",
                        "size": 123,
                    },
                    {
                        "architecture": "arm",
                        "variant": "v6",
                        "os": "linux",
                        "size": 13297953,
                        "status": "inactive",
                    },
                ],
            }
        ]
    }

    rows = parse_tag_page(payload)

    assert len(rows) == 1
    assert len(rows[0].variants) == 1
    assert rows[0].variants[0].digest == "sha256:normal"


def test_parse_tag_page_skips_images_with_a_null_digest():
    """digest 키는 있지만 값이 None인 경우도 같은 이유로 건너뛴다. 태그 행은 남는다."""
    payload = {
        "results": [
            {
                "name": "wily-20160526",
                "digest": "sha256:tagdigest",
                "tag_last_pushed": None,
                "images": [
                    {
                        "architecture": "amd64",
                        "variant": None,
                        "digest": None,
                        "os": "",
                        "size": 50976456,
                        "status": "inactive",
                    }
                ],
            }
        ]
    }

    rows = parse_tag_page(payload)

    assert len(rows) == 1
    assert rows[0].tag == "wily-20160526"
    assert rows[0].variants == ()


def test_parse_tag_page_handles_missing_tag_digest_alongside_missing_image_digest():
    """이미지 digest도, 태그 digest도 없는 경우 — manifest_digest는 기존처럼 None이다."""
    payload = {
        "results": [
            {
                "name": "wily-20160526",
                "tag_last_pushed": None,
                "images": [
                    {
                        "architecture": "amd64",
                        "variant": None,
                        "os": "",
                        "size": 50976456,
                        "status": "inactive",
                    }
                ],
            }
        ]
    }

    rows = parse_tag_page(payload)

    assert len(rows) == 1
    assert rows[0].manifest_digest is None
    assert rows[0].variants == ()


def test_parse_repository_marks_library_namespace_as_official(repository_payload):
    row = parse_repository(repository_payload)

    assert row.name == "python"
    assert row.is_official is True
    assert row.source_url == "https://hub.docker.com/_/python"
    assert "Image Variants" in row.readme


def test_hub_client_follows_pagination_until_next_is_null():
    pages = [
        {
            "next": "https://hub.docker.com/v2/repositories/library/python/tags?page=2",
            "results": [],
        },
        {"next": None, "results": []},
    ]
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        return httpx2.Response(200, json=pages[len(calls) - 1])

    transport = httpx2.MockTransport(handler)
    with httpx2.Client(transport=transport) as http:
        collected = list(HubClient(http).iter_tag_pages("python", page_size=100))

    assert len(collected) == 2
    assert "page_size=100" in calls[0]
    assert calls[1].endswith("page=2")


def test_hub_client_stops_at_max_pages():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"next": "https://example.invalid/next", "results": []})

    transport = httpx2.MockTransport(handler)
    with httpx2.Client(transport=transport) as http:
        collected = list(HubClient(http).iter_tag_pages("python", max_pages=3))

    assert len(collected) == 3


def test_hub_client_fetches_a_repository_from_the_library_namespace(repository_payload):
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        return httpx2.Response(200, json=repository_payload)

    transport = httpx2.MockTransport(handler)
    with httpx2.Client(transport=transport) as http:
        payload = HubClient(http).fetch_repository("python")

    # library 네임스페이스와 끝의 슬래시가 빠지면 허브가 404를 준다.
    assert calls == ["https://hub.docker.com/v2/repositories/library/python/"]
    assert payload == repository_payload


def test_hub_client_raises_when_the_repository_is_missing():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404, json={"detail": "object not found"})

    transport = httpx2.MockTransport(handler)
    with httpx2.Client(transport=transport) as http:
        with pytest.raises(httpx2.HTTPStatusError):
            HubClient(http).fetch_repository("does-not-exist")
