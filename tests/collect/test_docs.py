# tests/collect/test_docs.py
"""docker-library/docs의 README 원본을 받는다. Hub 본문은 25,000자에서 잘린다."""

import httpx2
import pytest

from whatfrom.collect.docs import docs_page_url, fetch_readme


def client(handler) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_fetch_readme_reads_the_raw_file_of_the_repository():
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(str(request.url))
        return httpx2.Response(200, text="# Image Variants\n")

    assert fetch_readme(client(handler), "postgres") == "# Image Variants\n"
    assert seen == [
        "https://raw.githubusercontent.com/docker-library/docs/master/postgres/README.md"
    ]


def test_fetch_readme_raises_when_the_file_is_missing():
    with pytest.raises(httpx2.HTTPStatusError):
        fetch_readme(client(lambda request: httpx2.Response(404)), "nope")


def test_the_document_page_is_the_github_view_of_the_same_file():
    assert (
        docs_page_url("postgres")
        == "https://github.com/docker-library/docs/blob/master/postgres/README.md"
    )
