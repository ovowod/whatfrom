# src/whatfrom/retrieval.py
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from whatfrom.contracts import Candidate, Evidence, Platform
from whatfrom.embed import Embedder
from whatfrom.models import Document, DocumentChunk, ImageTag, Repository


def search_chunks(
    session: Session, embedder: Embedder, question: str, limit: int = 5
) -> list[tuple[DocumentChunk, float]]:
    """코사인 거리 기준 최근접 청크. 거리는 0(동일)~2(정반대)."""
    vector = embedder.embed([question])[0]
    distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
    rows = session.execute(
        select(DocumentChunk, distance)
        .options(selectinload(DocumentChunk.document))
        .where(DocumentChunk.embedding.is_not(None))
        .order_by(distance)
        .limit(limit)
    ).all()
    return [(chunk, float(dist)) for chunk, dist in rows]


def search_candidates(
    session: Session,
    embedder: Embedder,
    question: str,
    chunk_k: int = 5,
    tags_per_repo: int = 5,
) -> list[Candidate]:
    """벡터 검색으로 리포와 근거를 찾고, 그 리포의 최근 태그를 후보로 세운다.

    Phase 0에는 구조화 필터가 없다. 요구사항 조건으로 태그를 좁히는 일은
    SearchPlan이 도착하는 F7의 몫이다 (스펙 §11).
    """
    hits = search_chunks(session, embedder, question, limit=chunk_k)
    if not hits:
        return []

    evidence_by_repo: dict[str, list[Evidence]] = {}
    for chunk, _distance in hits:
        document: Document = chunk.document
        evidence_by_repo.setdefault(document.repository, []).append(
            Evidence(
                section_title=document.section_title,
                # 부모 문맥을 넘긴다: 검색은 청크로, 근거는 섹션 전문으로 (스펙 §6).
                content=document.content,
                source_url=document.source_url,
            )
        )

    candidates: list[Candidate] = []
    for repository, evidence in evidence_by_repo.items():
        repo = session.get(Repository, repository)
        if repo is None:
            continue
        tags = (
            session.execute(
                select(ImageTag)
                .options(selectinload(ImageTag.variants))
                .where(ImageTag.repository == repository)
                .order_by(ImageTag.last_pushed_at.desc().nulls_last())
                .limit(tags_per_repo)
            )
            .scalars()
            .all()
        )

        for tag in tags:
            platforms = sorted(
                (
                    Platform(
                        os=v.os,
                        architecture=v.architecture,
                        arch_variant=v.arch_variant,
                        os_version=v.os_version,
                        size_bytes=v.size_bytes,
                        digest=v.digest,
                    )
                    for v in tag.variants
                ),
                # os_version까지 넣어야 전순서가 된다. 빼면 Windows 커널 버전만
                # 다른 두 행이 동률이 되어 순서가 DB 행 순서에 맡겨진다.
                key=lambda p: (p.os, p.architecture, p.arch_variant, p.os_version),
            )
            candidates.append(
                Candidate(
                    image=f"{repository}:{tag.tag}",
                    repository=repository,
                    tag=tag.tag,
                    digest=tag.manifest_digest,
                    platforms=platforms,
                    last_pushed_at=tag.last_pushed_at,
                    source_url=repo.source_url,
                    collected_at=tag.collected_at,
                    evidence=_dedupe_evidence(evidence),
                )
            )
    return candidates


def _dedupe_evidence(evidence: list[Evidence]) -> list[Evidence]:
    seen: set[str] = set()
    unique: list[Evidence] = []
    for item in evidence:
        if item.section_title in seen:
            continue
        seen.add(item.section_title)
        unique.append(item)
    return unique
