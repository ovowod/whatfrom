# whatfrom 설계

시스템이 무엇을 지키고 어떻게 나뉘는지 적는다. 무엇을 어떤 순서로 만드는지는 [roadmap](roadmap.md)에 있다.

이 문서는 v2 설계다. 추천 1개와 Dockerfile 초안을 만들던 v1에서 후보 비교표로 바뀌었다([ADR 0003](adr/0003-judge-candidates-instead-of-recommending.md)).
roadmap의 Phase v2를 마치기 전까지 코드는 v1 동작을 일부 남기고 있다.

## 1. 하는 일

자연어 요구사항을 받아 **실재하는 컨테이너 이미지 후보**를 문서 근거와 함께 비교해 준다.
주 사용자는 프로젝트 파일을 읽을 수 있는 LLM agent다. 사람도 같은 API를 쓴다.

이름은 Dockerfile의 첫 줄에서 왔다.

```dockerfile
FROM ???
```

agent는 Dockerfile을 직접 쓴다. 진입점, 의존성 파일, 포트를 아는 쪽이 그쪽이기 때문이다.
agent가 혼자 얻기 어려운 것은 실재하는 태그, 아키텍처별 digest와 크기, 그리고 공식 문서의 근거다. 이 시스템은 그것을 준다.

입력 예시:

> FastAPI 서비스이고 numpy를 사용해. 이미지 크기는 작았으면 좋겠지만 빌드 안정성이 더 중요해. ARM64에서도 실행해야 해.

agent는 질문과 함께 구조화 힌트(언어와 버전, 대상 아키텍처, 판단 요구 등)를 보낼 수 있다.

출력 예시(후보 비교표):

| 후보 | numpy must build reliably | build stability over size |
| --- | --- | --- |
| `python:3.13-slim@sha256:…` | 만족 — "…glibc…" (Image Variants) | 만족 — "…" |
| `python:3.13-alpine@sha256:…` | 불만족 — "…musl libc…" (Image Variants) | 불만족 — "…" |

후보마다 아키텍처, 크기, 배포판, 출처 URL, 수집 시점이 붙는다. 순위 1위가 추천이다.

## 2. 불변식

> **LLM은 이미지 이름과 태그를 만들지 않고, 근거 없는 주장을 만들지 않는다.**
> 후보는 검색이 정하고, 순위는 서버가 정한다. LLM은 후보마다 판단 요구를 판정하고 그 근거를 인용한다.

이 제약은 안전장치가 아니라 아키텍처를 정하는 조건이다.

- LLM에 문서를 넘기고 답을 받는 구조는 쓸 수 없다.
- 후보 생성(검색), 판정(LLM), 순위(서버)가 서로 다른 단계로 나뉜다.
- 판정의 근거 인용은 넘겨준 근거 chunk 안에 글자 그대로 있어야 한다. 검증은 결정론적이다.
- 이 프로젝트의 기술적 주장은 모두 여기서 나온다.

## 3. 범위

### 포함

- 공식 이미지 10개: `python`, `node`, `eclipse-temurin`, `golang`, `nginx`, `postgres`, `redis`, `debian`, `ubuntu`, `alpine`
- 구조화 데이터: 이미지, 태그, 아키텍처, 크기, digest, 업데이트 시각
- 서술형 데이터: 공식 README
- 입력: 자연어 질문과 선택적인 구조화 힌트
- 출력: 후보 비교표(판정과 근거 인용), 후보별 사실(digest, 아키텍처, 크기, 배포판), 근거 chunk, 출처, 수집 시점

### 제외

| 항목 | 이유 |
| --- | --- |
| CVE / Trivy 스캔 | 데이터 부피와 갱신 비용이 MVP 범위를 넘는다. 2단계로 미룬다 |
| Docker Hub 전체 / 비공식 이미지 | 품질을 관리할 수 없다 |
| 사용자 인증·멀티테넌시 | 드는 시간에 비해 얻는 것이 적다 |
| 프론트엔드 | API와 평가 report로 충분하다 |
| Dockerfile 생성 | 질문만으로는 진입점, 의존성 파일, 포트를 알 수 없다. 프로젝트를 보는 agent가 더 잘 쓴다([ADR 0003](adr/0003-judge-candidates-instead-of-recommending.md)) |

## 4. 아키텍처

요청 경로:

```
자연어 요구사항 (+ 구조화 힌트)
      │
      ▼
 ┌─────────────┐
 │   query     │  LLM #1: 요구사항 → 검색 조건 + 판단 요구(JSON). 힌트의 값은 그대로 쓴다
 └─────────────┘
      │
      ▼
 ┌───────────────────────────────────────┐
 │             retrieval                 │  LLM 없음. 결정론적.
 │  ┌──────────────┐  ┌───────────────┐  │
 │  │ 구조화 필터   │→ │ 벡터 검색      │  │
 │  │ SQL 빌더     │  │ (후보로 제한)  │  │
 │  └──────────────┘  └───────────────┘  │
 │  repository를 정하지 못하면 상위 여러   │
 │  repository에서 후보를 섞는다           │
 └───────────────────────────────────────┘
      │  후보 이미지 N개 + 근거 chunk
      ▼
 ┌─────────────┐
 │   judge     │  LLM #2: 후보 × 판단 요구마다 판정 + 근거 인용
 └─────────────┘
      │
      ▼
 ┌─────────────┐
 │   verify    │  LLM 없음. 후보가 DB에 실재하는가? 인용이 근거 chunk에 글자 그대로 있는가?
 └─────────────┘
      │
      ▼
 ┌─────────────┐
 │   rank      │  LLM 없음. 판정으로 순위를 정한다. 동점이면 검색 순위
 └─────────────┘
      │
      ▼
    응답 (후보 비교표 + digest, 출처 URL, 수집 시점)
```

요청 경로와 따로 배치로 실행하는 것:

```
collector (CLI 배치)  →  PostgreSQL + pgvector  ←  indexer (chunk 나누기 + 임베딩)
      Docker Hub API                                  공식 README, upstream 문서(Phase v2)
```

agent는 HTTP API를 MCP server로 감싼 tool로 부른다. MCP server는 API를 노출하는 얇은 adapter이고 로직을 갖지 않는다.

## 5. 컴포넌트 경계

| 모듈 | 하는 일 | 입력 → 출력 | LLM |
| --- | --- | --- | --- |
| `collector` | Docker Hub 수집 | repository 목록 → images·variants·documents 행 | ✗ |
| `indexer` | 문서 chunk 나누기·임베딩 | documents → embedding 채움 | ✗ |
| `query` | 요구사항 해석 | 자연어 + 힌트 → `SearchPlan` + 판단 요구 | ✓ |
| `retrieval` | 후보 생성 | `SearchPlan` → `Candidate[]` | ✗ |
| `judge` | 판정 | `Candidate[]` + 판단 요구 → 후보 비교표 | ✓ |
| `verify` | 실재성·인용 검증 | 후보 비교표 → 검증한 후보 비교표 | ✗ |
| `rank` | 순위 | 검증한 후보 비교표 → 순위 | ✗ |
| `llm` | 공급자 추상화 | Protocol. 외부 API / Ollama / Fake | — |
| `api` | HTTP 경계 | FastAPI 라우터·검증·에러 매핑 | ✗ |
| `mcp` | agent 경계 | API를 MCP tool로 노출 | ✗ |
| `eval` | 채점 | golden set → 지표 report | ✗ |

LLM을 쓰는 모듈은 `query`와 `judge` 둘뿐이고, 둘 다 입출력이 Pydantic 모델이다. 그래서 다음이 가능하다.

- `retrieval`, `verify`, `rank`는 LLM 없이 테스트한다. 검색 품질과 순위를 LLM의 비결정성과 떼어 잴 수 있다.
- `query`와 `judge`는 fake provider로 테스트한다. CI가 API 비용 없이 돈다.
- 큐·캐시·배칭을 붙일 자리가 분명하다. LLM 호출 2곳, 임베딩 1곳이다.

`collector`는 API 서버와 다른 프로세스에서 도는 CLI 배치다. **웹 요청 경로에서는 Docker Hub를 부르지 않는다.**
외부 API 장애가 서비스 장애로 번지지 않고, 수집이 실패해도 마지막 스냅샷으로 서비스가 계속된다.

## 6. 데이터 모델

**태그와 아키텍처는 1:N이다.** `python:3.12-slim`은 태그 하나지만 amd64, arm64, s390x마다 digest와 크기가 다르다.
한 테이블에 합치면 "ARM64에서 이 이미지 크기는?"에 답할 수 없다.

```sql
repositories(
  name PK, is_official, description, source_url, collected_at
)

image_tags(
  id PK, repository FK, tag, manifest_digest,
  last_pushed_at, collected_at,
  -- 태그명에서 규칙으로 파생 (LLM 아님)
  language_version,      -- '3.12.4'
  version_major_minor,   -- '3.12'
  distribution,          -- 'debian' | 'alpine' | 'ubuntu'
  distro_codename,       -- 'bookworm' | 'trixie'
  variant,               -- 'slim' | 'full' | 'alpine'
  UNIQUE(repository, tag)
)

image_variants(
  id PK, tag_id FK, os, architecture, arch_variant,
  digest, size_bytes,
  UNIQUE(tag_id, os, architecture, arch_variant)
)

documents(                        -- 부모
  id PK, repository FK, doc_type, section_title,
  content, source_url, collected_at
)

document_chunks(                  -- 자식 (검색 단위)
  id PK, document_id FK, chunk_index, content,
  embedding vector(1024)
)
```

### 파생 컬럼은 규칙으로 만든다

`3.12-slim-bookworm` → `{version: 3.12, variant: slim, distribution: debian, codename: bookworm}` 변환은 순수 함수인 태그 파서가 맡는다.
LLM에 맡기면 결과가 매번 다르고, 비싸고, 틀린다.
파서는 테이블 주도 테스트로 수십 케이스를 고정할 수 있어서 신뢰도를 가장 싸게 얻는 곳이다.

파생 컬럼은 모두 nullable이다. 수집은 이 컬럼을 비운 채 태그를 적재하고, 태그 파서가 나중에 채운다.
그래서 파서는 이미 적재된 행에 다시 실행할 수 있어야 한다.

### documents와 document_chunks를 나눈 이유: Parent-Child Retrieval

검색은 chunk로 하고, LLM에는 그 chunk가 속한 섹션 전체를 넘긴다.
"Image Variants" 섹션의 한 문장이 걸렸을 때, 섹션 전체가 있어야 alpine과 slim을 제대로 비교할 수 있다.

### 임베딩 모델

`bge-m3`(1024차원)를 쓴다. 질문은 한국어, README는 영어라서 다국어 모델이 필요하다.

구조로도 언어 차이를 막는다. `SearchPlan`에 **영어로 쓴 `semantic_question` 필드**를 두어, 벡터 검색은 항상 영어 대 영어로 한다.
다국어 모델과 영어 정규화를 함께 쓰는 셈이다.

## 7. 핵심 계약

`SearchPlan`이 검색의 중심 계약이다. 구조화 힌트도 같은 모양으로 받는다.

```python
class SearchPlan(BaseModel):
    repository: str | None  # 'python'
    version_prefix: str | None  # '3.12'
    architectures: list[str]  # ['arm64']
    distributions: list[str]  # ['debian']
    exclude_distributions: list[str]  # ['alpine']
    max_size_mb: float | None
    semantic_question: str  # 영어. 벡터 검색용
    requirements: list[str]  # 판단 요구. 영어 문장. 예: 'numpy must build reliably'
```

**LLM은 이 JSON만 만든다.** SQL은 검증된 필드로 백엔드가 조립한다.
읽기 전용 계정, SELECT 허용 목록, LIMIT 강제 같은 방어가 필요 없어지는 것은 아니다. 다만 **임의 SQL이 만들어질 경로가 애초에 없다.**
Text-to-SQL의 위험은 이렇게 없앴다.

필터로 거를 수 있는 요구(아키텍처, 버전, 크기 상한)는 검색 조건에 들어가 SQL이 처리한다. LLM에 판정을 묻지 않는다.
필터로 거를 수 없는 요구만 판단 요구가 된다.

판정 단계의 계약은 후보 비교표다.

```python
class Judgment(BaseModel):
    candidate: str  # 후보 이미지. 후보 목록에 있어야 한다
    requirement: int  # 판단 요구의 번호
    verdict: Literal["satisfied", "violated", "unknown"]
    chunk_id: int | None  # 근거 chunk. 넘겨준 근거 안에 있어야 한다
    quote: str | None  # chunk 안에 글자 그대로 있어야 한다
```

- verify는 후보가 DB에 실재하는지와, `chunk_id`가 넘겨준 근거 안에 있고 `quote`가 그 chunk 안에 글자 그대로 있는지(공백 정규화)를 본다.
- 인용 검증에 실패한 칸은 `unknown`으로 내리고 그 사실을 응답에 표시한다. 비교표 전체를 버리지 않는다.
- 순위는 불만족이 적은 순서, 그다음 만족이 많은 순서, 그다음 검색 순위로 정한다.

판단 요구가 없는 질문(예: "slim과 full의 차이가 뭐야?")은 판정하지 않고 후보와 근거만 돌려준다.

## 8. 실패 모드와 단계적 저하

원칙은 **실패하면 502를 주는 것이 아니라, 한 단계씩 나빠지더라도 끝까지 답하는 것**이다. 사용자가 빈손으로 돌아가는 경로가 없어야 한다.

```
정상:        SearchPlan → 구조화 필터 + 벡터 검색 → 판정 → 검증 → 순위
             ↓ query LLM 실패 (1회 재시도 후) / 스키마 위반
1단계 저하:  힌트만으로, 힌트도 없으면 SearchPlan 없이 벡터 검색만 → 판정
             ↓ 필터 결과 0건
2단계 저하:  약한 조건부터 순서대로 완화 + "arm64 조건을 완화했습니다" 명시
             ↓ judge LLM 실패
3단계 저하:  후보 비교표 없이 후보, 사실, 근거 chunk만 반환. 순위는 검색 순위
```

**인용 검증이 핵심이다.** 인용이 근거 chunk에 없으면 그 판정은 버리고 알 수 없음으로 둔다.
그럴듯한 근거를 지어낸 판정을 내보내느니 모른다고 한다. 불변식을 지키는 마지막 관문이다. 여기서 타협하면 프로젝트의 주장 전체가 무너진다.

### verify가 판정을 버려도 다시 판정하지 않는다

`judge`를 다시 부르면 시스템 프롬프트, 후보, 근거가 모두 같다. 다른 답을 기대할 근거가 없다.

- 의미 있는 재판정이 되려면 실패 이유를 프롬프트에 넣어야 한다. 그건 재시도가 아니라 별개의 기능이다.
- 비용은 확실하다. 판정 단계는 요청에서 가장 긴 LLM 호출이다.

다시 볼 조건은 인용 검증 실패율을 관측할 수 있고, 그 값이 의미 있게 높을 때다.
넣게 되더라도 실패 이유를 프롬프트에 넣는 형태로만 넣는다.

### 외부 의존성별 정책

**LLM API**

- 연결 timeout 3초, 읽기 timeout은 `llm_timeout_seconds`(기본 120초)로 따로 둔다.
  - kimi-k3 실측이 59초라 짧은 timeout으로는 끝나지 않는다.
  - 부하 측정에 쓴 실측 분포는 중앙값 51.9초, 최대 120.1초다.
- `429`와 `5xx`만 지수 백오프(+jitter)로 최대 2회 재시도한다.
- `400`과 스키마 위반은 재시도하지 않는다. 같은 결과가 나온다.
- 서킷브레이커와 동시 호출 제한을 둔다.

**Docker Hub**

- 요청 경로에서 부르지 않는다. 수집 배치가 실패해도 마지막 스냅샷으로 서비스는 정상 동작한다.

**PostgreSQL**

- 커넥션 풀에 상한을 둔다. SQLAlchemy 기본값을 설정으로 명시했고, 부하 측정 뒤 다시 본다.
  - `db_pool_size` 5
  - `db_max_overflow` 10
  - `db_pool_timeout_seconds` 30초
- statement timeout은 API engine에만 `db_statement_timeout_ms` 5000으로 건다.
  - CLI 배치의 긴 upsert와 색인에는 걸지 않는다.
  - timeout을 넘긴 SQL은 다른 DB 오류처럼 500이 된다.

## 9. 테스트 전략

LLM을 두 모듈에 가둔 설계의 이득을 여기서 얻는다.

| 대상 | 방법 | 네트워크/API |
| --- | --- | --- |
| 태그 파서 | 테이블 주도 단위 테스트 약 50케이스 | 없음 |
| `retrieval` | 시드 DB fixture로 결정론적 통합 테스트 | 없음 |
| `query`·`judge` | `FakeLLMProvider` 계약 테스트 | 없음 |
| `collector` | 녹화한 HTTP 응답 fixture(respx) | 없음 |
| `verify` | **없는 태그나 근거에 없는 인용을 내놓는 FakeLLM**으로 환각 시나리오를 명시해 테스트 | 없음 |
| `rank` | 판정 조합별 단위 테스트 | 없음 |
| 품질 평가 | golden set 러너, 수동 `make eval` | 있음(비용) |

CI는 네트워크와 API 비용 없이 모두 통과해야 한다.
golden set 평가만 수동이다. 비결정적이고 돈이 들어서, 테스트가 아니라 측정 도구로 따로 둔다.

구현은 TDD로 한다. 태그 파서와 `verify`는 특히 테스트를 먼저 쓰는 쪽이 훨씬 빠르다.

## 10. 평가 지표

golden set 40문항(YAML)으로 잰다. 판정 칸마다 정답을 다는 대신, 문항의 허용 집합과 명시적 오답 집합을 재활용한다.

| 지표 | 정의 |
| --- | --- |
| 추천 정확도 | 순위 1위가 라벨의 **허용 집합**에 들어간 비율 |
| 오답 적발률 | 후보에 명시적 오답이 있을 때, 그 후보가 불만족 판정을 하나 이상 받은 비율 |
| 오판정률 | 허용 집합의 후보가 불만족 판정을 받은 비율 |
| 인용 검증 통과율 | 근거 인용이 글자 그대로 검증을 통과한 칸의 비율 |
| 인용 뒷받침률 | 인용 문장이 판정을 실제로 뒷받침하는 칸의 비율. 표본을 사람이 확인한다 |
| 태그 실재율 | 후보가 DB에 실재하는 비율 (목표 100%) |
| 조건 일치율 | 요구사항의 구조화 조건(arch, version, distro)을 만족한 비율 |
| 출처 제공률 | 답변에 출처 URL과 수집 시점이 들어간 비율 (목표 100%) |
| 문서 Hit@5 | 문항이 묻는 repository의 상위 5개 chunk에 기대 섹션이 **하나 이상** 나온 문항의 비율. 관련 근거 중 회수한 비율(Recall)이 아니라 문항마다 hit 여부를 센다 |

정답을 **허용 집합**으로 두는 이유가 있다. `python:3.13-slim`과 `python:3.12-slim`이 둘 다 타당한 질문이 있다.
정답을 하나로 강제하면 지표가 시스템 품질이 아니라 작성자 취향을 재게 된다.
라벨은 허용 이미지 집합과 **명시적 오답 집합**(예: numpy 질문에서 `alpine`)을 함께 가진다.

인용 뒷받침률을 LLM judge로 재지 않는 이유: judge의 판단을 믿으려면 judge를 다시 사람 라벨과 대조해야 한다. 표본을 사람이 직접 보는 편이 싸고 분명하다.

인프라 단계에서는 따로 잰다: p50/p95/p99, 처리량, 오류율, 큐 길이, 캐시 적중률, LLM 호출 수, 거부(429/503) 수.

## 11. 기술 스택

- FastAPI / Pydantic / SQLAlchemy
- PostgreSQL + pgvector
  - 벡터 DB를 따로 두지 않는다. 구조화 필터와 벡터 검색을 한 쿼리로 결합할 수 있는 것이 핵심 이점이다.
- 임베딩: `bge-m3` (sentence-transformers)
- LLM: 외부 API가 주력이다. `LLMProvider` Protocol로 추상화해 Ollama와 Fake를 런타임 설정으로 바꾼다.
- Redis (Phase 3)
- pytest / ruff
- Docker Compose
- k6, Prometheus, Grafana (Phase 3)

## 12. 리스크: golden set의 순환논리

"python 3.12 + numpy + arm64에 무엇이 맞나"의 정답은 결국 작성자의 판단이다.
자기가 만든 채점표로 자기 시스템을 채점하면 순환논리가 된다.

**완화책:** 정답 라벨마다 근거 URL을 반드시 남긴다(공식 문서, numpy 설치 가이드 등).
그러면 라벨이 "내 생각"이 아니라 "문서에 근거한 판단"이 된다. golden set을 읽을 때 이 규칙을 강제한다.
