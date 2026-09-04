import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from whatfrom.collect.hub import HubClient, parse_repository, parse_tag_page

FIXTURES = Path(__file__).parent / "fixtures"


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
    assert len(rows[0].variants) == 2


def test_parse_tag_page_keeps_arch_variant_and_size(tags_payload):
    rows = parse_tag_page(tags_payload)
    arm = next(v for v in rows[0].variants if v.architecture == "arm64")

    assert arm.arch_variant == "v8"
    assert arm.size_bytes == 47609438
    assert arm.os == "linux"


def test_parse_tag_page_normalizes_null_variant_to_empty_string(tags_payload):
    """UNIQUE 제약이 NULL을 서로 다른 값으로 취급하므로 빈 문자열로 정규화한다."""
    rows = parse_tag_page(tags_payload)
    amd = next(v for v in rows[0].variants if v.architecture == "amd64")

    assert amd.arch_variant == ""


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
