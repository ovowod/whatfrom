"""임베딩 모델 비교 실험(2026-09-24). DB에는 쓰지 않는다.

DB의 청크와 골든셋 질문을 모델마다 메모리에서 임베딩하고, 지금 검색 규칙(섹션마다 가장 가까운
청크 1개, 거리가 같으면 청크 id 순)으로 지표를 잰다. 지표 정의와 한계는 같은 폴더의 README에 있다.

사용(프로젝트 루트에서):
    uv run --with numpy --with httpx python \\
        eval/experiments/2026-09-24-embedding-comparison/compare.py [--cache-dir DIR] [--recompute]

벡터 캐시는 --cache-dir(기본: 이 폴더의 cache/, Git 추적 제외)에 둔다. 캐시에는 모델 식별자,
청크(id와 본문), 질문(접두어 포함)의 해시가 함께 저장되고, 지금 값과 다르면 멈춘다.
OpenAI 모델은 OPENAI_API_KEY 환경 변수나 ~/.config/whatfrom/openai.key의 키를 쓴다.
"""

import argparse
import hashlib
import json
import os
import pickle
import re
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import select

from whatfrom.core.config import settings
from whatfrom.core.db import make_engine, session_scope
from whatfrom.core.models import Document, DocumentChunk
from whatfrom.eval.goldenset import load_goldenset

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GOLDENSET = ROOT / "eval" / "goldenset.yaml"
PLANS = HERE / "plans.json"
OLLAMA = "http://localhost:11434/v1/embeddings"
OPENAI = "https://api.openai.com/v1/embeddings"
QWEN_TASK = "Given a web search query, retrieve relevant passages that answer the query"

# 공식 README의 "Image Variants" 상위 절은 이 한 문장뿐이다(본문 확인). 제목만 보면 기대 섹션과
# 맞지만 답이 없으므로, 이 소개문의 적중을 뺀 값을 따로 잰다.
INTRO = re.compile(
    r"The `[^`]+` images come in many flavors, each designed for a specific use case\."
)
# 보조: 본문이 이보다 짧은 섹션의 적중을 뺀다. 짧아도 쓸모 있는 절까지 빠지므로 참고값이다.
SHORT_CHARS = 150

# 이름: (공급자, 모델, 질문 접두어). 접두어는 각 모델의 공식 사용법을 따른다.
MODELS = {
    "bge-m3": ("ollama", "bge-m3", ""),
    "qwen3-0.6b": ("ollama", "qwen3-embedding:0.6b", f"Instruct: {QWEN_TASK}\nQuery: "),
    "qwen3-0.6b-noinst": ("ollama", "qwen3-embedding:0.6b", ""),
    "arctic-embed2": ("ollama", "snowflake-arctic-embed2", "query: "),
    "openai-3-small": ("openai", "text-embedding-3-small", ""),
    "openai-3-large": ("openai", "text-embedding-3-large", ""),
}

METRICS = (
    "global_hit5",
    "repo_included",
    "in_repo_hit2",
    "plan_evidence_hit",
    "global_hit5_no_intro",
    "plan_evidence_hit_no_intro",
    "global_hit5_no_short",
    "plan_evidence_hit_no_short",
)


def sha(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def load_corpus():
    """(골든셋 문항, 청크 행). 행은 (청크 id, 섹션 id, 리포, 섹션 제목, 청크 본문, 섹션 본문)."""
    cases = load_goldenset(GOLDENSET).cases
    with session_scope(make_engine(settings.database_url)) as session:
        rows = session.execute(
            select(
                DocumentChunk.id,
                Document.id,
                Document.repository,
                Document.section_title,
                DocumentChunk.content,
                Document.content,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.embedding.is_not(None))
            .order_by(DocumentChunk.id)
        ).all()
    return cases, [tuple(r) for r in rows]


def openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return key
    return Path.home().joinpath(".config/whatfrom/openai.key").read_text().strip()


def embed(client: httpx.Client, provider: str, model: str, inputs: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(inputs), 32):
        batch = inputs[i : i + 32]
        if provider == "ollama":
            response = client.post(OLLAMA, json={"model": model, "input": batch})
        else:
            response = client.post(
                OPENAI,
                headers={"Authorization": f"Bearer {openai_key()}"},
                json={"model": model, "input": batch, "dimensions": 1024},
            )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda d: d["index"])
        out += [d["embedding"] for d in data]
    return out


def vectors(name: str, cases, rows, cache_dir: Path, recompute: bool):
    """(청크 벡터, 질문 벡터, 질문당 초). 해시가 맞지 않는 캐시는 쓰지 않는다."""
    provider, model, prefix = MODELS[name]
    fingerprint = {
        "model": f"{provider}:{model}",
        "chunks": sha([[r[0], r[4]] for r in rows]),
        "queries": sha([[c.id, prefix + c.question] for c in cases]),
    }
    path = cache_dir / f"{name}.pkl"
    if path.exists() and not recompute:
        cached = pickle.loads(path.read_bytes())
        if cached["fingerprint"] != fingerprint:
            sys.exit(
                f"{name}: 캐시 해시가 지금 청크·질문·모델과 다르다. --recompute로 다시 계산한다."
            )
        return cached["chunk_vecs"], cached["query_vecs"], cached["query_seconds"]
    with httpx.Client(timeout=120) as client:
        chunk_vecs = embed(client, provider, model, [r[4] for r in rows])
        start = time.perf_counter()
        query_vecs = {c.id: embed(client, provider, model, [prefix + c.question])[0] for c in cases}
        query_seconds = (time.perf_counter() - start) / len(cases)
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": fingerprint,
        "chunk_vecs": chunk_vecs,
        "query_vecs": query_vecs,
        "query_seconds": query_seconds,
    }
    path.write_bytes(pickle.dumps(payload))
    return chunk_vecs, query_vecs, query_seconds


def unit(matrix) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=float)
    return matrix / np.linalg.norm(matrix, axis=-1, keepdims=True)


def sections(rows, distance: np.ndarray, repository: str | None = None) -> list[int]:
    """섹션마다 가장 가까운 청크 1개, 거리·청크 id 순. search_chunks_by_vector와 같은 규칙이다."""
    best: dict[int, int] = {}
    for i in sorted(range(len(rows)), key=lambda i: (distance[i], rows[i][0])):
        if repository is None or rows[i][2] == repository:
            best.setdefault(rows[i][1], i)
    return sorted(best.values(), key=lambda i: (distance[i], rows[i][0]))


def score(name: str, cases, rows, plans, cache_dir: Path, recompute: bool) -> dict:
    chunk_vecs, query_vecs, query_seconds = vectors(name, cases, rows, cache_dir, recompute)
    assert len(chunk_vecs[0]) == 1024, len(chunk_vecs[0])
    matrix = unit(chunk_vecs)

    def expected(case, idx: int, exclude: str | None = None) -> bool:
        body = rows[idx][5]
        if exclude == "intro" and INTRO.fullmatch(body.strip()):
            return False
        if exclude == "short" and len(body) < SHORT_CHARS:
            return False
        return rows[idx][2] in case.requires_repositories and any(
            e in rows[idx][3] for e in case.expected_sections
        )

    def label(idx: int) -> str:
        return f"{rows[idx][2]}|{rows[idx][3]}"

    per_case = {}
    for case in cases:
        distance = 1 - matrix @ unit(query_vecs[case.id])
        top = sections(rows, distance)[:5]
        in_repo = [i for r in case.requires_repositories for i in sections(rows, distance, r)[:2]]
        # 지금 코드의 근거 규칙: 전역 상위 5개에 그 리포가 있으면 그 섹션들,
        # 없으면 리포 안 상위 2개.
        repository = (plans.get(case.id) or {}).get("repository")
        if repository:
            evidence = [i for i in top if rows[i][2] == repository]
            evidence = evidence or sections(rows, distance, repository)[:2]
        else:
            evidence = top

        def hit(picked: list[int], exclude: str | None = None, case=case) -> bool | None:
            if not case.expected_sections:
                return None
            return any(expected(case, i, exclude) for i in picked)

        per_case[case.id] = {
            "global_hit5": hit(top),
            "repo_included": set(case.requires_repositories) <= {rows[i][2] for i in top},
            "in_repo_hit2": hit(in_repo),
            "plan_evidence_hit": hit(evidence),
            "global_hit5_no_intro": hit(top, "intro"),
            "plan_evidence_hit_no_intro": hit(evidence, "intro"),
            "global_hit5_no_short": hit(top, "short"),
            "plan_evidence_hit_no_short": hit(evidence, "short"),
            "top5": [label(i) for i in top],
            "evidence": [label(i) for i in evidence],
        }
    totals = {k: sum(1 for v in per_case.values() if v[k]) for k in METRICS}
    return {
        "totals": totals,
        "declared": sum(bool(c.expected_sections) for c in cases),
        "cases": len(cases),
        "query_seconds": query_seconds,
        "per_case": per_case,
    }


def summarize(results: dict, rows, cases) -> str:
    lines = [
        f"청크 {len(rows)}개 (id {rows[0][0]}~{rows[-1][0]}), 질문 {len(cases)}개",
        f"청크 해시 {sha([[r[0], r[4]] for r in rows])}",
        "",
    ]
    for name, r in results.items():
        t, d, n = r["totals"], r["declared"], r["cases"]
        lines += [
            f"{name:18} 전역 Hit@5 {t['global_hit5']}/{d}  필요 리포 포함 {t['repo_included']}/{n}"
            f"  리포 안 Hit@2 {t['in_repo_hit2']}/{d}"
            f"  고정 검색 조건 근거 Hit {t['plan_evidence_hit']}/{d}"
            f"  질문 임베딩(단회 평균) {r['query_seconds'] * 1000:.0f}ms",
            f"{'':18} 소개문 적중 제외: 전역 Hit@5 {t['global_hit5_no_intro']}/{d}"
            f"  근거 Hit {t['plan_evidence_hit_no_intro']}/{d}",
            f"{'':18} (보조) 150자 미만 섹션 적중 제외: 전역 Hit@5 {t['global_hit5_no_short']}/{d}"
            f"  근거 Hit {t['plan_evidence_hit_no_short']}/{d}",
        ]
    base = results.get("bge-m3")
    if base:
        lines.append("")
        keys = ("global_hit5", "repo_included", "plan_evidence_hit", "plan_evidence_hit_no_intro")
        for name, r in results.items():
            if name == "bge-m3":
                continue
            for key in keys:
                now, before = r["per_case"], base["per_case"]
                gain = [k for k, v in now.items() if v[key] and not before[k][key]]
                loss = [k for k, v in now.items() if before[k][key] and not v[key]]
                lines.append(f"{name:18} {key:26} + {gain}  - {loss}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*", default=list(MODELS))
    parser.add_argument("--cache-dir", type=Path, default=HERE / "cache")
    parser.add_argument("--recompute", action="store_true")
    args = parser.parse_args()

    cases, rows = load_corpus()
    plans = json.loads(PLANS.read_text())["plans"]
    results = {
        name: score(name, cases, rows, plans, args.cache_dir, args.recompute) for name in args.names
    }
    text = summarize(results, rows, cases)
    print(text, end="")
    (HERE / "summary.txt").write_text(text)
    (HERE / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
