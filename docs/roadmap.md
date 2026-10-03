# whatfrom roadmap

무엇을 어떤 순서로 만드는지 적는다. 시스템이 어떻게 생겼는지는 [설계 문서](design.md)에 있다.

## 원칙

- **수직 슬라이스(Walking Skeleton):** 얇지만 끝까지 관통하는 경로를 먼저 만든다. 그 뒤 각 feature가 그 경로의 한 구간을 깊게 판다.
- **feature 단위:** 각 feature는 1~3일 단위이고, 완료 판정이 있다.
- **자를 먼저 만든다:** 측정 harness는 그 harness로 잴 개선 작업보다 항상 먼저 온다. baseline이 없으면 개선을 증명할 수 없다.
- **매번 다시 잰다:** feature마다 eval을 다시 돌려 점수를 기록한다. 이 기록이 프로젝트 결과의 본문이 된다.

## 항목 계열

| 계열 | 뜻 | 결과를 남기는 곳 |
| --- | --- | --- |
| F | 기능을 만드는 feature | 코드, README 측정 결과 |
| M | 결정을 내리기 위한 측정 실험. 코드를 바꾸지 않을 수도 있다 | `eval/experiments/<날짜>-<주제>/` |

## 현재 위치

F10, F11의 앞부분(F11-a), F14(단계별 LLM 설정), M1-a(조건 추출 단계의 LLM 비교), F15(Anthropic native provider)까지 끝났다. 다음은 **M1-b(추천 단계의 LLM 비교)** 다.
지금 LLM(kimi-k3)은 추천 단계 한 번에 중앙값 51.9초가 걸려 전체 시간 대부분을 차지하고, 측정마다 시간 초과가 나온다.
F11-b와 F12는 고른 모델의 속도와 실패 양상을 보고 설계하므로 M1 뒤에 온다.

## Phase 0: 관통 경로 (완료)

| # | feature | 완료 판정 | 상태 |
| --- | --- | --- | --- |
| F0 | scaffolding: compose(postgres+pgvector), FastAPI, pytest, ruff | `docker compose up` 후 `/health` 200, CI 통과 | 완료 |
| F1 | 수집 v0: `python` repository의 태그·manifest | `image_tags` 200행 이상, `image_variants`에 arm64 존재 | 완료 |
| F2 | README 수집 → chunk 나누기 → 임베딩 → pgvector | CLI 유사도 검색이 "Image Variants" 섹션 반환 | 완료 |
| F3 | Walking Skeleton `/recommend` | 한국어 질문 1개 → 실재하는 태그 + 근거 응답 | 완료 (2026-09-05) |

F3 시점의 품질은 낮다. 그것이 의도다.

## Phase 1: 자를 만든다 (완료)

| # | feature | 완료 판정 | 상태 |
| --- | --- | --- | --- |
| F4 | golden set 40문항 + 채점 러너 | `make eval`이 지표 5종 report 출력 | 완료 (2026-09-14) |

baseline 숫자를 README에 기록한다. 이후 모든 feature는 이 표의 변화로 정당화된다.

## Phase 2: 검색 품질 (완료)

| # | feature | 완료 판정 | 상태 |
| --- | --- | --- | --- |
| F5 | 수집 확장: 10개 repository, rate limit·재시도·증분 수집 | 재실행 시 변경분만 갱신, 중단 후 재개 가능 | 완료 (2026-09-18) |
| F6 | 태그 파서 + 파생 컬럼 | 파서 단위 테스트 50케이스 통과 | 완료 (2026-09-19) |
| F7 | `SearchPlan` 추출(LLM #1) + SQL 빌더 | 조건 일치율 상승, fake provider로 CI 통과 | 완료 (2026-09-19) |
| F8 | 검색 결과의 섹션 다양성 | 검색 전용 모드의 문서 Hit@5 상승 | 완료 (2026-09-24) |
| F9 | Parent-Child·출처·digest·Dockerfile 생성 | 출처 제공률 100%, 태그 실재율 100% | 완료 (2026-09-24) |
| M0 | 임베딩 모델 비교 | 교체 여부 결정 | 완료 (2026-09-24) |

- **F5**는 수집 확장(F5-B)과 repository별 후보 태그 선택(F5-A)으로 나눠 했다.
- **F8**은 원래 "Hybrid 결합 + 재정렬, 완료 판정은 추천 정확도 상승"이었다. 키워드 결합과 재정렬 모델을 실험했지만 이득이 없었다.
  - 재정렬 모델은 고친 문항과 새로 놓친 문항이 상쇄됐고, 문항당 15초 이상이 더 붙었다.
  - 그래서 섹션마다 가장 가까운 chunk 하나만 돌려주는 규칙으로 바꾸고, 완료 판정도 문서 Hit@5 상승으로 바꿨다. 결과는 29/40 → 31/40이다.
  - 키워드 결합과 재정렬 모델은 F8 범위에서 뺐다. 그중 재정렬 모델은 Phase 4 후보로 둔다.
- **F9** 결과: 추천에 digest를 붙이고 `FROM`을 고정했다. 출처 제공률 40/40, 태그 실재율 39/39다(`eval/results/2026-09-24T07-22-39.json`).

**M0 (임베딩 모델 비교)**

bge-m3, qwen3-embedding 0.6B, snowflake-arctic-embed2, OpenAI text-embedding-3-small·large를 비교했다.
교체할 만큼 확실한 이득을 확인하지 못해 bge-m3를 유지한다.
제목만 보는 Hit 지표가 한 문장짜리 소개문도 성공으로 세어 근거 품질을 과대평가할 수 있다는 점도 확인했다.
자세한 내용은 [실험 기록](../eval/experiments/2026-09-24-embedding-comparison/README.md)에 있다.

## Phase 3: 백엔드 견고성 (진행 중)

| # | 항목 | 완료 판정 | 상태 |
| --- | --- | --- | --- |
| F10 | k6 부하 시나리오 + Prometheus/Grafana | 아래 참고 | 완료 (2026-09-26) |
| F11-a | 비동기 `/recommend` + 추천 작업 수 상한 | 아래 참고 | 완료 (2026-10-01) |
| F14 | 단계별 LLM 설정 + 단계별 평가 모드 | 아래 참고 | 완료 (2026-10-03) |
| M1-a | 조건 추출 단계의 LLM 비교 | 아래 참고 | 완료 (2026-10-03) |
| F15 | 단계별 API 종류 + Anthropic native provider | 아래 참고 | 완료 (2026-10-03) |
| M1-b | 추천 단계의 LLM 비교 | 아래 참고 | **다음** |
| F11-b | 캐시(임베딩·결과) + 서킷브레이커 | p95 하락, LLM 호출 수 감소 | |
| F12 | Redis 큐 + 워커 + bounded queue + 429/503 + DLQ | 아래 참고 | |
| F13 | 임베딩·reranker 동적 배칭 | 처리량 향상 배수 측정 | |

F10을 F11보다 먼저 하는 이유는 Phase 1과 같다. 자를 먼저 만든다.

### F10: 부하 기준선 (완료)

**완료 판정:** 0.2→2 RPS 스파이크에서 아래 값을 README에 기록한다.

- Kimi 실측 분포(배율 1.0)와 빠른 LLM 분포(배율 0.1) 각각의 시작 구간별 p50/p95/p99와 추천 성공률(표본 수 포함)
- 완료 구간별 처리량
- `dropped_iterations`

**결과:** 모의 LLM으로 기준선을 쟀다. 스파이크 성공률은 Kimi 실측 분포에서 6.9%, 빠른 LLM 분포에서 100%다.
동기 핸들러와 응답 직렬화가 같은 스레드 풀(40개)을 두 번 기다렸다. 그래서 서버가 끝낸 일 대부분이 클라이언트 timeout 뒤에 나갔다(`load/results/`, README "부하 측정").

### F11: 캐시와 장애 대응 (일부만 완료)

**F11은 끝나지 않았다.** 동시성 제한(F11-a)만 끝났고, 캐시와 서킷브레이커(F11-b)는 시작하지 않았다.

원래 범위는 "캐시 + timeout·재시도·서킷브레이커·동시성 제한"이다.
timeout과 재시도는 Phase 0에서 이미 넣었다. 남은 것을 둘로 나눴다.

| 항목 | 범위 | 상태 |
| --- | --- | --- |
| F11-a | 비동기 `/recommend` + 추천 작업 수 상한. 모델과 무관한 구조 개선 | 완료 |
| F11-b | 캐시(임베딩·결과) + 서킷브레이커. 고른 모델의 속도와 실패 양상을 보고 설계한다 | M1 뒤에 시작 |

**F11-a를 먼저 한 이유:** F10에서 서버가 끝낸 일 대부분이 클라이언트 timeout 뒤에 나가는 문제가 드러났다.
이 문제를 풀려면 F11 범위 중 동시성 제한이 필요했고, 이 부분은 어떤 LLM을 쓰든 같다.

**F11-a 결과:** `/recommend`를 비동기로 바꿔 응답 검증이 스레드 풀을 다시 기다리지 않게 했다.
추천 작업 수 상한(32)을 넘는 요청은 503으로 거절한다.
Kimi 실측 분포에서 헛일 0%, 스레드 대기 p95 0.05초, 회복 구간 성공률은 4.9% → 96.7%다.

### F14: 단계별 LLM 설정 (완료)

조건 추출 단계와 추천 단계에 서로 다른 LLM을 쓸 수 있게 하고, M1에 필요한 평가 모드를 만든다.

- 단계마다 base URL, 모델, API 키, 요청 본문에 덧붙일 JSON을 따로 둔다. 비어 있으면 공통 설정 `WHATFROM_LLM_*`을 쓴다.
- 평가에 두 모드를 더한다. 조건 추출 단계만 돌리는 모드와, 이전 결과의 검색 조건을 고정해 추천 단계만 돌리는 모드다.
- 평가 결과에 LLM 호출마다 재시도 횟수와 token 사용량을 남긴다.

**완료 판정:** 설정을 비워 두면 지금과 같은 요청이 나가고, 두 평가 모드가 fake provider로 CI를 통과한다.

**결과:** 조건 추출 단계만 재는 `make eval-plan`과, 검색 조건을 고정하고 추천 단계만 재는 `make eval-recommend PLANS=<결과 JSON>`을 만들었다.
단계별 설정은 빈 값도 설정하지 않은 것으로 본다. API 키만 예외로, 빈 값이면 인증 header를 보내지 않는다.
LLM 호출 기록에는 실패한 호출도 하나씩 남는다.

### F15: Anthropic native provider (완료)

M1-b에 Claude Sonnet을 넣기 위해, 단계마다 LLM API 종류를 고르고 Anthropic native Messages API로 부를 수 있게 한다.
Anthropic의 OpenAI 호환 layer는 `response_format`과 `reasoning_effort`를 무시해 비교 조건을 만들 수 없다([조사 노트](research/2026-10-03-m1b-recommend-stage.md)).

- 단계별 설정 `WHATFROM_PLAN_LLM_API`, `WHATFROM_RECOMMEND_LLM_API`(`openai_compatible`, `anthropic`)를 둔다. 비우면 지금과 같다.
- Anthropic provider도 같은 인터페이스와 같은 호출 기록을 낸다. thinking과 effort는 `EXTRA_BODY`로 정한다.

**완료 판정:** API 종류를 비워 두면 지금과 같은 요청이 나가고, Anthropic 요청·응답·실패 처리가 가짜 transport 테스트로 CI를 통과한다.

**결과:** `WHATFROM_RECOMMEND_LLM_API=anthropic`이면 추천 단계가 `/v1/messages`로 간다. 결정은 [ADR 0002](adr/0002-anthropic-via-native-messages-api.md)에 있다.

- `EXTRA_BODY`의 `thinking`과 `output_config.effort`는 schema와 병합한다. 코드가 정하는 키와 객체가 아닌 `output_config`는 시작할 때 막는다.
- 출력이 잘리거나(`max_tokens` 16,000) 모델이 거부하면 그 이유로 실패하고 호출 기록을 남긴다. 529(과부하)도 재시도한다.
- 실제 Anthropic API는 아직 부르지 않았다. M1-b 사전 확인에서 처음 부른다.

### M1: LLM 모델 비교

LLM 지연을 줄일 모델을 단계마다 고른다. 두 단계는 하는 일과 정답 지표가 달라 따로 비교한다.
kimi-k3는 reasoning을 끌 수 없어, 지연의 대부분이 reasoning token에서 나오는 것으로 본다.
지금은 `reasoning_effort`를 보내지 않아 기본값 `max`로 돌고, 가장 낮은 값은 `low`다.
모델별 reasoning 설정과 가격은 [조사 노트](research/2026-10-03-m1-llm-candidates.md)에 있다.

**공통 규칙**

- kimi-k3 기준선은 비교 대상 모델과 같은 시기에 전체 평가로 다시 잰다. 기준선은 운영 설정(`max`)으로 잰 값이다.
- kimi-k3도 다른 모델처럼 reasoning 최소 수준(`low`)으로 따로 재서 후보에 넣는다.
- 모델마다 1회 돌리고, 정확도 하한 ±2문항 안에 든 모델만 한 번 더 돌린다.
- timeout(120초)과 재시도(429·5xx 최대 2회)는 운영과 같게 둔다. 재시도가 섞인 결과의 지연은 신뢰할 수 없다고 표시한다.
- **고르는 기준:** 1. 성능(지연, 정확도) 2. 비용 순서로 본다.
  - 정확도 하한을 넘은 모델 중 가장 빠른 p95와의 차이가 기준선 요청 전체 p95의 5% 안이면 성능이 비슷한 것으로 본다.
  - 성능이 비슷한 모델 중 비용이 가장 낮은 모델을 고른다. 가장 싼 비용의 110% 안이면 비용이 같은 것으로 보고 정확도가 높은 쪽을 고른다.
  - M1-a 결과를 본 뒤 정했다. 처음 규칙은 비용을 기록만 했다.
- 결과는 `eval/experiments/<날짜>-<주제>/`에 남긴다. kimi-k3를 유지하는 결론도 될 수 있다.

**M1-a: 조건 추출 단계**

- **비교 대상 모델:** kimi-k3(기준선 `max`, 후보 `low`), `gpt-6-luna`, `grok-4.3`, `gemini-3.5-flash-lite`. 각 모델의 reasoning 최소 수준으로 잰다.
  - 1·2회차 결과를 본 뒤 `gpt-6-luna` `low`와 `gpt-6.1-sol` `low`를 추가했다. 고른 kimi-k3 `low`보다 훨씬 싼 GPT에 reasoning을 조금 준 설정이 비교 대상에 없었다.
- **정확도 하한:** repository 추출 일치율과 조건 추출 일치율이 kimi-k3 기준선보다 2문항 넘게 떨어지지 않는다.

**M1-a 결과:** 조건 추출 단계에 `gpt-6-luna` + `reasoning_effort: low`를 쓴다. 기준선 말고 정확도 하한을 넘은 설정은 이것 하나다. 운영 설정은 M1-b 완료 판정 뒤에 바꾼다.

- 기준선 kimi-k3(`max`)보다 조건 추출 단계 p95가 24.1초 → 4.6초, 40문항 비용이 $0.303 → $0.0075로 준다.
- 정확도는 5회 평균 repository 추출 35.4, 조건 추출 38.2로 하한(35, 38)을 넘는다. 조건 추출은 0.2문항 차이로 넘었다.
- 처음 기준선이 fake embedder로 돌아 다시 쟀다. 다시 잰 기준선은 정확도가 1문항씩 높아, kimi-k3 `low`와 `gpt-6.1-sol` `low`가 하한에 못 미치게 됐다.
- 고르는 기준을 공통 규칙(성능이 비슷하면 비용)으로 고쳤다. 처음 규칙은 비용을 거의 보지 않았다. 다시 잰 기준선에서는 어느 규칙으로도 결과가 같다.
- golden set의 `alpine-static-binary` 라벨과, repository를 도구나 언어로만 암시하는 문항의 채점을 다시 봐야 한다.

자세한 내용은 [실험 기록](../eval/experiments/2026-10-03-plan-llm-comparison/README.md)에 있다.

**M1-b: 추천 단계**

- **비교 대상 모델:** M1-a의 4개에 `gpt-6.1-sol`과 `claude-sonnet-5-5`를 더한다. kimi-k3를 뺀 모델은 reasoning 최소 수준과 `low` 두 가지로 잰다.
  `claude-sonnet-5-5`는 F15의 Anthropic provider로 부른다. 최소 수준은 `{"thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}`, `low`는 `{"output_config": {"effort": "low"}}`다.
  `gpt-6.1-sol`은 `none`과 `minimal`이 없어 최소 수준이 `low`이므로 한 번만 잰다.
- **입력 고정:** kimi-k3 기준선에서 나온 검색 조건 40개를 고정해, 모든 모델이 같은 후보와 근거를 받게 한다. M1-a의 `results/kimi-max-r1.json`(실제 embedder로 다시 잰 기준선)을 쓴다.
- **측정 전에 할 것:** 측정 도구가 embedder를 지정하고 검증하게 한다.
- **정확도 하한:** 추천 정확도 35/40 이상. 지금 golden set(`fe366444`)에서 kimi-k3가 낸 최저치다. 측정마다 2~3문항씩 흔들리므로 기준선(37/40)을 하한으로 두지 않는다.
- **완료 판정:** 두 단계에서 고른 모델 조합을 전체 평가로 한 번 돌려 확인한다.

### F12: 큐와 워커

**완료 판정:** 과부하에서 p99가 유계이고, 정상 요청 성공률을 유지한다.

과부하는 M1에서 고른 LLM의 처리량을 넘도록 스파이크를 키워 만든다. 기준은 큐 없이 503이 나기 시작하는 지점이다.
빠른 LLM에서는 0.2→2 RPS 스파이크로는 몰림이 생기지 않아, 큐의 효과가 보이지 않기 때문이다.

## Phase 4: 여유가 있으면

**이 단계는 하지 않아도 된다.** 아래 순서로 한다.

1. K8s: probe, HPA, rolling update·rollback, 응답에 `model_version` 포함
2. vLLM 비교 실험
3. CVE 2단계

F8에서 뺀 재정렬 모델도 이 단계의 후보다. README 틀에서 나온 chunk를 구분하는 방법이 생긴 뒤 다시 본다.
