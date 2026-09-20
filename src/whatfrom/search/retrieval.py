# src/whatfrom/search/retrieval.py
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from whatfrom.core.contracts import Candidate, Evidence, Platform, SearchPlan
from whatfrom.core.embed import Embedder
from whatfrom.core.models import Document, DocumentChunk, ImageTag, Repository
from whatfrom.search.filters import TagConditions, relaxations, tag_filters
from whatfrom.search.tagselect import TagRef, select_tags, stale_pinned_lines

# 지정 리포지토리가 벡터 검색 상위 청크에 없을 때 그 안에서 따로 찾아 붙일 근거 수.
FORCED_EVIDENCE_CHUNKS = 2


def search_chunks(
    session: Session, embedder: Embedder, question: str, limit: int = 5
) -> list[tuple[DocumentChunk, float]]:
    """질문을 임베딩한 뒤 최근접 청크를 찾는다. CLI처럼 두 단계를 한 번에 할 때 쓴다.

    요청 경로는 이걸 쓰지 않는다 — 임베딩이 HTTP 호출이라 DB 트랜잭션 밖에서
    끝내야 하기 때문이다. 그쪽은 search_chunks_by_vector를 직접 부른다.
    """
    return search_chunks_by_vector(session, embedder.embed([question])[0], limit)


def search_chunks_by_vector(
    session: Session, vector: list[float], limit: int = 5, repository: str | None = None
) -> list[tuple[DocumentChunk, float]]:
    """코사인 거리 기준 최근접 청크. 거리는 0(동일)~2(정반대).

    한 섹션(documents 한 행)에서는 가장 가까운 청크 하나만 돌려준다. 태그 목록처럼
    청크가 많은 섹션이 상위를 독차지하면 다른 리포지토리의 문서가 밀려나고,
    LLM에 넘기는 근거도 같은 섹션 전문이 중복된다.

    거리가 같으면 청크 id가 작은 쪽이 앞이다. 공식 이미지 README는 같은 틀에서
    만들어져서 이미지 이름만 다른 청크가 있다. 그 거리가 같게 나오므로 규칙이
    없으면 limit에 따라 순서가 달라진다.

    repository를 주면 그 리포지토리의 청크 안에서만 찾는다.
    """
    distance = DocumentChunk.embedding.cosine_distance(vector).label("distance")
    ranked = (
        select(
            DocumentChunk.id.label("chunk_id"),
            distance,
            func.row_number()
            .over(
                partition_by=DocumentChunk.document_id,
                order_by=(distance, DocumentChunk.id),
            )
            .label("rank"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.embedding.is_not(None))
    )
    if repository is not None:
        ranked = ranked.where(Document.repository == repository)
    best = ranked.subquery()
    rows = session.execute(
        select(DocumentChunk, best.c.distance)
        .join(best, best.c.chunk_id == DocumentChunk.id)
        .options(selectinload(DocumentChunk.document))
        .where(best.c.rank == 1)
        .order_by(best.c.distance, DocumentChunk.id)
        .limit(limit)
    ).all()
    return [(chunk, float(dist)) for chunk, dist in rows]


def search_candidates(
    session: Session,
    embedder: Embedder,
    question: str,
    chunk_k: int = 5,
    tags_per_repo: int = 20,
) -> list[Candidate]:
    """질문을 임베딩한 뒤 후보를 만든다. 요청 경로는 search_candidates_by_vector를 쓴다."""
    return search_candidates_by_vector(
        session, embedder.embed([question])[0], chunk_k, tags_per_repo
    )


def search_candidates_by_vector(
    session: Session,
    vector: list[float],
    chunk_k: int = 5,
    tags_per_repo: int = 20,
) -> list[Candidate]:
    """벡터 검색으로 리포와 근거를 찾고, 그 리포의 태그 중에서 후보를 세운다.

    태그 선택 규칙은 tagselect.select_tags에 있다.

    질문 내용은 쓰지 않는다. 검색 조건으로 후보를 만드는 경로는
    search_candidates_with_plan이다. 조건 추출에 실패했거나 LLM을 부르지 않는
    검색 전용 평가가 이 함수를 쓴다.
    """
    hits = search_chunks_by_vector(session, vector, limit=chunk_k)
    candidates: list[Candidate] = []
    for repository, evidence in _evidence_by_repo(hits).items():
        refs = _tag_refs(session, repository)
        candidates += _build_candidates(
            session, repository, select_tags(refs, tags_per_repo), evidence
        )
    return candidates


@dataclass(frozen=True)
class PlannedSearch:
    candidates: list[Candidate]
    # 조건 완화, 리포지토리를 강제하지 못한 이유 같은 사용자에게 알릴 말.
    notes: list[str]
    # 스펙 §8의 정상 경로가 아닌 단계로 후보를 만들었는가. 완화, 강제 실패가 그렇다.
    degraded: bool


def search_candidates_with_plan(
    session: Session,
    vector: list[float],
    plan: SearchPlan,
    chunk_k: int = 5,
    tags_per_repo: int = 20,
) -> PlannedSearch:
    """검색 조건으로 후보를 만든다.

    plan.repository가 있으면 그 리포지토리에서만 후보를 낸다. 조건의 뜻이 리포지토리마다
    다르고("3.12"는 python의 버전이다), 다른 제품을 섞으면 LLM #2가 조건을 거치지 않은
    후보를 고를 수 있다. 문서나 태그가 없어 강제할 수 없으면 벡터 검색 리포지토리로
    돌아가고 버전 조건은 쓰지 않는다.

    완화는 후보 전체를 기준으로 한다. 한 리포지토리라도 조건을 만족하면 그 후보만 낸다.
    리포지토리별로 완화하면 조건을 만족하는 후보 사이에 위반 후보가 섞인다.
    """
    notes: list[str] = []
    degraded = False
    evidence_by_repo = _evidence_by_repo(search_chunks_by_vector(session, vector, limit=chunk_k))

    forced = False
    if plan.repository is not None:
        evidence = _forced_evidence(session, vector, plan.repository, evidence_by_repo)
        if evidence and _has_tags(session, plan.repository):
            evidence_by_repo = {plan.repository: evidence}
            forced = True
        else:
            degraded = True
            notes.append(
                f"{plan.repository}의 문서나 태그가 없어 검색으로 찾은 이미지에서 "
                "후보를 만들었습니다."
            )
    if plan.version_prefix is not None and not forced:
        notes.append(
            f"리포지토리를 특정하지 못해 버전 조건({plan.version_prefix})을 쓰지 않았습니다."
        )

    conditions = TagConditions(
        version_prefix=plan.version_prefix if forced else None,
        architectures=tuple(plan.architectures),
        distributions=tuple(plan.distributions),
        exclude_distributions=tuple(plan.exclude_distributions),
        max_size_mb=plan.max_size_mb,
    )
    refs_by_repo = {repository: _tag_refs(session, repository) for repository in evidence_by_repo}

    relaxed: list[str] = []
    chosen_by_repo: dict[str, list[TagRef]] = {}
    stage = conditions
    for stage, relaxed_note in relaxations(conditions):
        if relaxed_note is not None:
            relaxed.append(relaxed_note)
        for repository, refs in refs_by_repo.items():
            allowed = None if stage.empty else _allowed_ids(session, repository, stage)
            chosen = select_tags(refs, tags_per_repo, allowed, stage.version_prefix)
            if chosen:
                chosen_by_repo[repository] = chosen
        if chosen_by_repo:
            break

    if relaxed:
        degraded = True
        notes.append(f"조건에 맞는 태그가 없어 {', '.join(relaxed)}을 풀었습니다.")
    # 실제로 쓰인 단계의 버전으로, 최종 후보에 들어간 줄기만 알린다. 버전 조건을 풀었으면
    # 되살린 줄기가 없으므로 알릴 것도 없다.
    if stage.version_prefix is not None:
        for repository, chosen in chosen_by_repo.items():
            for line in stale_pinned_lines(refs_by_repo[repository], stage.version_prefix, chosen):
                notes.append(
                    f"{repository} {line} 줄기는 가장 최근 줄기보다 마지막 푸시가 "
                    "60일 넘게 뒤처져 있습니다."
                )

    candidates: list[Candidate] = []
    for repository, chosen in chosen_by_repo.items():
        candidates += _build_candidates(session, repository, chosen, evidence_by_repo[repository])
    return PlannedSearch(candidates=candidates, notes=notes, degraded=degraded)


def _evidence_by_repo(hits: list[tuple[DocumentChunk, float]]) -> dict[str, list[Evidence]]:
    """청크가 걸린 리포지토리별 근거. 순서는 먼저 걸린 리포지토리부터다."""
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
    return evidence_by_repo


def _forced_evidence(
    session: Session,
    vector: list[float],
    repository: str,
    evidence_by_repo: dict[str, list[Evidence]],
) -> list[Evidence]:
    """지정 리포지토리의 근거. 상위 청크에 없으면 그 안에서 따로 찾는다.

    근거 없이 후보를 내면 LLM #2가 문서 없이 고른다. 색인되지 않아 청크가 없으면 빈 목록이다.
    """
    if repository in evidence_by_repo:
        return evidence_by_repo[repository]
    hits = search_chunks_by_vector(
        session, vector, limit=FORCED_EVIDENCE_CHUNKS, repository=repository
    )
    return _evidence_by_repo(hits).get(repository, [])


def _has_tags(session: Session, repository: str) -> bool:
    return (
        session.execute(
            select(ImageTag.id).where(ImageTag.repository == repository).limit(1)
        ).first()
        is not None
    )


def _allowed_ids(session: Session, repository: str, conditions: TagConditions) -> frozenset[int]:
    return frozenset(
        session.execute(
            select(ImageTag.id).where(ImageTag.repository == repository, *tag_filters(conditions))
        ).scalars()
    )


def _tag_refs(session: Session, repository: str) -> list[TagRef]:
    """선택에 필요한 네 컬럼만 가볍게 전부 가져온다. variants까지 붙이면
    python 리포 기준 태그 300개에 변종 1868행이 딸려온다."""
    return [
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


def _build_candidates(
    session: Session, repository: str, chosen: list[TagRef], evidence: list[Evidence]
) -> list[Candidate]:
    repo = session.get(Repository, repository)
    if repo is None or not chosen:
        return []

    # 고른 것만 변종과 함께 다시 가져온다. IN 절은 순서를 보장하지 않으므로
    # select_tags가 정한 순서로 다시 세운다.
    by_id = {
        tag.id: tag
        for tag in session.execute(
            select(ImageTag)
            .options(selectinload(ImageTag.variants))
            .where(ImageTag.id.in_([ref.id for ref in chosen]))
        ).scalars()
    }
    candidates: list[Candidate] = []
    for tag in (by_id[ref.id] for ref in chosen):
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
                version=tag.language_version,
                distribution=tag.distribution,
                distro_codename=tag.distro_codename,
                variant=tag.variant,
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
