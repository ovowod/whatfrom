"""다시 색인한 뒤 오른 문서 Hit@5가 원본 README 변경 때문인지 확인한다. DB에 쓰지 않는다.

지금 색인에서 본문이 바뀐 섹션만 다시 색인하기 전 본문으로 되돌려 메모리에서 임베딩하고,
검색 규칙(섹션마다 가장 가까운 청크 1개, 거리가 같으면 청크 id 순)으로 문서 Hit@5를 잰다.
이전 본문은 docker-library/docs의 고정 커밋에서 받고, 다시 색인하기 전 섹션 해시
(sections-before.json)와 같은지 확인한다.

사용(프로젝트 루트에서, 틀 문장 섹션을 뺀 색인 334개 청크 위에서):
    uv run --with numpy python eval/experiments/2026-09-24-template-exclusion/readme_swap.py
"""

import hashlib
import json
from pathlib import Path

import httpx2
import numpy as np
from sqlalchemy import select

from whatfrom.core.config import settings
from whatfrom.core.db import make_engine, session_scope
from whatfrom.core.embed import get_embedder
from whatfrom.core.models import Document, DocumentChunk
from whatfrom.eval.goldenset import load_goldenset
from whatfrom.index.chunk import chunk_text, split_sections

HERE = Path(__file__).parent
# 다시 색인하기 전 README. 그 뒤의 커밋은 모두 docker-library의 자동 갱신("Run update.sh")이다.
COMMITS = {"golang": "05ceefa", "node": "836988a", "redis": "4d7ff2d", "nginx": "fba621a"}
RAW = "https://raw.githubusercontent.com/docker-library/docs/{commit}/{repository}/README.md"


def unit(vector) -> np.ndarray:
    array = np.asarray(vector, dtype=float)
    return array / np.linalg.norm(array)


def old_bodies(changed: list[str], before: dict[str, str]) -> dict[str, str]:
    """본문이 바뀐 섹션의 이전 본문. 해시가 다시 색인하기 전과 다르면 멈춘다."""
    bodies: dict[str, str] = {}
    with httpx2.Client(timeout=30.0) as client:
        for repository, commit in COMMITS.items():
            readme = client.get(RAW.format(commit=commit, repository=repository)).text
            for section in split_sections(readme):
                key = f"{repository}|{section.title}"
                if key in changed:
                    digest = hashlib.sha256(section.body.encode()).hexdigest()
                    assert digest == before[key], f"이전 본문의 해시가 다르다: {key}"
                    bodies[key] = section.body
    assert set(bodies) == set(changed), set(changed) - set(bodies)
    return bodies


def top5(rows, query: np.ndarray) -> list[int]:
    matrix = np.array([unit(r[4]) for r in rows])
    distance = 1 - matrix @ query
    best: dict[int, int] = {}
    for i in sorted(range(len(rows)), key=lambda i: (distance[i], rows[i][0])):
        best.setdefault(rows[i][1], i)
    return sorted(best.values(), key=lambda i: (distance[i], rows[i][0]))[:5]


def main() -> None:
    before = json.loads((HERE / "sections-before.json").read_text())
    after = json.loads((HERE / "sections-after.json").read_text())
    changed = sorted(k for k in set(after) & set(before) if after[k] != before[k])
    bodies = old_bodies(changed, before)

    embedder = get_embedder("openai_compatible")
    cases = [c for c in load_goldenset(Path("eval/goldenset.yaml")).cases if c.expected_sections]
    vectors = embedder.embed([c.question for c in cases])
    queries = dict(zip([c.id for c in cases], vectors, strict=True))
    with session_scope(make_engine(settings.database_url)) as session:
        rows = [
            tuple(r)
            for r in session.execute(
                select(
                    DocumentChunk.id,
                    Document.id,
                    Document.repository,
                    Document.section_title,
                    DocumentChunk.embedding,
                )
                .join(Document, Document.id == DocumentChunk.document_id)
                .order_by(DocumentChunk.id)
            )
        ]

    # 바뀐 섹션의 청크를 이전 본문의 청크로 바꾼다. 섹션 안 순서는 원래 청크 id 뒤에 소수로 둔다.
    swapped = [r for r in rows if f"{r[2]}|{r[3]}" not in bodies]
    for key, body in bodies.items():
        repository, title = key.split("|", 1)
        document = next(r[1] for r in rows if r[2] == repository and r[3] == title)
        first = min(r[0] for r in rows if r[1] == document)
        pieces = chunk_text(body)
        for i, vector in enumerate(embedder.embed(pieces)):
            swapped.append((first + i / 1000, document, repository, title, vector))

    result = {"changed_sections": changed, "chunks": len(rows), "cases": {}}
    totals = {"now": 0, "before_bodies": 0}
    for case in cases:
        query = unit(queries[case.id])
        entry = {}
        for label, source in (("now", rows), ("before_bodies", swapped)):
            picked = [source[i] for i in top5(source, query)]
            hit = any(
                r[2] in case.requires_repositories
                and any(e in r[3] for e in case.expected_sections)
                for r in picked
            )
            totals[label] += hit
            entry[label] = {"hit_at5": hit, "top5": [f"{r[2]}|{r[3]}" for r in picked]}
        result["cases"][case.id] = entry
    result["hit_at5"] = totals

    (HERE / "readme_swap.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"청크 {len(rows)}, 본문이 바뀐 섹션 {len(changed)}")
    print(f"문서 Hit@5: 지금 {totals['now']}, 바뀐 섹션만 이전 본문 {totals['before_bodies']}")
    for case_id, entry in result["cases"].items():
        if entry["now"]["hit_at5"] != entry["before_bodies"]["hit_at5"]:
            print(f"  {case_id}: 이전 본문 {entry['before_bodies']['top5']}")
            print(f"  {' ' * len(case_id)}  지금    {entry['now']['top5']}")


if __name__ == "__main__":
    main()
