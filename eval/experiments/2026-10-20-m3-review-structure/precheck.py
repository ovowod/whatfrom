"""M3 사전 확인: 판정 칸이 가장 많은 문항으로 판정 단계를 한 번 불러 finish_reason을 본다.

eval과 같은 경로(label 고정 입력 → 검색 → 근거 번호 → 판정 prompt)로 prompt를 만들고,
OpenAICompatibleProvider와 같은 요청 본문을 보낸다. provider는 finish_reason을 버리므로
원래 응답 본문을 직접 읽는다.
"""

import sys

from whatfrom.cli import _label_plans
from whatfrom.core.config import settings
from whatfrom.core.contracts import JudgmentTable, SearchPlan
from whatfrom.core.db import make_engine, session_factory
from whatfrom.core.embed import get_embedder
from whatfrom.eval.goldenset import load_goldenset
from whatfrom.recommend.advisor import build_prompt
from whatfrom.recommend.evidence import number_evidence
from whatfrom.recommend.judge import CANDIDATES_HEADING, SYSTEM_PROMPT, _normalize
from whatfrom.recommend.llm import OpenAICompatibleProvider, stage_llm
from whatfrom.search.retrieval import search_candidates_with_plan

goldenset = load_goldenset(__import__("pathlib").Path("eval/goldenset.yaml"))
open_session = session_factory(make_engine(settings.database_url))
embedder = get_embedder("openai_compatible")
stage = stage_llm(settings, "recommend")
provider = OpenAICompatibleProvider(
    base_url=stage.base_url, model=stage.model, api_key=stage.api_key, extra_body=stage.extra_body
)

for case_id in sys.argv[1:]:
    case = next(c for c in goldenset.cases if c.id == case_id)
    plan = SearchPlan.model_validate(_label_plans([case])[case.id])
    vector = embedder.embed([case.question])[0]
    with open_session() as session:
        candidates = search_candidates_with_plan(session, vector, plan).candidates
    evidence, candidates = number_evidence(candidates)
    prompt = build_prompt(
        case.question, candidates, evidence, case.requirements, CANDIDATES_HEADING
    )
    body = provider._post(SYSTEM_PROMPT, prompt, JudgmentTable, "judgment_table", lambda _: None)
    choice = body["choices"][0]
    usage = body.get("usage", {})
    dropped = missing = None
    try:
        table = JudgmentTable.model_validate_json(choice["message"]["content"])
        cells = len(table.judgments)
        parsed = "ok"
        # 서버 정규화를 그대로 돌려, 후보 이름을 잘못 옮겨 버려지는 판정이 없는지 본다.
        kept, thrown = _normalize(table.judgments, candidates, evidence, len(case.requirements))
        dropped = len(thrown)
        missing = sum(cell.problem == "missing" for cell in kept)
    except Exception as exc:  # 사전 확인이라 어떤 실패든 그대로 보여 준다
        cells, parsed = None, f"invalid: {exc}"[:200]
    print(
        f"{case_id}: candidates={len(candidates)} requirements={len(case.requirements)} "
        f"expected_cells={len(candidates) * len(case.requirements)} "
        f"finish_reason={choice.get('finish_reason')} parsed={parsed} cells={cells} "
        f"dropped={dropped} missing={missing} "
        f"prompt_tokens={usage.get('prompt_tokens')} "
        f"completion_tokens={usage.get('completion_tokens')}"
    )
