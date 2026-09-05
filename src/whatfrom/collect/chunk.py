import re
from dataclasses import dataclass

HEADING = re.compile(r"^(#{1,2}) +(.+?)\s*$")
FENCE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class Section:
    title: str
    body: str


def split_sections(markdown: str) -> list[Section]:
    """h1/h2 제목으로 문서를 자른다. h2 제목에는 상위 h1을 앞에 붙인다.

    h3 이하는 제목으로 취급하지 않고 상위 섹션 본문에 남긴다 — 이 정도 크기가
    LLM에 넘길 부모 문맥으로 적당하다.
    """
    sections: list[Section] = []
    current_h1 = ""
    title: str | None = None
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        if title is None:
            return
        text = "\n".join(body).strip()
        if text:
            sections.append(Section(title=title, body=text))

    for line in markdown.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        match = None if in_fence else HEADING.match(line)
        if match is None:
            body.append(line)
            continue

        flush()
        level, heading = len(match.group(1)), match.group(2)
        if level == 1:
            current_h1 = heading
            title = heading
        else:
            title = f"{current_h1} > {heading}" if current_h1 else heading
        body = []

    flush()
    return sections


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> list[str]:
    """공백 경계에서 자른 고정 크기 청크. 인접 청크는 overlap 만큼 겹친다."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            space = text.rfind(" ", start, end)
            if space > start:
                end = space
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]
