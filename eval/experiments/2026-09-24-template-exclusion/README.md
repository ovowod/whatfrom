# 틀 문장 섹션 제외 (2026-09-24)

**결론: 틀 문장 섹션 10개를 빼도 문서 Hit@5는 그대로다. 다시 색인한 뒤 오른 2문항은 원본 README가 바뀐 결과다.**

공식 README 틀에서 나온, 본문 전체가 한 문장뿐인 상위 절 10개를 색인에서 뺐다.
`Image Variants` 소개문 7개와 `Supported tags and respective Dockerfile links`의 FAQ 링크 3개다.
이 실험은 그 변경의 효과와, 다시 색인한 뒤 달라진 숫자의 원인을 나눠 잰다.

## 조건

| 항목 | 값 |
| --- | --- |
| 코드 | 브랜치 `feat/digest-and-timing`, 틀 문장 제외 커밋 `788c8b7` 이후 |
| 임베딩 | bge-m3 (ollama) |
| 골든셋 | `fe366444`, 40문항 |
| 다시 색인하기 전 | 청크 344개, 섹션 135개 (`sections-before.json`) |
| 다시 색인한 뒤 | 청크 334개, 섹션 125개 (`sections-after.json`) |
| 검색 규칙 | 섹션마다 가장 가까운 청크 1개, 거리가 같으면 청크 id 순 |

`sections-*.json`은 `리포지토리|섹션 제목`마다 본문의 SHA-256이다.

## 1. 같은 청크·벡터로 틀 문장 섹션만 뺀다 (`same_vectors.py`)

다시 색인하기 전의 청크와, 임베딩 모델 비교 실험(`../2026-09-24-embedding-comparison/`)의 bge-m3 벡터 캐시를 그대로 썼다.
틀 문장 섹션만 메모리에서 빼고 쟀다. 원본 README 변경이나 청크 id 순서 변화가 섞이지 않는 비교다.

```text
청크 344 -> 334 | 뺀 섹션 10
hit5 31 -> 31 + [] - []
repo_ok 39 -> 39 + [] - []
후보 리포 선택이 바뀐 문항: ['alpine-static-binary']
근거가 바뀐 문항: 1
  alpine-static-binary - ['python|Supported tags and respective `Dockerfile` links'] + ['golang|Supported tags and respective `Dockerfile` links > Simple Tags']
```

`hit5`는 전역 상위 5개 섹션의 문서 Hit@5, `repo_ok`는 전역 상위 5개에 필요 리포지토리가 모두 있는지다.
근거는 임베딩 모델 비교 실험의 고정 검색 조건(`plans.json`)으로 고른 근거 섹션이다.

## 2. 다시 색인 전후의 섹션 비교

다시 색인하자 사라진 섹션은 틀 문장 섹션 10개뿐이고, 새로 생긴 섹션은 없다.
본문이 바뀐 섹션이 8개 있다.

- golang: `Supported tags … > Shared Tags`, `Supported tags … > Simple Tags`
- node, redis: `Supported tags and respective Dockerfile links`
- nginx: `Supported tags and respective Dockerfile links`, `Quick reference`, `Quick reference (cont.)`, `What is nginx?`

모두 docker-library/docs의 자동 갱신(`Run update.sh`, 2026-09-21~23)이다.
태그 버전, Dockerfile 링크의 커밋 해시, nginx 저장소 이름(`nginxinc` → `nginx`), nginx 로고 URL이 바뀌었고 설명 문장은 그대로다.

## 3. 바뀐 섹션만 이전 본문으로 되돌린다 (`readme_swap.py`)

다시 색인한 뒤 검색 전용 평가의 문서 Hit@5는 33/40이었다. `python-slim-vs-full`과 `node-alpine-vs-slim`이 새로 맞았다.

지금 색인에서 본문이 바뀐 8개 섹션만 다시 색인하기 전 본문으로 바꿔 메모리에서 임베딩하고 쟀다.
이전 본문은 docker-library/docs의 고정 커밋(golang `05ceefa`, node `836988a`, redis `4d7ff2d`, nginx `fba621a`)에서 받았고, 8개 모두 `sections-before.json`의 해시와 같다.
문항별 상위 5개는 `readme_swap.json`에 있다.

| 색인 | 문서 Hit@5 |
| --- | --- |
| 지금 (다시 색인한 뒤) | 33/40 |
| 바뀐 8개 섹션만 이전 본문 | 31/40 |

달라진 문항은 위 2개뿐이다. 두 문항 모두 이전 본문에서는 nginx의 `Supported tags` 섹션이 상위 5개에 있었고, 지금은 그 자리에 기대 섹션인 `Image Variants`의 alpine 하위 절이 들어왔다.
이 섹션은 링크의 커밋 해시만 바뀌었다. 태그 목록처럼 의미 없는 변화도 5위 경계의 순위를 바꿀 수 있다.

질문 벡터는 앱 경로(`/v1/embeddings`)와 임베딩 비교 실험의 캐시(`/api/embed`)가 같았다(40문항 모두 코사인 1.000000). 1과 3의 차이는 청크 쪽에서 나온다.

## 한계

- `same_vectors.py`는 다시 색인하기 전의 청크에서만 돌아간다. 지금은 청크가 바뀌어 캐시 해시 검사에서 멈춘다. 위 출력은 그때 기록한 값이다.
- `readme_swap.py`는 지금 색인(청크 334개)을 전제한다. 다시 색인하면 `sections-after.json`과 달라져 결과가 바뀔 수 있다.
- 바꾼 섹션의 청크 id는 원래 첫 청크 id 뒤에 소수로 붙였다. 거리가 정확히 같은 경우의 순서는 실제 색인과 다를 수 있다.
- 성공 판정은 섹션 제목 포함 여부다. 새로 들어온 하위 절이 질문에 충분히 답하는지는 판정하지 않았다.
