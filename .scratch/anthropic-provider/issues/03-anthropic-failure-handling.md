# 03: Anthropic 응답의 실패 처리

**What to build:** Anthropic 호출이 실패하면 원인이 이유에 그대로 드러나고, 실패한 호출도 기록이 하나 남는다. 출력이 잘리거나 모델이 거부한 경우를 schema 검증 실패와 구분할 수 있다. spec §Anthropic provider(응답), §HTTP 호출(재시도).

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] thinking block이 text block 앞에 와도 text block만 검증한다.
- [ ] `stop_reason`이 `max_tokens`이면 "출력이 잘렸다"는 이유로 실패한다.
- [ ] `stop_reason`이 `refusal`이면 "모델이 거부했다"는 이유로 실패한다.
- [ ] text block이 없거나 응답 모양이 다르면 응답 계약 위반으로 실패한다.
- [ ] 실패한 호출도 시도 횟수, 실패 이유, 읽을 수 있었던 usage가 기록에 남는다.
- [ ] Anthropic 오류 문서로 529가 과부하 응답인지 확인하고, 맞으면 재시도 대상에 더한다. 429·529 재시도의 시도 횟수가 기록된다.
- [ ] `make lint`, `make test` 통과.
