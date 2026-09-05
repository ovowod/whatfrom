from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import httpx2

HUB_BASE = "https://hub.docker.com/v2"


@dataclass(frozen=True)
class VariantRow:
    os: str
    architecture: str
    arch_variant: str
    os_version: str
    digest: str
    size_bytes: int


@dataclass(frozen=True)
class TagRow:
    tag: str
    manifest_digest: str | None
    last_pushed_at: datetime | None
    variants: tuple[VariantRow, ...]


@dataclass(frozen=True)
class RepositoryRow:
    name: str
    is_official: bool
    description: str | None
    source_url: str
    readme: str


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    # Hub는 나노초 9자리를 보낸다. fromisoformat이 마이크로초로 잘라 받는다.
    return datetime.fromisoformat(value)


def parse_tag_page(payload: dict) -> list[TagRow]:
    rows: list[TagRow] = []
    for result in payload.get("results", []):
        variants = tuple(
            VariantRow(
                os=image["os"],
                architecture=image["architecture"],
                arch_variant=image.get("variant") or "",
                # Linux는 null로 온다. UNIQUE 제약이 NULL을 구분값으로 보므로 ""로 정규화
                os_version=image.get("os_version") or "",
                digest=image["digest"],
                size_bytes=image["size"],
            )
            # os=unknown은 attestation manifest(provenance/SBOM)이지 이미지가 아니다.
            for image in result.get("images", [])
            if image.get("os") != "unknown"
        )
        rows.append(
            TagRow(
                tag=result["name"],
                manifest_digest=result.get("digest"),
                last_pushed_at=_parse_timestamp(result.get("tag_last_pushed")),
                variants=variants,
            )
        )
    return rows


def parse_repository(payload: dict) -> RepositoryRow:
    name = payload["name"]
    namespace = payload.get("namespace")
    return RepositoryRow(
        name=name,
        is_official=namespace == "library",
        description=payload.get("description"),
        source_url=f"https://hub.docker.com/_/{name}",
        readme=payload.get("full_description") or "",
    )


class HubClient:
    """Docker Hub Hub API v2 읽기 전용 클라이언트. 배치에서만 쓴다."""

    def __init__(self, client: httpx2.Client) -> None:
        self._client = client

    def fetch_repository(self, repository: str) -> dict:
        response = self._client.get(f"{HUB_BASE}/repositories/library/{repository}/")
        response.raise_for_status()
        return response.json()

    def iter_tag_pages(
        self, repository: str, page_size: int = 100, max_pages: int | None = None
    ) -> Iterator[dict]:
        url = f"{HUB_BASE}/repositories/library/{repository}/tags?page_size={page_size}"
        seen = 0
        while url:
            response = self._client.get(url)
            response.raise_for_status()
            payload = response.json()
            yield payload
            seen += 1
            if max_pages is not None and seen >= max_pages:
                return
            url = payload.get("next")
