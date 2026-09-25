# src/whatfrom/loadtest/mock_llm.py
"""부하 시험용 OpenAI 호환 모의 LLM 서버.

실제 LLM으로 부하를 걸면 비용이 요청 수에 비례하고, 결과가 우리 서버가 아니라 공급자
상태(응답 시간 변동, 동시 요청 제한)로 정해진다. 이 서버는 유료 평가에 기록된 검색 조건과
추천을 돌려주고, 기록된 시간 × 배율만큼 기다린다. 앱은 LLM 주소만 바꾸면 되므로 HTTP
클라이언트, 연결 풀, 타임아웃이 운영과 같은 경로를 탄다.

사용(프로젝트 루트에서):
    uv run python -m whatfrom.loadtest.mock_llm \\
        --results eval/results/<결과>.json --scale 1.0 --port 8081
"""

import argparse
import asyncio
import json
import math
import re
import statistics
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from whatfrom.core.contracts import Recommendation, SearchPlan

# 앱의 프롬프트 형식(recommend.planner.build_plan_prompt, recommend.advisor.build_prompt)에서
# 질문과 후보를 꺼낸다. loadtest는 whatfrom.core만 import하므로 형식을 여기에 다시 적고,
# 테스트가 실제 생성 함수로 만든 프롬프트로 어긋남을 잡는다.
_PLAN_QUESTION = re.compile(r"\AQuestion: (.*?)\n\nRepositories you may choose from:", re.DOTALL)
_ADVISE_QUESTION = re.compile(
    r"\ARequirement: (.*?)\n\nCandidates \(choose exactly one\):", re.DOTALL
)
_CANDIDATES = re.compile(r"Candidates \(choose exactly one\):\n(.*?)(?:\n\n|\Z)", re.DOTALL)
_CANDIDATE_LINE = re.compile(r"^- (\S+) \| ", re.MULTILINE)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Recorded:
    """유료 평가 한 문항의 LLM 기록."""

    plan: dict | None
    image: str | None
    seconds_plan: float
    seconds_advise: float


def _seconds(case: dict, key: str) -> float | None:
    value = case.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def load_records(questions_path: Path, results_path: Path) -> dict[str, Recorded]:
    """질문 원문(앞뒤 공백 제외)을 키로 평가 결과의 기록을 모은다.

    질문 파일의 모든 문항에 기록이 있고 두 단계 시간이 유한한 0 이상 숫자여야 한다. 아니면
    멈춘다. 잘못된 결과 파일로 대기가 0초나 중앙값이 된 채 부하 시험이 정상처럼 끝나면 안 된다.
    """
    questions = json.loads(questions_path.read_text(encoding="utf-8"))["questions"]
    results = json.loads(results_path.read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in results["cases"]}
    records: dict[str, Recorded] = {}
    problems: list[str] = []
    for item in questions:
        case = cases.get(item["id"])
        if case is None:
            problems.append(f"{item['id']}: 결과 파일에 없음")
            continue
        seconds_plan = _seconds(case, "seconds_plan")
        seconds_advise = _seconds(case, "seconds_advise")
        if seconds_plan is None or seconds_advise is None:
            problems.append(f"{item['id']}: 단계 시간이 없거나 올바르지 않음")
            continue
        try:
            SearchPlan.model_validate(case.get("plan") or {})
        except ValidationError:
            problems.append(f"{item['id']}: 검색 조건이 SearchPlan 형식이 아님")
            continue
        records[item["question"].strip()] = Recorded(
            plan=case.get("plan"),
            image=case.get("recommended_image"),
            seconds_plan=seconds_plan,
            seconds_advise=seconds_advise,
        )
    if problems:
        raise ValueError(f"{results_path}로 재생할 수 없다: " + "; ".join(problems))
    return records


class _Message(BaseModel):
    role: str
    content: str


class _JsonSchema(BaseModel):
    name: str


class _ResponseFormat(BaseModel):
    json_schema: _JsonSchema


class _ChatRequest(BaseModel):
    messages: list[_Message]
    response_format: _ResponseFormat


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _find(records: dict[str, Recorded], pattern: re.Pattern[str], prompt: str) -> Recorded | None:
    match = pattern.search(prompt)
    return records.get(match.group(1).strip()) if match else None


def _candidates(prompt: str) -> list[str]:
    """후보 목록 절에서만 이미지를 읽는다. 근거 본문의 "- x | y" 줄은 후보가 아니다."""
    section = _CANDIDATES.search(prompt)
    return _CANDIDATE_LINE.findall(section.group(1)) if section else []


def create_mock_app(
    records: dict[str, Recorded], scale: float = 1.0, sleep: Sleep = asyncio.sleep
) -> FastAPI:
    """질문 파일에 없는 질문도 오류 없이 답한다.

    부하 중에 모의 서버가 오류를 내면 측정이 오염된다.
    """
    if not (math.isfinite(scale) and scale > 0):
        raise ValueError(f"scale은 유한한 양수여야 한다: {scale}")
    app = FastAPI(title="whatfrom mock LLM")
    median_plan = _median([r.seconds_plan for r in records.values()])
    median_advise = _median([r.seconds_advise for r in records.values()])

    @app.post("/v1/chat/completions")
    async def complete(request: _ChatRequest) -> dict:
        prompt = next((m.content for m in request.messages if m.role == "user"), "")
        name = request.response_format.json_schema.name
        if name == "search_plan":
            record = _find(records, _PLAN_QUESTION, prompt)
            delay = record.seconds_plan if record else median_plan
            plan = record.plan if record and record.plan else {}
            content = SearchPlan.model_validate(plan).model_dump_json()
        elif name == "recommendation":
            candidates = _candidates(prompt)
            if not candidates:
                raise HTTPException(400, "no candidates in the prompt")
            record = _find(records, _ADVISE_QUESTION, prompt)
            delay = record.seconds_advise if record else median_advise
            recorded = record.image if record else None
            image = recorded if recorded in candidates else candidates[0]
            content = Recommendation(
                image=image, reason="모의 LLM 응답이다.", dockerfile=f"FROM {image}\n"
            ).model_dump_json()
        else:
            raise HTTPException(400, f"unknown schema: {name}")

        await sleep(delay * scale)
        return {
            "id": "mock",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="부하 시험용 모의 LLM 서버")
    parser.add_argument("--results", type=Path, required=True, help="평가 결과 JSON")
    parser.add_argument("--questions", type=Path, default=Path("load/questions.json"))
    parser.add_argument("--scale", type=float, default=1.0, help="기록된 LLM 시간에 곱할 배율")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    records = load_records(args.questions, args.results)
    print(f"기록 {len(records)}문항, 배율 {args.scale}")
    uvicorn.run(create_mock_app(records, args.scale), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
