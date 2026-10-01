# 부하 기준선: 동시성 제한 (2026-09-30)

README "부하 측정"의 "기준 측정 결과 (2026-09-30, 동시성 제한)"에 쓴 원본이다.

| 파일 | 내용 |
| --- | --- |
| `2026-09-30T12-45-05-867Z-kimi-x1.0.json` | k6 결과, Kimi 실측 분포(모의 LLM 배율 1.0) |
| `2026-09-30T12-57-19-660Z-fast-x0.1.json` | k6 결과, 빠른 LLM 분포(배율 0.1) |
| `kimi-x1.0-prometheus.json` | Kimi 실측 실행의 서버 지표. `load/snapshot_prometheus.py`가 서버가 쉰 뒤 조회해 PromQL과 결과를 저장했다 |
| `fast-x0.1-prometheus.json` | 빠른 LLM 실행의 서버 지표. 같은 스크립트로 저장했다 |

- 모의 LLM 입력: `load/baselines/2026-09-26/replay-kimi-k3.json`, 질문 순서 `load/questions.json`(시드 0)
- 앱: 브랜치 `feat/admission-limit`, 추천 작업 수 상한 32(기본값), uvicorn 워커 1개
- 도구: k6 `grafana/k6:2.3.0`, Prometheus `v3.13.3`. 실행마다 앱과 모의 서버를 새로 띄우고 Prometheus를 익명 볼륨까지 새로 만들었다.
- 설계 문서의 완료 기준 다섯 개를 판정 스크립트로 확인했다(보내지 못한 요청 0, 필수 지표 존재 전제). 결과는 PASS.
