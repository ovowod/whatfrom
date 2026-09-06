import httpx2
import pytest

from whatfrom.httpclient import RemoteCallError, post_json

URL = "http://svc.invalid/v1/thing"


def _client(handler) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_post_json_returns_the_parsed_body():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"ok": True})

    assert post_json(_client(handler), URL, {"a": 1}) == {"ok": True}


def test_post_json_sends_bearer_auth_when_a_key_is_given():
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx2.Response(200, json={})

    post_json(_client(handler), URL, {}, api_key="k")

    assert seen["auth"] == "Bearer k"


def test_post_json_omits_auth_when_no_key_is_set():
    """로컬 Ollama는 API 키를 요구하지 않는다."""
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx2.Response(200, json={})

    post_json(_client(handler), URL, {}, api_key="")

    assert seen["auth"] is None


def test_post_json_retries_a_503_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx2.Response(503, text="overloaded")
        return httpx2.Response(200, json={"ok": True})

    body = post_json(_client(handler), URL, {}, sleep=lambda _: None)

    assert calls["n"] == 2
    assert body == {"ok": True}


def test_post_json_does_not_retry_a_400():
    """4xx는 같은 요청이면 같은 답이 온다 — 재시도는 지연만 늘린다 (스펙 §8)."""
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return httpx2.Response(400, text="bad request")

    with pytest.raises(RemoteCallError, match="api error 400"):
        post_json(_client(handler), URL, {}, sleep=lambda _: None)

    assert calls["n"] == 1


def test_post_json_gives_up_after_max_retries():
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return httpx2.Response(429, text="slow down")

    with pytest.raises(RemoteCallError, match="after 3 attempts"):
        post_json(_client(handler), URL, {}, max_retries=2, sleep=lambda _: None)

    assert calls["n"] == 3


def test_post_json_wraps_connection_failures():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to host")

    with pytest.raises(RemoteCallError, match="connection failed"):
        post_json(_client(handler), URL, {}, max_retries=0, sleep=lambda _: None)


def test_post_json_rejects_a_non_json_body():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>not json</html>")

    with pytest.raises(RemoteCallError, match="not JSON"):
        post_json(_client(handler), URL, {})


def test_post_json_does_not_retry_a_read_timeout():
    """읽기 타임아웃은 이미 예산을 다 쓴 뒤다. 재시도하면 대기가 배로 늘 뿐이다.

    LLM 타임아웃이 120초인데 3회 시도하면 요청 하나가 6분을 넘긴다.
    """
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        raise httpx2.ReadTimeout("too slow", request=request)

    with pytest.raises(RemoteCallError, match="request failed"):
        post_json(_client(handler), URL, {}, sleep=lambda _: None)

    assert calls["n"] == 1


def test_post_json_retries_a_connect_error():
    """연결 단계 실패는 아직 아무것도 못 보낸 상태고 3초에 끝난다. 재시도가 싸다."""
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx2.ConnectError("refused", request=request)
        return httpx2.Response(200, json={"ok": True})

    assert post_json(_client(handler), URL, {}, sleep=lambda _: None) == {"ok": True}
    assert calls["n"] == 2
