# M1-b 추천 단계 LLM 조사 (2026-10-03)

M1-b(추천 단계)에 필요한 API 사실을 공급자 공식 문서로 확인한다.
[M1 후보 조사 노트](2026-10-03-m1-llm-candidates.md)가 이미 확인한 것(ID, base URL, reasoning `EXTRA_BODY`, usage 규약, 가격, 기본 rate limit)은 반복하지 않는다.
모든 출처의 접근일은 2026-10-03이다. 공식 문서에서 확인하지 못한 것은 "확인하지 못한 것"에 모았다.

## 추천 단계 요청의 크기

kimi-k3 기준선(`eval/experiments/2026-10-03-plan-llm-comparison/results/kimi-max-r1.json`)의 추천 단계 호출 38건(timeout 2건 제외)에서 잰 값이다.

| 항목 | 최소 | 중앙값 | p95 | 최대 | 38건 합계 |
| --- | --- | --- | --- | --- | --- |
| 입력 token (`prompt_tokens`) | 941 | 2,092 | 5,555 | 20,618 | 126,443 |
| 출력 token (`completion_tokens`, reasoning 포함) | 698 | 1,342 | 3,354 | 3,589 | 60,795 |
| reasoning token | 189 | 762 | 2,564 | 3,050 | 38,317 |
| 보이는 출력 (출력 − reasoning) | 406 | 약 580 | 약 800 | 908 | 22,478 |

- prompt는 system prompt + 후보 목록 + README 근거다(`src/whatfrom/recommend/advisor.py`의 `build_prompt`). 문자 수는 1.2K~47K다.
- 최대 입력 두 건(`temurin-17-jammy-pinned` 20,618, `alpine-static-binary` 16,379)은 근거 chunk가 많은 문항이다.
- timeout 2건(`node-arm64-graviton`, `temurin-jdk-build-stage`)의 prompt는 6.8K, 11K 문자로 특별히 크지 않았다.
- Dockerfile을 포함한 JSON 답 자체는 1,000 token을 넘지 않았다. 출력 길이의 대부분은 reasoning이다.

보내는 schema(`strict_json_schema(Recommendation)`)는 다음과 같다. 실제로 출력해 확인했다.

```json
{"type": "object", "title": "Recommendation", "description": "<한국어 docstring>",
 "additionalProperties": false,
 "properties": {"image": {"type": "string", "title": "Image"},
                "reason": {"type": "string", "title": "Reason"},
                "dockerfile": {"type": "string", "title": "Dockerfile"},
                "alternatives": {"type": "array", "items": {"type": "string"}, "title": "Alternatives"}},
 "required": ["image", "reason", "dockerfile", "alternatives"]}
```

- 중첩은 1단계다. 객체 배열, nullable, `anyOf`, `enum`, 문자열 길이 제약이 없다.
- `alternatives`는 `default_factory=list`라 Pydantic이 `"default"`를 내보내지 않는다. `SearchPlan`과 달리 `"default": null`도 없다.
- 즉 M1-a에서 다섯 공급자가 모두 받은 `SearchPlan` schema가 쓰는 keyword의 부분집합이다.

## 요약

| model | Q1 schema 제약에 걸리나 | Q2 출력 token 기본값 / 최대, reasoning 포함 여부 | Q3 rate limit (낮은 tier) | 추천 단계 40문항 입력 비용 추정 |
| --- | --- | --- | --- | --- |
| kimi-k3 | 문서화된 제약 없음 | 기본 131,072 / 최대 1,048,576. reasoning 포함 | Tier 1: 동시 15, 100 RPM, 2M TPM | $0.40 |
| gpt-6-luna | 걸리지 않음 (속성 5,000개·중첩 10단계 한도) | 기본값 문서 없음 / 최대 128,000. reasoning 포함 | Tier 1: 500 RPM, 500K TPM | $0.013 |
| gpt-6.1-sol | gpt-6-luna와 같음 | 기본값 문서 없음 / 최대 128,000. reasoning 포함 | Tier 1: 500 RPM, 500K TPM | $0.27 |
| grok-4.3 | 걸리지 않음 (400 목록에 해당 없음) | 기본 128,000. reasoning 미포함 | Tier 0: 37 RPS, 10M TPM (reasoning·cached 포함) | $0.17 |
| gemini-3.5-flash-lite | 문서상 걸리지 않음. 호환 layer 처리는 문서 없음 | 호환 layer 기본값 문서 없음 / 모델 최대 65,536. thinking 포함 | model별 RPM·TPM 비공개(AI Studio에서만). Tier 1 지출 상한 $10/10분 | $0.040 |
| claude-sonnet-5-5 (추가 후보) | 호환 layer는 `response_format`을 **무시**한다. native는 걸리지 않음 | 호환 layer 기본값 문서 없음 / 최대 128,000. thinking 포함. native는 `max_tokens` 필수 | Start tier: 1,000 RPM, 2M ITPM, 400K OTPM | $0.27 |

비용 열은 기준선의 입력 token(38건 126,443 → 40건 약 133K)에 각 모델의 input 단가를 곱한 하한이다. 출력은 모델마다 reasoning 양이 달라 넣지 않았다. tokenizer가 달라 입력 token 수도 모델마다 다르다.

핵심:

- **schema 위험은 없다.** `Recommendation`은 `SearchPlan`보다 단순하고, 다섯 후보의 문서화된 제약 어디에도 걸리지 않는다.
- **출력 길이 위험도 낮다.** 우리는 max token을 보내지 않는다. 보이는 출력은 1,000 token 미만이고, 확인된 기본값과 최대값은 모두 수만 token 이상이다. 다만 OpenAI·Gemini·Anthropic 호환 layer의 기본값은 문서에 없다.
- **Gemini 429는 RPM(또는 초 단위 한도)일 가능성이 가장 높다.** 지출 상한과 TPM으로는 설명되지 않는다. 우리 재시도(약 1초, 2초)가 분 단위 창보다 짧아 재시도가 한도를 더 소모했다.
- **Claude Sonnet은 지금 코드로는 쓸 수 없다.** 호환 layer가 `response_format`과 `reasoning_effort`를 무시한다. JSON schema 강제와 effort 설정은 native Messages API에서만 문서화되어 있다.

## Q1. `Recommendation` schema와 strict structured output

**gpt-6-luna, gpt-6.1-sol (OpenAI).** 문서화된 한도는 "up to 5000 object properties total, with up to 10 levels of nesting", 이름·enum·const 문자열 합 120,000자, enum 값 1,000개다. 미지원 keyword는 `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`, `then`, `else`다. 문자열 길이·배열 길이 제약 미지원은 fine-tuned model에만 해당한다. 모든 객체에 `additionalProperties: false`가 필요하다 ([Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)). 우리 schema는 속성 4개, 중첩 1단계라 어디에도 걸리지 않는다.

- "outputs will be produced in the same order as the ordering of keys in the schema" (같은 문서). 우리 순서는 `image` → `reason` → `dockerfile` → `alternatives`다. `reason`을 먼저 쓰게 하려면 field 순서를 바꿔야 한다. 정확도에 영향이 있는지는 문서에 없다.
- 응답이 잘리거나 거부되면 schema를 따르지 않을 수 있다: "if the model refuses to answer for safety reasons, or if for example you reach a max tokens limit and the response is incomplete" (같은 문서).

**grok-4.3 (xAI).** 400을 내는 schema는 빈 `enum`/`anyOf`, `true`/`false` schema, `maxContains`/`minContains`, 배열형 `items`뿐이다. `additionalProperties`는 기본이 `false`다. `minLength`/`maxLength`는 2,048, `minItems`/`maxItems`는 256까지 엔진이 보장하고, 넘으면 받아들이되 모델 행동에 맡긴다 ([Structured Outputs](https://docs.x.ai/developers/model-capabilities/text/structured-outputs)). 우리 schema는 이 제약을 쓰지 않는다.

**kimi-k3 (Moonshot).** schema 제약 목록이 문서에 없다. chat API reference는 `json_schema`를 권장하고 "If you encounter schema validation issues, please submit feedback at walle GitHub Issues"라고만 한다 ([Kimi chat API](https://platform.kimi.ai/docs/api/chat)). K3 quickstart: "Parse only that field, not `reasoning_content`." ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)). 기준선이 같은 schema로 이미 돌고 있다.

**gemini-3.5-flash-lite (Google).** native 문서가 지원한다고 밝힌 keyword(`type`, `title`, `description`, `properties`, `required`, `additionalProperties`, `items` 등)로 우리 schema를 모두 표현할 수 있다. 한계로는 "Not all JSON Schema features are supported."와 "Very large or deeply nested schemas may be rejected."만 적혀 있다 ([Structured output](https://ai.google.dev/gemini-api/docs/structured-output)). 크기나 깊이의 수치 기준은 없다. 같은 Google 문서는 "When the model output has a different order for the fields than the defined structured schema, this can lead to repeating text"라며 "Make all output fields required"를 권한다 ([Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)). 우리 schema는 모든 field가 required다. OpenAI 호환 layer가 `strict`를 어떻게 다루는지는 여전히 문서에 없다.

**claude-sonnet-5-5 (Anthropic).** OpenAI 호환 layer의 요청 field 표: "`response_format` | Ignored. For JSON output, use Structured Outputs with the native Claude API" ([OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)). 즉 schema가 전혀 강제되지 않는다. native Structured Outputs(`output_config.format`)는 `claude-sonnet-5-5`를 지원하고, `default`, `description`, `additionalProperties: false`(필수), `enum`, `anyOf` 등을 지원한다. 미지원은 수치 제약, `minLength`/`maxLength`, `minItems` 0·1 밖의 배열 제약, 재귀 schema다 ([Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)). 우리 schema는 native에서는 문제가 없다. `title`은 지원 목록에 명시되어 있지 않다.

## Q2. 출력 길이 기본값과 한도

우리 코드는 `max_tokens`나 `max_completion_tokens`를 보내지 않는다. 그래서 각 공급자의 기본값이 곧 한도다.

| model | 기본값 | 최대 | reasoning이 한도에 포함되나 | 출처 |
| --- | --- | --- | --- | --- |
| kimi-k3 | 131,072 | 1,048,576 | 포함 | [Kimi chat API](https://platform.kimi.ai/docs/api/chat), [Thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models) |
| gpt-6-luna | 문서 없음 | 128,000 | 포함 | [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [openai-python params](https://github.com/openai/openai-python/blob/main/src/openai/types/chat/completion_create_params.py) |
| gpt-6.1-sol | 문서 없음 | 128,000 | 포함 | [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol), 위 SDK |
| grok-4.3 | 128,000 | 문서 없음 (context 1,000,000) | **미포함** | [xAI chat completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions), [Grok 4.3](https://docs.x.ai/developers/models/grok-4.3) |
| gemini-3.5-flash-lite | 호환 layer 문서 없음 | 65,536 | 포함 | [Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite), [Thinking](https://ai.google.dev/gemini-api/docs/thinking) |
| claude-sonnet-5-5 | 호환 layer 문서 없음. native는 필수 | 128,000 | 포함 | [Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview), [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) |

근거 문장:

- **Kimi.** `max_completion_tokens`: "for Kimi K3 it defaults to 131072 and can be set up to 1048576. If the result reaches the maximum number of tokens without ending, the finish reason will be "length"" ([Kimi chat API](https://platform.kimi.ai/docs/api/chat)). thinking 문서: "the sum of tokens in `reasoning_content` and `content` must be less than or equal to `max_tokens`" ([Thinking models](https://platform.kimi.ai/docs/guide/use-thinking-models)). 이 문장은 K2 계열 예시 옆에 있어 K3에 그대로 적용되는지는 명시되지 않았다.
- **OpenAI.** `max_completion_tokens`: "An upper bound for the number of tokens that can be generated for a completion, including visible output tokens and reasoning tokens." `max_tokens`는 deprecated이고 o-series와 호환되지 않는다 ([openai-python `completion_create_params.py`](https://github.com/openai/openai-python/blob/main/src/openai/types/chat/completion_create_params.py)). reasoning guide는 한도에 걸리면 "before any visible output tokens are produced" 끝날 수 있다고 하고, 처음에는 "at least 25,000 tokens for reasoning and outputs"를 남기라고 권한다 ([Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)). 생략했을 때의 기본값은 문서에 없다.
- **xAI.** `max_completion_tokens`: "only applies to visible output tokens (i.e. does not apply to tokens used for reasoning or function calls). Defaults to 128,000 when unset" ([xAI chat completions](https://docs.x.ai/developers/rest-api-reference/inference/chat-completions)).
- **Gemini.** "`max_output_tokens` ... sets the maximum number of tokens a response can generate, including thought tokens." 한도에 걸리면 "returns truncated or empty output (while still billing for any thinking tokens generated)" ([Thinking](https://ai.google.dev/gemini-api/docs/thinking)). 모델의 output token limit은 65,536이다 ([Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)).
- **Anthropic.** 호환 layer는 `max_tokens`와 `max_completion_tokens`를 "Fully supported"라고만 한다. 생략 시 기본값은 없다 ([OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)). native Messages API에서 `max_tokens`는 optional 표시가 없는 필수 field다 ([Create a Message](https://platform.claude.com/docs/en/api/messages/create)). "Thinking counts toward `max_tokens` even when the thinking content isn't returned." ([Effort](https://platform.claude.com/docs/en/build-with-claude/effort)). `stop_reason: "max_tokens"`이면 "The output may be incomplete and not match your schema" ([Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).

**판단.** 기준선의 보이는 출력은 최대 908 token, reasoning 포함 최대 3,589 token이다. `low` 이하 reasoning이면 이보다 적을 것으로 본다. 확인된 기본값(131,072, 128,000)과 최대값(65,536 이상) 어디에도 가깝지 않다. 잘림 위험은 "기본값이 문서에 없는" 세 곳(OpenAI, Gemini 호환 layer, Anthropic 호환 layer)이 아주 작은 기본값을 쓰는 경우뿐이다. 첫 호출에서 `finish_reason`을 보면 판별된다. 지금 코드는 `finish_reason`을 읽지 않으므로, 잘리면 schema 검증 실패(`response did not match Recommendation schema`)로만 드러난다.

## Q3. 추천 단계와 관련된 rate limit

추천 단계는 순차 40문항이다. 호출 하나가 몇 초 이상 걸리므로 RPM보다 TPM이 먼저 문제될지 확인한다. 기준선 입력은 최대 20,618 token이고, 40문항 합계가 약 133K token이다.

| model | 낮은 tier의 한도 | 추천 단계 40문항에 충분한가 | 출처 |
| --- | --- | --- | --- |
| kimi-k3 | Tier 1($10): 동시 15, 100 RPM, 2M TPM | 충분 | [조사 노트](2026-10-03-m1-llm-candidates.md), [Kimi limits](https://platform.kimi.ai/docs/pricing/limits) |
| gpt-6-luna | Tier 1: 500 RPM, 500K TPM. Tier 2: 5,000 RPM, 2M TPM | 충분 | [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| gpt-6.1-sol | Tier 1: 500 RPM, 500K TPM. Tier 2: 5,000 RPM, 1M TPM | 충분 | [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| grok-4.3 | Tier 0: 37 RPS, 10M TPM. Tier 1($50): 50 RPS, 15M TPM | 충분 | [xAI rate limits](https://docs.x.ai/developers/rate-limits) |
| gemini-3.5-flash-lite | model별 RPM·TPM은 AI Studio에서만 보인다. Tier 1 지출 상한 $10/10분 | 문서로 판단 불가 | [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits) |
| claude-sonnet-5-5 | Start tier: 1,000 RPM, 2M ITPM, 400K OTPM. 월 지출 상한 $500 | 충분 | [Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits) |

공급자별 계산 방식의 차이:

- **xAI**는 "Your per-second limit is derived from your per-minute request budget (RPM / 60)"이다. TPM에는 prompt, completion, reasoning, cached prompt token이 모두 들어간다 ([xAI rate limits](https://docs.x.ai/developers/rate-limits)).
- **Gemini**의 TPM은 "Tokens per minute (input)"으로 입력만 센다 ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)).
- **Anthropic**은 입력(ITPM)과 출력(OTPM)을 따로 세고, 대부분 모델에서 cache read는 ITPM에 넣지 않는다. token bucket이라 "a rate of 60 requests per minute (RPM) might be enforced as 1 request per second"다. 새 조직은 "Evaluation tier"에서 표준보다 낮은 한도로 시작할 수 있다 ([Anthropic rate limits](https://platform.claude.com/docs/en/api/rate-limits)).

### Gemini 3.5 Flash-Lite Tier 1과 M1-a의 429

**문서가 밝힌 것** ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)):

- 한도는 RPM, TPM(입력), RPD 세 축이다. "exceeding any of them will trigger a rate limit error. For example, if your RPM limit is 20, making 21 requests within a minute will result in an error, even if you haven't exceeded your TPM or other limits."
- "Rate limits are applied per project, not per API key." RPD는 태평양 시간 자정에 초기화된다.
- Tier 1 자격은 "Set up and link an active billing account"이고, billing tier cap은 $250다. Tier 2는 "Paid $100 + 3 days from first successful payment"다.
- 지출 기반 한도는 Tier 1에서 "rolling 10-minute window"에 $10다.
- model별 수치: "Rate limits depend on a variety of factors (such as your usage tier) and can be viewed in Google AI Studio." 또 "Specified rate limits are not guaranteed and actual capacity may vary."
- 오류 표에는 429 코드가 세 가지 있다. `rate_limit_exceeded`는 "You have exceeded the per-minute or per-second request or token limit", `quota_exceeded`는 "You have exceeded your daily quota", `too_many_requests`는 "You have made too many requests in a short period of time"다 ([API errors](https://ai.google.dev/gemini-api/docs/api-errors)). 초 단위 한도도 있다는 뜻이다.

**M1-a에서 일어난 일** (`results/gemini-minimal-r1.json`, 조건 추출 단계만):

- 호출 하나가 약 1초, 입력은 약 470 token이었다. 첫 17건이 약 19초 안에 성공했다(분당 약 50회 속도).
- 18번째부터 429가 나기 시작했다. 약 57초 동안 시도 41회(재시도 포함) 중 6문항이 3회 모두 429로 실패했다. 그 뒤 회복했다.
- 지출 상한으로는 설명되지 않는다. 호출당 비용이 $0.0002 안팎이라 $10/10분과 거리가 멀다.
- 입력 TPM으로도 설명되기 어렵다. 1분 동안 보낸 입력이 재시도를 포함해도 약 20K token 이하다.
- 따라서 **RPM 또는 초 단위 요청 한도에 걸렸을 가능성이 가장 높다.** 단, 그 project의 실제 RPM 값은 AI Studio에서만 보이고, 우리 코드는 429 응답 본문을 남기지 않아 세 코드 중 무엇이었는지 판별할 수 없다.
- 우리 재시도는 약 1초, 약 2초 뒤에 두 번 한다(`httpclient.post_json`). 분 단위 창을 기다리지 못하고, 재시도도 요청으로 세어져 한도를 더 소모했다.

**추천 단계에서의 위험.** 추천 단계 호출은 조건 추출보다 길어 순차 실행의 요청 속도가 낮다. 호출이 3초 걸리면 분당 약 20회다. RPM이 그보다 낮으면 같은 일이 생긴다. 추천 단계 입력은 최대 20K token이라, TPM 값이 작다면 TPM에도 걸릴 수 있다.

**피하는 방법 (Google 문서에 있는 것만):**

1. **AI Studio의 Rate Limit 페이지에서 그 project의 Gemini 3.5 Flash-Lite RPM·TPM을 확인한다** ([Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)). 문서가 수치를 주지 않으므로 이것이 유일한 출처다.
2. **요청 속도를 한도 밑으로 낮춘다.** 문서의 지출 한도 대응 항목은 "Reduce the rate of expensive requests"다 (같은 문서). M1 규칙상 재시도가 섞인 지연은 신뢰할 수 없으므로, 재시도보다 측정 도구에서 문항 사이 간격을 두는 쪽이 맞다.
3. **재시도하려면 더 길게 기다린다.** 문서는 "exponential backoff"와 jitter를 권하고, 공식 Python SDK는 "up to four times with an initial delay of approximately 1 second and a maximum delay of 60 seconds"로 재시도한다 ([Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)). 우리 운영 재시도(2회, 약 1·2초)는 이보다 훨씬 짧다.
4. **한도 상향을 요청하거나 tier를 올린다.** "Request paid tier rate limit increase" 양식이 있고, Tier 2는 $100 결제 + 3일 뒤 자동 승급이다 (같은 문서).
5. Batch API는 한도가 따로지만(Tier 1에서 Gemini 3.5 Flash-Lite 대기 token 10,000,000), 지연을 재는 M1에는 맞지 않는다 (같은 문서).

## Q4. Claude Sonnet을 후보에 넣을 수 있나

**ID와 상태.** 현재 Sonnet은 `claude-sonnet-5-5`(Claude Sonnet 5.5)다. 2026-09-28 출시, "Active (latest)", 퇴역은 "Not sooner than September 28, 2027"이다. context 1M, 최대 출력 128K다 ([Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview), [Models overview](https://platform.claude.com/docs/en/models/overview)). 비교 지연 표시는 "Fast"다(Haiku 4.5는 "Fastest").

**가격.** 1M token당 input $2, output $10, cache read $0.20, 5분 cache write $2.50, 1시간 cache write $4. Batch는 50% 할인이다 ([Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)). thinking token은 "billed as output tokens, even when the thinking text isn't returned to you" ([Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)). gpt-6.1-sol($2/$10)과 단가가 같다.

**thinking과 effort.**

- "Adaptive thinking is on by default." 기본 effort는 `high`다. effort 값은 `low`, `medium`, `high`, `xhigh`, `max`다 ([Effort](https://platform.claude.com/docs/en/build-with-claude/effort)).
- 가장 낮은 thinking 설정은 `thinking: {"type": "between_tools"}`로 up-front thinking을 끈다. `low`·`medium`·`high` effort에서만 받는다. "If your requests don't use tools, the response contains only text, as with `disabled` on Claude Sonnet 5." ([What's new in Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/whats-new-sonnet-5-5)).
- `thinking: {"type": "disabled"}`와 `{"type": "enabled", "budget_tokens": N}`은 Sonnet 5.5에서 400이다 (같은 문서).
- effort는 native 요청의 `output_config.effort`로 정한다 ([Effort](https://platform.claude.com/docs/en/build-with-claude/effort)).
- "Effort is a behavioral signal, not a strict token budget. At lower effort levels, Claude still thinks on sufficiently difficult problems" (같은 문서).
- `temperature`, `top_p`, `top_k`를 기본값이 아닌 값으로 보내면 400이다 ([Claude Sonnet 5.5](https://platform.claude.com/docs/en/models/sonnet-5-5/overview)). 우리 코드는 보내지 않는다.

native 요청에서 M1-b의 두 설정은 이렇게 된다.

- 최소: `{"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}`
- `low`: `{"output_config": {"effort": "low"}}` (adaptive thinking 유지)

**OpenAI 호환 endpoint** ([OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)):

- base URL `https://api.anthropic.com/v1/`, Claude API key를 쓴다. header 표에서 `authorization`은 "Fully supported"다. 우리 코드의 `Authorization: Bearer`와 `/chat/completions` 경로가 그대로 맞는다.
- 문서 원문: "This compatibility layer is primarily intended to test and compare model capabilities, and is not considered a long-term or production-ready solution for most use cases."
- **`response_format`: Ignored.** schema 강제가 없다.
- **`reasoning_effort`: Ignored.** effort를 정할 수 없다.
- thinking은 `thinking` parameter로 켤 수 있다고만 하고, 예시는 `{"thinking": {"type": "enabled", "budget_tokens": 2000}}`(Sonnet 4.6)다. 이 값은 Sonnet 5.5에서 400이다. `between_tools`나 `output_config`를 호환 layer가 전달하는지는 문서에 없다.
- "Most unsupported fields are silently ignored rather than producing errors."
- system message는 하나로 합쳐 맨 앞에 둔다. 우리는 system 하나라 영향이 없다.
- 응답: `usage.prompt_tokens`, `completion_tokens`, `total_tokens`는 "Fully supported", **`usage.completion_tokens_details`는 "Always empty"**다. 우리 기록의 `reasoning_tokens`는 항상 None이 된다.
- prompt caching은 지원하지 않는다. rate limit은 `/v1/messages`와 같다. `retry-after` header를 준다.

**결론: 지금 코드로는 부를 수는 있지만 쓸 수는 없다.**

- 요청 자체는 200으로 돌아올 가능성이 높다. 모르는 field는 조용히 무시하기 때문이다.
- 그러나 `response_format`이 무시되므로 JSON 출력은 prompt에만 기댄다. 우리는 `content`를 그대로 `model_validate_json`에 넣으므로, 코드 블록으로 감싼 JSON이나 설명이 붙은 답은 전부 검증 실패가 된다.
- effort를 정할 수 없어 기본값 `high` + adaptive thinking으로 돈다. M1-b의 "최소 수준과 `low`" 측정 조건을 만들 수 없다.
- reasoning token도 기록되지 않는다.

**최소 차이 (native Messages API로 부르는 경우).** `OpenAICompatibleProvider`와 별도 경로가 필요하다.

| 항목 | 지금 코드 | native에 필요한 것 | 출처 |
| --- | --- | --- | --- |
| URL | `{base_url}/chat/completions` | `https://api.anthropic.com/v1/messages` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create) |
| 인증 | `Authorization: Bearer` | `x-api-key`, `anthropic-version: 2023-06-01` | [Effort](https://platform.claude.com/docs/en/build-with-claude/effort)의 cURL 예시 |
| system | `messages[0].role = "system"` | top-level `system` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create) |
| schema | `response_format.json_schema` | `output_config.format = {"type": "json_schema", "schema": ...}`. `strict_json_schema` 결과를 그대로 쓸 수 있다 | [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) |
| 출력 한도 | 보내지 않음 | `max_tokens` 필수 | [Create a Message](https://platform.claude.com/docs/en/api/messages/create) |
| reasoning | `EXTRA_BODY` | `EXTRA_BODY`로 `thinking`, `output_config.effort`. 단 `output_config`를 schema와 병합해야 한다 | [Effort](https://platform.claude.com/docs/en/build-with-claude/effort) |
| 응답 | `choices[0].message.content` | `content` 배열에서 `type: "text"` block. thinking block이 앞에 올 수 있다 | [Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) |
| usage | `prompt_tokens`, `completion_tokens`, `completion_tokens_details.reasoning_tokens` | `input_tokens`, `output_tokens`(thinking 포함, 과금 기준), `output_tokens_details.thinking_tokens` | [Create a Message](https://platform.claude.com/docs/en/api/messages/create) |

- `output_config`는 schema(`format`)와 effort가 같은 객체 안에 있다. 지금처럼 `EXTRA_BODY`를 먼저 펼치고 고정 field로 덮어쓰면 effort가 지워진다. 병합해야 한다.
- 새 schema의 첫 요청은 grammar 컴파일 때문에 느리고, 컴파일 결과는 마지막 사용 후 24시간 cache된다 ([Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)). 측정 전에 한 번 호출해 데워야 한다.
- 거부 시 `stop_reason: "refusal"`이며 schema를 따르지 않을 수 있다 (같은 문서).

## Q5. 공식 지연·처리량 수치

다섯 후보와 Sonnet 모두 1차 문서에 token/s나 TTFT 수치가 없다. 정성 표시만 있다.

- kimi-k3: 수치 없음 ([Kimi K3 quickstart](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)).
- gpt-6-luna, gpt-6.1-sol: model page의 "Speed: Fast" 표시뿐이다 ([조사 노트](2026-10-03-m1-llm-candidates.md)).
- grok-4.3: "Fast, reliable model" 서술뿐이다 ([Grok 4.3](https://docs.x.ai/developers/models/grok-4.3)).
- gemini-3.5-flash-lite: 수치 없음 ([Gemini 3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)).
- claude-sonnet-5-5: "Comparative latency: Fast", "Relative to the current lineup. Actual latency depends on prompt length, output length, and thinking effort." ([Models overview](https://platform.claude.com/docs/en/models/overview)).

지연에 관해 문서가 밝힌 정성적 사실:

- OpenAI·Anthropic 모두 새 schema의 첫 요청에 추가 지연이 있다 ([Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).
- Google: "Higher latency or token usage often occurs because Gemini 3.x models have thinking enabled by default." ([Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)).

## 우리 코드와의 호환성 이슈

1. **Claude Sonnet은 지금 provider로 측정할 수 없다.** 호환 layer가 `response_format`과 `reasoning_effort`를 무시한다. 넣으려면 native Messages API 경로가 필요하다(Q4 표). `output_config`는 `EXTRA_BODY`와 병합해야 한다.
2. **429 응답 본문을 남기지 않는다.** `post_json`은 재시도 대상 status의 본문을 버리고 `api error 429`만 남긴다. Gemini의 세 가지 429 코드와 Anthropic의 지출 상한 429(`enforced_spend_limit_reached`, `retry-after` 없음, 재시도해도 실패)를 구분할 수 없다.
3. **`retry-after`를 읽지 않는다.** Anthropic과 xAI는 `retry-after`를 주고, 우리 백오프(약 1·2초)는 분 단위 한도에 짧다. M1 규칙상 재시도가 섞인 지연은 쓰지 않으므로, 측정 도구에서 요청 간격을 두는 편이 낫다.
4. **`finish_reason`을 읽지 않는다.** 출력이 잘리면 schema 검증 실패로만 보인다. 잘림 위험은 낮지만, 기본값이 문서에 없는 공급자(OpenAI, Gemini 호환 layer, Anthropic 호환 layer)는 첫 호출에서 `finish_reason`을 확인한다.
5. **reasoning token 기록.** Anthropic 호환 layer는 `completion_tokens_details`가 항상 비어 있다. Gemini는 M1-a에서도 reasoning field가 없었다. 두 공급자는 reasoning 비중을 알 수 없다.
6. **schema는 고칠 필요가 없다.** `Recommendation`은 `SearchPlan`이 쓴 keyword의 부분집합이고, 다섯 후보의 문서화된 제약에 걸리지 않는다. field 순서(`image` → `reason` → `dockerfile` → `alternatives`)대로 출력된다는 OpenAI 문서 문장은 참고로 둔다.
7. **첫 요청 지연.** 새 schema를 쓰는 첫 요청이 느리다(OpenAI, Anthropic). 추천 단계는 M1-a와 schema가 달라 다시 데워야 한다.

## 확인하지 못한 것

- **OpenAI Chat Completions에서 `max_completion_tokens`를 생략했을 때의 기본값.** 최대 128,000만 확인했다.
- **Gemini OpenAI 호환 layer의 기본 출력 한도**와 `strict` 처리. 모델 최대 65,536만 확인했다.
- **Gemini 3.5 Flash-Lite의 Tier 1 RPM·TPM 수치.** AI Studio에서만 보인다. M1-a의 429가 세 코드 중 무엇이었는지도 알 수 없다(본문 미기록).
- **Anthropic 호환 layer에서 `max_tokens`를 생략했을 때의 동작.** native는 필수 field다.
- **Anthropic 호환 layer가 `thinking: {"type": "between_tools"}`와 `output_config`를 전달하는지.** 문서는 `thinking` 예시(`enabled` + `budget_tokens`, Sonnet 4.6)만 보여 준다.
- **Anthropic 호환 layer의 `completion_tokens`가 thinking token을 포함하는지.** native `output_tokens`는 포함한다.
- **Anthropic native structured outputs가 `title` keyword를 받는지.** 지원 목록에 없고 미지원 목록에도 없다.
- **kimi-k3에서 reasoning이 `max_completion_tokens`에 포함되는지.** thinking 문서의 문장은 K2 계열 예시 옆에 있다. M1-a 실측에서 `completion_tokens`가 reasoning을 포함한 것만 확인했다.
- **kimi-k3 structured output의 schema 제약.** 문서에 목록이 없다.
- **grok-4.3의 최대 출력 token.** 기본값 128,000과 context 1,000,000만 문서에 있다.
- **어느 공급자도 지연·처리량 수치를 공개하지 않았다.**
