# src/whatfrom/models.py
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Repository(Base):
    __tablename__ = "repositories"

    name: Mapped[str] = mapped_column(String(200), primary_key=True)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    tags: Mapped[list["ImageTag"]] = relationship(back_populates="repo")
    documents: Mapped[list["Document"]] = relationship(back_populates="repo")


class ImageTag(Base):
    __tablename__ = "image_tags"
    __table_args__ = (UniqueConstraint("repository", "tag", name="uq_image_tags_repo_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository: Mapped[str] = mapped_column(ForeignKey("repositories.name"))
    tag: Mapped[str] = mapped_column(String(300))
    manifest_digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # 태그명에서 규칙으로 파생된다. F6까지는 비어 있다.
    language_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    version_major_minor: Mapped[str | None] = mapped_column(String(20), nullable=True)
    distribution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    distro_codename: Mapped[str | None] = mapped_column(String(30), nullable=True)
    variant: Mapped[str | None] = mapped_column(String(30), nullable=True)

    repo: Mapped[Repository] = relationship(back_populates="tags")
    variants: Mapped[list["ImageVariant"]] = relationship(
        back_populates="image_tag", cascade="all, delete-orphan"
    )


class ImageVariant(Base):
    __tablename__ = "image_variants"
    __table_args__ = (
        UniqueConstraint(
            "tag_id", "os", "architecture", "arch_variant", name="uq_image_variants_identity"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("image_tags.id", ondelete="CASCADE"))
    os: Mapped[str] = mapped_column(String(30))
    architecture: Mapped[str] = mapped_column(String(30))
    arch_variant: Mapped[str] = mapped_column(String(30), default="")
    digest: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)

    image_tag: Mapped[ImageTag] = relationship(back_populates="variants")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("repository", "doc_type", "section_title", name="uq_documents_section"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repository: Mapped[str] = mapped_column(ForeignKey("repositories.name"))
    doc_type: Mapped[str] = mapped_column(String(30))
    section_title: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    repo: Mapped[Repository] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_chunks_doc_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")
