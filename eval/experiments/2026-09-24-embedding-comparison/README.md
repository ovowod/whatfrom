# 임베딩 모델 비교 (2026-09-24)

**결론: 지금 모델(bge-m3)을 교체할 만큼 확실한 이득을 확인하지 못해 bge-m3를 유지한다.**
bge-m3가 더 낫다는 것을 입증한 것은 아니다. 전역 검색의 필요 리포 포함은 bge-m3가 가장 높았고, qwen3·arctic의 근거 Hit 상승에는 내용 없는 "Image Variants" 소개문 적중이 섞여 있었다.

**가장 중요한 발견은 모델 순위보다 지표 쪽이다.** 기대 섹션 제목만 보는 Hit 지표는 한 문장짜리 소개문도 성공으로 세어 근거 품질을 과대평가할 수 있다. 프로젝트의 문서 Hit@5도 같은 방식으로 채점한다.
지금 기준선(bge-m3)에는 소개문 적중이 없어 기록된 숫자는 맞지만, 지표나 색인을 고칠 필요가 있다.

## 조건

| 항목 | 값 |
| --- | --- |
| 코드 | main `e1d83a0` (섹션당 1개 규칙, PR #13 머지 직후) |
| 색인 | README 원본(docker-library/docs)으로 만든 청크 344개, `document_chunks.id` 460~803 |
| 청크 해시 | `0d8db712de4c06c2f031a3cc0a2c23a8bec4f8995434308169e69a4ea2eca2fb` (청크 id와 본문의 SHA-256) |
| 골든셋 | `fe366444`, 40문항 |
| 검색 조건 | `plans.json`. 유료 평가 `2026-09-23T14-14-55`(kimi-k3)에 기록된 검색 조건 40개를 고정했다. LLM은 부르지 않았다 |
| 검색 규칙 | 지금 코드와 같다. 섹션마다 가장 가까운 청크 1개, 거리가 같으면 청크 id 순 |
| 차원 | 모두 1024. OpenAI는 `dimensions=1024`로 받았다 |

모델마다 문서와 질문을 모두 다시 임베딩했다. 질문 접두어는 각 모델의 공식 사용법을 따랐다. qwen3는 지시문(`Instruct: ... Query: `), arctic-embed2는 `query: `, 나머지는 없음이다. qwen3는 접두어 없이도 쟀다.

## 지표

| 지표 | 무엇을 재나 |
| --- | --- |
| 전역 Hit@5 | 검색 조건 없는 전역 상위 5개 섹션에 필요 리포지토리의 기대 섹션이 있는가. 검색 전용 평가의 문서 Hit@5와 같은 조건 |
| 필요 리포 포함 | 전역 상위 5개에 필요 리포지토리가 모두 있는가. 태그를 보지 않으므로 평가의 후보 포함률(허용 정답 태그가 후보에 있는가)과 다르다 |
| 리포 안 Hit@2 | 필요 리포지토리 안에서만 찾은 상위 2개 섹션에 기대 섹션이 있는가. 검색 정책 없이 모델의 섹션 매칭만 본다 |
| 고정 검색 조건 근거 Hit | 고정한 검색 조건으로 지금 코드의 근거 규칙을 따른 근거에 기대 섹션이 있는가. 전역 상위 5개에 그 리포가 있으면 그 섹션들, 없으면 리포 안 상위 2개를 쓴다. **모델과 검색 정책이 합쳐진 점수**다 |

필요 리포 포함을 뺀 세 지표는 기대 섹션 제목이 하나라도 들어 있으면 성공으로 센다. 필요 리포 포함은 리포지토리만 보고 제목은 보지 않는다. 어느 지표도 본문이 답을 충분히 뒷받침하는지와 추천 정확도는 재지 않는다.

그래서 두 가지 보정값을 함께 잰다.

- **소개문 적중 제외 (주 보정값):** 본문을 확인한 "Image Variants" 소개문 7개("The `x` images come in many flavors, each designed for a specific use case." 한 문장)의 적중을 뺀다.
- **150자 미만 섹션 적중 제외 (보조):** 본문이 150자 미만인 섹션의 적중을 뺀다. 짧아도 쓸모 있는 절(redis 실행 명령 54자)까지 빠지고, 긴 섹션이 질문에 답하는지는 보지 않으므로 근거 품질의 판정이 아니라 참고값이다.

이번 자료에서는 두 보정값이 모든 모델에서 같았다. 짧은 섹션 가운데 기대 섹션으로 걸린 것은 소개문뿐이었다.

## 결과

| 모델 | 전역 Hit@5 | 필요 리포 포함 | 리포 안 Hit@2 | 근거 Hit | 근거 Hit (소개문 적중 제외) |
| --- | --- | --- | --- | --- | --- |
| bge-m3 (지금) | 31/40 | **39/40** | 32/40 | 31/40 | 31/40 |
| qwen3-embedding:0.6b (지시문) | 31/40 | 35/40 | 31/40 | 34/40 | 30/40 |
| qwen3-embedding:0.6b (지시문 없음) | 31/40 | 36/40 | 31/40 | 33/40 | 26/40 |
| snowflake-arctic-embed2 | **34/40** | 37/40 | 31/40 | **35/40** | 31/40 |
| text-embedding-3-small | 30/40 | 34/40 | 32/40 | **35/40** | **33/40** |
| text-embedding-3-large | 32/40 | 36/40 | **34/40** | 34/40 | 32/40 |

전역 Hit@5도 소개문 적중을 빼면 bge-m3 31, qwen3(지시문) 27, qwen3(지시문 없음) 24, arctic 30, OpenAI small 28, large 30이다. bge-m3는 소개문 적중이 없었다.
문항별로 얻고 잃은 목록은 `summary.txt`에, 문항별 상위 5개와 근거는 `results.json`에 있다.

## 근거 본문 확인

근거 Hit에서 bge-m3와 결과가 달라진 문항의 근거 본문을 읽었다.

- **"Image Variants" 소개문은 한 문장뿐이다.** 실제 설명은 `Image Variants > python:<version>-slim` 같은 하위 절에 있다. 제목만 보는 채점은 소개문이 걸려도 성공으로 센다.
- 다른 모델이 얻은 문항 가운데 `python-slim-vs-full`(arctic), `postgres-ci-ephemeral`, `redis-cache-sidecar`(qwen3·arctic)는 소개문만 새로 가져온 것이라, 질문에 답하는 근거가 늘지 않았다.
- 실제로 늘어난 근거는 `node-alpine-vs-slim`의 `node:<version>-slim` 하위 절(qwen3·arctic)과 `postgres-version-pinned-16`의 태그 목록(arctic)이다.
- arctic은 `python-latest-stable`에서 최신 안정 버전을 보여 주는 태그 목록(Shared Tags)을 잃고 "What is Python?"만 가져왔다. 실제 손실이다.
- 본문 150자 미만 섹션은 11개이고, 그중 7개가 리포지토리마다 있는 "Image Variants" 소개문이다. 나머지 4개 중에는 redis 실행 명령(54자)처럼 짧지만 쓸모 있는 절도 있다.

소개문을 빼고 남는 적중이 질문에 충분히 답하는지까지 확인한 것은 위에 적은 문항뿐이다. 더 정확하게 비교하려면 문항별로 근거 적절성을 판정해야 한다.

bge-m3 대비 근거 Hit(소개문 적중 제외)의 변화는 다음과 같다.

| 모델 | 얻은 문항 | 잃은 문항 |
| --- | --- | --- |
| qwen3 (지시문) | `node-alpine-vs-slim` | `golang-multistage-build`, `temurin-jdk-build-stage` |
| arctic-embed2 | `node-alpine-vs-slim`, `postgres-version-pinned-16` | `python-scientific-stack`, `python-latest-stable` |
| OpenAI small | `node-lts-prod`, `node-alpine-vs-slim`, `postgres-version-pinned-16` | `python-scientific-stack` |
| OpenAI large | `python-slim-vs-full`, `postgres-prod-default`, `postgres-version-pinned-16` | `python-alpine-explicit`, `golang-cgo-sqlite` |

## 한계

- 40문항 중 몇 문항 차이이고, 검색 조건은 유료 평가 한 번의 기록이다.
- 성공 판정이 섹션 제목 포함 여부다. 소개문 제외는 이번에 본문을 확인한 7개만 뺀다. 그 밖의 섹션이 질문에 답하는지는 판정하지 않았다.
- OpenAI small은 소개문 적중을 빼고도 2문항 순증했지만, 40문항에서 2문항이고 질문마다 외부 API를 불러야 해 교체 근거로 삼지 않는다.
- 질문 임베딩 시간(`summary.txt`)은 질문 하나씩 보낸 단회 평균이라 모델 속도 순위로 해석하기 어렵다. 로컬 모델은 Windows의 Ollama, OpenAI는 API 왕복 시간이 포함된다.
- **완전히 재현되지는 않는다.** 스크립트는 로컬 DB의 색인을 읽고, 색인은 계속 바뀌는 GitHub 원본 README에서 만든다. 다시 색인해 청크가 바뀌면 숫자가 달라질 수 있다. 위의 청크 해시와 `summary.txt` 첫 줄의 해시가 같을 때만 같은 조건이다.

## 다시 돌리기

프로젝트 루트에서 실행한다. Ollama에 `bge-m3`, `qwen3-embedding:0.6b`, `snowflake-arctic-embed2`가 있어야 하고, OpenAI 모델에는 `OPENAI_API_KEY`나 `~/.config/whatfrom/openai.key`가 필요하다.

```bash
uv run --with numpy --with httpx python eval/experiments/2026-09-24-embedding-comparison/compare.py
```

벡터 캐시는 이 폴더의 `cache/`에 쌓이고 Git에서 제외된다. 다른 위치의 캐시를 쓰려면 `--cache-dir`로 넘긴다. 캐시에는 모델 식별자, 청크(id와 본문), 질문(접두어 포함)의 해시가 함께 저장되고, 지금 값과 다르면 멈춘다. 그때는 `--recompute`로 다시 계산한다.

## 파일

- `compare.py`: 재현 스크립트
- `plans.json`: 고정한 검색 조건 40개와 출처 실행 정보
- `results.json`: 6개 구성의 합계와 문항별 판정·상위 5개·근거
- `summary.txt`: 결과 요약과 bge-m3 대비 얻고 잃은 문항
