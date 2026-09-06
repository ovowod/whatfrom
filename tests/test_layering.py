# tests/test_layering.py
"""레이어링 규칙을 강제한다.

stage 패키지(collect, index, search, recommend)는 자기 자신과 whatfrom.core만
임포트할 수 있다. stage끼리는 서로 임포트하지 않는다. whatfrom.core는 어떤
stage도 임포트하지 않는다. stage들을 조합하는 건 api.py와 cli.py뿐이다.

임포트 여부는 실제로 import하지 않고 ast로 소스를 파싱해서 확인한다.
"""

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parents[1] / "src"
WHATFROM_DIR = SRC_ROOT / "whatfrom"
STAGES = ["collect", "index", "search", "recommend"]


def _display_path(path: Path) -> str:
    return str(path.relative_to(SRC_ROOT.parent))


def _whatfrom_imports(path: Path) -> list[tuple[str, ast.AST]]:
    """파일에서 whatfrom.으로 시작하는 절대 임포트들을 (모듈 이름, 노드) 쌍으로 모은다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("whatfrom."):
                    found.append((alias.name, node))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0 and node.module.startswith("whatfrom."):
                found.append((node.module, node))
    return found


def _is_within(module: str, package: str) -> bool:
    """module이 package 자신이거나 그 하위 모듈인지 확인한다."""
    return module == package or module.startswith(package + ".")


@pytest.mark.parametrize("stage", STAGES)
def test_stage_package_imports_only_itself_and_core(stage: str) -> None:
    stage_dir = WHATFROM_DIR / stage
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
