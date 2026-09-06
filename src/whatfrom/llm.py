import time
from collections.abc import Callable
from typing import Protocol

import httpx2
from pydantic import BaseModel, ValidationError

from whatfrom.config import settings
from whatfrom.contracts import Recommendation
from whatfrom.httpclient import RemoteCallError, post_json


def strict_json_schema(model: type[BaseModel]) -> dict:
    """모든 속성을 required에 넣은 JSON 스키마.

    OpenAI의 strict 모드는 properties에 있는 필드가 전부 required에도 있어야
    하고, 아니면 요청을 400으로 거부한다. Pydantic은 기본값이 있는 필드를
    required에서 빼므로 alternatives가 누락된다.

    Kimi와 vLLM은 이걸 통과시켜서 지금까지 드러나지 않았다. 보정하지 않으면
    base_url만 바꿔 다른 백엔드로 옮길 수 있다는 말이 OpenAI에서만 거짓이 된다.

    파이썬 쪽에서는 기본값을 그대로 둔다. 서버에 요구하는 것과 우리가 받아들이는
    것을 굳이 같게 맞출 이유가 없고, 응답은 어차피 Pydantic이 다시 검증한다.
    """
    schema = model.model_json_schema()
    schema["required"] = list(schema["properties"])
    return schema


class LLMProvider(Protocol):
    def recommend(self, system: str, prompt: str) -> Recommendation: ...


class FakeLLMProvider:
    """CI용. 네트워크도 API 키도 쓰지 않는다.

    error를 주면 실패를, recommendation을 주면 그 값을 그대로 돌려준다.
    후보에 없는 image를 담은 Recommendation을 주면 환각 시나리오가 된다.
    """

    def __init__(
        self, recommendation: Recommendation | None = None, error: Exception | None = None
    ) -> None:
        self._recommendation = recommendation
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def recommend(self, system: str, prompt: str) -> Recommendation:
        self.calls.append((system, prompt))
        if self._error is not None:
            raise self._error
        if self._recommendation is None:
            raise RemoteCallError("FakeLLMProvider has no recommendation configured")
        return self._recommendation


class OpenAICompatibleProvider:
    """OpenAI 호환 `/v1/chat/completions` 클라이언트.

    vLLM·Ollama·Kimi·대부분의 호스팅 API가 이 규약을 쓴다. base_url만 바꾸면
    구현을 그대로 두고 백엔드를 갈아끼울 수 있다 (스펙 §12).

    temperature는 보내지 않는다. 공급자마다 허용값이 달라서 — kimi-k3는 1만
    받고 0을 400으로 거부한다 — 하나를 박아두면 base_url만 바꾸면 된다는
    전제가 깨진다. 결정성이 필요해지면 그때 설정으로 노출한다.

    타임아웃·재시도 정책은 httpclient.post_json이 갖는다 — 임베딩과 같은 정책이라
    같은 루프를 두 번 적지 않는다.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        client: httpx2.Client | None = None,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = (base_url or settings.llm_base_url).rstrip("/")
        self._model = model or settings.llm_model
        self._api_key = settings.llm_api_key if api_key is None else api_key
        self._client = client or httpx2.Client(
            timeout=httpx2.Timeout(
                settings.llm_timeout_seconds,
                connect=3.0,
                read=settings.llm_timeout_seconds,
                write=10.0,
            )
        )
        self._max_retries = max_retries
        self._sleep = sleep

    def recommend(self, system: str, prompt: str) -> Recommendation:
        body = post_json(
            self._client,
            f"{self._base_url}/chat/completions",
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "recommendation",
                        "strict": True,
                        "schema": strict_json_schema(Recommendation),
                    },
                },
            },
            api_key=self._api_key,
            max_retries=self._max_retries,
            sleep=self._sleep,
        )

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RemoteCallError(f"malformed completion response: {exc}") from exc

        # json_schema 강제 수준은 서버마다 다르다 — 무시하는 서버도 있다.
        # 그래서 스키마 준수를 서버에 맡기지 않고 항상 여기서 다시 검증한다.
        try:
            return Recommendation.model_validate_json(content)
        except ValidationError as exc:
            raise RemoteCallError(f"response did not match Recommendation schema: {exc}") from exc


def get_provider(name: str) -> LLMProvider:
    if name == "fake":
        return FakeLLMProvider()
    if name == "openai_compatible":
        return OpenAICompatibleProvider()
    raise ValueError(f"unknown provider: {name!r} (expected 'fake' or 'openai_compatible')")
