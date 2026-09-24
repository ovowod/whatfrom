# src/whatfrom/index/indexer.py
import re
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from whatfrom.core.embed import EMBEDDING_DIM, Embedder
from whatfrom.core.models import Document, DocumentChunk
from whatfrom.index.chunk import chunk_text, split_sections

DOC_TYPE = "readme"

# 공식 이미지 README 틀에서 나온, 본문 전체가 이 한 문장뿐인 상위 절이 있다. 답이 없는데
# 제목이 "Image Variants", "Supported tags"라 LLM 근거 자리를 차지하고, 제목만 보는
# 문서 Hit@5 채점을 속인다. 실제 내용은 하위 절에 있다. 본문 전체가 일치할 때만 뺀다.
# 길이 기준은 쓰지 않는다. redis 실행 명령(54자)처럼 짧아도 쓸모 있는 절이 있다.
# 새 틀 문장을 발견하면 여기에 추가한다.
IMAGE_VARIANTS_INTRO = re.compile(
    r"The `[^`]+` images come in many flavors, each designed for a specific use case\."
)
SHARED_SIMPLE_TAGS_FAQ = re.compile(
    r"\(See \[\"What's the difference between 'Shared' and 'Simple' tags\?\" in the FAQ\]"
    r"\([^)]*\)\.\)"
)
TEMPLATE_ONLY = (IMAGE_VARIANTS_INTRO, SHARED_SIMPLE_TAGS_FAQ)


def is_template_only(body: str) -> bool:
    return any(pattern.fullmatch(body.strip()) for pattern in TEMPLATE_ONLY)


def index_readme(
    session: Session,
    repository: str,
    readme: str,
    source_url: str,
    embedder: Embedder,
    collected_at: datetime,
) -> int:
    """README를 섹션(부모)과 청크(자식)로 적재하고 청크 임베딩을 채운다."""
    # 차원이 어긋난 임베더를 여기서 막는다. 통과시키면 수천 건을 계산하고
    # API 비용을 쓴 뒤에야 pgvector가 insert를 거부한다.
    if embedder.dimension != EMBEDDING_DIM:
        raise ValueError(
            f"embedder produces {embedder.dimension}-dim vectors but the schema "
            f"stores {EMBEDDING_DIM}"
        )

    created = 0
    seen_titles: list[str] = []
    for section in split_sections(readme):
        if is_template_only(section.body):
            # seen_titles에 넣지 않으므로 이전 색인에 남은 같은 섹션은 아래에서 지워진다.
            continue
        seen_titles.append(section.title)
        document = session.execute(
            select(Document).where(
                Document.repository == repository,
                Document.doc_type == DOC_TYPE,
                Document.section_title == section.title,
            )
        ).scalar_one_or_none()
        if document is None:
            document = Document(
                repository=repository, doc_type=DOC_TYPE, section_title=section.title
            )
            session.add(document)

        document.content = section.body
        document.source_url = source_url
        document.collected_at = collected_at

        # 섹션 본문이 바뀌면 청크를 통째로 다시 만든다.
        document.chunks.clear()
        session.flush()

        pieces = chunk_text(section.body)
        for index, (piece, vector) in enumerate(zip(pieces, embedder.embed(pieces), strict=True)):
            document.chunks.append(
                DocumentChunk(chunk_index=index, content=piece, embedding=vector)
            )
        created += len(pieces)

    # 이번 실행에 나오지 않은 섹션은 README에서 사라진 것이다. 남겨두면 옛 내용이
    # 계속 검색에 뜬다. 청크는 FK의 ON DELETE CASCADE가 함께 지운다.
    stale = delete(Document).where(
        Document.repository == repository,
        Document.doc_type == DOC_TYPE,
    )
    if seen_titles:
        stale = stale.where(Document.section_title.not_in(seen_titles))
    session.execute(stale)

    session.flush()
    return created
