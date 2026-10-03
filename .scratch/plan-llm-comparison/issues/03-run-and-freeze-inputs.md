# 03: 측정 실행과 입력 고정

**What to build:** 측정 설정과 회차를 지정하면 운영과 같은 조건으로 평가를 돌리고, 결과 JSON을 실험 폴더에 이름 붙여 복사한다. 측정 전후 repository 목록을 기록해 입력이 같았는지 남긴다. spec §실험 스크립트, §입력 고정, §측정 규칙.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] kimi-max는 전체 평가, 나머지는 `--plan-only`로 돌린다.
- [ ] 항상 `--llm-provider openai_compatible`을 넘기고, LLM timeout을 120초로 고정한다.
- [ ] 측정 설정을 `WHATFROM_PLAN_LLM_*`로 넣고, 공급자 키는 `WHATFROM_PLAN_LLM_API_KEY`로만 넘긴다. 키는 결과와 출력에 남지 않는다.
- [ ] 결과 JSON을 측정 설정 이름과 회차를 붙여 실험 폴더에 복사한다.
- [ ] DB의 repository 목록과 repository별 색인 문서 수를 기록하는 명령이 있다.
- [ ] 미측정 문항이 있는 결과면 실패로 알리고 복사하지 않는다.
- [ ] fake provider와 fixture DB로 명령 조립, 결과 복사, 미측정 문항 거부를 확인하는 테스트가 있다.
