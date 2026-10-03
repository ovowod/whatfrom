import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

import httpx2
from pydantic import BaseModel, ValidationError

from whatfrom.core.config import DEFAULT_LLM_API, LLMApi, Settings, settings
from whatfrom.core.contracts import Recommendation, SearchPlan
from whatfrom.core.httpclient import RemoteCallError, post_json


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


T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    def recommend(self, system: str, prompt: str) -> Recommendation: ...

    def plan(self, system: str, prompt: str) -> SearchPlan: ...


class FakeLLMProvider:
    """CI용. 네트워크도 API 키도 쓰지 않는다.

    error를 주면 실패를, recommendation을 주면 그 값을 그대로 돌려준다.
    후보에 없는 image를 담은 Recommendation을 주면 환각 시나리오가 된다.

    plan을 주지 않으면 조건이 하나도 없는 SearchPlan을 돌려준다. 조건이 없으면
    후보를 거르지 않으므로, plan을 신경 쓰지 않는 테스트는 예전과 같은 후보를 받는다.
    plan_error를 주면 검색 조건 추출만 실패한다.
    """

    def __init__(
        self,
        recommendation: Recommendation | None = None,
        error: Exception | None = None,
        plan: SearchPlan | None = None,
        plan_error: Exception | None = None,
    ) -> None:
        self._recommendation = recommendation
        self._error = error
        self._plan = plan
        self._plan_error = plan_error
        self.calls: list[tuple[str, str]] = []
        self.plan_calls: list[tuple[str, str]] = []

    def recommend(self, system: str, prompt: str) -> Recommendation:
        self.calls.append((system, prompt))
        if self._error is not None:
            raise self._error
        if self._recommendation is None:
            raise RemoteCallError("FakeLLMProvider has no recommendation configured")
        return self._recommendation

    def plan(self, system: str, prompt: str) -> SearchPlan:
        self.plan_calls.append((system, prompt))
        if self._plan_error is not None:
            raise self._plan_error
        return self._plan if self._plan is not None else SearchPlan()


@dataclass(frozen=True)
class LLMCall:
    """LLM 호출 하나의 기록. 실패한 호출도 하나 남긴다.

    stage는 "plan"(조건 추출 단계) 또는 "recommend"(추천 단계)다. token 수는 응답의
    usage에서 읽고, 응답이 없거나 usage가 없으면 None이다.
    """

    stage: str
    attempts: int
    ok: bool
    error: str | None
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None


def _validated[M: BaseModel](text: str, model: type[M]) -> M:
    """응답 text를 schema 모델로 검증한다. 서버의 schema 강제를 믿지 않는다."""
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        raise RemoteCallError(f"response did not match {model.__name__} schema: {exc}") from exc


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
        extra_body: dict | None = None,
        on_call: Callable[[LLMCall], None] | None = None,
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
        self._extra_body = extra_body or {}
        # 평가가 호출 기록을 모을 때만 넘긴다. API는 provider 하나를 여러 요청이 함께
        # 쓰므로 마지막 호출 정보를 provider에 두지 않고 callback으로 내보낸다.
        self._on_call = on_call

    def recommend(self, system: str, prompt: str) -> Recommendation:
        return self._complete(system, prompt, Recommendation, "recommendation", "recommend")

    def plan(self, system: str, prompt: str) -> SearchPlan:
        return self._complete(system, prompt, SearchPlan, "search_plan", "plan")

    def _complete(self, system: str, prompt: str, model: type[T], name: str, stage: str) -> T:
        """JSON 스키마를 강제해 부르고, 응답을 model로 다시 검증한다.

        성공하든 실패하든 호출 기록을 하나 남긴다.
        """
        attempts = 0
        usage: object = None

        def count(attempt: int) -> None:
            nonlocal attempts
            attempts = attempt

        try:
            body = self._post(system, prompt, model, name, count)
            # 검증 전에 읽는다. 검증에서 실패해도 응답은 왔으니 비용이 들었다.
            usage = body.get("usage") if isinstance(body, dict) else None
            result = self._parse(body, model)
        except RemoteCallError as exc:
            self._record(stage, attempts, usage, str(exc))
            raise
        self._record(stage, attempts, usage, None)
        return result

    def _record(self, stage: str, attempts: int, usage: object, error: str | None) -> None:
        if self._on_call is None:
            return
        self._on_call(LLMCall(stage, attempts, error is None, error, *self._token_counts(usage)))

    @staticmethod
    def _token_counts(usage: object) -> tuple[int | None, int | None, int | None]:
        """OpenAI 형식의 usage에서 입력·출력·reasoning token 수를 읽는다. 없는 값은 None."""
        if not isinstance(usage, dict):
            return None, None, None
        details = usage.get("completion_tokens_details")
        reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
        return usage.get("prompt_tokens"), usage.get("completion_tokens"), reasoning

    def _post(
        self,
        system: str,
        prompt: str,
        model: type[BaseModel],
        name: str,
        on_attempt: Callable[[int], None],
    ) -> dict:
        return post_json(
            self._client,
            f"{self._base_url}/chat/completions",
            {
                # reasoning 설정처럼 공급자마다 이름이 다른 parameter. 설정 검증이
                # model·messages·response_format을 막으므로 아래 값을 덮어쓰지 않는다.
                **self._extra_body,
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": name,
                        "strict": True,
                        "schema": strict_json_schema(model),
                    },
                },
            },
            api_key=self._api_key,
            max_retries=self._max_retries,
            sleep=self._sleep,
            on_attempt=on_attempt,
        )

    @staticmethod
    def _parse(body: dict, model: type[T]) -> T:
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RemoteCallError(f"malformed completion response: {exc}") from exc

        # json_schema 강제 수준은 서버마다 다르다 — 무시하는 서버도 있다.
        # 그래서 스키마 준수를 서버에 맡기지 않고 항상 여기서 다시 검증한다.
        return _validated(content, model)


ANTHROPIC_VERSION = "2023-06-01"
# native Messages API에서 필수다. thinking token을 포함한다. M1-a 기준선의 추천 단계
# 출력은 reasoning 포함 최대 약 3,600 token이었다. 바꿀 필요가 생기면 그때 설정으로 연다.
ANTHROPIC_MAX_TOKENS = 16_000


class AnthropicProvider(OpenAICompatibleProvider):
    """Anthropic native Messages API 클라이언트 (spec F15).

    Anthropic의 OpenAI 호환 layer는 response_format과 reasoning_effort를 무시한다. 그러면
    schema가 강제되지 않고 effort도 정할 수 없어 native API로 부른다. 호출 기록, timeout,
    재시도는 OpenAI 호환 provider와 같다.
    """

    def _post(
        self,
        system: str,
        prompt: str,
        model: type[BaseModel],
        name: str,
        on_attempt: Callable[[int], None],
    ) -> dict:
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        # 빈 key면 인증 header를 보내지 않는다(F14의 빈 key 규칙).
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return post_json(
            self._client,
            f"{self._base_url}/messages",
            {
                **self._extra_body,
                "model": self._model,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": ANTHROPIC_MAX_TOKENS,
                # effort와 schema가 같은 객체에 들어간다. 덮어쓰면 EXTRA_BODY의 effort가 지워진다.
                "output_config": {
                    **self._extra_body.get("output_config", {}),
                    "format": {"type": "json_schema", "schema": strict_json_schema(model)},
                },
            },
            max_retries=self._max_retries,
            sleep=self._sleep,
            on_attempt=on_attempt,
            headers=headers,
        )

    @staticmethod
    def _parse(body: dict, model: type[T]) -> T:
        """thinking block은 건너뛰고 text block만 검증한다.

        잘리거나 거부된 답은 schema 검증 실패로 뭉뚱그리지 않고 그 이유로 실패한다.
        """
        stop_reason = body.get("stop_reason") if isinstance(body, dict) else None
        if stop_reason == "max_tokens":
            raise RemoteCallError(f"response truncated at max_tokens={ANTHROPIC_MAX_TOKENS}")
        if stop_reason == "refusal":
            raise RemoteCallError("model refused to answer")
        try:
            texts = [block["text"] for block in body["content"] if block.get("type") == "text"]
        except (KeyError, TypeError, AttributeError) as exc:
            raise RemoteCallError(f"malformed messages response: {exc}") from exc
        if not texts:
            raise RemoteCallError("malformed messages response: no text block")
        if not all(isinstance(text, str) for text in texts):
            raise RemoteCallError("malformed messages response: text block is not a string")
        return _validated("".join(texts), model)

    @staticmethod
    def _token_counts(usage: object) -> tuple[int | None, int | None, int | None]:
        """output_tokens는 thinking token을 포함한다(과금 기준)."""
        if not isinstance(usage, dict):
            return None, None, None
        details = usage.get("output_tokens_details")
        thinking = details.get("thinking_tokens") if isinstance(details, dict) else None
        return usage.get("input_tokens"), usage.get("output_tokens"), thinking


class StagedProvider:
    """조건 추출 단계와 추천 단계를 각자의 provider로 보낸다."""

    def __init__(self, plan: LLMProvider, recommend: LLMProvider) -> None:
        self._plan = plan
        self._recommend = recommend

    def recommend(self, system: str, prompt: str) -> Recommendation:
        return self._recommend.recommend(system, prompt)

    def plan(self, system: str, prompt: str) -> SearchPlan:
        return self._plan.plan(system, prompt)


@dataclass(frozen=True)
class StageLLM:
    """한 단계가 실제로 쓰는 LLM 설정. 단계별 값이 없으면 공통 설정에서 채운 결과다."""

    base_url: str
    model: str
    api_key: str
    extra_body: dict | None
    api: LLMApi


def stage_llm(config: Settings, stage: str) -> StageLLM:
    """stage는 "plan"(조건 추출 단계) 또는 "recommend"(추천 단계)다.

    None(설정하지 않음)만 공통 설정으로 채운다. 빈 API 키는 "인증 없음"이라 그대로 둔다.
    """

    def or_common(name: str):
        value = getattr(config, f"{stage}_llm_{name}")
        return getattr(config, f"llm_{name}") if value is None else value

    return StageLLM(
        base_url=or_common("base_url"),
        model=or_common("model"),
        api_key=or_common("api_key"),
        extra_body=getattr(config, f"{stage}_llm_extra_body"),
        api=getattr(config, f"{stage}_llm_api") or DEFAULT_LLM_API,
    )


PROVIDERS: dict[LLMApi, type[OpenAICompatibleProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
}


def _stage_provider(
    config: Settings,
    stage: str,
    client: httpx2.Client | None,
    on_call: Callable[[LLMCall], None] | None,
) -> LLMProvider:
    resolved = stage_llm(config, stage)
    return PROVIDERS[resolved.api](
        base_url=resolved.base_url,
        model=resolved.model,
        api_key=resolved.api_key,
        client=client,
        extra_body=resolved.extra_body,
        on_call=on_call,
    )


def get_provider(
    name: str,
    config: Settings | None = None,
    client: httpx2.Client | None = None,
    on_call: Callable[[LLMCall], None] | None = None,
) -> LLMProvider:
    """config는 테스트가 설정을, client는 가짜 transport를 넣을 수 있게 둔다.

    on_call은 평가가 호출 기록을 모을 때만 넘긴다. fake provider는 기록을 남기지 않는다.
    """
    config = config or settings
    if name == "fake":
        return FakeLLMProvider()
    if name == "openai_compatible":
        return StagedProvider(
            plan=_stage_provider(config, "plan", client, on_call),
            recommend=_stage_provider(config, "recommend", client, on_call),
        )
    raise ValueError(f"unknown provider: {name!r} (expected 'fake' or 'openai_compatible')")
