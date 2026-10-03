# 조건 추출 단계의 LLM 비교 (M1-a, 2026-10-03)

**결론: 조건 추출 단계에 `gpt-6-luna` + `reasoning_effort: low`(luna-low)를 쓴다.**
기준선 말고 정확도 하한을 넘은 설정은 luna-low 하나다.

- 기준선 kimi-k3(`max`)보다 조건 추출 단계 p95가 24.1초에서 4.6초로 줄고, 40문항 비용은 $0.303에서 $0.0075로 준다.
- 정확도는 5회 평균 repository 추출 35.4, 조건 추출 38.2로 하한(35, 38)을 넘는다. 조건 추출은 0.2문항 차이로 넘었다.
- kimi-low와 sol-low는 조건 추출이, luna-none은 repository 추출이 하한에 못 미쳤다.

운영 설정은 아직 바꾸지 않는다. M1-b가 끝나면 두 단계 조합을 전체 평가로 확인한 뒤 바꾼다.

정확도에 대해서는 "기준선보다 나쁘다는 증거가 없다"까지만 말할 수 있다. 상위 설정 사이의 1~3문항 차이는 회차 사이의 흔들림 안에 있다.

## 조건

| 항목 | 값 |
| --- | --- |
| 코드 | `a2cde46`. 이 commit에 luna-low·sol-low 측정 설정과, 기준선에 실제 embedder를 쓰는 수정을 더한 상태(commit 전)로 쟀다 |
| golden set | version 1, `fe366444`, 40문항, 출처 확인일 2026-09-12 |
| repository 목록 | `repositories-before.json`과 `repositories-after.json`이 같다. 10개, 색인 문서 6~20개 |
| embedding | 기준선 전체 평가는 운영과 같은 bge-m3(로컬 ollama). 나머지는 조건 추출 단계만 돌아 embedding을 쓰지 않는다 |
| 측정 시각 | 2026-10-03 08:27~12:19 UTC 시작. 사전 확인 08:25(추가 설정은 11:30) |
| timeout, 재시도 | 120초, 429·5xx 최대 2회. 운영과 같다 |
| 절차 | `.scratch/plan-llm-comparison/spec.md`(merge 전에 지우므로 git history에 있다), 도구는 `experiment.py`, 집계 규칙은 `plan_llm.py` |

| 측정 설정 | 모델 | `EXTRA_BODY` | 모드 | 1M token당 가격 (입력 / 출력) |
| --- | --- | --- | --- | --- |
| kimi-max (기준선) | `kimi-k3` | 없음(기본값 `max`) | 전체 평가 | $3.00 / $15.00 |
| kimi-low | `kimi-k3` | `{"reasoning_effort": "low"}` | `--plan-only` | $3.00 / $15.00 |
| luna-none | `gpt-6-luna` | `{"reasoning_effort": "none"}` | `--plan-only` | $0.10 / $0.50 |
| grok-none | `grok-4.3` | `{"reasoning_effort": "none"}` | `--plan-only` | $1.25 / $2.50 |
| gemini-minimal | `gemini-3.5-flash-lite` | `{"extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}}` | `--plan-only` | $0.30 / $2.50 |
| luna-low (추가) | `gpt-6-luna` | `{"reasoning_effort": "low"}` | `--plan-only` | $0.10 / $0.50 |
| sol-low (추가) | `gpt-6.1-sol` | `{"reasoning_effort": "low"}` | `--plan-only` | $2.00 / $10.00 |

가격은 [조사 노트](../../../docs/research/2026-10-03-m1-llm-candidates.md)의 값이다(접근일 2026-10-03). cached input 할인은 반영하지 않아 실제보다 조금 크게 나온다.

## 지표

| 지표 | 무엇을 재나 |
| --- | --- |
| repository 추출 | 추출한 repository가 문항의 필요 repository에 있는 문항 수 / 40 |
| 조건 추출 | repository를 뺀 다섯 필드(`architectures`, `distributions`, `exclude_distributions`, `version_prefix`, `max_size_mb`)가 모두 정답과 맞는 문항 수 / 40. repository는 repository 추출이 따로 잰다 |
| p50, p95 | 조건 추출 단계 소요 시간. nearest-rank, 재시도 없는 회차의 문항을 합친다 |
| 40문항 비용 | 조건 추출 단계 호출의 token × 단가. 회차 평균 |

정확도 하한은 기준선 1회차보다 2문항 낮은 값이다. 기준선이 repository 추출 37, 조건 추출 40이라 하한은 35, 38이다.

## 결과

| 측정 설정 | 회차 | repository 추출 | 조건 추출 | p50 | p95 | 40문항 비용 | 판정 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| kimi-max | 1 | 37 | 40 | 9.8초 | 24.1초 | $0.303 | 기준선 |
| **luna-low** | 5 | 35.4 | 38.2 | 2.4초 | 4.6초 | **$0.0075** | 통과, **고름** |
| kimi-low | 2 | 38 | 37.5 | 5.0초 | 8.1초 | $0.172 | 조건 추출 하한 미달 |
| sol-low | 2 | 34 | 37 | 2.1초 | 3.8초 | $0.105 | 두 지표 모두 하한 미달 |
| luna-none | 2 | 33.5 | 37.5 | 1.2초 | 2.2초 | $0.005 | 두 지표 모두 하한 미달 |
| grok-none | 1 | 22 | 34 | 0.8초 | 1.1초 | $0.050 | 하한 미달 |
| gemini-minimal | 1 | 26 | 29 | 1.1초 | 5.1초 | 알 수 없음 | 하한 미달 |

전체 표(token, reasoning token, `usage` 보고율, 실패 호출)는 `summary.md`에 있다. 문항별 결과는 `results/<설정>-r<회차>.json`에 있다.

회차별 값:

| 측정 설정 | repository 추출 | 조건 추출 | p95 |
| --- | --- | --- | --- |
| kimi-low | 38, 38 | 38, 37 | 8.4, 7.8초 |
| luna-low | 37, 34, 36, 36, 34 | 39, 37, 38, 38, 39 | 3.8, 4.0, 4.6, 4.2, 4.3초 |
| sol-low | 34, 34 | 37, 37 | 3.5, 3.8초 |
| luna-none | 36, 31 | 38, 37 | 2.2, 2.0초 |

기준선 전체 평가의 요청 p95는 132.7초(중앙값 58.8초)였다. 추천 단계만 p95 117.3초다. 추천 정확도는 37/40이다.

실험 전체에 쓴 비용은 알 수 있는 것만 약 $4.1다. 무효가 된 fake embedder 기준선 $1.87과 다시 잰 기준선 약 $1.59가 대부분이다. 다시 잰 기준선의 추천 단계 호출 2건이 timeout으로 끝나 token 수를 알 수 없고, gemini-minimal도 비용을 알 수 없다.

## 진행 경위

처음 계획에는 없던 측정과 규칙 변경이 있었다. 모두 앞 결과를 본 뒤에 정했다.

1. **처음 다섯 설정을 쟀다.** 하한 근처인 kimi-low와 luna-none은 2회차를 돌렸다.
2. **luna-low와 sol-low를 추가했다.** kimi-low($0.172)와 luna-none($0.005)의 비용 차이가 컸다. 그 사이를 메울 "GPT에 reasoning을 조금 준 설정"이 비교 대상에 없었다.
3. **luna-low를 3회 더 돌렸다.** 2회차 repository 추출이 34로 낮게 나와 흔들림의 폭을 보려 했다.
4. **고르는 기준을 고쳤다.** 아래 이유로 "성능이 비슷하면 비용이 낮은 쪽"으로 고쳤다.
5. **기준선을 다시 쟀다.** 처음 기준선은 `.env`에 `WHATFROM_EMBEDDER`가 없어 fake embedder로 돌았다. 검색 결과가 달라지면 추천 단계 prompt와 지연도 달라지므로, 요청 전체 p95를 쓰는 고르는 기준에 그대로 쓸 수 없었다. 실제 embedder로 다시 잰 기준선은 정확도가 1문항씩 높아(37, 40) 하한이 35, 38로 올랐다. 그 결과 kimi-low와 sol-low가 하한에 못 미쳤다. 처음 기준선은 `invalid/kimi-max-r1-fake-embedder.json`에 남겼다.

5번 전(처음 기준선, 하한 34, 37)에는 kimi-low, luna-low, sol-low가 모두 하한을 넘었다. 처음 규칙으로는 sol-low가, 고친 규칙으로는 luna-low가 골라졌다. 다시 잰 기준선에서는 규칙과 상관없이 luna-low만 남는다.

### 고르는 기준을 고친 이유

고르는 기준은 1. 성능(지연, 정확도) 2. 비용이어야 했다. 그런데 미리 정한 규칙은 이 의도를 담지 못했다.

- roadmap은 비용을 "모두 기록한다"고만 했고, spec은 비용을 지연과 정확도가 모두 같을 때만 보게 했다.
- 지연이 비슷한지는 가장 낮은 p95의 110% 안인지로 판단했다. 4초 안팎에서는 0.4초 차이로 갈린다.
- 처음 기준선에서 이 규칙대로면 sol-low(3.8초)가 luna-low(4.6초)보다 0.8초 빨라 sol-low가 골라졌다. 요청 전체로 보면 0.8초는 1% 안팎이다.
- 2번에서 설정을 추가한 이유도 비용이었다. 비용을 거의 보지 않는 규칙으로 고르면 그 이유와 맞지 않는다.

**고친 규칙**

1. 정확도 하한은 그대로다.
2. **성능이 비슷한 묶음:** 하한을 넘은 설정 중 가장 빠른 p95와의 차이가 기준선 요청 전체 p95의 5% 안이면 묶는다.
   - 단계 지연이 아니라 요청 전체를 잣대로 삼는다. 사용자가 느끼는 것은 요청 전체 시간이다.
   - 다시 잰 기준선의 요청 p95가 132.7초라 폭은 6.6초다.
3. **묶음 안에서 고르기:** 비용이 가장 낮은 설정을 고른다. 가장 싼 비용의 110% 안에 드는 설정이 여럿이면 정확도 합이 높은 쪽을 고른다. 묶음에 설정이 여럿이고 그중 비용을 모르는 설정이 있으면 결론 없음이다. 묶음에 설정이 하나뿐이면 성능이 먼저라 비용을 몰라도 그 설정을 고른다.

이번 결과에서 하한을 넘은 설정은 kimi-max(24.1초)와 luna-low(4.6초)다. luna-low에서 6.6초 안에는 luna-low만 들어 그대로 골라진다.

5%와 10%는 결과를 본 뒤 정했다. 단계 지연 대비 비율로 숫자를 고르면 결과에 맞춰 고르게 되므로(luna-low는 sol-low보다 21% 느리다), 잣대를 요청 전체 시간으로 바꾸고 그 위에서 사용자가 정했다. M1-b에는 이 규칙을 측정 전에 적용한다.

## 해석과 한계

**repository 추출의 흔들림은 대부분 애매한 문항에서 나온다.** 처음 기준선을 포함한 11개 회차의 실패를 모으면 다음과 같다.

- `alpine-static-binary`는 모든 설정이 모든 회차에서 틀렸다. 질문에 alpine이 나오지 않는데 정답이 `alpine`이다. golden set 라벨을 다시 봐야 한다.
- 나머지 실패는 대부분 모델이 repository를 비운 경우다. 질문이 repository를 도구나 언어로만 암시한다(pandas → python, esbuild → node, Gradle → eclipse-temurin, Go 코드 → golang). 이런 문항 대여섯 개가 회차마다 맞고 틀린다.
- repository 추출이 비어도 추천은 계속된다. 검색이 repository로 거르지 않을 뿐이다. 최종 추천에 주는 영향은 M1-b 완료 판정의 전체 평가가 잰다.

**그 밖의 한계**

- 정확도 하한은 기준선 1회차로 정했다. 기준선을 다시 재자 정확도가 1문항씩 달라졌고, 그것만으로 kimi-low와 sol-low의 판정이 바뀌었다. 하한 근처의 판정은 그만큼 흔들린다.
- luna-low의 조건 추출(38.2)은 하한(38)을 0.2문항 차이로 넘었다.
- kimi-low, sol-low는 2회만 쟀다.
- grok-none은 repository 추출이 22로 특히 낮다. 원인은 보지 않았다.
- gemini-minimal은 429로 호출 6건이 실패했다. 6건을 모두 맞혔다고 해도 32 / 35로 하한 미달이다. Gemini의 token 규약은 사전 확인에서 판별되지 않아 비용을 알 수 없다.

## 사전 확인

다섯 설정과 추가 두 설정 모두 HTTP 200이었다(`precheck.jsonl`). strict `json_schema`의 `"default": null`을 거부한 공급자가 없어 schema는 고치지 않았다.

원본 `usage`에서 확인한 것:

- kimi-k3는 `completion_tokens_details.reasoning_tokens`를 보고하고, `completion_tokens`가 reasoning을 포함한다(927 + 321 = 1248, reasoning 259). 조사 노트의 공식 문서 예시와 다르다.
- grok-4.3의 `cost_in_usd_ticks`는 1 tick = 1e-10 USD로, cached input 할인을 반영한 계산과 맞았다.
- gemini-3.5-flash-lite는 reasoning 필드가 없고 total이 prompt + completion과 같았다. 규약을 판별할 근거가 아니다.

## M1-b로 넘기는 것

- 검색 조건 고정 입력: `results/kimi-max-r1.json`(전체 모드, 실제 embedder, golden set `fe366444`).
- 같은 파일의 추천 단계 지표를 kimi-k3 기준선 참고값으로 쓸 수 있다. 추천 정확도 37/40, 문서 Hit@5 33/40, 추천 단계 p95 117.3초이고, 추천 단계 호출 2건이 timeout이었다.
- 고르는 기준은 위 "고친 규칙"을 쓴다. 5% 폭은 M1-b의 기준선 요청 p95로 다시 계산한다.
- 측정 도구는 `--embedder`를 지정하고, 결과 meta의 embedder를 검증한다. 이 실험의 `measure`는 그렇게 고쳤다.

## 다시 돌리기

이 폴더는 이번 실험의 기록이다. 측정 전후 repository 목록과 결과 파일이 이미 있어, 이 폴더에서는 새로 잴 수 없다(같은 파일과 회차 번호를 덮어쓰지 않는다).
다시 재려면 `experiment.py`와 `plan_llm.py`를 새 날짜 폴더(`eval/experiments/<날짜>-<주제>/`)로 복사해 그 폴더에서 돌린다. 경로는 모두 script가 있는 폴더 기준이다.

project root에서, `.env`나 셸 환경 변수에 공급자 API key를 두고:

```bash
EXP=eval/experiments/<새 폴더>
uv run python $EXP/experiment.py precheck all
uv run python $EXP/experiment.py snapshot before
uv run python $EXP/experiment.py measure <설정> <회차>
uv run python $EXP/experiment.py snapshot after
uv run python $EXP/experiment.py summary
```

## Review Log

### 2026-10-03: Codex review
- ✅ "비슷한 성능"의 폭이 fake embedder로 돈 기준선에서 나옴: 기준선을 실제 embedder로 다시 잰다. `measure`가 전체 평가에 `--embedder openai_compatible`을 넘기고, 집계가 기준선의 embedder를 검증한다. fake로 돈 결과는 `invalid/`로 옮겼다. 제대로 돌지 않은 기준선으로 판단하면 안 된다.
- ✅ 조건 추출의 정의가 코드와 다름: repository를 뺀 다섯 필드로 정의를 고쳤다. 정의가 코드와 다르면 안 된다.
- ✏️ 비용을 모를 때의 규칙이 문서와 코드에서 다름: 코드가 아니라 문서를 고쳤다. 묶음에 설정이 하나뿐이면 성능이 먼저라 비용을 몰라도 고른다.
- ✏️ 다시 돌리기 명령으로 새 실험을 시작할 수 없음: 옵션을 더하지 않고, 두 script를 새 날짜 폴더로 복사해 돌리라고 문서만 고쳤다. 일회성 실험이다.
