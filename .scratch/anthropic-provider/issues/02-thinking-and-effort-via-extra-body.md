# 02: `EXTRA_BODY`로 thinking과 effort 정하기

**What to build:** `anthropic` 단계의 `EXTRA_BODY`로 Sonnet의 thinking과 effort를 정할 수 있고, 그래도 schema 설정은 지워지지 않는다. 코드가 정하는 값을 덮어쓰려는 설정은 시작할 때 실패한다. M1-b가 Sonnet을 최소 수준과 `low`로 잴 수 있게 된다. spec §Anthropic provider(요청의 `EXTRA_BODY` 병합), §`EXTRA_BODY` 검증.

**Blocked by:** 01

**Status:** ready-for-agent

- [x] `EXTRA_BODY`의 `thinking`이 요청에 그대로 남는다.
- [x] `EXTRA_BODY`의 `output_config.effort`와 코드의 `output_config.format`이 한 `output_config`에 함께 남는다.
- [x] `anthropic` 단계의 `EXTRA_BODY`에 `model`, `messages`, `system`, `max_tokens`, `output_config.format`이 있으면 시작할 때 실패한다.
- [x] `anthropic` 단계의 `EXTRA_BODY`에 `output_config`가 있으면 JSON 객체여야 한다. null, 숫자, 문자열, 배열이면 시작할 때 실패한다.
- [x] OpenAI 호환 단계의 기존 검증(`model`, `messages`, `response_format`)은 그대로다.
- [x] `make lint`, `make test` 통과.
