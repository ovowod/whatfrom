# whatfrom

프로젝트 요구사항을 설명하면, 실재하는 컨테이너 베이스 이미지 후보와 선택 이유를 근거 문서와 함께 제안한다.
주 사용자는 Dockerfile을 쓰는 LLM agent다. agent가 학습 데이터만으로는 얻기 어려운 것을 준다.

- 수집 시점에 실재하는 태그와 후보마다 `image@digest` 참조
- 아키텍처별 지원 여부와 크기
- 공식 문서에서 글자 그대로 가져와 검증한 근거 인용

Dockerfile은 프로젝트를 보는 쪽(사람이나 LLM agent)이 쓴다([ADR 0003](docs/adr/0003-judge-candidates-instead-of-recommending.md)).

whatfrom이라는 이름은 Dockerfile의 `FROM`에 어떤 이미지를 쓸지 고민하는 데서 시작됐다.

```dockerfile
FROM ???
```

다음과 같은 요구사항을 입력할 수 있다.

> FastAPI 서비스이고 numpy를 사용해. 이미지 크기는 작으면 좋겠지만 빌드 안정성이 더 중요해.
> ARM64에서도 실행해야 해.

## 설계 변경

처음(v1)에는 추천 이미지 하나와 Dockerfile 초안을 함께 만들었다.
초안의 품질을 재려다 보니, 질문 한두 문장으로는 진입점, 의존성 파일, 포트를 알 수 없어 초안이 추측일 수밖에 없었다. 그 초안을 재는 장치가 산출물보다 커졌다.
그래서 Dockerfile 생성을 빼고, 후보마다 `image@digest` 참조와 검증된 근거 인용을 주도록 바꿨다([ADR 0003](docs/adr/0003-judge-candidates-instead-of-recommending.md)).
지키는 원칙도 "이미지를 지어내지 않는다"에서 "이미지도 인용도 지어내지 않는다"로 넓어졌다.
두 번째 LLM 호출을 후보 비교표로 할지, 인용을 붙인 단일 선택으로 둘지는 비교 실험으로 정한다. 진행 순서는 [roadmap](docs/roadmap.md)에 있다.

## 추천과 검증 원칙

**LLM은 검색으로 찾은 후보 안에서 이미지를 선택하고, 비교와 설명을 맡는다.**
이미지 이름과 태그를 임의로 만들어 추천하지 못하도록 응답을 검증한다.

- 후보 검색과 LLM의 근거 생성은 분리되어 있다.
- LLM은 두 번 부른다. 먼저 질문에서 검색 조건(repository, 버전, 아키텍처, 배포판, 크기)만 뽑고, 그 조건으로 만든 후보에서 추천을 받는다. 검색 조건은 이미지 이름이 아니며, repository는 수집된 목록과 대조한다.
- 조건에 맞는 후보가 없으면 크기, 배포판, 아키텍처, 버전 순서로 조건을 풀고 그렇다고 알린다. 검색 조건 추출에 실패하거나 뽑은 repository의 문서나 태그가 없으면 벡터 검색으로 찾은 repository에서 후보를 만든다. 이런 경우는 추천이 있어도 `degraded`로 표시한다.
- 주 추천 이미지가 후보에 없거나 DB에서 확인되지 않으면 추천을 폐기하고 후보 목록을 반환한다.
- 대안 이미지가 같은 검증을 통과하지 못하면 해당 대안만 제거한다.
- 추천에는 수집 시점 digest를 붙인다. 이동 태그는 그 뒤 바뀌었을 수 있으므로 응답의 `recommended.collected_at`을 함께 본다.
- 후보마다 `reference`(`image@digest`)를 준다. Dockerfile의 `FROM`에 그대로 쓴다. digest를 수집하지 못한 후보는 `null`이다.
- 근거는 응답 최상위 `evidence`에 (repository, section 제목)마다 한 번씩 번호를 붙여 담는다. 후보는 `evidence_numbers`로 가리키고, 두 번째 LLM도 같은 번호로 근거를 본다.
- 추천 이유는 주장 목록(`claims`)이다. 주장마다 근거 인용(근거 번호와 글자 그대로의 문장)이 붙는다.
- 인용은 그 요청에서 LLM에 넘긴 근거 본문으로 검증한다. 근거 번호가 없거나, 인용이 비었거나, 공백을 정규화해도 본문에 없거나(대소문자는 구분), 추천이나 대안이 아닌 repository의 근거면 실패다.
- 검증에 실패한 인용은 `verified: false`와 실패 이유(`problem`)를 달고 남긴다. 추천은 버리지 않는다. 검증을 통과한 인용 수는 `verified_citations`이고, 0이면 알림을 남긴다.
- 인용 검증은 인용이 실제 문서에 있는지까지만 본다. 인용이 주장을 뒷받침하는지는 평가에서 잰다.
- 임베딩·LLM 호출 오류와 추천 검증 실패는 HTTP 200 응답으로 처리하고, 확보된 후보가 있으면 함께 반환한다. 임베딩 실패나 검색 결과가 없는 경우에는 후보 목록도 비어 있다.

응답의 추천은 다음과 같은 모양이다(일부 생략).

```json
{
  "recommendation": {
    "image": "python:3.13-slim",
    "alternatives": [],
    "claims": [
      {
        "text": "alpine은 glibc 대신 musl을 쓴다.",
        "citations": [
          {"evidence": 2, "quote": "it does use musl libc instead of glibc", "verified": true, "problem": null}
        ]
      }
    ],
    "verified_citations": 1
  },
  "candidates": [
    {"image": "python:3.13-slim", "reference": "python:3.13-slim@sha256:…", "evidence_numbers": [1, 2]}
  ],
  "evidence": [
    {"number": 1, "repository": "python", "section_title": "How to use this image", "content": "…", "source_url": "…"},
    {"number": 2, "repository": "python", "section_title": "Image Variants", "content": "…", "source_url": "…"}
  ]
}
```

이 처리는 모든 오류에 적용되지는 않는다. 잘못된 요청은 422를 반환하며, DB 오류는 위의 복구 처리에 포함되지 않는다.
태그 검증은 수집된 DB를 기준으로 하므로, 요청 시점에 해당 태그를 실제로 내려받을 수 있는지까지 보장하지는 않는다.

## 구조

```text
요구사항 ─▶ 임베딩 ─▶ 검색 조건 추출 ─▶ 벡터 검색 + 조건 거르기 ─▶ 후보 태그 선택 ─▶ 검토 ─▶ 검증 ─▶ 응답
                      (LLM)             (PostgreSQL + pgvector)                      (LLM)
```

수집(`collect`)과 색인(`index`)은 CLI 배치로 실행한다. 추천 요청을 처리하는 동안에는 Docker Hub를 호출하지 않는다.

| 패키지 | 역할 |
| --- | --- |
| `collect` | Docker Hub 태그와 아키텍처별 이미지 정보(digest, 크기) 수집 |
| `index` | 공식 README를 섹션과 청크로 나누고 임베딩 생성 |
| `search` | 벡터 검색, 검색 조건으로 태그 거르기, 후보 태그 선택 |
| `recommend` | 검색 조건 추출, LLM 추천, 추천 검증 |
| `eval` | golden set 평가 |

각 단계의 패키지는 내부 모듈과 공통 패키지인 `core`만 참조한다.
단계 간 연결은 `api.py`와 `cli.py`에서 담당하며, `tests/test_layering.py`가 이 의존성 규칙을 검사한다.

## 실행

Python 3.13 이상, uv, Docker Compose가 필요하다. 아래 명령은 프로젝트 루트에서 실행한다.

먼저 환경 설정 파일을 복사한다. 기존 `.env`가 있다면 필요한 설정만 수정한다.

```bash
cp .env.example .env
```

`.env`에 임베딩·LLM 엔드포인트, 모델명, 필요한 API 키를 입력하고 다음 두 설정을 변경한다.
예제 파일의 기본값은 테스트용 `fake`이므로, 엔드포인트와 키만 입력해서는 실제 모델을 사용하지 않는다.

```dotenv
WHATFROM_EMBEDDER=openai_compatible
WHATFROM_LLM_PROVIDER=openai_compatible
```

기본 임베딩 모델은 `bge-m3`이며, DB 스키마에 맞는 1024차원 벡터를 반환해야 한다.
로컬 Ollama를 사용한다면 서버를 실행한 상태에서 모델을 준비한다.
기본 접속 주소는 `http://localhost:11434/v1`이다.

```bash
ollama pull bge-m3
```

DB를 준비하고 공식 이미지 10개의 태그와 README를 수집·색인한 뒤 API 서버를 실행한다.
색인과 검색에는 같은 임베딩 모델을 사용해야 한다.

```bash
make up                 # PostgreSQL + pgvector 시작
uv run python -m whatfrom.cli init-db
uv run python -m whatfrom.cli collect --all
uv run python -m whatfrom.cli index --all
uv run uvicorn whatfrom.api:app
```

`collect`는 Docker Hub 태그 목록을 최근 갱신 순으로 받는다. 익명 요청은 repository당 최근 1,000개까지만 받을 수 있다.
다시 실행하면 digest나 푸시 시각이 바뀐 태그만 다시 쓰고, 실행마다 결과를 `collection_runs` 테이블에 기록한다.
digest가 없는 오래된 태그는 같은 이미지인지 판단할 수 없어 매번 다시 쓴다.
하나의 repository만 다룰 때는 `--all` 대신 이름을 넘긴다(`collect python`).

`index`는 README 본문을 Docker Hub가 아니라 GitHub의 [docker-library/docs](https://github.com/docker-library/docs) 원본에서 받는다.
Hub 본문은 25,000자에서 잘려 postgres는 Image Variants 절이 빠지고 eclipse-temurin은 태그 목록이 줄어든다.
원본을 받지 못한 repository는 실패로 표시하고 이전 색인을 그대로 둔다. 근거 청크의 출처 URL도 이 원본 문서다.

수집을 마치면 태그의 버전·배포판·코드네임·변형을 파생 컬럼에 채운다. 이름에 없는 값은 같은 이미지를 가리키는 다른 태그에서 가져온다.
예를 들어 `python:3.14`는 Linux 이미지가 `3.14-trixie`와 같아서 debian trixie가 된다.
이미 수집된 DB는 Docker Hub를 다시 부르지 않고 채울 수 있다.

```bash
uv run python -m whatfrom.cli derive --all
```

서버가 실행되면 다른 터미널에서 추천을 요청할 수 있다.

```bash
curl -s localhost:8000/recommend -H 'content-type: application/json' \
  -d '{"question": "numpy를 쓰는 FastAPI 서비스인데 ARM64에서도 돌아야 해"}'
```

LLM 공급자를 바꿀 때는 `.env`의 `WHATFROM_LLM_BASE_URL`, `WHATFROM_LLM_MODEL`, API 키를 변경한다.
사용할 엔드포인트는 OpenAI 호환 `/v1/chat/completions`와 JSON Schema 응답 형식을 지원해야 한다.

조건 추출 단계와 검토 단계에 다른 LLM을 쓸 수도 있다.
`WHATFROM_PLAN_LLM_*`은 조건 추출 단계, `WHATFROM_RECOMMEND_LLM_*`은 검토 단계의 설정이다.

| 설정 | 설정하지 않았을 때 |
| --- | --- |
| `..._BASE_URL`, `..._MODEL` | 공통 설정(`WHATFROM_LLM_BASE_URL`, `WHATFROM_LLM_MODEL`)을 쓴다 |
| `..._API_KEY` | 공통 키를 쓴다. 빈 값으로 설정하면 인증 header를 보내지 않는다 |
| `..._EXTRA_BODY` | 아무것도 덧붙이지 않는다 |

`..._EXTRA_BODY`는 요청 본문에 덧붙일 JSON 객체다. reasoning 설정처럼 공급자마다 이름이 다른 parameter를 넣는다.

```dotenv
WHATFROM_RECOMMEND_LLM_BASE_URL=https://api.openai.com/v1
WHATFROM_RECOMMEND_LLM_MODEL=gpt-6-luna
WHATFROM_RECOMMEND_LLM_API_KEY=sk-...
WHATFROM_RECOMMEND_LLM_EXTRA_BODY={"reasoning_effort": "none"}
```

`model`, `messages`, `response_format`은 코드가 정하므로 덧붙일 수 없고, 넣으면 시작할 때 실패한다.
`stream`처럼 응답 형식을 바꾸는 키는 막지 않지만, 넣으면 첫 호출이 오류로 실패한다.

## 품질 측정

`eval/goldenset.yaml`에는 repository 10종을 대상으로 한 40문항이 있다.
각 문항에는 허용 정답 목록과 근거 URL을 기록하고, 필요에 따라 명시적 오답, 구조화된 조건, 기대 문서 섹션을 지정한다.

- **정답은 여러 개를 허용한다.** 타당한 답이 여럿인 질문을 하나의 정답으로만 평가하면 작성자의 선호가 점수에 반영될 수 있다.
- **명시적 오답은 문서 근거로 틀렸다고 판단할 수 있는 답에만 지정한다.** 단지 덜 권장되는 답은 오답으로 취급하지 않는다.
- **근거 URL은 필수다.** 정답 기준을 외부 문서와 대조할 수 있도록 한다. URL을 기록하는 것만으로 정답의 타당성이 보장되지는 않는다.
- **버전 요구가 없으면 지원 중인 줄기를 모두 허용한다.** 질문이 버전·기간을 요구하거나 문서가 그 버전으로는 요구를 만족할 수 없다고 적은 경우에만 좁힌다. 기준일과 repository별 지원 줄기는 golden set 머리 주석에 출처와 함께 적었다.
- **질문에서 답이 될 제품을 알 수 있어야 한다.** 평가는 모델에 질문만 넘기므로, 제품명이나 그 제품에만 해당하는 단서를 질문에 넣는다.
- **검색 조건 추출의 정답은 따로 둔다.** `conditions`는 답이 지켜야 할 조건이라 문서 지식에서 나온 것도 있다. 추출기는 질문에 명시된 것만 뽑아야 하므로, 질문에 적힌 조건을 `expected_plan`에 따로 적는다. 라벨 규칙은 golden set 머리 주석에 있다.
- **판단 요구는 후보 하나만 보고 판정할 수 있는 것만 쓴다.** `requirements`는 판정 단계가 후보마다 판정할 요구다. 검색 조건으로 거르는 것, 후보끼리 견줘야 하는 선호, 패키지를 더 설치하면 되는 것은 쓰지 않는다. 질문만 보고 쓴 초안을 근거와 대조해 고쳤다.

실행 절차에 따라 수집·색인과 `.env` 설정을 마친 뒤 평가한다.

```bash
make eval-retrieval    # LLM 호출 없이 검색 지표만 측정
make eval-plan         # 조건 추출 단계만 불러 추출 지표 2종 측정
make eval-recommend PLANS=eval/results/<파일>.json  # 검색 조건을 고정하고 추천 단계만 측정
make eval             # .env의 모델 설정으로 전체 지표 측정
```

| 지표 | 정의 |
| --- | --- |
| 추천 정확도 | 추천이 허용 정답인 비율. 추천하지 못한 문항은 실패로 계산 |
| 후보 포함률 | 후보에 허용 정답이 하나 이상 포함된 비율. 추천 정확도의 상한 |
| 태그 실재율 | 추천된 태그가 DB에 존재하는 비율. 추천이 없는 문항은 분모에서 제외 |
| 조건 일치율 | 조건이 지정된 문항 중 추천이 아키텍처·버전·배포판·크기 조건을 모두 만족한 비율. 추천이 없으면 실패로 계산 |
| 출처 제공률 | 추천 이미지에 출처 URL과 수집 시점이 있는 비율. 추천이 없으면 반환된 후보 전체를 검사 |
| 문서 Hit@5 | 기대 섹션이 지정된 문항 중 상위 5개 청크에 해당 섹션이 하나 이상 포함된 비율. repository도 일치해야 함. 검색은 섹션마다 가장 가까운 청크 하나만 돌려주므로 상위 5개 섹션과 같다 |
| 인용 검증 통과율 | 주장들에 붙은 근거 인용 중 근거 본문에 글자 그대로 있는 비율. 인용이 없으면 미측정 |
| 오답 적발률 | 후보로 나온 명시적 오답 중 불만족 판정을 하나 이상 받은 비율. 판정이 없는 실행이면 미측정 |
| 오판정률 | 후보로 나온 허용 정답 중 불만족 판정을 하나 이상 받은 비율. 판정이 없는 실행이면 미측정 |
| 명시적 오답 후보 문항 | 명시적 오답이 후보에 하나 이상 나온 문항의 비율. 오답 적발률이 잴 기회가 있던 문항이다 |
| repository 추출 일치율 | 검색 조건의 repository가 문항의 필요 repository인 비율. 추출에 실패하면 실패로 계산 |
| 조건 추출 일치율 | 검색 조건의 버전·아키텍처·배포판·크기가 `expected_plan`과 모두 같은 비율. 조건이 없는 문항도 분모에 넣어 없는 조건을 만든 것을 잡는다 |

검색 전용 모드는 LLM을 부르지 않아 검색 조건이 없다. 두 추출 지표를 출력하지 않으며, 그 모드의 후보 포함률은 검색 조건 없이 잰 값이다.

조건 추출 단계만 재는 모드(`make eval-plan`)는 임베딩, 검색, 추천 단계를 부르지 않는다.
repository 목록, 정규화, 미측정 문항 처리가 전체 모드와 같아 두 추출 지표를 전체 모드와 비교할 수 있다.
조건 추출 단계의 모델만 바꿔 비교할 때 쓴다.

검색 조건 고정 모드(`make eval-recommend`)는 이전 전체 평가 결과의 문항별 검색 조건을 조건 추출 단계 대신 쓴다.
후보 생성, 추천 단계, 검증, 채점은 전체 모드와 같아, 모든 모델이 같은 후보와 근거를 받는다. 추천 단계의 모델만 바꿔 비교할 때 쓴다.
- 그 결과에서 추출이 실패한 문항은 이번에도 추출 실패로 다룬다.
- 결과 파일이 전체 모드가 아니거나, golden set 해시가 다르거나, 측정할 문항이 없으면 모델을 부르기 전에 멈춘다.
- 조건 추출 단계를 부르지 않으므로 두 추출 지표는 출력하지 않는다. 결과 meta의 `plans_from`에 고정한 파일 이름이 남는다.

label 고정 mode(`whatfrom eval --label-plans`)는 문항의 `expected_plan`과 repository를 검색 조건으로 쓴다.
조건 추출 품질을 섞지 않고 두 번째 LLM 호출만 잴 때 쓴다. 후보는 운영 경로와 같은 검색(조건 filter 포함)으로 나온다.
- `--no-recommend`를 함께 주면 검토 단계도 부르지 않는다. LLM 호출 없이 후보, 검색 지표, 대조군만 잰다.
- repository가 둘 이상인 문항이 있으면 모델을 부르기 전에 멈춘다.
- 결과 meta의 mode는 `label-plans`, `--no-recommend`이면 `label-plans-retrieval`이다.

인용 뒷받침률은 `sample-citations`로 뽑은 표본에서 각 인용이 주장에 맞게 붙었는지 검토해 잰다.

```bash
uv run python -m whatfrom.cli eval sample-citations eval/results/a.json eval/results/b.json --out-dir <directory>
uv run python -m whatfrom.cli eval score-citations <directory>/citation-sample.yaml <directory>/citation-key.json
```

- 결과 파일(안)마다 검증을 통과한 인용이 붙은 주장을 최대 50개, 문항당 최대 2개 뽑는다. 항목에는 질문, 주장, 그 주장의 인용 전부, LLM에게 넘긴 후보 목록이 들어간다. `--seed`가 같으면 같은 표본이다.
- 표본에는 결과 파일 이름이 없다. 어느 안의 주장인지는 key 파일에만 있으니 검토가 끝날 때까지 열지 않는다.
- `verdict`: 주장에 쓰인 문서 사실이 인용들로 모두 확인되면 `supported`, 하나라도 아니면 `unsupported`, 정말 정할 수 없으면 `unclear`, 문서 사실이 없으면 `no_doc_fact`다. 절차와 예시는 표본 파일 머리 주석에 있다. 사용자 조건은 전제로 받아들이고, 결론은 확인된 사실과 조건에서 합리적으로 나오면 된다.
- `candidate_ok`: 주장 속 후보 정보(크기, 아키텍처, 후보 비교)가 후보 목록과 맞으면 `match`, 틀리면 `mismatch`, 확인할 수 없으면 `unclear`, 후보 정보가 없으면 `not_applicable`이다.
- 뒷받침률은 supported ÷ (supported + unsupported)이고 합격선은 90%다. unclear가 표본의 10%를 넘으면 보류, 판정할 주장이 없으면 미측정이다. no_doc_fact는 분모와 unclear 한도에서 뺀다. 후보 정보 오류는 mismatch ÷ (match + mismatch)로 따로 내고 합격선은 없다.
- 빈 칸, 정한 어휘 밖의 값, 지우거나 복제한 행, 검토 칸 밖의 내용 변경이 있으면 안별 집계만 내고 합격 여부는 비운다.

LLM을 부르는 모드는 문항마다 LLM 호출 기록(`llm_calls`)을 결과에 남긴다.
호출마다 단계, 시도 횟수, 성공 여부와 실패 이유, 입력·출력·reasoning token 수가 있다.
실패한 호출도 기록이 하나씩 남고, 응답에 token 수가 없으면 `null`이다.

버전과 배포판 조건은 태그의 파생 값으로 판정한다. `python:3.14`처럼 이름에 배포판이 없는 태그도
같은 이미지의 `3.14-trixie`에서 가져온 값으로 판정하고, `nginx:stable`은 이름에 없는 버전 `1.30`으로 판정한다.
파생 값이 없는 태그(수집이 도중에 실패한 뒤의 변경 태그, 값이 엇갈린 별칭)는 태그 이름으로 판정한다.
크기 조건은 첫 번째 요구 아키텍처를 기준으로 검사하며, 아키텍처를 지정하지 않으면 `amd64`를 사용한다.

허용 정답은 이름이 허용 목록에 있거나, 허용 목록의 태그와 digest가 같은 태그다.
`eclipse-temurin:25-noble`과 `25-jdk-noble`처럼 이름만 다른 같은 이미지의 별칭을 목록에 전부 적을 수는 없기 때문이다.
다만 이름이 명시적 오답 목록에 있으면 digest가 같아도 오답이다. `postgres:latest`는 지금 `postgres:18`과 같은 이미지지만 메이저가 바뀔 수 있는 태그라 오답으로 지정했다.
digest는 측정 시점 DB 기준이며, digest로 인정한 문항은 결과와 report에 따로 표시한다.

필요한 repository가 색인되지 않은 문항은 **미측정**으로 분류하고 평가 분모에서 제외한다.
실행 결과는 `eval/results/`에 JSON으로 저장하며, 모델명과 임베딩 모델, golden set 해시를 함께 기록한다.
문항별 후보 목록과 그중 허용 정답 수, 무작위 선택 대조군과 검색 1위 대조군도 저장한다. 두 대조군은 후보가 있는 모든 mode에서 출력한다.
검색 1위 대조군은 판정 없이 검색 1위 후보를 추천으로 봤을 때의 정확도이고, 추천 정확도와 같은 허용 규칙으로 채점한다.
전체 모드에서는 문항별 검색 조건, 필드별 추출 일치 여부, 응답 알림(조건 완화, 추출 실패)도 저장한다.
추천의 digest·출처·수집 시점, 두 번째 LLM 호출에 보낸 prompt, 단계별 소요 시간(임베딩, 조건 추출 단계, 두 번째 LLM 호출, 전체)도 함께 저장한다.
추천 이미지, 대안, 주장 목록과 근거 인용별 검증 결과도 저장한다. 인용 지표와 인용 표본 검토는 이 파일만 읽는다.
이 결과 폴더는 Git 추적 대상에서 제외되어 있다.

모델 선택처럼 결정을 내린 비교는 `eval/experiments/<날짜>-<주제>/`에 조건, 결과, 한계, 결론과 재현 스크립트를 함께 남긴다.

- [임베딩 모델 비교 (2026-09-24)](eval/experiments/2026-09-24-embedding-comparison/README.md): bge-m3, qwen3-embedding 0.6B, snowflake-arctic-embed2, OpenAI text-embedding-3-small·large를 비교했다. 교체할 만큼 확실한 이득을 확인하지 못해 bge-m3를 유지한다. 제목만 보는 Hit 지표가 한 문장짜리 "Image Variants" 소개문도 성공으로 세어 근거 품질을 과대평가할 수 있다는 점도 확인했다.
- [틀 문장 섹션 제외 (2026-09-24)](eval/experiments/2026-09-24-template-exclusion/README.md): 본문이 README 틀의 한 문장뿐인 섹션 10개를 색인에서 뺐다. 같은 벡터로 재면 문서 Hit@5는 그대로이고, 다시 색인한 뒤 오른 2문항은 원본 README 변경 때문임을 확인했다.

- [판단 요구 라벨과 명시적 오답 보강 (2026-10-05)](eval/experiments/2026-10-05-v2-labels/README.md): golden set에 판단 요구를 넣고, 명시적 오답을 필수 요구 위반만 남도록 고쳤다. 후보로 나온 명시적 오답이 28개에서 84개로 늘었다.
- [V2 첫 측정 (2026-10-05~15)](eval/experiments/2026-10-05-v2-first-run/README.md): 라벨 고정 입력으로 단일 선택을 재고, 인용 검토 기준을 정했다. 인용 뒷받침률이 합격선에 못 미쳤다.

### 기준 측정 결과 (2026-10-15, V2)

라벨 고정 입력(조건 추출 단계 없이 golden set의 검색 조건)으로 잰 v2 첫 결과다.
검토 단계는 단일 선택이고 `gpt-6-luna` + `reasoning_effort: none`을 썼다. 경로를 확인하려고 돌린 것이라 모델 선택 근거로 쓰지 않는다.
golden set은 version 2 `a6e8db88`이다. 자세한 내용은 [실험 기록](eval/experiments/2026-10-05-v2-first-run/README.md)에 있다.

| 지표 | 값 |
| --- | --- |
| 추천 정확도 | 39/40 (97.5%) |
| 검색 1위 대조군 | 27/40 (67.5%) |
| 인용 검증 통과율 | 81/84 (96.4%) |
| 인용 뒷받침률 | 26/43 (60.5%), 합격선 90% 미달 |
| 후보 정보 오류 | 0/8 |
| 문서 Hit@5 | 33/40 |

인용은 근거 본문에 글자 그대로 있지만, 그 인용이 주장에 쓰인 문서 사실을 모두 받치는 주장은 열에 여섯이다.
결론에 필요한 사실을 인용하지 않거나 사실의 일부만 인용한 경우가 많았다. M3에서 이 부분을 개선한다.

이전 측정 결과는 [측정 기록](docs/measurement-history.md#품질-측정)에 있다.

## 부하 측정

트래픽이 몰릴 때 서버가 어떻게 버티는지 재고, 이후 캐시·동시성 제한·큐 같은 개선을 비교할 기준선을 남긴다.

### 모의 LLM을 쓰는 이유

유료 평가에서 요청 시간의 99.56%는 LLM 두 번이었다(임베딩 0.38%, 검색·DB·검증 0.06%).
실제 LLM으로 부하를 걸면 비용이 요청 수에 비례하고, 결과가 우리 서버가 아니라 공급자의 응답 시간 변동과 동시 요청 제한으로 정해진다.
그래서 LLM만 모의 서버(`whatfrom.loadtest.mock_llm`)로 바꾼다. 모의 서버는 유료 평가 결과에 기록된 문항별 검색 조건·추천과 LLM 시간을 재생한다.
앱은 LLM 주소만 바꾸므로 HTTP 클라이언트, 연결 풀, 타임아웃은 운영과 같은 경로를 탄다. 임베딩(로컬 bge-m3)과 DB는 실제로 쓴다.

LLM 시간은 두 가지로 쟀다.

- **Kimi 실측 (배율 1.0)**: `2026-09-24T07-22-39.json`에 기록된 시간 그대로. 한 요청에 약 65초.
- **빠른 LLM (배율 0.1)**: 같은 시간의 10분의 1. 한 요청에 약 6.5초.

### 부하

k6로 세 구간을 이어 보낸다. 질문은 golden set 40문항을 고정 시드로 섞은 순서표(`load/questions.json`)에서 꺼내, 실행마다 같은 질문이 같은 순서로 나간다.

| 구간 | 시간 | 부하 |
| --- | --- | --- |
| 평소 | 0~3분 | 0.2 RPS (5초에 1건) |
| 스파이크 | 3~6분 | 2 RPS |
| 회복 | 6~11분 | 0.2 RPS |

- 요청 타임아웃은 300초, 구간마다 종료 유예는 310초다. 구간이 끝나도 진행 중인 요청은 응답이나 타임아웃까지 기다린다.
- `/recommend`는 실패해도 200으로 답하므로, 응답 본문에 추천이 있을 때만 **추천 성공**으로 센다. 타임아웃과 연결 오류도 실패로 분모에 들어간다.
- 지연 시간과 성공률은 요청이 **시작된** 구간으로, 처리량은 요청이 **끝난** 구간의 응답 수 ÷ 구간 길이로 센다.
- 앱은 uvicorn 워커 1개로 실행한다. 추천 작업은 스레드 풀(기본 40개)에서 돈다.
- 추천 작업 수에는 상한(`WHATFROM_MAX_CONCURRENT_RECOMMENDATIONS`, 기본 32)이 있다. 자리가 없으면 추천 함수를 부르지 않고 503과 `Retry-After`로 바로 거절한다. 상한은 워커마다 따로 적용된다.

### 실행

앱 지표는 프로세스 전역이고, Prometheus는 이미지가 `/prometheus`를 볼륨으로 선언한다.
실행마다 앱을 새로 띄우고 익명 볼륨까지 새로 만들어야 앞 실행의 값이 섞이지 않는다.
그래서 스모크와 본 측정은 앱·모의 서버를 매번 새로 띄우고, 본 측정은 배율을 바꿀 때마다 Prometheus도 새로 띄운다.

DB를 띄운다. 임베딩은 로컬 Ollama의 `bge-m3`가 떠 있어야 한다.

```bash
make up
```

먼저 스모크로 구간마다 끝까지 관통하는지 확인한다. 배율 0.1로 짧게 돌린다.

```bash
uv run python -m whatfrom.loadtest.mock_llm \
  --results load/baselines/2026-09-26/replay-kimi-k3.json --scale 0.1 --port 8081
```

다른 터미널에서 앱을 모의 LLM에 연결해 띄운다.

```bash
WHATFROM_EMBEDDER=openai_compatible WHATFROM_LLM_PROVIDER=openai_compatible \
  WHATFROM_LLM_BASE_URL=http://127.0.0.1:8081/v1 WHATFROM_LLM_API_KEY=mock \
  uv run uvicorn whatfrom.api:app --host 0.0.0.0 --port 8000
```

```bash
docker compose --profile observability up -d
make load-smoke
```

스모크가 끝나면 앱과 모의 서버를 멈춘다.

본 측정은 배율마다 반복한다. 앱과 모의 서버가 멈춘 상태에서 Prometheus를 새로 띄운다.

```bash
docker compose --profile observability up -d --force-recreate --renew-anon-volumes prometheus
```

모의 서버를 이번 배율로 띄운다.

```bash
uv run python -m whatfrom.loadtest.mock_llm \
  --results load/baselines/2026-09-26/replay-kimi-k3.json --scale <배율> --port 8081
```

앱을 다시 위와 같이 띄운 뒤 본 측정을 돌린다.

```bash
date -u +%FT%TZ                     # 측정 시작 시각을 적어 둔다
make load LOAD_LABEL=<라벨>          # 최대 약 16분
```

k6가 끝나면 서버 지표를 저장한다. 스크립트는 진행 중 요청과 점유한 자리가 둘 다 0이 될 때까지 기다린 뒤, 한 번 더 수집될 시간을 두고 조회한다.

```bash
python3 load/snapshot_prometheus.py --start <적어 둔 시각> --out load/baselines/<폴더>/<라벨>-prometheus.json
```

| 분포 | 모의 서버 `--scale` | `LOAD_LABEL` |
| --- | --- | --- |
| Kimi 실측 | 1.0 | `kimi-x1.0` |
| 빠른 LLM | 0.1 | `fast-x0.1` |

두 실행 사이에는 앱과 모의 서버를 멈추고, Prometheus를 새로 띄우는 단계부터 반복한다.

- 결과는 `load/results/<시각>-<라벨>.json`에 저장된다(Git 추적 제외).
- 서버 지표는 앱의 `/metrics`로 나가고, Grafana `http://localhost:3000`의 "whatfrom 부하" 대시보드에서 본다.

### 기준 측정 결과 (2026-09-30, 동시성 제한)

원본 k6 결과와 Prometheus 조회 결과는 [`load/baselines/2026-09-30-admission/`](load/baselines/2026-09-30-admission/README.md)에 있다. 모의 LLM 입력과 부하, 질문 순서는 [이전 측정](docs/measurement-history.md#이전-측정-동시성-제한-전-2026-09-26)과 같다.

두 가지를 바꾼 뒤 같은 부하로 다시 쟀다.

- `/recommend`를 비동기 함수로 바꿔, 응답 모델 검증이 스레드 풀을 다시 기다리지 않는다. 요청이 스레드 풀 줄을 한 번만 선다.
- 추천 작업 수에 상한(32)을 두고, 넘치는 요청은 기다리지 않고 503으로 거절한다.

**시작 구간별 지연과 추천 성공률**

| 분포 | 구간 | n | p50 (전체) | p95 (전체) | 받아 준 요청 p95 | 추천 성공률 | 거절 | 보내지 못함 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kimi 실측 | 평소 | 37 | 66.4초 | 119.7초 | 119.7초 | 97.3% | 0 | 0 |
| Kimi 실측 | 스파이크 | 361 | 0.005초 | 85.2초 | 117.2초 | 23.3% | 274 | 0 |
| Kimi 실측 | 회복 | 61 | 66.4초 | 94.8초 | 95.9초 | 96.7% | 1 | 0 |
| 빠른 LLM | 평소 | 37 | 7.0초 | 12.3초 | 12.3초 | 100% | 0 | 0 |
| 빠른 LLM | 스파이크 | 361 | 6.6초 | 12.1초 | 12.1초 | 100% | 0 | 0 |
| 빠른 LLM | 회복 | 60 | 6.8초 | 9.9초 | 9.9초 | 100% | 0 | 0 |

"받아 준 요청"은 200 응답을 받은 요청이다. 전체 지연에는 거절된 요청(수 밀리초)도 들어가, Kimi 실측 스파이크의 전체 p50이 0.005초다.

**완료 구간별 초당 추천 성공**

| 분포 | 평소 | 스파이크 | 회복 | 마지막 대기 |
| --- | --- | --- | --- | --- |
| Kimi 실측 | 0.12 | 0.39 | 0.25 | 0.14 (90초) |
| 빠른 LLM | 0.19 | 1.92 | 0.25 | 0.54 (2초) |

거절(503)도 응답이라 초당 응답 수는 부풀어 보인다. 처리량 비교에는 초당 추천 성공을 쓴다.

**서버 쪽 (Prometheus)**

| 분포 | 시작한 작업 | k6가 받은 200 | 헛일 | 점유 최고치 / 상한 | 진행 중 요청 최고치 | 스레드 대기 p50 / p95 | 거절 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Kimi 실측 | 184 | 184 | 0% | 32 / 32 | 32 | 0.03초 / 0.05초 | 275 |
| 빠른 LLM | 458 | 458 | 0% | 17 / 32 | 17 | 0.03초 / 0.05초 | 0 |

**동시성 제한 전(2026-09-26)과 비교 (Kimi 실측)**

| 항목 | 제한 전 | 제한 후 |
| --- | --- | --- |
| 받아 준 요청 p95 (가장 느린 구간) | 구분 없음, 전체 p95 300.2초 | 119.7초 |
| 서버가 처리했지만 클라이언트가 받지 못한 요청 | 459건 중 395건 | 0건 |
| 진행 중 요청 최고치 | 371 | 32 |
| 스레드 대기 p95 | 300초 이상 | 0.05초 |
| 스파이크 초당 추천 성공 | 0.08 | 0.39 |
| 스파이크 추천 성공률 | 6.9% | 23.3% |
| 회복 구간 추천 성공률 | 4.9% | 96.7% |

**받아 준 요청은 평소 속도로 끝났다.** 세 구간 모두 받아 준 요청의 p95가 96~120초로, 유료 평가에서 요청 하나의 최대(137.8초) 안이다. 요청이 스레드를 기다린 시간은 p95 0.05초다.

**헛일이 없어졌다.** 서버가 시작한 184건이 모두 클라이언트에 200으로 전달됐다. 제한 전에는 서버가 459건을 처리했지만 k6가 제시간에 받은 것은 64건이었다.

**몰림이 끝나자 바로 회복했다.** 스파이크 동안 넘친 요청은 쌓이지 않고 거절됐으므로, 회복 구간 요청은 평소처럼 받아졌다(성공률 96.7%, 거절 1건).

**스파이크 성공률은 크게 오르지 않는다.** 처리량의 상한은 추천 작업 수와 요청 시간으로 정해진다. 스파이크 동안 서버는 분당 25~28건(초당 약 0.45건)의 작업을 시작했고, 이는 상한 32를 한 요청 시간(약 65초)으로 나눈 값(초당 약 0.49건)과 비슷하다. 넘치는 몫은 타임아웃 대신 503이 됐다. 이것이 의도한 동작이다.

**빠른 LLM에서는 달라진 것이 없다.** 점유 최고치가 17로 상한 아래였고, 모든 구간 성공률 100%, 거절 0이다. 제한 전 결과와 같다.

한계는 다음과 같다.

- 상한 32는 스레드 풀(40)에 여유를 둔 시작값이지, 최적값으로 검증한 값이 아니다. LLM 모델을 고른 뒤 그 모델의 시간으로 다시 잰다.
- 거절된 사용자는 `Retry-After`(60초) 뒤 다시 시도해야 한다. 잠깐 기다려 받아 주는 큐는 다음 단계(F12)의 범위다.
- 모의 LLM과 로컬 한 대 측정의 한계는 [이전 측정](docs/measurement-history.md#이전-측정-동시성-제한-전-2026-09-26)과 같다.

## 개발

테스트에는 PostgreSQL과 pgvector가 필요하다. `make up`으로 DB를 시작하면 초기화 스크립트가 테스트용 `whatfrom_test` DB도 생성한다.

```bash
make up
make test    # 외부 API 호출과 API 비용 없이 실행
make lint
```

LLM과 임베딩 호출은 fake 구현이나 모의 응답으로 테스트한다.
실제 모델을 사용하는 golden set 평가는 실행마다 결과가 달라질 수 있고 비용이 발생하므로 CI에서 실행하지 않는다.
