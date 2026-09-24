"""같은 청크·bge-m3 벡터·질문 벡터로 틀 문장 섹션 10개만 뺀 효과를 잰다. DB에 쓰지 않는다.

다시 색인하기 전(청크 344개)에 돌린 스크립트를 그대로 남긴다. 임베딩 모델 비교 실험의 bge-m3
벡터 캐시를 쓰는데, 캐시는 청크 id와 본문의 해시가 맞아야 열린다. 다시 색인한 지금은 청크가
바뀌어 이 스크립트가 캐시 해시 불일치로 멈춘다. 결과는 같은 폴더의 README에 있다.

사용(프로젝트 루트에서, 다시 색인하기 전의 DB에서만):
    uv run --with numpy --with httpx python \\
        eval/experiments/2026-09-24-template-exclusion/same_vectors.py
"""

import importlib.util
import json
import re
from pathlib import Path

E = Path("eval/experiments/2026-09-24-embedding-comparison")
CACHE = Path("eval/results/embedding-compare-2026-09-24/cache")
spec = importlib.util.spec_from_file_location("cmp", E / "compare.py")
cmp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cmp)
INTRO = re.compile(
    r"The `[^`]+` images come in many flavors, each designed for a specific use case\."
)
FAQ = re.compile(
    r"\(See \[\"What's the difference between 'Shared' and 'Simple' tags\?\" in the FAQ\]"
    r"\([^)]*\)\.\)"
)

cases, rows = cmp.load_corpus()
chunk_vecs, query_vecs, _ = cmp.vectors("bge-m3", cases, rows, CACHE, False)
plans = json.loads((E / "plans.json").read_text())["plans"]
keep = [
    i
    for i, r in enumerate(rows)
    if not (INTRO.fullmatch(r[5].strip()) or FAQ.fullmatch(r[5].strip()))
]
removed = {r[1] for r in rows} - {rows[i][1] for i in keep}
print("청크", len(rows), "->", len(keep), "| 뺀 섹션", len(removed))


def run(idx):
    sub = [rows[i] for i in idx]
    matrix = cmp.unit([chunk_vecs[i] for i in idx])
    out = {}
    for c in cases:
        d = 1 - matrix @ cmp.unit(query_vecs[c.id])
        top = cmp.sections(sub, d)[:5]
        repo = (plans.get(c.id) or {}).get("repository")
        if repo:
            ev = [i for i in top if sub[i][2] == repo] or cmp.sections(sub, d, repo)[:2]
        else:
            ev = top

        def hit(picked, c=c):
            return any(
                sub[i][2] in c.requires_repositories
                and any(e in sub[i][3] for e in c.expected_sections)
                for i in picked
            )

        out[c.id] = {
            "hit5": hit(top) if c.expected_sections else None,
            "repos": sorted({sub[i][2] for i in top}),
            "repo_ok": set(c.requires_repositories) <= {sub[i][2] for i in top},
            "evidence": sorted(f"{sub[i][2]}|{sub[i][3]}" for i in ev),
        }
    return out


a, b = run(list(range(len(rows)))), run(keep)
for key in ("hit5", "repo_ok"):
    gain = [c for c in a if b[c][key] and not a[c][key]]
    loss = [c for c in a if a[c][key] and not b[c][key]]
    before = sum(bool(v[key]) for v in a.values())
    after = sum(bool(v[key]) for v in b.values())
    print(key, before, "->", after, "+", gain, "-", loss)
print("후보 리포 선택이 바뀐 문항:", [c for c in a if a[c]["repos"] != b[c]["repos"]])
changed = [c for c in a if a[c]["evidence"] != b[c]["evidence"]]
print("근거가 바뀐 문항:", len(changed))
for c in changed:
    print(
        " ",
        c,
        "-",
        sorted(set(a[c]["evidence"]) - set(b[c]["evidence"])),
        "+",
        sorted(set(b[c]["evidence"]) - set(a[c]["evidence"])),
    )
