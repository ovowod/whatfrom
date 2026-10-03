# 03: 사전 확인

**What to build:** 측정 설정 하나를 지정하면 `temurin-17-jammy-pinned`로 추천 단계를 평가와 같은 provider 코드로 한 번 부른다. 잘림, 거부, usage, schema 결과를 사전 확인 기록에 남긴다. spec §사전 확인.

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] openai_compatible과 anthropic provider를 모두 부를 수 있다.
- [ ] 보낸 prompt가 `kimi-max-r1`의 해당 문항 `advise_prompt`와 같은지 확인한다.
- [ ] `finish_reason`(Anthropic은 `stop_reason`), 원본 usage, reasoning token, schema 검증 결과를 기록한다. 실패하면 응답 본문도 남긴다.
- [ ] 실패를 일시적(429, 5xx, timeout, 연결 실패)과 영구적(그 밖의 4xx), 잘림, 거부로 나눈다.
- [ ] 가짜 transport로 성공, 잘림, 거부, 429 기록을 확인하는 테스트가 있다. 실제 API는 CI에서 부르지 않는다.
