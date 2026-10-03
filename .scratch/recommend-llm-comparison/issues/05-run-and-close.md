# 05: 측정 수행과 완료 판정

**What to build:** 실제 API로 사전 확인과 측정을 돌려 추천 단계 설정을 고른다. 그다음 두 단계 조합을 전체 평가로 확인한다. 유료 키가 필요해 사람이 한다. spec §기준선과 측정 시기, §완료 판정, Further Notes.

**Blocked by:** 01, 02, 03, 04

**Status:** ready-for-human

- [ ] 준비: Kimi, OpenAI, xAI, Gemini, Anthropic 키와 실제 embedder, 수집과 색인이 끝난 DB.
- [ ] 측정 동안 수집과 색인을 돌리지 않는다.
- [ ] AI Studio에서 Gemini RPM 한도를 보고 요청 간격(`60 / RPM × 2`초, 볼 수 없으면 6초)을 정한다.
- [ ] 11개 설정의 사전 확인을 모두 돌린다. 잘림이나 거부가 나온 설정은 측정하지 않는다.
- [ ] 회차마다 측정 전후 snapshot을 남긴다.
- [ ] 1회차를 하루(KST) 안에 kimi-max부터 잰다. Gemini 두 설정은 이어서 재지 않는다.
- [ ] 집계가 알려 준 설정을 다시 잰다. Gemini는 다시 잴 때마다 간격을 2배로 늘린다. 다른 날이면 kimi-max부터 다시 잰다.
- [ ] `luna-low` + 고른 설정으로 전체 평가를 한 번 돌려 세 하한을 확인한다.
- [ ] 통과하면 `.env.example`과 README를 바꾼다. 실험 README와 roadmap을 갱신한다.
