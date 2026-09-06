# src/whatfrom/search/retrieval.py
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from whatfrom.core.contracts import Candidate, Evidence, Platform
from whatfrom.core.embed import Embedder
from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository
from whatfrom.search.tagselect import TagRef, select_tags


def search_chunks(
    session: Session, embedder: Embedder, question: str, limit: int = 5
) -> list[tuple[DocumentChunk, float]]:
    """질문을 임베딩한 뒤 최근접 청크를 찾는다. CLI처럼 두 단계를 한 번에 할 때 쓴다.

    요청 경로는 이걸 쓰지 않는다 — 임베딩이 HTTP 호출이라 DB 트랜잭션 밖에서
    끝내야 하기 때문이다. 그쪽은 search_chunks_by_vector를 직접 부른다.
    """
    return search_chunks_by_vector(session, embedder.embed([question])[0], limit)


def search_chunks_by_vector(
    session: Session, vector: list[float], limit: int = 5
) -> list[tuple[DocumentChunk, float]]:
    """코사인 거리 기준 최근접 청크. 거리는 0(동일)~2(정반대)."""
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
    """질문을 임베딩한 뒤 후보를 만든다. 요청 경로는 search_candidates_by_vector를 쓴다."""
    return search_candidates_by_vector(
        session, embedder.embed([question])[0], chunk_k, tags_per_repo
    )


def search_candidates_by_vector(
    session: Session,
    vector: list[float],
    chunk_k: int = 5,
    tags_per_repo: int = 5,
) -> list[Candidate]:
    """벡터 검색으로 리포와 근거를 찾고, 그 리포의 태그 중에서 후보를 세운다.

    태그 선택 규칙은 tagselect.select_tags에 있다.

    질문 내용은 아직 태그 선택에 영향을 주지 않는다. 요구사항 조건으로 태그를
    좁히는 일은 SearchPlan이 도착하는 F7의 몫이다 (스펙 §11).
    """
    hits = search_chunks_by_vector(session, vector, limit=chunk_k)
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
        # 선택에 필요한 네 컬럼만 가볍게 전부 가져온다. variants까지 붙이면
        # python 리포 기준 태그 300개에 변종 1868행이 딸려온다.
        refs = [
            TagRef(
                id=row.id,
                tag=row.tag,
                manifest_digest=row.manifest_digest,
                last_pushed_at=row.last_pushed_at,
            )
            for row in session.execute(
                select(
                    ImageTag.id,
                    ImageTag.tag,
                    ImageTag.manifest_digest,
                    ImageTag.last_pushed_at,
                ).where(ImageTag.repository == repository)
            )
        ]
        chosen = select_tags(refs, tags_per_repo)
        if not chosen:
            continue

        # 고른 것만 변종과 함께 다시 가져온다. IN 절은 순서를 보장하지 않으므로
        # select_tags가 정한 순서로 다시 세운다.
        by_id = {
            tag.id: tag
            for tag in session.execute(
                select(ImageTag)
                .options(selectinload(ImageTag.variants))
                .where(ImageTag.id.in_([ref.id for ref in chosen]))
            )
            .scalars()
        }
        tags = [by_id[ref.id] for ref in chosen]

        for tag in tags:
            # image_variants 행을 그대로 옮긴다. 아키텍처 이름으로 접으면
            # python:3.13의 amd64 세 행(linux 하나, windows 둘)이 서로를 덮는다.
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
