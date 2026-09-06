import math

from whatfrom.core.embed import EMBEDDING_DIM, FakeEmbedder


def test_fake_embedder_returns_unit_vectors_of_the_declared_dimension():
    vectors = FakeEmbedder().embed(["alpine uses musl libc"])

    assert len(vectors) == 1
    assert len(vectors[0]) == EMBEDDING_DIM
    assert math.isclose(sum(x * x for x in vectors[0]), 1.0, rel_tol=1e-6)


def test_fake_embedder_is_deterministic_across_calls():
    """builtin hash()는 프로세스마다 salt가 달라 쓸 수 없다."""
    first = FakeEmbedder().embed(["alpine musl"])[0]
    second = FakeEmbedder().embed(["alpine musl"])[0]

    assert first == second


def test_fake_embedder_scores_shared_vocabulary_higher():
    embedder = FakeEmbedder()
    query, near, far = embedder.embed(
        [
            "alpine musl libc build problems",
            "alpine linux musl libc caveat",
            "postgres database replication settings",
        ]
    )

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cosine(query, near) > cosine(query, far)


def test_fake_embedder_handles_empty_text_without_dividing_by_zero():
    vector = FakeEmbedder().embed([""])[0]

    assert len(vector) == EMBEDDING_DIM
    assert all(x == 0.0 for x in vector)


def test_fake_embedder_handles_a_korean_only_question():
    """한국어가 이 제품의 주 입력 언어다.

    영벡터가 되면 pgvector가 코사인 거리를 nan으로 돌려주고 검색 순서가
    아무 의미도 없어진다. 기존 테스트 질문에 우연히 영어가 섞여 있어서
    이 문제가 드러나지 않았다.
    """
    vector = FakeEmbedder().embed(["빌드 안정성이 중요해"])[0]

    assert any(x != 0.0 for x in vector)
    assert math.isclose(sum(x * x for x in vector), 1.0, rel_tol=1e-6)


def test_fake_embedder_keeps_latin_words_whole_next_to_korean():
    """조사가 붙어도 영어 단어는 그대로 남아야 영어 README와 매칭된다.

    \\w+ 로 자르면 "numpy를"이 한 토큰이 되어 README의 "numpy"와 다른
    버킷으로 간다.
    """
    embedder = FakeEmbedder()

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    mixed, english = embedder.embed(["numpy를 쓰는데 alpine이 괜찮을까", "numpy alpine"])

    assert cosine(mixed, english) > 0.5
