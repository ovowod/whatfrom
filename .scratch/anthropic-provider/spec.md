# F15 단계별 API 종류 + Anthropic native provider

- Status: ready-for-agent
- roadmap: `docs/roadmap.md` Phase 3 M1-b (비교 대상 모델에 Claude Sonnet 추가)
- 근거: `docs/research/2026-10-03-m1b-recommend-stage.md` Q4

## Problem Statement

M1-b(추천 단계의 LLM 비교)에 Claude Sonnet을 넣고 싶다. 그런데 지금 코드로는 Sonnet을 비교 조건대로 잴 수 없다.

- 우리 LLM 호출은 OpenAI 호환 Chat Completions 하나뿐이다. 단계별로 바꿀 수 있는 것도 base URL, 모델, API key, 덧붙일 JSON뿐이다(F14).
- Anthropic의 OpenAI 호환 layer는 호출은 되지만 production용이 아니라고 문서에 적혀 있다. 우리에게 필요한 것 세 가지를 무시한다.
  - `response_format`을 무시해 schema가 강제되지 않는다. 코드 블록으로 감싼 답은 검증에서 실패한다.
  - `reasoning_effort`를 무시해 기본값 `high`로만 돈다. M1-b의 "최소 수준과 `low`" 조건을 만들 수 없다.
  - reasoning token을 보고하지 않는다.

## Solution

단계마다 LLM API 종류를 고를 수 있게 하고, Anthropic native Messages API로 부르는 provider를 더한다.

- 단계별 설정에 API 종류를 하나 더한다. 비워 두면 지금처럼 OpenAI 호환이다.
- 추천 단계만 Anthropic으로 두는 식으로 단계마다 다른 API를 쓸 수 있다.
- Anthropic provider도 같은 인터페이스(조건 추출, 추천)와 같은 호출 기록을 낸다. 평가와 API는 바뀌지 않는다.

## User Stories

1. 개발자로서, 추천 단계만 Claude Sonnet으로 설정하고 싶다. 그래야 조건 추출 단계는 다른 모델로 두고 추천 단계만 비교할 수 있다.
2. 개발자로서, API 종류 설정을 비워 두면 지금과 같은 요청이 나가기를 바란다. 그래야 기존 `.env`와 측정이 그대로 동작한다.
3. 개발자로서, API 종류를 공급자 이름이나 URL로 짐작하지 않고 명시적으로 고르고 싶다. F14와 같은 원칙이다.
4. 개발자로서, 모르는 API 종류를 넣으면 시작할 때 실패하기를 바란다. 오타가 첫 호출 때 드러나면 측정을 날린다.
5. 개발자로서, Sonnet이 strict JSON schema를 따르는 답을 내기를 바란다. 그래야 다른 모델과 같은 검증을 통과한다.
6. 개발자로서, `EXTRA_BODY`로 Sonnet의 thinking과 effort를 정하고 싶다. 그래야 최소 수준과 `low`로 잴 수 있다.
7. 개발자로서, `EXTRA_BODY`에 effort를 넣어도 schema 설정이 지워지지 않기를 바란다. 둘이 같은 `output_config` 객체에 들어간다.
8. 개발자로서, `EXTRA_BODY`가 코드가 정하는 값(모델, 메시지, system, 출력 한도, schema)을 덮어쓰려 하면 시작할 때 실패하기를 바란다.
9. 개발자로서, Sonnet 호출도 시도 횟수, 성공 여부, 실패 이유, 입력·출력·reasoning token을 같은 형식으로 기록하기를 바란다. 그래야 M1-b 집계가 그대로 쓴다.
10. 개발자로서, 출력이 한도에서 잘리면 schema 검증 실패가 아니라 "잘렸다"는 이유로 실패하기를 바란다. 원인을 바로 알 수 있다.
11. 개발자로서, 모델이 답을 거부하면 거부했다는 이유로 실패하기를 바란다.
12. 개발자로서, thinking block이 앞에 와도 답(text block)만 검증하기를 바란다.
13. 개발자로서, Anthropic 호출도 timeout(120초)과 재시도(429·5xx 최대 2회)를 다른 공급자와 같게 쓰기를 바란다. 운영과 같은 조건으로 잰다.
14. 개발자로서, Anthropic의 과부하 응답도 재시도 대상이기를 바란다.
15. 개발자로서, API key가 Anthropic 방식의 header로 나가고, 빈 값이면 인증 header를 보내지 않기를 바란다. F14의 빈 key 규칙과 같다.
16. 개발자로서, 평가 결과 meta에 단계별 API 종류가 남기를 바란다. 그래야 같은 모델 이름이라도 어떤 경로로 불렀는지 안다.
17. 개발자로서, 임베딩 호출의 요청과 응답 처리가 이 변경으로 바뀌지 않기를 바란다. 529 재시도는 공통 정책에 들어가지만, 529는 Anthropic만 보내므로 임베딩에서 실제로 바뀌는 동작은 없다.
18. 개발자로서, fake provider로 도는 CI가 그대로 통과하기를 바란다.

## Implementation Decisions

### 설정

| 설정 | 값 | 비었을 때 |
| --- | --- | --- |
| `WHATFROM_PLAN_LLM_API`, `WHATFROM_RECOMMEND_LLM_API` | `openai_compatible`, `anthropic` | `openai_compatible` |

- 공통 설정은 두지 않는다. 지금 공통 설정(kimi-k3)에 쓸 일이 없다.
- 빈 값은 설정하지 않은 것으로 본다(F14의 다른 단계별 설정과 같다).
- 모르는 값이면 시작할 때 실패한다.
- `WHATFROM_LLM_PROVIDER`는 그대로 둔다. `fake`이면 단계별 설정을 쓰지 않는 것도 같다.
- `anthropic` 단계는 base URL을 따로 설정해야 한다. 비우면 공통 base URL(Moonshot)로 가서 첫 호출이 실패한다. 이 경우를 따로 막지는 않는다.

### Anthropic provider

- OpenAI 호환 provider 옆에 둔다. 같은 인터페이스(`plan`, `recommend`)와 같은 호출 기록 callback을 받는다.
- 단계별 provider를 만들 때 그 단계의 API 종류로 클래스를 고른다. 두 단계를 묶는 방식은 그대로다.

**요청**

- `POST {base_url}/messages`. base URL 예: `https://api.anthropic.com/v1`.
- header: `x-api-key`와 `anthropic-version: 2023-06-01`. API key가 빈 값이면 `x-api-key`를 보내지 않는다.
- system 메시지는 top-level `system`으로, 사용자 prompt는 `messages`의 user 메시지 하나로 보낸다.
- schema는 `output_config.format = {"type": "json_schema", "schema": ...}`로 보낸다. schema는 OpenAI 호환 provider와 같은 strict schema 함수로 만든다.
- `max_tokens`는 16,000으로 고정한다.
  - native API에서 필수다.
  - thinking token을 포함한다. M1-a 기준선의 추천 단계 출력은 reasoning 포함 최대 약 3,600 token이었다.
  - 설정으로 열지 않는다. 바꿀 필요가 생기면 그때 연다.
- `EXTRA_BODY`는 먼저 펼친다. 그 위에 코드가 정하는 값을 쓴다. `output_config`만 병합한다(`EXTRA_BODY`의 `output_config`에 `format`을 더한다).
- `temperature` 등 sampling parameter는 보내지 않는다. Sonnet 5.5는 기본값이 아니면 400이다.

**`EXTRA_BODY` 검증 (`anthropic` 단계)**

- `model`, `messages`, `system`, `max_tokens`가 있으면 시작할 때 실패한다.
- `output_config`가 있으면 JSON 객체여야 한다. null, 숫자, 문자열, 배열이면 시작할 때 실패한다. 객체가 아니면 schema를 병합할 수 없고, 그 오류는 요청 전에 나서 호출 기록에도 남지 않는다.
- `output_config.format`이 있으면 시작할 때 실패한다. `output_config.effort`는 받는다.
- OpenAI 호환 단계의 기존 검증(`model`, `messages`, `response_format`)은 그대로다.

**응답**

- `content` 배열에서 `type`이 `text`인 block의 text를 이어 붙여 schema 모델로 검증한다. thinking block은 건너뛴다.
- `stop_reason`이 `max_tokens`이면 "출력이 잘렸다"는 이유로 실패한다.
- `stop_reason`이 `refusal`이면 "모델이 거부했다"는 이유로 실패한다.
- text block이 없거나 응답 모양이 다르면 지금처럼 응답 계약 위반으로 실패한다.
- 실패한 호출도 기록을 하나 남긴다(F14와 같다). `usage`는 검증 전에 읽는다.

**usage → 호출 기록**

| 호출 기록 | Anthropic usage |
| --- | --- |
| 입력 token | `input_tokens` |
| 출력 token | `output_tokens` (thinking token 포함, 과금 기준) |
| reasoning token | `output_tokens_details.thinking_tokens`. 없으면 null |

출력 token이 reasoning을 포함하므로 M1 집계의 Moonshot·OpenAI와 같은 규칙으로 비용을 계산한다.

### HTTP 호출

- 공통 HTTP 함수가 추가 header를 받게 한다. 지금은 Bearer 인증만 만든다.
  - Anthropic provider는 Bearer 대신 자기 header를 넘긴다.
  - 임베딩과 OpenAI 호환 provider의 호출은 바뀌지 않는다.
- 재시도 대상 status에 529를 더한다. Anthropic의 과부하 응답이다. 구현 때 Anthropic 오류 문서로 확인한다.
  - 공통 정책이라 임베딩과 OpenAI 호환 호출에도 적용된다. 529는 Anthropic만 보내므로 그쪽에서 실제로 바뀌는 동작은 없다.
- timeout과 재시도 횟수는 공통 정책 그대로다.

### 평가 결과 meta

- 단계별 meta(`base_url`, `model`, `extra_body`)에 `api`를 더한다. 부르지 않은 단계는 지금처럼 null이다.
- report의 모델 표시는 바꾸지 않는다.

## Testing Decisions

- **테스트 지점은 기존의 provider 경계 하나다.** 설정을 넣어 provider를 만들고(`get_provider`에 설정과 가짜 transport를 넘긴다), `plan`/`recommend`를 불러 결과와 실제로 나간 요청, 호출 기록을 확인한다. 내부 helper는 테스트하지 않는다.
- 선례는 OpenAI 호환 provider의 기존 테스트다. 가짜 transport로 URL, header, 본문을 확인하고, 호출 기록 callback을 받는다.
- 확인할 동작:
  - API 종류를 비우면 OpenAI 호환 요청이 그대로 나간다(기존 테스트가 계속 통과).
  - 추천 단계만 `anthropic`이면 추천은 `/messages`로, 조건 추출은 `/chat/completions`로 간다.
  - Anthropic 요청의 header, top-level `system`, `max_tokens`, `output_config.format`.
  - `EXTRA_BODY`의 `output_config.effort`와 `thinking`이 schema와 함께 남는다.
  - thinking block이 앞에 있어도 text block으로 검증한다.
  - `stop_reason`이 `max_tokens`, `refusal`일 때 이유가 담긴 실패와 호출 기록.
  - usage가 호출 기록의 세 숫자로 옮겨진다. thinking token이 없으면 null.
  - 429·529는 재시도하고 시도 횟수가 기록된다.
  - 빈 API key면 `x-api-key`를 보내지 않는다.
- 설정 검증은 기존 설정 테스트의 방식을 따른다. 모르는 API 종류, `anthropic` 단계의 금지 키와 `output_config.format`, 객체가 아닌 `output_config`(null, 숫자, 배열)가 시작할 때 실패하는지 본다.
- 평가 결과 meta의 `api`는 기존 평가 CLI 테스트의 meta 확인에 더한다.
- 실제 Anthropic API는 CI에서 부르지 않는다. 실제 호출은 M1-b의 사전 확인이 한다.
- `make lint`, `make test` 통과.

## Out of Scope

- M1-b 측정 도구와 측정 설정(Sonnet 최소·`low` 포함). M1-b spec에서 다룬다.
- OpenAI 호환 provider의 `finish_reason` 확인, 429 응답 본문 기록, `retry-after` 처리. M1-b 조사의 호환성 이슈지만 이 기능과 별개다.
- 공통 API 종류 설정, `max_tokens` 설정.
- Anthropic prompt caching, tool use, streaming, batch.
- Anthropic의 OpenAI 호환 layer. 쓰지 않는다.
- OpenAI 호환이 아닌 다른 공급자.

## Further Notes

- 같은 schema의 첫 요청은 grammar 컴파일로 느리다(24시간 cache). M1-b 사전 확인이 데운다.
- 추가한 meta의 `api` 때문에 M1-a 실험 도구의 결과 검증(단계별 meta 비교)은 이 변경 뒤의 결과와 맞지 않는다. M1-a는 끝났고, 다시 잴 때는 새 폴더에서 도구를 고쳐 쓴다.
- "OpenAI 호환 layer 대신 native API를 쓴다"는 결정은 close-out 때 ADR로 남길지 정한다.
- Claude Sonnet 5.5: `claude-sonnet-5-5`, 1M token당 $2 / $10. thinking token은 output 단가다.

## Review Log

### 2026-10-03: Codex review
- ✅ `output_config`의 type을 검증하지 않음: `output_config`는 JSON 객체여야 하고, 아니면 시작할 때 실패한다. 설정 테스트에 null, 숫자, 배열을 더했다. 객체가 아니면 시작할 때 막아야 한다.
- ✏️ 529 재시도를 공통으로 넣으면 "임베딩은 바뀌지 않는다"와 어긋남: 호출마다 재시도 목록을 고르게 하지 않고, 529는 공통 정책에 두되 User Story 17과 §HTTP 호출의 문장만 고쳤다. 529는 Anthropic만 보낸다.
