# 01: 라벨 고정 입력과 검색 1위 대조군

**What to build:** `whatfrom eval`이 조건 추출 단계 대신 golden set 라벨(`expected_plan`의 필드와 `requires_repositories`의 repository)을 검색 조건으로 쓸 수 있다. 추천 단계를 부르는 실행과, 부르지 않고 저하 경로로 후보와 근거만 얻는 실행(LLM 0회)을 모두 지원한다. 후보가 있는 모든 실행은 검색 1위 대조군(판정 없이 검색 1위 후보를 추천으로 본 추천 정확도)을 기존 대조군 옆에 낸다.

spec: `.scratch/v2-eval/spec.md` (라벨 고정 입력, 검색 1위 대조군)

**Blocked by:** None (can start immediately)

**Status:** done

- [x] 라벨 고정 입력 실행은 조건 추출 단계를 부르지 않고, 검색 조건이 문항의 라벨과 같다.
- [x] 후보는 운영 경로와 같은 검색(조건 필터 포함)으로 나오고, eval이 순서를 바꾸지 않는다.
- [x] 추천 단계를 부르지 않는 라벨 고정 입력 실행은 LLM 호출이 0회이고, 후보와 검색 지표와 대조군을 낸다.
- [x] repository가 둘 이상인 문항이 있으면 실행이 멈춘다.
- [x] 결과 meta의 mode로 라벨 고정 입력 실행을 구분할 수 있고, 조건 추출 지표는 내지 않는다.
- [x] 기존 `--retrieval-only`와 `--plans`의 동작은 그대로다.
- [x] 검색 1위 대조군은 추천 정확도와 같은 허용 규칙(digest 인정 포함)으로 채점하고, 후보가 없는 문항은 오답이다.
- [x] 검색 1위 대조군이 요약과 결과 JSON(집계와 문항별)에 나온다.
- [x] CLI seam(fake embedder, fake provider, 테스트 DB)과 집계 함수 테스트로 위를 확인한다.
