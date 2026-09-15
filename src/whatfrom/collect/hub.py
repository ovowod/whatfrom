import math
import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime

import httpx2

HUB_BASE = "https://hub.docker.com/v2"

TAGS_ORDERING = "last_updated"

# 수집 배치의 재시도 정책. 요청 경로(core/httpclient)와 다르다.
MAX_ATTEMPTS = 5
RATE_LIMIT_MARGIN = 5
MAX_WAIT_SECONDS = 300.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# 요청 경로는 읽기 타임아웃을 재시도하지 않는다. 사용자가 기다리는 동안 대기가
# 배로 늘기 때문이다. 배치에는 기다리는 사용자가 없으니 재시도한다.
RETRYABLE_TRANSPORT_ERRORS = (
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.ReadTimeout,
    httpx2.ReadError,
    httpx2.PoolTimeout,
    httpx2.RemoteProtocolError,
)
OFFSET_LIMIT_MESSAGE = "pagination offset too large"


class OffsetLimitReached(Exception):
    """익명 요청이 닿을 수 있는 오프셋(1,000)을 넘었다. 실패가 아니라 범위의 끝이다."""


class RateLimitWaitTooLong(Exception):
    """기다려야 할 시간이 상한을 넘었다. 몇 시간씩 멈춰 있는 대신 실패로 끝낸다."""


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
    """Docker Hub Hub API v2 읽기 전용 클라이언트. 배치에서만 쓴다.

    모든 요청은 _get을 거친다. rate limit 헤더를 보고 미리 늦추고, 429·5xx·
    연결 실패·읽기 타임아웃을 재시도한다. 익명 오프셋 제한 403은 재시도하지
    않고 OffsetLimitReached로 알린다.
    """

    def __init__(
        self,
        client: httpx2.Client,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._now = now

    def fetch_repository(self, repository: str) -> dict:
        return self._get(f"{HUB_BASE}/repositories/library/{repository}/").json()

    def iter_tag_pages(
        self, repository: str, page_size: int = 100, max_pages: int | None = None
    ) -> Iterator[dict]:
        url = (
            f"{HUB_BASE}/repositories/library/{repository}/tags"
            f"?page_size={page_size}&ordering={TAGS_ORDERING}"
        )
        seen = 0
        while url:
            payload = self._get(url).json()
            yield payload
            seen += 1
            if max_pages is not None and seen >= max_pages:
                return
            url = payload.get("next")

    def _get(self, url: str) -> httpx2.Response:
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = self._client.get(url)
            except RETRYABLE_TRANSPORT_ERRORS as exc:
                last_error = exc
                wait = _backoff(attempt)
            else:
                if response.status_code == 403 and _is_offset_limit(response):
                    raise OffsetLimitReached(url)
                if response.status_code not in RETRY_STATUSES:
                    response.raise_for_status()
                    # 페이싱은 성공 응답 뒤에 한다. 이 대기가 상한을 넘으면
                    # RateLimitWaitTooLong이 나서 방금 받은 응답을 버리게 되지만,
                    # 관측된 Hub 윈도우는 1분 내로 풀리므로 드물고, 수집기가 다음
                    # 실행에서 도달 가능한 범위를 처음부터 다시 훑으니 감수한다.
                    self._respect_rate_limit(response)
                    return response
                last_error = httpx2.HTTPStatusError(
                    f"{response.status_code} from {url}",
                    request=response.request,
                    response=response,
                )
                wait = self._retry_wait(response, attempt)
            if attempt < MAX_ATTEMPTS - 1:
                self._wait(wait)
        assert last_error is not None
        raise last_error

    def _respect_rate_limit(self, response: httpx2.Response) -> None:
        remaining = _header_number(response, "x-ratelimit-remaining")
        reset = _header_number(response, "x-ratelimit-reset")
        # 헤더가 없으면 기다리지 않는다. 없다고 멈추면 Hub가 헤더만 빼도 수집이 선다.
        if remaining is None or reset is None:
            return
        if remaining <= RATE_LIMIT_MARGIN:
            self._wait(reset - self._now())

    def _retry_wait(self, response: httpx2.Response, attempt: int) -> float:
        if response.status_code != 429:
            return _backoff(attempt)
        retry_after = _header_number(response, "retry-after")
        if retry_after is not None:
            return retry_after
        reset = _header_number(response, "x-ratelimit-reset")
        if reset is not None:
            return reset - self._now()
        return _backoff(attempt)

    def _wait(self, seconds: float) -> None:
        if seconds <= 0:
            return
        if seconds > MAX_WAIT_SECONDS:
            raise RateLimitWaitTooLong(f"{seconds:.0f}s exceeds {MAX_WAIT_SECONDS:.0f}s")
        self._sleep(seconds)


def _backoff(attempt: int) -> float:
    return 2**attempt + random.random()


def _header_number(response: httpx2.Response, name: str) -> float | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    # "nan"/"inf"도 float()을 통과한다. sleep(nan)까지 흘러가지 않도록 걸러낸다.
    if not math.isfinite(number):
        return None
    return number


def _is_offset_limit(response: httpx2.Response) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    message = body.get("message", "")
    return isinstance(message, str) and OFFSET_LIMIT_MESSAGE in message
