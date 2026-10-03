# 01: 결과 집계와 모델 선택

**What to build:** 실험 폴더의 결과 JSON과 repository 목록 기록을 넣으면, 검증을 거쳐 측정 설정별 비교 표와 고른 측정 설정(또는 미완료·결론 없음)이 나온다. 네트워크를 쓰지 않는다. spec §결과 검증, §회차 합치기, §지표, §출력 token 보정, §고르는 기준.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [x] 실험 폴더(`eval/experiments/<측정일>-plan-llm-comparison/`)와 집계 테스트를 둘 위치를 정한다. 테스트가 CI에서 돈다.
- [x] fake provider, 조건 추출 단계 설정 불일치, 모드 불일치(kimi-max는 `full`, 나머지는 `plan-only`), golden set hash 불일치, 문항 집합 불일치, 측정 전후 repository 목록 불일치 중 하나라도 있으면 표를 만들지 않고 이유를 밝히며 멈춘다.
- [x] kimi-max 결과가 없으면 모델을 고르지 않고 미완료로 보고한다.
- [x] 전체 평가 결과에서는 조건 추출 단계 호출 기록만 쓴다.
- [x] p50·p95를 nearest-rank로 계산하고, 재시도 섞인 회차는 지연에서 뺀다.
- [x] 회차 합치기 표대로 일치율과 비용을 회차 평균으로 내고, 실험 전체 비용은 따로 낸다.
- [x] 공급자별 출력 token 보정을 적용하고, token 수가 None인 호출이 있으면 비용을 "알 수 없음"으로 두며 `usage` 보고율을 함께 낸다.
- [x] 정확도 하한, 동점 묶음(`p95 ≤ 최소 p95 × 1.10`), 묶음 안 고르기, 재시도 확인 규칙, 후보 없음 처리를 spec대로 따르고, 입력 순서를 바꿔도 같은 답을 낸다.
- [x] 위 규칙마다 작은 fixture로 테스트한다. `make lint`, `make test` 통과.
