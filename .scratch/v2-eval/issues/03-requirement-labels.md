# 03: 판단 요구 라벨

**What to build:** golden set 문항마다 기대 판단 요구 목록(`requirements`)이 있다. loader가 이 키를 필수로 검증한다. 라벨은 질문만 보고 쓴 초안을 근거와 대조하고 사람이 40문항 모두 검토해 넣는다.

spec: `.scratch/v2-eval/spec.md` (golden set, 라벨 작업 절차)

**Blocked by:** None (can start immediately)

**Status:** ready-for-human

- [ ] loader: `requirements` 키가 없으면 거부, 빈 목록은 수락, 빈 문장이나 공백만 있는 문장은 거부한다.
- [ ] golden set `version`을 2로 올린다. 머리 주석에 판단 요구 작성 기준(짧은 영어 문장, 후보 하나만 보고 판정 가능, 필터 조건과 선호는 쓰지 않음)을 적는다.
- [ ] subagent가 질문만 보고(accept, reject, rationale, conditions 없이) 40문항의 초안을 쓴다.
- [ ] 초안을 문항의 conditions, reject, rationale과 대조해 어긋나는 문항을 표시한 검토 표를 만든다. 표시 기준: 필터로 거를 수 있는 요구, 선호, 베이스 이미지에 이미 있는 것을 요구함, reject를 설명하는 요구가 없음, 빌드 stage 문항을 런타임 요구로 씀.
- [ ] 사용자가 40문항을 모두 검토하고, 확정한 라벨을 golden set에 반영한다.
- [ ] 실제 파일 테스트: 모든 문항이 `requirements`를 가진다.
