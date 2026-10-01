# Spec review followups

2026-10-01에 최초 commit `d852387`부터 HEAD(`63bd1bd`)까지를 `docs/superpowers/specs/`의 spec 13개와 대조해 리뷰했다. 리뷰는 Standards와 Spec 두 축으로 나눠 진행했고, 그 결과를 issue로 남긴다.

리뷰에서 나온 지적이고 maintainer가 아직 검토하지 않았으므로, 모든 issue는 `needs-triage`로 시작한다. 번호는 대략적인 우선순위다. 01–10은 Spec 축, 11–16은 Standards 축이다.

## Issue로 만들지 않은 지적

다음 지적은 의도된 선택이거나 이미 근거가 남아 있어 issue로 만들지 않았다.

- `retrieval.search_candidates`가 테스트에서만 쓰인다. candidate-selection spec §6이 범위 밖으로 명시했다.
- F10 회복 구간이 앞 구간의 질문을 다시 쓴다. 코드 주석에 의도라고 적혀 있다.
- `semantic_question`이 빠져 원래 질문을 그대로 임베딩한다. search-plan spec §3.1이 의도적으로 뺐다.
- spec보다 넓게 구현된 부분: `plan_error` 인자, 추출 prompt 규칙, `normalize_plan`의 별칭 확장, `WINDOWS_RELEASE`, mock 서버의 plan 검증, `snapshot_prometheus.py`. 리뷰에서 모두 합리적인 이탈로 판단했다.

## Review Log

### 2026-10-01: Codex review
- ✅ Fallback에서 digest를 접는 순서 때문에 허용된 태그가 사라짐 (02): 처리 순서를 "지원 줄기 거르기 → `allowed_ids` → digest 접기 → 정렬·limit"으로 고쳤다. 같은 digest 중 긴 이름만 허용된 경우의 회귀 테스트를 완료 조건에 넣었다. 기존 코드도 거르기를 먼저 한다.
- ✅ 계층 테스트의 완료 조건이 규칙을 다 강제하지 못함 (10): `whatfrom.core` 외의 내부 import를 모두 금지하도록 바꿨다. `from whatfrom import …` 형태를 해석하는 parser 수정과 그 fixture 테스트를 완료 조건에 넣었다.
- ✅ 시간 기록 리팩터링의 `CaseScore` 생성자 호환성 (15): 테스트 fixture는 새 시간 기록 객체로 고치고, 출력 assertion만 그대로 유지한다. 호환 생성자는 두지 않는다.
- ✅ PyYAML 설명이 여전히 틀림 (06): PyYAML이 필요한 명령으로 `eval`과 `load-questions`를 모두 적는다. CLI import와 명령 실행을 구분한다.
- ✏️ 용어 검사 정규식이 "리포트"까지 잡음 (16): 검사를 예외 처리하는 대신 "리포트" → `report`를 범위에 넣었다. 음차하지 않는다는 규칙과 맞추기 위해서다.
