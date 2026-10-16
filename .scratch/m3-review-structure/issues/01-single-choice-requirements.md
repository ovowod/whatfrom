# 01: 단일 선택에 판단 요구와 인용 지시를 넣는다

**What to build:** label 고정 입력에서 문항의 판단 요구가 검토 단계까지 넘어가고, 단일 선택 prompt가 번호를 붙인 판단 요구와 새 인용 지시를 싣는다. 판단 요구는 검색 조건과 별도의 입력이다. 이 전달 경로는 02의 판정 단계도 쓴다.

spec: `.scratch/m3-review-structure/spec.md` (판단 요구 전달, 단일 선택 보강)

**Blocked by:** None (can start immediately)

**Status:** done

- [x] label 고정 입력은 문항의 `requirements`를 검토 단계에 넘긴다. 다른 mode와 `/recommend`는 빈 목록을 넘긴다.
- [x] `SearchPlan`과 조건 추출 단계의 출력 schema는 바뀌지 않는다.
- [x] 단일 선택 prompt에 판단 요구가 번호와 함께 실린다. 빈 목록이면 판단 요구가 없다고 분명히 적는다.
- [x] 인용 지시가 "주장에 쓴 문서 사실마다 그 사실을 담은 문장을 인용한다. 사실 하나에 문장이 둘 이상 필요하면 모두 인용한다"는 뜻을 담는다. 02의 판정 단계 prompt도 같은 문구를 쓸 수 있게 둔다.
- [x] 후보 줄과 근거의 형식은 바뀌지 않는다. 인용 검토 도구가 기존 결과와 새 결과에서 모두 후보 정보를 읽는다.
- [x] `Recommendation` 출력 schema와 `/recommend` 응답 형식은 바뀌지 않는다.
- [x] CLI seam: fake provider로 label 고정 입력을 돌리면 문항별 prompt 기록에 그 문항의 판단 요구 문장이 있다. 판단 요구가 없는 문항과 `--plans` 실행의 prompt에는 판단 요구가 없음이 적힌다.
