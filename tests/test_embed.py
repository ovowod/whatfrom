import math

from whatfrom.embed import EMBEDDING_DIM, FakeEmbedder


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
