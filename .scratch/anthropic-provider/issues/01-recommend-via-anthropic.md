# 01: 추천 단계를 Anthropic으로 부르기

**What to build:** 단계별 API 종류를 `anthropic`으로 두면 그 단계가 Anthropic native Messages API로 불리고, 답이 schema 모델로 검증되며, 호출 기록이 다른 공급자와 같은 형식으로 남는다. 예를 들어 추천 단계만 `anthropic`이면 추천은 `/messages`로, 조건 추출은 `/chat/completions`로 간다. spec §설정, §Anthropic provider(요청, 응답의 성공 경로, usage), §HTTP 호출(추가 header), §평가 결과 meta.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] `WHATFROM_PLAN_LLM_API`, `WHATFROM_RECOMMEND_LLM_API`가 `openai_compatible`, `anthropic`을 받는다. 비우면(빈 값 포함) `openai_compatible`이고, 모르는 값이면 시작할 때 실패한다.
- [x] API 종류를 비우면 지금과 같은 요청이 나간다. 기존 provider 테스트가 그대로 통과한다.
- [x] 공통 HTTP 함수가 추가 header를 받는다. 임베딩과 OpenAI 호환 provider의 요청은 바뀌지 않는다.
- [x] Anthropic 요청은 `{base_url}/messages`로 가고, `x-api-key`와 `anthropic-version: 2023-06-01` header, top-level `system`, user 메시지 하나, `max_tokens` 16,000, `output_config.format`(strict schema)을 담는다. 빈 API key면 `x-api-key`를 보내지 않는다.
- [x] 응답의 text block을 schema 모델로 검증해 돌려준다.
- [x] 호출 기록에 `input_tokens`, `output_tokens`, `output_tokens_details.thinking_tokens`(없으면 null)가 입력·출력·reasoning token으로 남는다.
- [x] 평가 결과 meta의 단계별 값에 `api`가 남는다.
- [x] `make lint`, `make test` 통과.
