# 01: 단계마다 다른 LLM으로 추천 받기

**What to build:** `.env`에 단계별 설정을 넣으면 API와 평가가 조건 추출 단계와 추천 단계를 각자의 base URL, 모델, API 키, 덧붙일 JSON으로 부른다. 단계별 설정을 비워 두면 지금과 똑같이 동작한다. spec §2~4.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 단계별 설정이 없으면 두 단계의 요청 본문이 지금과 같다.
- [ ] 단계별 설정이 있으면 조건 추출 단계와 추천 단계가 각자의 base URL, 모델, 키, 덧붙일 JSON으로 요청한다.
- [ ] 단계별 API 키는 설정하지 않았을 때만 공통 키를 쓰고, 빈 값으로 설정하면 인증 헤더를 보내지 않는다.
- [ ] 덧붙일 JSON이 JSON 객체가 아니거나 `model`·`messages`·`response_format`을 담으면 시작할 때 실패한다.
- [ ] 평가 결과 meta에 단계별 base URL, 모델, 덧붙일 JSON이 남고 API 키는 남지 않는다. 부르지 않은 단계는 `null`이다.
- [ ] report의 모델 표시는 두 단계 모델이 같으면 하나, 다르면 둘을 보인다.
- [ ] `.env.example`(단계별 키 줄은 주석)과 README 설정 절에 단계별 설정이 적혀 있다.
