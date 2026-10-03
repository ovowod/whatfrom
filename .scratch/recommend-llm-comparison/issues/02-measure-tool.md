# 02: 측정 도구와 결과 검증

**What to build:** 새 실험 폴더에 측정 설정 11개와 `measure`를 만든다. 고정 검색 조건으로 추천 단계만 재고, 입력과 설정이 같은지 검증한 결과만 복사한다. spec §측정 설정, §입력 고정과 결과 검증, §요청 간격.

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 측정 설정을 이름으로 고를 수 있고, 설정마다 API 종류, `EXTRA_BODY`, 키 환경 변수, 가격, 요청 간격을 갖는다.
- [ ] `--plans kimi-max-r1.json`, `--embedder`, `--llm-provider`를 항상 명시해 eval을 실행한다.
- [ ] 공급자 키는 `WHATFROM_RECOMMEND_LLM_API_KEY`로만 넘기고, 기록과 출력에 남기지 않는다.
- [ ] spec의 검증 항목이 하나라도 어긋나면 결과를 복사하지 않는다. 특히 embedder와 문항별 `advise_prompt`를 검사하고, 어긋난 문항 ID를 알려 준다.
- [ ] 회차 전후 snapshot(repository 목록, 허용 정답 태그 digest)을 남기는 명령이 있다.
- [ ] 결과는 `results/<설정>-r<회차>.json`으로 복사하고, 이미 있으면 멈춘다.
- [ ] 가짜 eval 결과로 각 검증 실패를 확인하는 테스트가 있다.
