# 추천 단계의 LLM 비교 (M1-b, 2026-10-04)

**결론: 추천 단계에는 `gpt-6-luna` + `reasoning_effort: none`(luna-none)을 고른다. 다만 운영 설정은 아직 바꾸지 않는다.**

- 추천 정확도는 2회 평균 37로 kimi-k3 `max`와 같다. 추천 단계 p95는 87.9초에서 3.4초로, 40문항 비용은 약 $1.33에서 $0.018로 준다.
- 고른 기준은 고른 이미지가 정답인지뿐이다. Dockerfile 초안의 질은 재지 않았고, 결과 파일을 보면 luna-none의 Dockerfile이 kimi-k3보다 허술하다(§해석과 한계).
  Dockerfile 품질 기준을 정하고 이 결과 파일로 다시 채점한 뒤 운영 설정을 바꾼다.

**완료 판정:** 두 단계 조합을 전체 평가로 확인했다.

- M1-a에서 고른 조건 추출 단계 `luna-low`와 조합하면 통과하지 못했다. 2회 평균 repository 추출이 33.5로 하한 35 아래다.
- 조건 추출 단계를 운영 설정(kimi-k3 `max`)으로 두고 추천 단계만 luna-none으로 바꾼 조합은 통과했다. 요청 전체 p95는 132.7초에서 20.9초다.
- 그래서 M1-a의 "조건 추출 단계에 luna-low를 쓴다"는 결론은 이 판정으로 뒤집혔다(§완료 판정).

## 조건

| 항목 | 값 |
| --- | --- |
| 코드 | 1회차 `72c8b1f`, 2회차 `45a1fdf`. 그 사이 commit은 Gemini 비용 규칙과 측정 script뿐이라 측정 경로는 같다 |
| golden set | version 1, `fe366444`, 40문항, 출처 확인일 2026-09-12 |
| 고정 입력 | M1-a의 `results/kimi-max-r1.json`. 검색 조건을 `eval --plans`로 고정하고, 결과마다 문항별 추천 단계 prompt가 이 파일과 글자 그대로 같은지 확인했다 |
| DB | 회차마다 전후 snapshot이 같다. repository 10개, golden set 허용 정답 태그 262개의 digest |
| embedding | bge-m3(로컬 ollama). 운영과 같다 |
| 측정 시각 | 사전 확인 00:27 UTC. 1회차 00:31~02:33, 2회차 02:43~05:22 UTC 시작. 모두 2026-10-04 KST 하루 안이다 |
| timeout, 재시도 | 120초, 429·5xx 최대 2회. 운영과 같다. 재시도가 섞인 호출은 0건이다 |
| Gemini 간격 | 문항 사이 1회차 6초, 2회차 12초. 429는 나지 않았다 |
| 절차 | `.scratch/recommend-llm-comparison/spec.md`(git history에 있다), 도구는 `recommend_experiment.py`, 판정 규칙은 `recommend_llm.py`, 회차 실행은 `run-round.sh` |

| 측정 설정 | API | 모델 | `EXTRA_BODY` | 1M token당 가격 (입력 / 출력) |
| --- | --- | --- | --- | --- |
| kimi-max (기준선) | openai_compatible | `kimi-k3` | 없음(기본값 `max`) | $3.00 / $15.00 |
| kimi-low | openai_compatible | `kimi-k3` | `{"reasoning_effort": "low"}` | $3.00 / $15.00 |
| luna-none | openai_compatible | `gpt-6-luna` | `{"reasoning_effort": "none"}` | $0.10 / $0.50 |
| luna-low | openai_compatible | `gpt-6-luna` | `{"reasoning_effort": "low"}` | $0.10 / $0.50 |
| sol-low | openai_compatible | `gpt-6.1-sol` | `{"reasoning_effort": "low"}` | $2.00 / $10.00 |
| grok-none | openai_compatible | `grok-4.3` | `{"reasoning_effort": "none"}` | $1.25 / $2.50 |
| grok-low | openai_compatible | `grok-4.3` | `{"reasoning_effort": "low"}` | $1.25 / $2.50 |
| gemini-minimal | openai_compatible | `gemini-3.5-flash-lite` | `thinking_level: minimal` | $0.30 / $2.50 |
| gemini-low | openai_compatible | `gemini-3.5-flash-lite` | `thinking_level: low` | $0.30 / $2.50 |
| sonnet-min | anthropic | `claude-sonnet-5-5` | `{"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}` | $2.00 / $10.00 |
| sonnet-low | anthropic | `claude-sonnet-5-5` | `{"output_config": {"effort": "low"}}` | $2.00 / $10.00 |

가격은 [조사 노트](../../../docs/research/2026-10-03-m1b-recommend-stage.md)의 값이다(접근일 2026-10-03).
Gemini의 과금 출력은 `completion_tokens`로 센다. 사전 확인의 원본 usage에서 `total_tokens`가 `prompt_tokens + completion_tokens`와 같았다. 문서로 확인한 사실은 아니다.

## 지표와 판정 규칙

| 지표 | 무엇을 재나 |
| --- | --- |
| 추천 정확도 | 추천 단계가 고른 이미지가 golden set의 정답 목록에 있는 문항 수 / 40. 이름이 달라도 digest가 같으면 정답이다 |
| p50, p95 | 추천 단계 소요 시간. nearest-rank, 재시도 없는 회차의 문항을 합친다 |
| 40문항 비용 | 추천 단계 호출의 token × 단가. 회차 평균. usage가 없는 호출이 하나라도 있으면 알 수 없음이다 |

- 정확도 하한은 35/40이다. 1회차가 33~37이면 한 번 더 재고 회차 평균으로 판정한다.
- 하한을 넘은 설정 중 가장 빠른 p95에서 5% 안(kimi-max-r1의 요청 전체 p95 132.7초 기준, 6.6초)이면 성능이 비슷한 것으로 보고, 그중 가장 싼 설정을 고른다.
- 참고 기준(kimi-max-r1의 추천 단계 p95에 M1-a 조건 추출 단계 luna-low의 p95를 더한 121.9초, 5%는 6.1초)으로도 같은 설정이 뽑혔다.

## 결과

`summary.md`가 전체 표다.

| 측정 설정 | 회차 | 추천 정확도 | p50 | p95 | 40문항 비용 | 판정 |
| --- | --- | --- | --- | --- | --- | --- |
| kimi-max | 2 | 37 | 36.9초 | 87.9초 | 약 $1.33 | 기준선. timeout으로 usage가 없는 호출이 있어 표에는 알 수 없음 |
| **luna-none** | 2 | 37 | 2.4초 | 3.4초 | **$0.018** | 통과, **고름** |
| luna-low | 2 | 37 | 4.2초 | 5.9초 | $0.021 | 통과. 비용이 luna-none의 110% 밖 |
| gemini-low | 2 | 37 | 1.8초 | 2.3초 | $0.063 | 통과 |
| gemini-minimal | 2 | 35.5 | 1.9초 | 2.4초 | $0.066 | 통과 |
| grok-none | 2 | 35.5 | 2.0초 | 3.1초 | $0.189 | 통과 |
| sonnet-low | 1 | 38 | 5.9초 | 7.2초 | $0.674 | 통과 |
| sonnet-min | 2 | 37 | 6.4초 | 7.8초 | $0.672 | 통과 |
| sol-low | 1 | 38 | 6.6초 | 11.2초 | $0.405 | 통과. 가장 빠른 p95에서 6.6초 밖 |
| kimi-low | 2 | 37.5 | 12.6초 | 16.1초 | $0.699 | 통과. 가장 빠른 p95에서 6.6초 밖 |
| grok-low | 2 | 35.5 | 8.6초 | 17.2초 | 알 수 없음 | 통과. 가장 빠른 p95에서 6.6초 밖 |

- 가장 빠른 p95는 gemini-low의 2.3초다. 8.9초 안에 든 7개 설정 중 가장 싼 것이 luna-none이다.
- gemini-minimal, grok-none, grok-low는 평균이 하한과 1문항 안이다. 회차를 늘려도 luna-none보다 비싸 결론이 바뀌지 않아 더 재지 않았다.

## 완료 판정

두 단계를 함께 부르는 전체 평가다. repository 추출 35, 조건 추출 38, 추천 정확도 35를 모두 넘어야 통과다. 결과 파일은 `completion/`에 있다.

| 조건 추출 단계 + 추천 단계 | 회차 | repository 추출 | 조건 추출 | 추천 정확도 | 요청 전체 p95 | 40문항 비용 | 판정 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| luna-low + luna-none | 1 | 35 | 37 | 37 | 6.8초 | $0.026 | |
| luna-low + luna-none | 2 | 32 | 39 | 37 | 6.9초 | $0.028 | |
| luna-low + luna-none | 평균 | **33.5** | 38.0 | 37 | | | **미달** |
| kimi-max + luna-none | 1 | 35 | 39 | 37 | **20.9초** | $0.335 | **통과** |

- 첫 조합의 1회차는 조건 추출이 1문항 모자랐다. 1회 결과로는 흔들림인지 가를 수 없어, M1 공통 규칙(하한 ±2문항이면 한 번 더 재고 평균)을 완료 판정에도 적용해 한 번 더 쟀다. 결과를 본 뒤 정한 것이다.
- luna-low의 repository 추출을 M1-a 5회와 합친 7회(37, 34, 36, 36, 34, 35, 32)의 평균은 34.9로 하한 아래다. M1-a의 5회 평균 35.4는 하한을 겨우 넘었던 값이다.
- 두 번째 조합은 조건 추출 단계가 운영 설정 그대로이고, M1-b에서 잰 것도 이 조합(kimi-k3 `max`의 검색 조건 + 추천 단계 교체)이다.
  요청 전체 p95 20.9초 중 조건 추출 단계가 17.6초, 추천 단계가 4.1초다. 남은 지연의 대부분은 조건 추출 단계다.

## 해석과 한계

**추천 정확도는 모델 차이를 거의 가르지 못한다.**
모든 설정이 35~38문항이고, reasoning을 켜도 꺼도 같다(luna none 37, low 37, kimi low 37.5, max 37).
20번의 측정에서 `alpine-static-binary`와 `node-arm64-graviton`은 모든 설정이 틀렸다. 실질적인 천장은 약 38문항이다.

**두 문항은 라벨이 아니라 하네스 문제다.**

- `alpine-static-binary`: 질문에 이미지 이름이 없어 조건 추출 단계가 repository를 비웠고, 벡터 검색이 temurin과 debian 문서만 가져왔다. 정답 alpine이 후보에 없었다. M1-a 기록은 라벨을 의심했지만 원인은 검색이다.
- `node-arm64-graviton`: 정답의 근거(arm64 musl 빌드는 Experimental 등급이고 릴리스 전에 테스트하지 않는다)는 `nodejs/docker-node`의 README에 있고, 색인하는 `docker-library/docs`에는 없다. 일반적인 musl 경고가 있는 절도 이 문항의 근거로 검색되지 않았다. 모델은 크기만 보고 alpine을 골랐다.
- 라벨을 모델의 답에 맞추면 틀린 추천을 정답으로 인정하게 되므로 golden set은 고치지 않는다.

**Dockerfile 초안의 질은 재지 않았다.**
채점은 고른 이미지만 보고, Dockerfile은 `FROM`이 추천 이미지인지만 검사한다. 결과 파일의 Dockerfile을 세면 차이가 보인다.

| 설정 | 빈 Dockerfile | 줄 수 중앙값 | multi-stage | `USER` |
| --- | --- | --- | --- | --- |
| kimi-max | 5/80 | 6 | 2 | 6 |
| sonnet-low | 1/40 | 6 | 3 | 7 |
| luna-none | 0/80 | 4 | 1 | 0 |
| luna-low | 0/80 | 3 | 2 | 0 |

kimi-max의 빈 Dockerfile은 timeout이나 verify가 지운 경우다.
luna-none은 빌드만 하고 실행 명령이 없거나(`golang-cgo-sqlite`), 서버 대신 import만 확인하는 `CMD`를 쓴 경우(`python-numpy-arm64`)가 있다.
Dockerfile 품질까지 기준에 넣으면 다른 설정이 뽑힐 수 있다.

**비교 규칙에 대한 관찰.**
완료 판정 2회차에서 repository 추출이 32로 떨어졌는데도 추천 정확도는 37 그대로였다. repository를 비워도 벡터 검색이 맞는 후보를 찾는 문항이 많다.
조건 추출 단계를 추출 일치율로 고르는 지금 규칙이 최종 추천의 질과 맞지 않을 수 있다. 결과를 본 뒤라 이번에는 규칙을 바꾸지 않았다.

## 사전 확인

11개 설정 모두 `temurin-17-jammy-pinned`(입력 약 20K token)에서 `ok`였다. 잘림과 거부는 없었다.
Gemini는 reasoning token을 보고하지 않는다. sonnet 두 설정의 thinking token은 0이었다. `precheck.jsonl`에 원본 usage가 있다.

## 후속 작업

- Dockerfile 품질 기준을 먼저 문서로 정하고, 이 폴더의 결과 파일로 다시 채점해 추천 단계 모델을 확정한다. 그 뒤 운영 설정을 바꾼다.
- 조건 추출 단계를 다시 고른다. kimi-k3를 빼려면 고르는 기준(추출 일치율 또는 전체 평가의 추천 정확도)을 측정 전에 정한다.
- `alpine-static-binary`의 검색과 `node-arm64-graviton`의 근거 문서를 고친다.

## 다시 돌리기

이 폴더는 이번 실험의 기록이다. snapshot과 결과 파일이 이미 있어 이 폴더에서는 새로 잴 수 없다.
다시 재려면 `recommend_experiment.py`, `recommend_llm.py`, `run-round.sh`를 새 날짜 폴더로 복사해 그 폴더 경로로 고쳐 돌린다.

project root에서, `.env`나 셸 환경 변수에 공급자 API key를 두고:

```bash
EXP=eval/experiments/<새 폴더>
uv run python $EXP/recommend_experiment.py precheck all
$EXP/run-round.sh 1
uv run python $EXP/recommend_experiment.py summary
```
