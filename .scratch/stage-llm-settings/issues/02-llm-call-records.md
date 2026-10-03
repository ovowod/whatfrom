# 02: 평가 결과에 LLM 호출 기록 남기기

**What to build:** 평가 결과 JSON의 문항마다 LLM 호출별 단계, 시도 횟수, token 사용량(입력·출력·reasoning), 성공 여부와 실패 이유가 남는다. 재시도가 섞인 문항과 실패한 호출의 비용을 가려낼 수 있다. spec §5.

**Blocked by:** None (can start immediately). 01과 같은 provider 코드를 고치므로 순서대로 한다면 01 뒤에 한다.

**Status:** done

- [x] 실패한 호출을 포함해 논리적 호출 하나당 기록이 정확히 하나 남는다(재시도 소진, timeout, JSON이 아닌 응답, schema 검증 실패).
- [x] 재시도가 일어난 호출은 시도 횟수가 2 이상으로 남는다. 실패한 호출도 시도 횟수가 남는다.
- [x] 응답은 왔지만 schema 검증에서 실패한 호출도 `usage`가 남는다.
- [x] 응답에 `usage`가 없으면 token 수가 `null`로 남고 실패하지 않는다.
- [x] 임베딩 호출의 동작은 바뀌지 않는다.
- [x] API 경로는 호출 기록을 받지 않으며, 여러 요청이 provider를 함께 써도 기록이 섞이지 않는다.
