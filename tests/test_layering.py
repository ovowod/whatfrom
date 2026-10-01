# tests/test_layering.py
"""layering 규칙을 강제한다.

stage package(collect, index, search, recommend, eval, loadtest)는 자기 자신과 whatfrom.core만
import할 수 있다. stage끼리는 서로 import하지 않는다. whatfrom.core는 어떤
stage도 import하지 않는다. stage들을 조합하는 건 api.py와 cli.py뿐이다.
그 밖의 최상위 module(metrics.py, admission.py 등)은 whatfrom.core만 import할 수 있다.

import 여부는 실제로 import하지 않고 ast로 source를 parsing해서 확인한다.
`from whatfrom import search` 형태도 whatfrom.search를 가져온 것으로 센다.
"""

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parents[1] / "src"
WHATFROM_DIR = SRC_ROOT / "whatfrom"
STAGES = ["collect", "index", "search", "recommend", "eval", "loadtest"]


def _display_path(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT.parent))


def _whatfrom_imports(path: Path) -> list[tuple[str, ast.AST]]:
    """파일에서 whatfrom.으로 시작하는 절대 임포트들을 (모듈 이름, 노드) 쌍으로 모은다."""
    return _parse_whatfrom_imports(path.read_text(encoding="utf-8"), str(path))


def _parse_whatfrom_imports(source: str, filename: str = "<source>") -> list[tuple[str, ast.AST]]:
    tree = ast.parse(source, filename=filename)
    found: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("whatfrom."):
                    found.append((alias.name, node))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module == "whatfrom":
                # from whatfrom import search는 whatfrom.search를 가져온다.
                for alias in node.names:
                    found.append((f"whatfrom.{alias.name}", node))
            elif node.module.startswith("whatfrom."):
                found.append((node.module, node))
    return found


def _is_within(module: str, package: str) -> bool:
    """module이 package 자신이거나 그 하위 모듈인지 확인한다."""
    return module == package or module.startswith(package + ".")


@pytest.mark.parametrize("stage", STAGES)
def test_stage_package_imports_only_itself_and_core(stage: str) -> None:
    stage_dir = WHATFROM_DIR / stage
    assert stage_dir.is_dir(), (
        f"{stage_dir}가 없다. 없는 디렉터리는 검사할 파일이 없어 그냥 통과한다."
    )
    for path in sorted(stage_dir.rglob("*.py")):
        for module, node in _whatfrom_imports(path):
            allowed = _is_within(module, f"whatfrom.{stage}") or _is_within(module, "whatfrom.core")
            assert allowed, (
                f"{_display_path(path)}의 {node.lineno}번째 줄: "
                f"whatfrom.{stage} 패키지는 자기 자신과 whatfrom.core만 임포트할 수 있는데 "
                f"'{module}'을(를) 임포트하고 있다."
            )


def test_core_imports_no_stage_package() -> None:
    core_dir = WHATFROM_DIR / "core"
    for path in sorted(core_dir.rglob("*.py")):
        for module, node in _whatfrom_imports(path):
            for stage in STAGES:
                assert not _is_within(module, f"whatfrom.{stage}"), (
                    f"{_display_path(path)}의 {node.lineno}번째 줄: "
                    f"whatfrom.core는 stage 패키지를 임포트하면 안 되는데 "
                    f"'{module}'(whatfrom.{stage})을(를) 임포트하고 있다."
                )


def test_no_relative_imports_under_src_whatfrom() -> None:
    for path in sorted(WHATFROM_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, (
                    f"{_display_path(path)}의 {node.lineno}번째 줄: "
                    f"상대 임포트는 금지된다 — 절대 경로(whatfrom....)로 바꿔라."
                )


def test_from_whatfrom_import_resolves_to_the_submodule() -> None:
    """`from whatfrom import search`도 whatfrom.search를 가져온 것으로 센다."""
    source = "from whatfrom import search, core as c\nfrom whatfrom.api import app\n"

    assert [module for module, _ in _parse_whatfrom_imports(source)] == [
        "whatfrom.search",
        "whatfrom.core",
        "whatfrom.api",
    ]


# stage를 조합하는 최상위 module. 나머지 최상위 module은 whatfrom.core만 import한다.
COMPOSERS = {"api", "cli"}


def _allowed_in_support_module(module: str) -> bool:
    return _is_within(module, "whatfrom.core")


def test_support_module_rule_rejects_every_internal_module_but_core() -> None:
    source = (
        "from whatfrom.core.config import Settings\n"
        "from whatfrom import search\n"
        "import whatfrom.api\n"
        "from whatfrom.metrics import observe\n"
    )

    rejected = [m for m, _ in _parse_whatfrom_imports(source) if not _allowed_in_support_module(m)]

    assert rejected == ["whatfrom.search", "whatfrom.api", "whatfrom.metrics"]


def test_top_level_support_modules_import_only_core() -> None:
    modules = sorted(
        path
        for path in WHATFROM_DIR.glob("*.py")
        if path.stem not in COMPOSERS | {"__init__", "__main__"}
    )
    assert modules, "검사할 최상위 module이 없다. 경로가 바뀌었으면 test를 고쳐라."
    for path in modules:
        for module, node in _whatfrom_imports(path):
            assert _allowed_in_support_module(module), (
                f"{_display_path(path)}의 {node.lineno}번째 줄: "
                f"stage를 조합하는 건 {', '.join(sorted(COMPOSERS))}뿐이다. "
                f"그 밖의 최상위 module은 whatfrom.core만 import할 수 있는데 "
                f"'{module}'을(를) import하고 있다."
            )
