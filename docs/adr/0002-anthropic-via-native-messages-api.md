# 0002. Anthropic 모델은 native Messages API로 부른다

- Status: accepted
- Date: 2026-10-03 (F15)

## Context

LLM 호출은 OpenAI 호환 Chat Completions 하나로 해 왔다. base URL만 바꾸면 공급자를 갈아끼울 수 있다는 것이 전제였다.

M1-b(추천 단계의 LLM 비교)에 Claude Sonnet을 넣으려고 Anthropic의 OpenAI 호환 layer(`https://api.anthropic.com/v1/`)를 확인했다. 호출은 되지만 우리에게 필요한 것을 무시한다([조사 노트](../research/2026-10-03-m1b-recommend-stage.md) Q4).

- `response_format`을 무시한다. schema가 강제되지 않아, 코드 블록으로 감싼 답은 검증에서 실패한다.
- `reasoning_effort`를 무시한다. 기본값 `high`로만 돌아 M1의 "reasoning 최소 수준과 `low`" 조건을 만들 수 없다.
- `usage.completion_tokens_details`가 항상 비어 reasoning token을 기록할 수 없다.
- Anthropic 문서는 이 layer를 "not considered a long-term or production-ready solution"이라고 한다.

## Decision

- Anthropic 모델은 native Messages API(`/v1/messages`)로 부른다. OpenAI 호환 layer는 쓰지 않는다.
- 단계마다 API 종류(`openai_compatible`, `anthropic`)를 설정으로 고른다. 공급자 이름이나 URL로 짐작하지 않는다.
- 두 provider는 공통 흐름(호출 기록, timeout, 재시도)을 같은 base class에서 물려받고, 요청 모양, 응답 해석, usage 읽기만 각자 구현한다.

## Consequences

- "base URL만 바꾸면 된다"는 전제는 OpenAI 호환 공급자에만 맞는다. 다른 API를 쓰는 공급자는 provider를 하나 더 둔다.
- Anthropic 쪽은 header(`x-api-key`, `anthropic-version`), top-level `system`, 필수 `max_tokens`(16,000 고정), `output_config` 병합을 따로 관리한다.
- 같은 모델 이름이라도 어떤 경로로 불렀는지 평가 결과 meta의 `api`로 구분한다.

다음 경우에는 이 결정을 다시 본다: Anthropic의 OpenAI 호환 layer가 `response_format`과 reasoning 설정을 지원하게 될 때.
