import json
from pathlib import Path

from whatfrom.index.chunk import Section, chunk_text, split_sections

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_split_sections_uses_h1_and_h2_headings():
    markdown = "# One\n\nbody one\n\n## Two\n\nbody two\n\n### Three\n\nbody three\n"

    sections = split_sections(markdown)

    assert [s.title for s in sections] == ["One", "One > Two"]
    assert "body three" in sections[1].body


def test_split_sections_nests_h2_titles_under_the_current_h1():
    markdown = "# Image Variants\n\nintro\n\n## `python:<version>-alpine`\n\nmusl libc\n"

    sections = split_sections(markdown)

    assert sections[1].title == "Image Variants > `python:<version>-alpine`"


def test_split_sections_ignores_headings_inside_fenced_code_blocks():
    markdown = "# Real\n\n```dockerfile\n# FROM python:3.13\n```\n\ntail\n"

    sections = split_sections(markdown)

    assert [s.title for s in sections] == ["Real"]
    assert "FROM python:3.13" in sections[0].body


def test_split_sections_drops_sections_with_empty_bodies():
    markdown = "# Empty\n\n## Full\n\ncontent here\n"

    sections = split_sections(markdown)

    assert [s.title for s in sections] == ["Empty > Full"]


def test_split_sections_ignores_text_before_the_first_heading():
    markdown = "preamble text\n\n# First\n\nbody\n"

    sections = split_sections(markdown)

    assert [s.title for s in sections] == ["First"]


def test_chunk_text_returns_single_chunk_when_short():
    assert chunk_text("short body", max_chars=800) == ["short body"]


def test_chunk_text_splits_long_text_with_overlap():
    text = " ".join(f"word{i}" for i in range(400))

    chunks = chunk_text(text, max_chars=200, overlap=50)

    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # 겹침이 있으므로 조각 길이 합은 원본보다 길다.
    assert sum(len(c) for c in chunks) > len(text)


def test_chunk_text_covers_the_whole_input():
    text = " ".join(f"word{i}" for i in range(400))

    chunks = chunk_text(text, max_chars=200, overlap=50)

    assert chunks[0].startswith("word0")
    assert chunks[-1].endswith("word399")
    # 양 끝만 보면 중간이 통째로 빠져도 통과한다. 실제로 전부 담겼는지 확인한다.
    # overlap 때문에 청크가 단어 중간에서 시작할 수 있어 조각 토큰이 섞인다.
    # 그래서 동등이 아니라 포함 관계로 본다.
    covered = {w for c in chunks for w in c.split()}
    assert {f"word{i}" for i in range(400)} <= covered


def test_real_readme_yields_an_alpine_section_mentioning_musl():
    payload = json.loads((FIXTURES / "hub_repository.json").read_text())

    sections = split_sections(payload["full_description"])
    alpine = next(s for s in sections if "alpine" in s.title)

    assert "musl" in alpine.body
    assert isinstance(alpine, Section)
