# tests/collect/test_hub_client.py
"""HubClient의 재시도·rate limit·오프셋 제한.

실제로 기다리지 않는다. sleep과 현재 시각을 주입해 대기 시간만 기록한다.
"""

import httpx2
import pytest

from whatfrom.collect.hub import HubClient, OffsetLimitReached, RateLimitWaitTooLong

REPO_URL = "https://hub.docker.com/v2/repositories/library/python/"
OFFSET_LIMIT_BODY = {
    "message": "pagination offset too large for anonymous requests; sign in to page further",
    "errinfo": {},
}


def make_client(responses: list, sleeps: list[float], now: float = 1000.0):
    """responses의 원소를 요청 순서대로 돌려준다. 예외 인스턴스면 그 예외를 던진다."""
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        item = responses[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    http = httpx2.Client(transport=httpx2.MockTransport(handler))
    client = HubClient(http, sleep=sleeps.append, now=lambda: now)
    return client, calls


def ok(headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(200, json={"name": "python"}, headers=headers or {})


def test_waits_until_reset_when_remaining_is_at_the_margin():
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "5", "x-ratelimit-reset": "1030"}
    client, _ = make_client([ok(headers)], sleeps, now=1000.0)

    client.fetch_repository("python")

    assert sleeps == [30.0]


def test_does_not_wait_while_remaining_is_above_the_margin():
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "6", "x-ratelimit-reset": "1030"}
    client, _ = make_client([ok(headers)], sleeps, now=1000.0)

    client.fetch_repository("python")

    assert sleeps == []


def test_does_not_wait_without_rate_limit_headers():
    """헤더가 없다고 기다리면 Hub가 헤더만 빼도 수집이 서버린다."""
    sleeps: list[float] = []
    client, _ = make_client([ok()], sleeps)

    client.fetch_repository("python")

    assert sleeps == []


def test_does_not_wait_when_reset_header_is_missing():
    """remaining만 있고 reset이 없으면 기다리지 않는다."""
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "0"}
    client, _ = make_client([ok(headers)], sleeps)

    client.fetch_repository("python")

    assert sleeps == []


def test_does_not_wait_when_reset_is_in_the_past():
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "5", "x-ratelimit-reset": "990"}
    client, _ = make_client([ok(headers)], sleeps, now=1000.0)

    client.fetch_repository("python")

    assert sleeps == []


def test_ignores_unparseable_remaining_header():
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "soon", "x-ratelimit-reset": "1030"}
    client, _ = make_client([ok(headers)], sleeps, now=1000.0)

    client.fetch_repository("python")

    assert sleeps == []


def test_ignores_non_finite_reset_header():
    """ "nan"/"inf"도 float()을 통과하니 따로 걸러야 한다."""
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "5", "x-ratelimit-reset": "nan"}
    client, _ = make_client([ok(headers)], sleeps)

    client.fetch_repository("python")

    assert sleeps == []


def test_retries_429_after_retry_after():
    sleeps: list[float] = []
    too_many = httpx2.Response(429, headers={"retry-after": "7"})
    client, calls = make_client([too_many, ok()], sleeps)

    assert client.fetch_repository("python") == {"name": "python"}
    assert sleeps == [7.0]
    assert len(calls) == 2


def test_retries_429_prefers_retry_after_over_reset():
    """retry-after와 reset이 둘 다 있으면 retry-after를 먼저 본다."""
    sleeps: list[float] = []
    too_many = httpx2.Response(429, headers={"retry-after": "7", "x-ratelimit-reset": "1020"})
    client, _ = make_client([too_many, ok()], sleeps, now=1000.0)

    client.fetch_repository("python")

    assert sleeps == [7.0]


def test_retries_429_until_reset_when_retry_after_is_missing():
    sleeps: list[float] = []
    too_many = httpx2.Response(429, headers={"x-ratelimit-reset": "1012"})
    client, _ = make_client([too_many, ok()], sleeps, now=1000.0)

    client.fetch_repository("python")

    assert sleeps == [12.0]


def test_refuses_to_wait_longer_than_the_cap():
    sleeps: list[float] = []
    too_many = httpx2.Response(429, headers={"retry-after": "1000"})
    client, _ = make_client([too_many], sleeps)

    with pytest.raises(RateLimitWaitTooLong):
        client.fetch_repository("python")
    assert sleeps == []


def test_waits_exactly_the_cap():
    """상한과 정확히 같은 대기는 넘긴 게 아니라 허용된다."""
    sleeps: list[float] = []
    too_many = httpx2.Response(429, headers={"retry-after": "300"})
    client, _ = make_client([too_many, ok()], sleeps)

    client.fetch_repository("python")

    assert sleeps == [300.0]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retries_server_errors(status):
    sleeps: list[float] = []
    client, calls = make_client([httpx2.Response(status), ok()], sleeps)

    client.fetch_repository("python")

    assert len(calls) == 2
    assert len(sleeps) == 1


def test_retries_read_timeouts():
    """요청 경로와 달리 배치는 읽기 타임아웃을 재시도한다. 기다리는 사용자가 없다."""
    sleeps: list[float] = []
    timeout = httpx2.ReadTimeout("slow", request=httpx2.Request("GET", REPO_URL))
    client, calls = make_client([timeout, ok()], sleeps)

    client.fetch_repository("python")

    assert len(calls) == 2


def test_retries_connect_errors():
    sleeps: list[float] = []
    error = httpx2.ConnectError("boom", request=httpx2.Request("GET", REPO_URL))
    client, calls = make_client([error, ok()], sleeps)

    assert client.fetch_repository("python") == {"name": "python"}
    assert len(calls) == 2


def test_retries_read_errors():
    sleeps: list[float] = []
    error = httpx2.ReadError("boom", request=httpx2.Request("GET", REPO_URL))
    client, calls = make_client([error, ok()], sleeps)

    assert client.fetch_repository("python") == {"name": "python"}
    assert len(calls) == 2


def test_does_not_retry_not_found():
    sleeps: list[float] = []
    client, calls = make_client([httpx2.Response(404)], sleeps)

    with pytest.raises(httpx2.HTTPStatusError):
        client.fetch_repository("python")
    assert len(calls) == 1
    assert sleeps == []


def test_gives_up_after_five_attempts_with_the_last_error():
    sleeps: list[float] = []
    client, calls = make_client([httpx2.Response(503)] * 5, sleeps)

    with pytest.raises(httpx2.HTTPStatusError) as raised:
        client.fetch_repository("python")
    assert raised.value.response.status_code == 503
    assert len(calls) == 5
    assert len(sleeps) == 4


def test_offset_limit_raises_without_retrying():
    sleeps: list[float] = []
    client, calls = make_client([httpx2.Response(403, json=OFFSET_LIMIT_BODY)], sleeps)

    with pytest.raises(OffsetLimitReached):
        client.fetch_repository("python")
    assert len(calls) == 1
    assert sleeps == []


def test_other_forbidden_responses_are_plain_http_errors():
    sleeps: list[float] = []
    forbidden = httpx2.Response(403, json={"message": "forbidden"})
    client, calls = make_client([forbidden], sleeps)

    with pytest.raises(httpx2.HTTPStatusError):
        client.fetch_repository("python")
    assert len(calls) == 1


def test_offset_limit_message_on_non_403_is_plain_http_error():
    """오프셋 제한 메시지라도 403이 아니면 일반 HTTP 에러다."""
    sleeps: list[float] = []
    not_found = httpx2.Response(404, json=OFFSET_LIMIT_BODY)
    client, calls = make_client([not_found], sleeps)

    with pytest.raises(httpx2.HTTPStatusError):
        client.fetch_repository("python")
    assert len(calls) == 1


def test_offset_limit_check_handles_non_dict_json_body():
    """403 바디가 객체가 아니면 .get을 쓸 수 없으니 오프셋 제한이 아니라고 봐야 한다."""
    sleeps: list[float] = []
    weird = httpx2.Response(403, json=["x"])
    client, calls = make_client([weird], sleeps)

    with pytest.raises(httpx2.HTTPStatusError):
        client.fetch_repository("python")
    assert len(calls) == 1


def test_tag_pages_are_ordered_by_last_update():
    """기본 정렬은 latest를 맨 앞에 고정한다. 명시해야 최근 갱신 순이 된다."""
    sleeps: list[float] = []
    page = httpx2.Response(200, json={"next": None, "results": []})
    client, calls = make_client([page], sleeps)

    list(client.iter_tag_pages("python"))

    assert "ordering=last_updated" in calls[0]
