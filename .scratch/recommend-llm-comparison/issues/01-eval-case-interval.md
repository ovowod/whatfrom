# 01: eval 문항 사이 대기 option

**What to build:** `whatfrom eval`에 문항 사이 대기 option을 더한다. 공급자의 분 단위 한도에 걸리지 않게 하려는 것이다. spec §요청 간격.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 기본값은 0이고, 0이면 지금 동작과 같다.
- [x] 대기는 문항과 문항 사이에만 넣고, `seconds_total`과 단계별 시간에 들어가지 않는다.
- [x] 결과 meta에 대기 값이 남는다.
- [x] 음수는 받지 않는다.
- [x] 대기가 소요 시간에 섞이지 않는 것을 확인하는 테스트가 있다.
