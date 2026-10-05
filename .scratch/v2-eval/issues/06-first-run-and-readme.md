# 06: 첫 실측과 README 기록

**What to build:** 확정한 golden set으로 라벨 고정 입력의 검색 1위 대조군을 재고, luna-none으로 V1의 인용 경로를 한 번 돌려 표본 도구까지 관통시킨다. 결과를 실험 기록과 README에 남기고, roadmap의 V2를 완료로 표시한다.

spec: `.scratch/v2-eval/spec.md` (첫 실측)

**Blocked by:** 01, 02, 03, 04, 05

**Status:** ready-for-human

- [ ] 라벨 고정 입력, 추천 단계 미호출로 검색 1위 대조군과 검색 지표를 잰다.
- [ ] luna-none(`gpt-6-luna`, `reasoning_effort: none`)으로 라벨 고정 입력에서 추천 단계를 한 번 돌린다. 인용 검증 통과율과 추천 정확도를 기록한다.
- [ ] strict schema 거부, 인용 0개, 도구 오류처럼 V1 경로가 실제 출력에서 깨지면 고치거나 기록한다.
- [ ] 그 결과로 표본을 뽑고 사용자가 채점해 인용 뒷받침률을 낸다.
- [ ] 결과를 `eval/experiments/<날짜>-v2-first-run/`에 남긴다(결과 JSON, 표본, 채점, README).
- [ ] 프로젝트 README에 검색 1위 대조군과 인용 지표를 요약하고, luna-none 결과가 모델 선택 근거가 아니라고 적는다.
- [ ] `make eval`이 v2 지표를 낸다. roadmap V2를 완료로 표시하고 결과 절을 적는다.
