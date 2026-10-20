# 후보 비교표와 인용을 붙인 단일 선택의 비교 (M3, 측정 전)

**상태: 측정 전.** 이 문서는 측정을 시작하기 전에 조건과 고르는 기준을 고정하려고 먼저 커밋한다.
측정한 뒤에는 결과와 판정 과정을 아래에 더하고, 이 절의 조건과 기준은 고치지 않는다.

검토 단계(두 번째 LLM 호출)를 단일 선택으로 둘지, 후보 비교표(판정 단계)로 바꿀지 정한다([ADR 0003](../../../docs/adr/0003-judge-candidates-instead-of-recommending.md)).
두 안을 같은 입력, 같은 모델, 같은 인용 지시로 재서 차이가 출력 구조에서 왔다고 말할 수 있게 한다.

## 비교하는 두 안

| 안 | 검토 단계 | eval option |
| --- | --- | --- |
| 단일 선택 | 후보 하나를 고르고 이유를 주장 목록으로 낸다. 판단 요구를 받고, 주장에 쓴 문서 사실마다 인용한다 | `--review single` |
| 판정 단계 | 모든 후보 × 판단 요구 칸에 만족, 불만족, 알 수 없음과 인용 목록을 낸다. 서버가 정규화하고 세 등급으로 순위를 정해 확인됨 1위를 추천한다 | `--review judge` |

- 판정 단계는 판단 요구가 없는 문항에서 LLM을 부르지 않고 검색 1위를 추천한다. 그래서 지표를 판단 요구가 있는 20문항과 없는 20문항으로 나눠 본다.
- 판정 칸의 인용이 하나라도 검증에 실패하면 그 칸은 알 수 없음이다. 확인됨 후보가 없으면 추천이 없고 오답으로 센다.

## 조건

| 항목 | 값 |
| --- | --- |
| 입력 | label 고정 입력(`eval --label-plans`). 조건 추출 단계를 부르지 않는다 |
| golden set | version 2, `a6e8db88`, 40문항(판단 요구 있음 20, 없음 20), 출처 확인일 2026-09-12. 고치지 않는다 |
| 검토 단계 모델 | 두 안 모두 `gpt-6-luna`, `{"reasoning_effort": "none"}`(luna-none). V2 첫 측정과 같다 |
| embedding | bge-m3(로컬 ollama). V2 첫 측정, 운영과 같다 |
| timeout, 재시도 | 120초, 429·5xx 최대 2회. 운영과 같다 |
| 회차 | 두 안을 같은 날(KST) 2회씩 돌린다. 회차마다 단일 선택부터 돌린다 |
| 코드 | 측정 시점의 commit을 결과에 적는다 |

측정 설정은 다음 환경 변수로 준다.

```bash
WHATFROM_RECOMMEND_LLM_BASE_URL=https://api.openai.com/v1
WHATFROM_RECOMMEND_LLM_MODEL=gpt-6-luna
WHATFROM_RECOMMEND_LLM_EXTRA_BODY='{"reasoning_effort": "none"}'
```

## 절차

1. **사전 확인.** 판정 칸이 가장 많은 문항(`temurin-jdk-build-stage`, `python-latest-stable`: 후보 20개 × 판단 요구 2개)으로 판정 단계를 한 번 부른다. 응답의 `finish_reason`이 잘림(`length`)이 아닌지 직접 본다. OpenAI 호환 provider는 `finish_reason`을 보지 않는다. 잘리면 측정을 시작하지 않는다.
2. **측정.** 측정하는 동안 수집과 색인을 돌리지 않는다. 회차마다 아래 순서로 돌린다.

   ```bash
   uv run python -m whatfrom.cli eval --label-plans --review single --embedder openai_compatible --llm-provider openai_compatible --results-dir eval/experiments/2026-10-20-m3-review-structure/results/single-r1
   ```

   ```bash
   uv run python -m whatfrom.cli eval --label-plans --review judge --embedder openai_compatible --llm-provider openai_compatible --results-dir eval/experiments/2026-10-20-m3-review-structure/results/judge-r1
   ```

   `--embedder`와 `--llm-provider`의 기본값은 `fake`라서 꼭 준다. 2회차는 `-r2` directory로 같은 두 명령을 돌린다.
3. **유효성 확인.** 아래 중 하나라도 어긋나면 그 실행을 무효로 두고 다시 잰다.
   - 네 실행 모두 40문항을 재고 건너뛴 문항이 0이다.
   - 네 실행의 문항별 후보 목록과 근거 목록(번호, repository, section)이 같다.
   - meta의 golden set hash, embedder, 검토 단계 설정이 위 조건과 같다.
4. **인용 검토 표본.** 두 안의 1회차 결과를 한 표본에 섞어 뽑는다. 50개가 안 되는 안은 같은 안의 2회차 결과로 채운다.

   ```bash
   uv run python -m whatfrom.cli eval sample-citations <single-r1>,<single-r2> <judge-r1>,<judge-r2> --requirements-only --per-case 3 --seed 0 --out-dir eval/experiments/2026-10-20-m3-review-structure/citations
   ```

   표본 파일 머리 주석의 절차로 표본에서 인용이 진술에 맞게 붙었는지 검토하고, `score-citations`로 집계한다.

## 고르는 기준

측정 전에 정했다(spec, 2026-10-17 Codex review 두 번 반영).

1. **단일 선택이 기본값이다.** 판정 단계는 아래 2와 3을 모두 만족할 때만 고른다.
2. **인용 뒷받침률:** 다음 중 하나다.
   - 판정 단계가 합격선(90%)을 넘고 단일 선택은 넘지 못한다.
   - 판정 단계가 단일 선택보다 20%p 넘게 높다. 20%p는 표본 50개씩일 때 두 비율 차이의 대략적인 95% 오차다.

   어느 한쪽의 채점 가능한 진술이 30개 미만이거나, 채점 결과가 보류나 미측정이면 이 조건을 만족하지 못한 것으로 본다.
3. **추천 정확도:** 판단 요구가 있는 20문항에서, 판정 단계의 2회 평균이 단일 선택의 2회 평균보다 2문항 넘게 낮지 않다.
4. **판단 요구가 없는 문항의 정확도는 판정에 쓰지 않는다.** 판정 단계를 고르면 V3에서 이 문항들을 단일 선택으로 넘기는 혼합 구조로 처리한다.
5. **지연과 비용은 기록만 한다.**

## 기록할 것

- 묶음별(전체, 요구 있음, 요구 없음) 추천 정확도(2회 평균), 검색 1위 대조군, 판정 단계의 참고 정확도
- 인용 뒷받침률, 인용 검증 통과율, 후보 정보 오류
- 판정 단계의 정규화 이유별 칸 수, 오답 적발률, 오판정률, 판정 실패 문항 수
- 인용 길이 분포
- 판정 단계의 출력 token, 묶음별 검토 단계 p95, 비용

## 미리 알려진 한계

- luna-none 한 설정으로만 잰다. 판정 단계처럼 칸이 많은 출력은 reasoning이 꺼져 있으면 불리할 수 있다. 이 결과는 모델 선택 근거가 아니다(M2에서 고른다).
- 판정 칸 진술은 고정 형식("…가 요구 '…'를 만족한다")이라, 이름을 가려도 형식으로 안을 알아볼 수 있다. 채점 기준이 "진술에 쓰인 문서 사실이 인용으로 확인되는가"라서 편향 위험은 작다고 본다.
- 20%p 기준은 독립 표본을 가정한 오차다. 한 문항에서 여러 진술을 뽑으므로 실제 오차는 더 클 수 있다.
- 단일 선택의 prompt가 V2 첫 측정과 다르다(판단 요구와 인용 지시). V2의 인용 뒷받침률 60.5%와 직접 비교하지 않는다.

## 사전 확인 결과 (2026-10-20)

판정 칸이 가장 많은 두 문항으로 판정 단계를 한 번씩 불렀다. 둘 다 잘리지 않았고, 40칸을 모두 냈다.

| 문항 | 후보 × 판단 요구 | `finish_reason` | 낸 칸 | 입력 / 출력 token |
| --- | --- | --- | --- | --- |
| `temurin-jdk-build-stage` | 20 × 2 | `stop` | 40 | 2,102 / 1,028 |
| `python-latest-stable` | 20 × 2 | `stop` | 40 | 3,473 / 4,478 |
