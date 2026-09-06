# src/whatfrom/core/embed.py
import hashlib
import math
import re
import time
from collections.abc import Callable
from typing import Protocol

import httpx2

from whatfrom.core.config import settings
from whatfrom.core.httpclient import DEFAULT_TIMEOUT, RemoteCallError, post_json

EMBEDDING_DIM = 1024
# 라틴 문자·숫자 덩어리 또는 비ASCII 덩어리. 한국어가 이 제품의 주 입력 언어인데
# [a-z0-9]+ 만 쓰면 순수 한국어 질문이 영벡터가 되고, pgvector가 코사인 거리를
# nan으로 돌려주면서 검색 순서가 아무 의미도 없어진다.
#
# \w+ 로는 안 되는 이유: "numpy를"처럼 조사가 영어 단어에 붙어버려서 영어 README와
# 매칭이 오히려 나빠진다. 두 문자 종류를 나눠서 잘라야 "numpy"가 온전히 남는다.
_TOKEN = re.compile(r"[a-z0-9]+|[^\x00-\x7f]+")


class Embedder(Protocol):
    # 스키마의 Vector(1024)와 맞아야 한다. 인덱싱 진입점이 이 값을 먼저 검사한다.
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _stable_bucket(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % EMBEDDING_DIM


class FakeEmbedder:
    """네트워크도 모델 다운로드도 없는 결정론적 임베더.

    토큰을 해시 버킷에 담아 정규화한다. 어휘가 겹칠수록 코사인 유사도가 높아지므로
    검색 배선을 검증하기에는 충분하다. 의미 유사도는 흉내내지 못한다 — 실물은
    OpenAICompatibleEmbedder다. builtin hash()는 프로세스마다 salt가 달라 쓸 수 없다.
    """

    dimension = EMBEDDING_DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * EMBEDDING_DIM
            for token in _TOKEN.findall(text.lower()):
                vector[_stable_bucket(token)] += 1.0
            norm = math.sqrt(sum(x * x for x in vector))
            vectors.append([x / norm for x in vector] if norm else vector)
        return vectors


class OpenAICompatibleEmbedder:
    """OpenAI 호환 `/v1/embeddings` 클라이언트.

    Ollama(`ollama pull bge-m3`)·TEI·vLLM·호스팅 API가 모두 이 규약을 쓴다.
    base_url만 바꾸면 구현을 그대로 두고 백엔드를 갈아끼울 수 있다.

    LLM과 달리 모델은 자유롭지 않다. 차원이 DB 스키마에 박혀 있어서,
    다른 차원을 내는 모델은 여기서 즉시 거부한다 — 수천 건을 계산하고
    API 비용을 쓴 뒤에 pgvector가 insert를 거부하는 것보다 낫다.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        client: httpx2.Client | None = None,
        dimension: int = EMBEDDING_DIM,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = (base_url or settings.embedding_base_url).rstrip("/")
        self._model = model or settings.embedding_model
        self._api_key = settings.embedding_api_key if api_key is None else api_key
        self._client = client or httpx2.Client(timeout=DEFAULT_TIMEOUT)
        self.dimension = dimension
        self._max_retries = max_retries
        self._sleep = sleep

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        body = post_json(
            self._client,
            f"{self._base_url}/embeddings",
            {"model": self._model, "input": texts},
            api_key=self._api_key,
            max_retries=self._max_retries,
            sleep=self._sleep,
        )

        try:
            # 응답 순서가 요청 순서와 같다는 보장이 없다. index로 되돌린다.
            rows = sorted(body["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in rows]
        except (KeyError, TypeError) as exc:
            raise RemoteCallError(f"malformed embeddings response: {exc}") from exc

        if len(vectors) != len(texts):
            raise RemoteCallError(f"asked for {len(texts)} embeddings, got {len(vectors)}")
        for vector in vectors:
            if len(vector) != self.dimension:
                raise RemoteCallError(
                    f"model {self._model!r} returned {len(vector)}-dim vectors "
                    f"but the schema stores {self.dimension}"
                )
        return vectors


def get_embedder(name: str) -> Embedder:
    if name == "fake":
        return FakeEmbedder()
    if name == "openai_compatible":
        return OpenAICompatibleEmbedder()
    raise ValueError(f"unknown embedder: {name!r} (expected 'fake' or 'openai_compatible')")
