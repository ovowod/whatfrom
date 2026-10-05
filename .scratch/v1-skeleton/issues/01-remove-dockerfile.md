# 01: Dockerfile 제거

**What to build:** `/recommend` 응답과 두 번째 LLM 호출에서 Dockerfile 초안을 뺀다. 사용자는 Dockerfile 없이 추천, 이유, 대안, 후보를 받는다. spec §응답 계약, §지우는 것, §함께 바꾸는 것.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] 응답의 추천과 LLM 출력 schema에 Dockerfile 필드가 없다.
- [ ] 두 번째 LLM 호출의 prompt에 Dockerfile 지시가 없다.
- [ ] Dockerfile의 `FROM` 고정과 Dockerfile 이미지 참조 검증, 그리고 검증에 걸린 Dockerfile을 비우는 API 처리가 사라진다. 추천 이미지와 대안의 실재성 검증은 그대로다.
- [ ] eval 결과 JSON과 부하 측정용 모의 LLM 응답에 Dockerfile이 없다.
- [ ] README의 Dockerfile 관련 규칙을 지운다.
- [ ] 끝난 실험 도구의 테스트는 fixture만 고쳐 통과시키고, 실험은 다시 돌리지 않는다.
- [ ] fake provider로 CI와 `make eval`이 통과한다.
