# 02: 사전 확인

**What to build:** 측정 설정 하나를 지정하면 golden set 첫 문항으로 조건 추출 단계를 평가와 같은 provider 코드로 한 번 부르고, 성공 여부·실패 종류·원본 `usage`를 사전 확인 기록에 남긴다. 이 호출이 첫 schema 처리 지연도 흡수한다. spec §측정 설정, §실험 스크립트, §사전 확인 규칙.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] spec의 측정 설정 5개(kimi-max, kimi-low, luna-none, grok-none, gemini-minimal)를 이름으로 고를 수 있다.
- [ ] 요청 본문을 따로 만들지 않고 평가와 같은 provider 코드로 부르며, 주입한 HTTP client로 원본 응답의 `usage`를 그대로 받는다.
- [ ] 실패를 일시적(429, 5xx, timeout, 연결 실패)과 영구적(그 밖의 4xx)으로 나눠 기록한다. schema 거부로 보이는 400은 응답 본문을 함께 남긴다.
- [ ] 공급자 키는 키 환경 변수에서 읽고, 기록과 출력에 남기지 않는다.
- [ ] 가짜 transport로 성공·일시적 실패·영구적 실패 기록을 확인하는 테스트가 있다. 실제 API는 CI에서 부르지 않는다.
