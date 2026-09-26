# 부하 기준선 (2026-09-26)

README "부하 측정" 절의 기준 측정 결과에 쓴 원본이다. `load/results/`는 실행마다 생겨 Git에서 제외하므로, 기준선으로 쓴 결과만 여기 남긴다.

| 파일 | 내용 |
| --- | --- |
| `2026-09-26T04-40-55-577Z-kimi-x1.0.json` | k6 결과, Kimi 실측 분포(모의 LLM 배율 1.0). 가상 사용자를 미리 모두 만들도록 고친 뒤의 재측정 |
| `2026-09-26T04-22-07-175Z-fast-x0.1.json` | k6 결과, 빠른 LLM 분포(배율 0.1) |
| `kimi-x1.0-prometheus.json` | Kimi 실측 실행의 서버 지표. Prometheus에서 조회한 PromQL과 결과를 그대로 저장했다 |
| `fast-x0.1-prometheus.json` | 빠른 LLM 실행의 서버 지표. 원본 시계열은 다음 측정 전 초기화로 지워져, 측정 직후 조회한 값을 옮겨 적었다 |
| `replay-kimi-k3.json` | 모의 LLM 입력. 유료 평가 결과(`eval/results/2026-09-24T07-22-39.json`, .gitignore 대상)에서 재생에 쓰는 다섯 필드만 뽑았다 |

- 모의 LLM 입력: `load/baselines/2026-09-26/replay-kimi-k3.json`(출처는 kimi-k3 유료 평가 `eval/results/2026-09-24T07-22-39.json`), 질문 순서 `load/questions.json`(시드 0)
- 코드: 브랜치 `feat/load-baseline`, k6 `grafana/k6:2.3.0`, Prometheus `v3.13.3`
- 첫 Kimi 실측 실행(`04-08-27-605Z`)은 k6가 스파이크 78건, 회복 21건을 보내지 못해 기준선에서 뺐다.
