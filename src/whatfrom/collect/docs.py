# src/whatfrom/collect/docs.py
"""공식 이미지 README 원본(docker-library/docs)을 받는다.

Docker Hub의 full_description은 25,000자에서 잘린다. postgres는 Image Variants
절이 통째로 빠지고 eclipse-temurin은 태그 목록이 줄어든다. Hub 본문은 이
저장소의 README를 올린 것이라, 원본을 받으면 잘리지 않은 같은 문서가 된다.
"""

import httpx2

DOCS_RAW = "https://raw.githubusercontent.com/docker-library/docs/master/{repository}/README.md"
DOCS_PAGE = "https://github.com/docker-library/docs/blob/master/{repository}/README.md"


def docs_page_url(repository: str) -> str:
    """근거 청크의 출처. 사람이 여는 GitHub 화면이다."""
    return DOCS_PAGE.format(repository=repository)


def fetch_readme(client: httpx2.Client, repository: str) -> str:
    """2xx가 아니면 예외를 낸다. 수동으로 다시 돌리는 배치라 재시도하지 않는다."""
    response = client.get(DOCS_RAW.format(repository=repository))
    response.raise_for_status()
    return response.text
