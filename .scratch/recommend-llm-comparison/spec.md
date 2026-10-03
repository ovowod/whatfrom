# M1-b 추천 단계의 LLM 비교

- Status: ready-for-agent
- roadmap: `docs/roadmap.md` Phase 3 M1 (공통 규칙, M1-b)
- 근거: `docs/research/2026-10-03-m1-llm-candidates.md`, `docs/research/2026-10-03-m1b-recommend-stage.md`
- 이어받는 실험: `eval/experiments/2026-10-03-plan-llm-comparison/`

## Problem Statement

추천 단계는 kimi-k3 `max`로 돌고, 기준선에서 요청 지연의 대부분을 차지한다(`seconds_advise` 중앙값 약 45초).
더 빠른 모델을 써도 추천 정확도가 유지되는지 알 수 없다.

M1-a 도구는 조건 추출 단계만 잰다. 추천 단계를 재려면 다음이 더 필요하다.

- 모든 설정이 같은 검색 조건, 후보, 근거를 받는다는 보장
- fake embedder로 잘못 도는 것을 막는 검사
- Gemini 429를 피할 요청 간격
- Anthropic native provider(F15)의 첫 실제 호출

## Solution

`kimi-max-r1`의 검색 조건을 `eval --plans`로 고정하고, 설정마다 추천 단계만 잰다.
정확도 하한을 넘은 설정 중 공통 규칙(성능 다음 비용)으로 하나를 고른다.
마지막으로 M1-a에서 고른 설정과 조합해 전체 평가로 완료를 판정한다.

1. **eval 요청 간격:** eval CLI에 문항 사이 대기 option을 더한다.
2. **측정 도구:** 새 실험 폴더에 사전 확인, 측정, 집계를 만든다. M1-a 도구는 고치지 않는다.
3. **사전 확인:** 설정마다 입력이 가장 큰 문항으로 한 번 부른다.
4. **측정:** 회차마다 하루(KST) 안에 kimi-max부터 잰다.
5. **완료 판정:** 두 단계 조합을 전체 평가로 돌리고, 통과하면 `.env.example`과 README를 바꾼다.

kimi-k3를 유지하는 결론도 될 수 있다.

## Implementation Decisions

### 측정 설정

모든 설정은 추천 단계(`WHATFROM_RECOMMEND_LLM_*`)에만 넣는다. 조건 추출 단계는 `--plans`라 부르지 않는다.

| 이름 | API | 모델 | `EXTRA_BODY` | 키 환경 변수 |
| --- | --- | --- | --- | --- |
| kimi-max (기준선) | openai_compatible | `kimi-k3` | 비움 (운영과 같음) | `MOONSHOT_API_KEY` |
| kimi-low | openai_compatible | `kimi-k3` | `{"reasoning_effort": "low"}` | `MOONSHOT_API_KEY` |
| luna-none | openai_compatible | `gpt-6-luna` | `{"reasoning_effort": "none"}` | `OPENAI_API_KEY` |
| luna-low | openai_compatible | `gpt-6-luna` | `{"reasoning_effort": "low"}` | `OPENAI_API_KEY` |
| sol-low | openai_compatible | `gpt-6.1-sol` | `{"reasoning_effort": "low"}` | `OPENAI_API_KEY` |
| grok-none | openai_compatible | `grok-4.3` | `{"reasoning_effort": "none"}` | `XAI_API_KEY` |
| grok-low | openai_compatible | `grok-4.3` | `{"reasoning_effort": "low"}` | `XAI_API_KEY` |
| gemini-minimal | openai_compatible | `gemini-3.5-flash-lite` | `{"extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}}` | `GEMINI_API_KEY` |
| gemini-low | openai_compatible | `gemini-3.5-flash-lite` | `{"extra_body": {"google": {"thinking_config": {"thinking_level": "low"}}}}` | `GEMINI_API_KEY` |
| sonnet-min | anthropic | `claude-sonnet-5-5` | `{"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}` | `ANTHROPIC_API_KEY` |
| sonnet-low | anthropic | `claude-sonnet-5-5` | `{"output_config": {"effort": "low"}}` | `ANTHROPIC_API_KEY` |

- base URL과 가격은 조사 노트를 따른다. Anthropic은 `https://api.anthropic.com/v1`, $2 / $10이다.
- timeout(120초)과 재시도(429·5xx 최대 2회)는 운영과 같다.

### 입력 고정과 결과 검증

- 모든 측정은 `--plans eval/experiments/2026-10-03-plan-llm-comparison/results/kimi-max-r1.json`으로 돈다.
- 측정 도구는 `--embedder`와 `--llm-provider`를 항상 명시한다. 둘 다 기본값이 `fake`다.
- 결과를 실험 폴더에 복사하기 전에 아래를 확인한다. 하나라도 어긋나면 복사하지 않는다.
  - meta의 모드가 `fixed-plans`이고 `plans_from`이 `kimi-max-r1.json`이다.
  - meta의 `embedder`, `embedding_model`이 `kimi-max-r1`과 같다.
  - meta의 추천 단계 base URL, 모델, `EXTRA_BODY`, API 종류가 측정 설정과 같다.
  - golden set hash가 `kimi-max-r1`과 같다. 측정한 문항이 40문항 전부다.
  - 문항별 `advise_prompt`가 `kimi-max-r1`과 글자 그대로 같다. 어긋나면 어긋난 문항 ID를 알려 준다.
    prompt에는 후보 이름 말고도 platform, push 날짜, 근거 본문이 들어가므로 후보 목록 비교로는 부족하다.
- 집계도 같은 검사를 다시 하고, 어긋나는 파일이 있으면 표를 만들지 않는다.
- 측정 동안 수집과 색인을 돌리지 않는다.
- 회차마다 첫 측정 전과 마지막 측정 뒤에 snapshot을 남긴다. 내용은 repository 목록과 golden set 허용 정답 태그의 digest다.
  둘이 다르면 그 회차 전체가 무효다. 채점은 측정 시점 DB의 digest로 이름이 다른 같은 이미지를 정답으로 치므로, prompt 검사로는 이 변화를 잡지 못한다.

### 기준선과 측정 시기

- `kimi-max-r1`의 추천 단계 결과로는 비교하지 않는다. kimi-max를 이 실험에서 다시 잰다.
- 한 회차의 모든 설정은 하루(KST) 안에 이어서 잰다. 회차는 kimi-max부터 시작한다.
- 결과 기록에 설정마다 측정 시작 시각을 남긴다.

### 요청 간격

- eval CLI에 문항 사이 대기 option을 더한다. 기본값은 0이다.
- 대기 시간은 `seconds_total`과 단계별 시간에 넣지 않는다.
- 측정 도구는 설정마다 간격을 갖고, 기본은 0이다.
- Gemini의 간격은 측정 전에 사람이 AI Studio에서 `gemini-3.5-flash-lite`의 RPM 한도를 보고 `60 / RPM × 2`초로 정한다. 한도를 볼 수 없으면 6초로 시작한다.
  사전 확인 한 번으로는 지속 가능한 속도를 알 수 없다. M1-a에서도 17건이 성공한 뒤 429가 났다.
- 재시도가 섞여 Gemini를 다시 잴 때마다 간격을 2배로 늘린다. 다시 재는 횟수는 §집계와 고르는 기준을 따른다.
- `gemini-minimal`과 `gemini-low`는 같은 quota를 쓰므로 이어서 재지 않고, 사이에 다른 설정을 둔다.

### 사전 확인

- 설정마다 `temurin-17-jammy-pinned`(입력 약 20K token)로 추천 단계를 한 번 부른다. 평가와 같은 provider 코드와 같은 prompt를 쓴다.
  - prompt는 `kimi-max-r1`의 해당 문항 `advise_prompt`와 같은지 확인한다.
- 주입한 transport로 원본 응답을 받아 아래를 기록한다.
  - `finish_reason`(Anthropic은 `stop_reason`)
  - 원본 usage와 reasoning token
  - schema 검증 결과
  - 실패하면 응답 본문
- 출력이 잘리거나(`length`, `max_tokens`) 거부되면(`refusal`) 그 설정은 측정하지 않는다. 원인을 기록하고 사람이 판단한다.
- 일시적 실패와 영구적 실패의 구분과 재시도 규칙은 M1-a와 같다.
- 이 호출이 새 schema의 첫 요청 지연도 흡수한다.

### 집계와 고르는 기준

- 정확도 하한은 추천 정확도 35/40 이상이다.
  - 태그 실재율, 출처 제공률, verify 거부는 기록만 하고, 기준선보다 나빠진 설정을 표시한다.
  - 하한 ±2문항(33~37) 안의 설정은 한 번 더 재고 회차 평균으로 판정한다. 평균이 하한과 1문항 안이면 회차를 더 늘릴지는 사람이 정한다.
- 지연은 `seconds_advise`의 p50, p95로 비교한다.
  - 5% 기준의 바탕은 `kimi-max-r1`의 `seconds_total` p95이고, 상수다.
  - 참고로 `kimi-max-r1`의 `seconds_advise` p95에 M1-a에서 고른 `luna-low`의 `seconds_plan` p95를 더한 값을 바탕으로 한 판정도 기록한다.
- 재시도가 섞인 문항 수를 보여 주고, 그 설정의 지연을 신뢰할 수 없다고 표시한다.
  - 재시도가 섞인 설정은 재시도 없는 확인 측정을 설정마다 최대 2번 한다. 그래도 섞이면 그 설정은 지연 판정에서 뺀다.
  - 지연(p50, p95)은 재시도가 없는 회차만으로 계산한다. 정확도는 모든 회차의 평균이다.
- 비용은 공급자별 token 보정 규칙(M1-a와 같음)으로 40문항 비용을 계산한다. 알 수 없으면 "알 수 없음"으로 둔다.
- 판정은 M1-a의 `choose()` 구조를 따른다. 하한만 기준선 상대값이 아니라 고정값 35다.
  - `incomplete`: kimi-max가 없다.
  - `needs_runs`: 하한 ±2문항 안이거나 재시도 확인이 남은 설정이 있다.
  - `inconclusive`: 비용을 비교해야 하는 그룹에 비용을 모르는 설정이 있다. 사람이 정한다.
  - 선택: 위에 해당하지 않으면 공통 규칙으로 하나를 고른다.

### 완료 판정

- 조건 추출 단계 `luna-low`와 추천 단계의 고른 설정으로 전체 평가를 한 번 돌린다.
- repository 추출 35, 조건 추출 38, 추천 정확도 35를 모두 넘으면 통과다.
- 통과하면 `.env.example`의 PLAN/RECOMMEND 예시와 README를 고른 설정으로 바꾼다. 코드 기본값은 바꾸지 않는다.
- 통과하지 못하면 운영 설정을 바꾸지 않고, 원인을 결과 기록에 남긴다.

## Out of Scope

- golden set 수정(`alpine-static-binary` 라벨 등). M1-b 뒤에 한다.
- 운영 코드에서 429 본문, `retry-after`, `finish_reason`을 다루는 일. F11-b에서 설계한다.
- M1-a 실험 도구와 결과 수정.

## Further Notes

- 결과는 `eval/experiments/<측정일>-recommend-llm-comparison/`에 남긴다.
- 실험 README에 코드 commit, golden set hash, 설정, 회차별 측정 시각, 사전 확인 결과, 배제한 설정과 이유를 적는다.
- 끝나면 roadmap의 M1-b를 완료로 바꾸고 고른 설정을 적는다.

## Review Log

### 2026-10-04: Codex review
- ✅ 후보가 같아도 입력이 같다는 보장이 없다: `candidate_images` 검사를 문항별 `advise_prompt` 검사로 바꿨다.
- ✏️ 정확도 채점이 고정되지 않은 DB 상태에 달려 있다: DB snapshot 대신 회차 전후 snapshot(repository 목록, 허용 정답 태그 digest)으로 바꿨다. kimi-max를 같은 시기에 다시 재므로 측정하는 동안만 DB가 같으면 된다.
- ✅ 사전 확인 한 번으로는 요청 간격을 정할 수 없다: AI Studio의 RPM 한도로 간격을 계산하고, 재시도가 섞이면 간격을 2배로 늘려 다시 재며, Gemini 두 설정을 이어서 재지 않게 했다.
- ✅ 지연을 믿을 수 없거나 비용을 모를 때의 판정이 없다: M1-a `choose()`의 결과 네 가지를 따르고, 재시도 확인을 모든 설정 최대 2번으로 했다. Gemini의 다시 재기 규칙도 여기에 합쳤다.
