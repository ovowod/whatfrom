import json

import httpx2
import pytest

from whatfrom.embed import EMBEDDING_DIM, OpenAICompatibleEmbedder
from whatfrom.httpclient import RemoteCallError


def _vec(fill: float, dim: int = EMBEDDING_DIM) -> list[float]:
    return [fill] * dim


def _embedder(handler, **kwargs) -> OpenAICompatibleEmbedder:
    kwargs.setdefault("sleep", lambda _: None)
    return OpenAICompatibleEmbedder(
        base_url="http://emb.invalid/v1",
        model="bge-m3",
        api_key="",
        client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        **kwargs,
    )


def test_embedder_posts_the_model_and_all_inputs_in_one_call():
    seen: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": _vec(0.1)}, {"index": 1, "embedding": _vec(0.2)}]
            },
        )

    _embedder(handler).embed(["a", "b"])

    assert seen["url"] == "http://emb.invalid/v1/embeddings"
    assert seen["body"]["model"] == "bge-m3"
    assert seen["body"]["input"] == ["a", "b"]


def test_embedder_restores_request_order_from_the_index_field():
    """응답 순서는 보장되지 않는다. 어긋나면 청크와 벡터가 조용히 뒤바뀐다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": _vec(0.2)}, {"index": 0, "embedding": _vec(0.1)}]
            },
        )

    vectors = _embedder(handler).embed(["first", "second"])

    assert vectors[0][0] == pytest.approx(0.1)
    assert vectors[1][0] == pytest.approx(0.2)


def test_embedder_rejects_vectors_of_the_wrong_dimension():
    """768차원 모델을 물리면 수천 건을 계산하기 전에 여기서 멈춘다."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"data": [{"index": 0, "embedding": _vec(0.1, 768)}]})

    with pytest.raises(RemoteCallError, match="768-dim"):
        _embedder(handler).embed(["a"])


def test_embedder_rejects_a_short_response():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"data": [{"index": 0, "embedding": _vec(0.1)}]})

    with pytest.raises(RemoteCallError, match="asked for 2 embeddings"):
        _embedder(handler).embed(["a", "b"])


def test_embedder_rejects_a_malformed_response():
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"unexpected": "shape"})

    with pytest.raises(RemoteCallError, match="malformed embeddings response"):
        _embedder(handler).embed(["a"])


def test_embedder_makes_no_request_for_an_empty_batch():
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError("should not have been called")

    assert _embedder(handler).embed([]) == []
