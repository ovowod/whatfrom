# M1 LLM 후보 조사 (2026-10-03)

M1-a(조건 추출 단계)와 M1-b(추천 단계)에서 비교할 model의 API 사실을 공급자 공식 문서로 확인한다.
모든 출처의 접근일은 2026-10-03이다. 공식 문서에서 확인하지 못한 것은 "확인하지 못한 것"에 따로 적었다.

우리 코드가 보내는 요청(`src/whatfrom/recommend/llm.py`, `src/whatfrom/core/httpclient.py`):

- `POST {base_url}/chat/completions`, header `Authorization: Bearer <key>` (API key가 빈 값이면 생략).
- 본문: `EXTRA_BODY`를 먼저 펼치고 `model`, `messages`(system + user), `response_format`을 덮어쓴다.
  `response_format`은 `{"type": "json_schema", "json_schema": {"name", "strict": true, "schema"}}`다.
- schema는 Pydantic이 만든 것에 모든 속성을 `required`로 넣은 것이다. `SearchPlan`에는 `anyOf: [{type: string}, {type: null}]`,
  `"default": null`, `title`, `description`, `additionalProperties: false`가 들어간다.
- `temperature`, `max_tokens`, `tools`, `stream`은 보내지 않는다.
- 응답에서 `choices[0].message.content`만 읽어 Pydantic으로 다시 검증한다.
- token 기록: `usage.prompt_tokens`, `usage.completion_tokens`, `usage.completion_tokens_details.reasoning_tokens`. 없으면 None이다.
- read timeout 120초. 429·500·502·503·504와 연결 실패는 최대 2회 재시도한다.

## 요약

| model | ID 확인 | base URL | 최소 reasoning `EXTRA_BODY` | `low` `EXTRA_BODY` | structured output | reasoning token 보고 | 가격 (1M token당 input / cached / output, USD) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| kimi-k3 | `kimi-k3` 확인 | `https://api.moonshot.ai/v1` | `{"reasoning_effort": "low"}` (끌 수 없음, 기본값은 `max`) | 최소와 같음 | `json_schema` + `strict: true` 지원 | 문서 예시에는 `reasoning_tokens` field가 없다. 실측에서는 보고했다(아래 정정) | $3.00 / $0.30 / $15.00 |
| gpt-6-luna | `gpt-6-luna` 확인, GA | `https://api.openai.com/v1` | `{"reasoning_effort": "none"}` (기본값은 `medium`) | `{"reasoning_effort": "low"}` | Structured Outputs 지원 | `completion_tokens_details.reasoning_tokens` | $0.10 / $0.01 / $0.50 |
| grok-4.3 | `grok-4.3` 확인 (alias `grok-4.3-latest`) | `https://api.x.ai/v1` | `{"reasoning_effort": "none"}` (기본값은 `low`) | `{"reasoning_effort": "low"}` 또는 생략 | `json_schema` 지원 | `completion_tokens_details.reasoning_tokens`. 단, `completion_tokens`에 reasoning이 빠져 있다 | $1.25 / $0.20 / $2.50 |
| gemini-3.5-flash-lite | `gemini-3.5-flash-lite` 확인, Stable | `https://generativelanguage.googleapis.com/v1beta/openai` | 생략 (기본값이 `minimal`). 명시하려면 `{"extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}}` | `{"extra_body": {"google": {"thinking_config": {"thinking_level": "low"}}}}` | OpenAI 호환 layer의 strict `json_schema` 처리 범위는 문서에 없음 | OpenAI 호환 응답의 field는 문서에 없음 | $0.30 / $0.03 / $2.50 (output에 thinking 포함) |
| gpt-6.1-sol (M1-b만) | `gpt-6.1-sol` 확인 | `https://api.openai.com/v1` | `{"reasoning_effort": "low"}` (`none`·`minimal` 미지원, 기본값은 `medium`) | 최소와 같음 | Structured Outputs 지원 | `completion_tokens_details.reasoning_tokens` | $2.00 / $0.10 / $10.00 |

핵심:

- **다섯 ID 모두 공식 문서에 있다.** 다르게 표기된 ID는 없다.
- **kimi-k3 기준선은 지금 `max`로 돌고 있다.** reasoning을 끌 수 없다는 roadmap 서술은 맞다. 하지만 `reasoning_effort`를 보내지 않으면 기본값 `max`가 적용되고, `low`도 있다. 지금 `.env.example`과 기본 설정은 kimi-k3에 `reasoning_effort`를 보내지 않는다.
- **gpt-6.1-sol은 `none`을 받지 않는다.** M1-b의 "최소 수준과 `low`" 두 측정이 Sol에서는 같은 설정이다.
- **gemini-3.5-flash-lite도 reasoning을 끌 수 없다.** 기본값 `minimal`이 가장 낮다. 또 OpenAI 호환 문서의 `reasoning_effort` 대응표에 3.5 Flash-Lite가 없어, `thinking_level`을 `extra_body.google`로 넘기는 쪽이 문서로 확인되는 방법이다.
- **grok-4.3의 `completion_tokens`는 reasoning token을 포함하지 않는다.** OpenAI는 포함한다. 같은 `output_tokens` 열을 공급자 사이에 그대로 비교하면 안 된다.
- **xAI는 Chat Completions를 legacy endpoint로 둔다.** 지금은 동작하지만 새 기능은 Responses API에 먼저 온다.

## kimi-k3 (Moonshot, 기준선)

**ID와 상태.** `kimi-k3`. Moonshot 국제 문서의 K3 quickstart가 model ID와 base URL `https://api.moonshot.ai/v1`을 보여 준다 ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)). 중국 사이트 문서는 `https://api.moonshot.cn/v1`을 쓴다 ([platform.kimi.com chat API](https://platform.kimi.com/docs/api/chat)). 우리 기본값(`api.moonshot.ai`)은 국제 endpoint와 맞다.

**인증.** `Authorization: Bearer $MOONSHOT_API_KEY` ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)).

**reasoning.** quickstart 원문: "K3 always has thinking mode enabled and supports configuring its reasoning effort with the top-level `reasoning_effort` request field. Reasoning effort supports `low`, `high`, and `max` (default `max`)." FAQ: "How do I turn off Kimi K3's chain-of-thought? You can't — K3 always thinks. If the reasoning takes too long, set `reasoning_effort` to `low`" ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)). thinking model 문서도 K3에는 `thinking` parameter를 보내지 말라고 한다. `thinking: {type: "disabled"}`는 K2.6에만 해당한다 ([Thinking models](https://platform.kimi.com/docs/guide/use-thinking-models)).

- 최소 reasoning: `{"reasoning_effort": "low"}`. `low`와 같다.
- **roadmap 서술 확인:** "reasoning을 끌 수 없다"는 맞다. 다만 지금 기준선은 effort를 보내지 않으므로 `max`로 측정되고 있다.

**고정 parameter.** "`temperature=1.0`, `top_p=0.95`, `n=1`, `presence_penalty=0`, and `frequency_penalty=0` are fixed; omit them from requests." ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)). 우리 코드는 이 값들을 보내지 않는다. `max_completion_tokens`의 기본값은 131072다 (같은 문서).

**structured output.** "Use `json_schema` with `strict: true` to constrain the final `message.content`. Parse only that field, not `reasoning_content`." ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)). chat API reference는 `response_format`으로 `text`, `json_object`, `json_schema`(`strict` 기본값 true)를 받는다 ([platform.kimi.com chat API](https://platform.kimi.com/docs/api/chat)). 우리 요청 방식과 그대로 맞고, 이미 운영에서 쓰고 있다.

**usage.** chat API reference의 응답 예시 `usage`는 `prompt_tokens`, `completion_tokens`, `total_tokens`, `cached_tokens`, `prompt_tokens_details.{cached_tokens, cache_write_tokens}`뿐이다. `completion_tokens_details.reasoning_tokens`는 없다 ([Kimi chat API](https://platform.kimi.ai/docs/api/chat)). 문서대로라면 우리 기록의 `reasoning_tokens`는 kimi-k3에서 항상 None이 된다.

> **실측 정정 (2026-10-03, M1-a 사전 확인):** 실제 응답은 `completion_tokens_details.reasoning_tokens`를 보고했고, `completion_tokens`가 reasoning token을 포함했다(prompt 927 + completion 321 = total 1248, reasoning 259). 공식 문서의 응답 예시와 다르다. 자세한 내용은 [M1-a 실험 기록](../../eval/experiments/2026-10-03-plan-llm-comparison/README.md)에 있다. reasoning은 `message.reasoning_content`로 돌아오고, thinking 문서는 "`reasoning_content` counts toward token consumption"이라고 한다 ([Thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models)).

**가격.** 1M token당 input $3.00, cached input $0.30, output $15.00, cache write $3.00(TTL 5분)·$6.00(TTL 1시간) ([Kimi pricing](https://platform.kimi.ai/docs/pricing/chat)). reasoning token을 output 단가로 받는다는 명시 문장은 pricing 문서에서 찾지 못했다.

**rate limit.** 누적 충전액 기준이다. Tier 0($1): 동시 1, 3 RPM. Tier 1($10): 동시 15, 100 RPM, 2,000,000 TPM. Tier 2($20): 동시 40, 100 RPM ([Kimi limits](https://platform.kimi.ai/docs/pricing/limits)). Tier 0이면 40문항 평가가 3 RPM에 막힌다. quickstart는 8월에 tier 규칙을 바꾼다고 공지했으므로 위 값은 접근일 기준이다.

**지연.** 공식 문서에 지연·처리량 수치는 없다.

## gpt-6-luna (OpenAI)

**ID와 상태.** `gpt-6-luna`, snapshot도 `gpt-6-luna` 하나다. "Use `gpt-6-luna` in your API requests." Chat Completions(`v1/chat/completions`)를 지원한다 ([GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)). knowledge cutoff는 2026-05-18이다. 문서에 preview나 deprecated 표시는 없다.

**endpoint와 인증.** `https://api.openai.com/v1/chat/completions`, `Authorization: Bearer $OPENAI_API_KEY` ([Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)).

**reasoning.** model page 원문: "`reasoning.effort` supports `none`, `low`, `medium` (default), `high`, `xhigh`, and `max`." `minimal`은 목록에 없다. "Chat Completions supports function calling only with `reasoning_effort` set to `none`." ([GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)). Chat Completions에서 parameter 이름은 top-level `reasoning_effort`다 ([Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)). reasoning guide는 `none`을 "Latency-critical tasks that do not benefit from any reasoning or multi-chained tool calls"용이라고 설명한다 ([Reasoning models](https://developers.openai.com/api/docs/guides/reasoning?api-mode=chat)).

- 최소 reasoning: `{"reasoning_effort": "none"}`
- `low`: `{"reasoning_effort": "low"}`
- 아무것도 보내지 않으면 `medium`이다. 기본값으로 측정하면 안 된다.
- 우리 코드는 function calling을 쓰지 않으므로 `none` 제약과 무관하다.

**structured output.** model page의 feature 목록에 `structured_outputs`가 있다 ([GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)). strict 모드는 모든 field가 `required`, `additionalProperties: false`, root는 `anyOf`가 아닌 object여야 한다. `anyOf`는 지원하고 nullable은 `null`을 포함한 `anyOf`나 type 배열로 쓴다. "If you turn on Structured Outputs by supplying `strict: true` and call the API with an unsupported JSON Schema, you will receive an error." ([Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). `default` keyword는 지원 목록에도 미지원 목록에도 없다. 또 "the first request you make with any schema will have additional latency as our API processes the schema" (같은 문서).

**usage.** `usage.completion_tokens_details.reasoning_tokens`: "Tokens generated by the model for reasoning." `rejected_prediction_tokens` 설명에 "like reasoning tokens, these tokens are still counted in the total completion tokens for purposes of billing" ([Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)). 즉 `completion_tokens`가 reasoning token을 포함한다. reasoning guide: "billed as output tokens" ([Reasoning models](https://developers.openai.com/api/docs/guides/reasoning?api-mode=chat)). 우리 `_token_counts`가 그대로 읽는다.

**가격.** 1M token당 input $0.10, cached input $0.01, cache write $0.125, output $0.50. 272K input token을 넘는 prompt는 더 비싸다. Fast mode는 2배다 ([GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)).

**rate limit.** Free tier "Not supported". Tier 1: 500 RPM, 500,000 TPM ([GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)). 무료 계정으로는 호출할 수 없다.

**지연.** model page의 표시는 "Speed: Fast"뿐이다. 수치는 없다 ([GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna)).

## grok-4.3 (xAI)

**ID와 상태.** model name `grok-4.3`, alias `grok-4.3-latest`. region은 us-east-1, eu-west-1, us-west-2다 ([Grok 4.3 model page](https://docs.x.ai/developers/models/grok-4.3)). deprecated 표시는 없다. 오히려 2026-05-15 퇴역한 이전 slug들이 grok-4.3으로 redirect된다: "Requests to any reasoning model in the list above will be served by `grok-4.3` with `low` reasoning effort. Requests to any non-reasoning model ... with `none` reasoning effort." ([May 15 retirement](https://docs.x.ai/developers/migration/may-15-retirement)). 지금 xAI의 최신 model은 grok-4.7이고, grok-4.3은 가장 싼 축이다 ([xAI pricing](https://docs.x.ai/developers/pricing)).

**endpoint와 인증.** `https://api.x.ai/v1`, OpenAI SDK에 `base_url`만 바꿔 쓴다. `Authorization: Bearer $XAI_API_KEY` ([Reasoning guide](https://docs.x.ai/developers/model-capabilities/text/reasoning), [REST chat completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions)). 다만 "Chat Completions is offered as a legacy endpoint. New features will come to the Responses API first." ([Chat Completions (Legacy)](https://docs.x.ai/developers/model-capabilities/legacy/chat-completions)).

**reasoning.** model page: "Reasoning efforts (supported): `none`, `low`, `medium`, `high`, `xhigh`. Reasoning efforts (default): `low`" ([Grok 4.3 model page](https://docs.x.ai/developers/models/grok-4.3)). REST reference의 `reasoning_effort` 설명: "The supported values and the default depend on the model." ([REST chat completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions)).

- 최소 reasoning: `{"reasoning_effort": "none"}`. reasoning을 끌 수 있다.
- `low`: `{"reasoning_effort": "low"}`. 기본값이므로 생략해도 같다. 명시하는 쪽이 기록에 남는다.
- reasoning guide의 "Reasoning cannot be disabled", 기본값 `high` 서술은 grok-4.5·4.6·4.7에 대한 것이다. grok-4.3에는 model page를 따른다 ([Reasoning guide](https://docs.x.ai/developers/model-capabilities/text/reasoning)).
- 같은 guide: "`presencePenalty`, `frequencyPenalty`, and `stop` cannot be used with reasoning models. Requests that include them return an error." 우리 코드는 보내지 않는다.

**structured output.** model page에 "Structured outputs: Yes" ([Grok 4.3 model page](https://docs.x.ai/developers/models/grok-4.3)). `response_format.type`에 `json_schema`, `json_object`, `text`를 받는다. 지원 type에 `null`, `anyOf`, `$ref/$defs`가 있고, nullable은 "a type array ... or an `anyOf` variant that includes `null`"로 쓴다. 400을 내는 schema는 빈 `enum`/`anyOf`, `true`/`false` schema, `maxContains`/`minContains`, 배열형 `items`뿐이다 ([Structured Outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)). 우리 schema는 거부 목록에 해당하지 않는다. `strict` flag의 의미는 문서에 따로 없다.

**usage.** `usage.completion_tokens_details.reasoning_tokens`가 required field로 있다. `completion_tokens`의 설명은 "Total completion token used."다. 그런데 문서 예시가 `"prompt_tokens": 32, "completion_tokens": 9, "total_tokens": 135, ... "reasoning_tokens": 94`다 ([REST chat completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions)). 32 + 9 + 94 = 135이므로 **`completion_tokens`는 reasoning token을 빼고 센다.** OpenAI와 다르다. `max_completion_tokens`도 "only applies to visible output tokens (i.e. does not apply to tokens used for reasoning ...)"이다 (같은 문서). 과금: "the reasoning tokens are billed as part of your total consumption" ([Reasoning guide](https://docs.x.ai/developers/model-capabilities/text/reasoning)). 어느 단가로 받는지는 명시되지 않았다.

**가격.** 200K prompt token 미만에서 1M token당 input $1.25, cached input $0.20, output $2.50. 200K 이상이면 요청 전체가 두 배 단가다. Batch는 20% 할인이다 ([Grok 4.3 model page](https://docs.x.ai/developers/models/grok-4.3), [xAI pricing](https://docs.x.ai/developers/pricing)).

**rate limit.** tier는 2026-01-01 이후 누적 지출로 정한다. Tier 0은 $0(기본)이다 ([Rate limits](https://docs.x.ai/developers/rate-limits)). grok-4.3은 Tier 0에서 37 RPS, 10M TPM이다 (같은 문서, [Grok 4.3 model page](https://docs.x.ai/developers/models/grok-4.3)). 40문항 평가에는 충분하다.

**지연.** model 설명은 "Fast, reliable model with strong tool calling and instruction following capabilities."뿐이다. 수치는 없다 ([Grok 4.3 model page](https://docs.x.ai/developers/models/grok-4.3)).

## gemini-3.5-flash-lite (Google)

**ID와 상태.** Model code `gemini-3.5-flash-lite`, "Stable: gemini-3.5-flash-lite", latest update 2026년 7월. Structured outputs와 Thinking 모두 "Supported"다 ([Gemini 3.5 Flash-Lite model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)). models 목록에도 stable로 있다 ([Gemini models](https://ai.google.dev/gemini-api/docs/models)).

**endpoint와 인증.** base URL `https://generativelanguage.googleapis.com/v1beta/openai/`, `Authorization: Bearer GEMINI_API_KEY`. REST 예시 경로는 `.../v1beta/openai/chat/completions`다 ([OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)). 우리 코드는 끝의 `/`를 지우고 `/chat/completions`를 붙이므로 같은 URL이 된다. 문서 원문: "Support for the OpenAI libraries is still in beta while we extend feature support." (같은 문서).

**reasoning.** thinking 문서의 model별 표: "gemini-3.5-flash-lite | On (minimal) | minimal, low, medium, high" ([Thinking](https://ai.google.dev/gemini-api/docs/thinking)). 기본값이 이미 `minimal`이다. Gemini 3.5 문서는 `minimal`에 대해 "Note, minimal does not guarantee that thinking is off, the model may reason very minimally for complex tasks."라고 한다 ([What's new in Gemini 3.5](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5)). OpenAI 호환 문서: "Reasoning cannot be turned off for Gemini 2.5 Pro or 3 models." ([OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)).

OpenAI 호환 layer에서 reasoning을 정하는 방법은 두 가지다 ([OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)).

1. top-level `reasoning_effort`. 대응표에는 Gemini 3.1 Pro, 3.1 Flash-Lite, 3 Flash, 2.5만 있다. **3.5 Flash-Lite 열은 없다.** 3.1 Flash-Lite와 3 Flash에서는 `minimal`→`minimal`, `low`→`low`다.
2. `extra_body.google.thinking_config.thinking_level`. REST 예시에서 요청 JSON에 `"extra_body"` key가 **글자 그대로** 들어간다: `"extra_body": { "google": { "thinking_config": { "thinking_level": "low", "include_thoughts": true } } }`. 문서는 "`reasoning_effort` and `thinking_level` / `thinking_budget` overlap functionality, so they can't be used at the same time."라고 한다.

우리 `EXTRA_BODY`는 요청 본문 top-level에 펼쳐지므로 2번은 이렇게 넣는다.

- 최소 reasoning: 생략(기본값 `minimal`), 또는 `{"extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}}`
- `low`: `{"extra_body": {"google": {"thinking_config": {"thinking_level": "low"}}}}`
- `{"reasoning_effort": "minimal"}` / `{"reasoning_effort": "low"}`도 동작할 가능성이 높지만, 3.5 Flash-Lite에 대한 대응은 문서로 확인하지 못했다.

**structured output.** OpenAI 호환 문서는 OpenAI SDK의 `client.beta.chat.completions.parse(..., response_format=CalendarEvent)` 예시만 보여 준다 ([OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)). 그 SDK 경로가 보내는 요청은 우리 것과 같은 `json_schema` + `strict: true`다. native structured output 문서가 밝힌 schema 범위는 `string`, `number`, `integer`, `boolean`, `object`, `array`, `null`(type 배열로), `title`, `description`, `properties`, `required`, `additionalProperties`, `enum`, `format`, `minimum`/`maximum`, `items`, `prefixItems`, `minItems`/`maxItems`다. `anyOf`는 예시에 쓰이지만 목록에는 없다. "Not all JSON Schema features are supported." ([Structured output](https://ai.google.dev/gemini-api/docs/structured-output)). `default` keyword와 호환 layer가 `strict`를 어떻게 다루는지는 문서에 없다.

**usage.** OpenAI 호환 문서는 streaming에서 `stream_options={'include_usage': True}`로 usage를 받는 예시만 있다. `completion_tokens_details.reasoning_tokens`를 채우는지, `completion_tokens`가 thinking token을 포함하는지는 문서에 없다 ([OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)).

**가격.** Standard paid tier, 1M token당 input $0.30, "Output price (including thinking tokens)" $2.50, context caching $0.03(+ 저장 $1.00/1M token/시간). Free tier는 "Free of charge"지만 "Used to improve our products: Yes"다 ([Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)).

**rate limit.** 문서는 tier 자격(Tier 1: billing 계정 연결)과 10분당 지출 상한(Tier 1 $10)만 밝힌다. model별 RPM·TPM은 "can be viewed in Google AI Studio"라서 문서에 없다 ([Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)). 지출 상한을 넘으면 429 `RESOURCE_EXHAUSTED`다 (같은 문서).

**지연.** "a low-latency, cost-effective multimodal model"이라는 서술뿐이다. 수치는 없다 ([Gemini 3.5 Flash-Lite model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)).

## gpt-6.1-sol (OpenAI, M1-b만)

**ID와 상태.** `gpt-6.1-sol`, snapshot도 `gpt-6.1-sol`. knowledge cutoff 2026-04-30. "Chat Completions is supported without tool calling." ([GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)). endpoint와 인증은 gpt-6-luna와 같다.

**reasoning.** "`reasoning.effort` supports `low`, `medium` (default), `high`, `xhigh`, and `max`. The `none` and `minimal` reasoning efforts are not supported." ([GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)). reasoning guide는 같은 내용과 함께, 미지원 effort를 보내면 400이 나는 예로 GPT-6 Astra의 `none`을 든다 ([Reasoning models](https://developers.openai.com/api/docs/guides/reasoning?api-mode=chat)). Sol에 `none`을 보냈을 때 400인지는 명시되지 않았지만, 400으로 보는 것이 안전하다.

- 최소 reasoning = `low`: `{"reasoning_effort": "low"}`
- M1-b의 "최소 수준과 `low` 두 가지" 측정은 Sol에서 한 번이면 된다.

**structured output, usage.** feature에 structured outputs가 있고, usage 형식과 reasoning 과금은 gpt-6-luna와 같다 ([GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol), [Create chat completion](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)).

**가격.** 1M token당 input $2.00, cached input $0.10, cache write $2.50, output $10.00 ([GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)).

**rate limit.** Free "Not supported". Tier 1: 500 RPM, 500,000 TPM (같은 문서).

**지연.** model page 표시는 "Reasoning: Highest, Speed: Fast"뿐이다 (같은 문서).

## 우리 코드와의 호환성 이슈

1. **kimi-k3 기준선의 reasoning 수준.** `reasoning_effort`를 보내지 않으면 `max`다. 지금 기준선은 `max`로 측정한 값이다. M1의 "각 model의 reasoning 최소 수준" 원칙을 kimi-k3에도 적용하려면 `{"reasoning_effort": "low"}`로도 재야 한다. roadmap은 kimi-k3를 최소 수준·`low` 측정에서 뺐는데, 이 전제를 다시 볼 필요가 있다.
2. **reasoning token 기록의 공급자별 차이.**
   - kimi-k3: 문서 예시에는 `completion_tokens_details`가 없지만, 실측에서는 `reasoning_tokens`를 보고했다(위 정정).
   - grok-4.3: `completion_tokens`가 reasoning을 빼고 센다. 비용을 `output_tokens × 단가`로 계산하면 reasoning만큼 적게 나온다. `completion_tokens + reasoning_tokens`로 계산해야 한다.
   - OpenAI: `completion_tokens`가 reasoning을 포함한다. 위 식을 OpenAI에 쓰면 두 번 센다.
   - gemini: 문서로 확인하지 못했다. 첫 호출에서 `usage`를 직접 봐야 한다.
   - 결론: 비용 계산을 공급자별로 다르게 해야 하고, 지금 `LLMCall`의 세 숫자만으로는 어느 규약인지 알 수 없다.
3. **strict schema의 `"default": null`.** OpenAI 공식 Python SDK는 strict schema를 만들 때 `None` default를 지운다: "strip `None` defaults as there's no meaningful distinction here" ([openai-python `_pydantic.py`](https://github.com/openai/openai-python/blob/main/src/openai/lib/_pydantic.py)). OpenAI 문서는 `default`를 지원 목록에도 미지원 목록에도 넣지 않았고, 미지원 schema는 error라고 한다 ([Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). 우리 `strict_json_schema`는 `SearchPlan`의 `default: null`을 그대로 보낸다. OpenAI·Gemini에서 400이 날 수 있으므로 평가 전에 한 번씩 호출해 확인해야 한다. 400은 재시도하지 않으므로 40문항이 전부 실패로 끝난다.
4. **Gemini의 `extra_body` 중첩.** Gemini 고유 설정은 요청 JSON 안에 `"extra_body"` key를 글자 그대로 넣어야 한다. 우리 `EXTRA_BODY` 설정 이름과 겹쳐 헷갈리기 쉽다. `WHATFROM_*_LLM_EXTRA_BODY={"extra_body": {"google": {...}}}` 꼴이 된다. `reasoning_effort`와 `thinking_level`을 같이 넣으면 안 된다.
5. **xAI Chat Completions는 legacy.** 지금 동작에는 문제가 없지만, 새 기능은 Responses API에만 올 수 있다.
6. **schema 첫 요청 지연(OpenAI).** 새 schema의 첫 요청에는 추가 지연이 있다. 40문항의 첫 문항이 p95를 끌어올릴 수 있으므로, 평가 전에 단계마다 한 번 호출해 데우거나 첫 문항을 표시해야 한다.
7. **rate limit.** Kimi Tier 0(3 RPM)과 OpenAI Free(호출 불가)에서는 평가가 429로 막힌다. 재시도가 섞이면 roadmap 규칙상 지연을 신뢰할 수 없다. Kimi는 Tier 1($10) 이상, OpenAI는 Tier 1 이상이어야 한다.
8. **parameter 금지 목록.** kimi-k3의 고정 sampling parameter, xAI reasoning model의 `presencePenalty`·`frequencyPenalty`·`stop`은 우리 코드가 보내지 않으므로 지금은 문제가 없다. `EXTRA_BODY`에 넣지 않도록 주의한다.

## 확인하지 못한 것

- **gemini-3.5-flash-lite의 `reasoning_effort` 대응.** OpenAI 호환 문서의 대응표에 3.5 Flash-Lite 열이 없다.
- **Gemini OpenAI 호환 응답의 `usage` 형식.** `completion_tokens_details.reasoning_tokens`를 주는지, `completion_tokens`가 thinking을 포함하는지 문서에 없다.
- **Gemini OpenAI 호환 layer의 strict `json_schema` 처리.** `strict`의 의미, `anyOf`+`null`, `default` keyword 지원 여부가 문서에 없다.
- **OpenAI strict 모드가 `"default": null`을 받는지.** 문서에 언급이 없다. SDK가 지우는 것만 확인했다.
- **kimi-k3가 reasoning token을 output 단가로 받는지.** pricing 문서에 명시 문장이 없다. `completion_tokens`가 reasoning을 포함한다는 것은 실측으로 확인했다.
- **grok-4.3 reasoning token의 과금 단가.** "billed as part of your total consumption"이라고만 한다.
- **gpt-6.1-sol에 `none`을 보냈을 때의 응답.** 미지원이라고만 하고 400인지 명시하지 않았다 (Astra는 400이라고 명시).
- **Gemini 3.5 Flash-Lite의 model별 RPM·TPM.** AI Studio에서만 보인다.
- **지연·처리량 수치.** 네 공급자 모두 1차 문서에 수치가 없다. 정성 표시("Fast", "low-latency")만 있다.
- **kimi-k3 국제 문서의 chat API reference와 thinking 문서 중 일부**는 중국 사이트(`platform.kimi.com`)에서 읽었다. 국제 사이트와 내용이 다를 수 있다.
