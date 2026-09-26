"""부하 시험의 질문 순서. 실행마다 같은 질문을 같은 순서로 보내야 두 실행을 비교할 수 있다."""

import argparse
import json
from pathlib import Path

from whatfrom import cli
from whatfrom.eval.goldenset import load_goldenset

GOLDENSET = Path("eval/goldenset.yaml")
COMMITTED = Path("load/questions.json")


def test_question_order_is_a_series_of_seeded_permutations():
    order = cli.question_order(count=40, blocks=12, seed=0)

    assert len(order) == 480
    for start in range(0, 480, 40):
        assert sorted(order[start : start + 40]) == list(range(40))
    assert order == cli.question_order(count=40, blocks=12, seed=0)


def test_a_different_seed_gives_a_different_order():
    assert cli.question_order(40, 12, seed=0) != cli.question_order(40, 12, seed=1)


def test_the_committed_question_file_matches_the_goldenset():
    """골든셋을 고치고 파일을 다시 만들지 않으면 k6가 옛 질문을 보낸다."""
    expected = cli.question_file(load_goldenset(GOLDENSET))

    assert json.loads(COMMITTED.read_text(encoding="utf-8")) == expected


def test_load_questions_writes_the_questions_and_the_order(tmp_path):
    out = tmp_path / "questions.json"

    cli.cmd_load_questions(argparse.Namespace(goldenset=str(GOLDENSET), out=str(out), seed=0))

    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["seed"] == 0
    assert len(document["questions"]) == 40
    assert set(document["questions"][0]) == {"id", "question"}
    assert len(document["order"]) == 480
