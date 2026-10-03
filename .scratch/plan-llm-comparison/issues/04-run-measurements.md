# 04: 측정 수행

**What to build:** 실제 공급자 API로 사전 확인과 측정을 같은 날 돌려, 집계가 고른 측정 설정(또는 미완료·결론 없음)을 얻는다. 유료 키와 tier가 필요해 사람이 한다. spec §사전 확인 규칙, §측정 규칙, §고르는 기준, Further Notes.

**Blocked by:** 01, 02, 03

**Status:** ready-for-human

- [ ] 준비: Kimi Tier 1 이상, OpenAI Tier 1 이상, Gemini billing 연결(Tier 1), xAI 키. 수집과 색인이 끝난 DB.
- [ ] 측정 전 repository 목록을 기록하고, 측정 동안 수집과 색인을 돌리지 않는다.
- [ ] 5개 측정 설정의 사전 확인을 모두 돌린다.
  - [ ] schema 때문에 400이 나면 `strict_json_schema`가 `"default": null`을 지우게 고치고 테스트를 더한 뒤, 모든 사전 확인을 다시 한다.
  - [ ] kimi-max가 끝내 실패하면 멈추고 M1-a를 미완료로 둔다.
- [ ] kimi-max, kimi-low, luna-none, grok-none, gemini-minimal 순서로 1회씩 측정한다.
- [ ] 집계를 돌려 경계 모델(하한 ±2문항)과 재시도 확인이 필요한 설정을 다시 돌린다. 재시도 확인은 설정마다 최대 2번이다.
- [ ] 측정 뒤 repository 목록을 다시 기록하고, 집계가 검증을 통과해 결과를 낸다.
