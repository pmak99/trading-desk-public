"""The engine's layering, enforced: import-linter's contracts in the root pyproject.toml.

Until 2026-09-14 (audit 5.2) the layers were convention only. Infrastructure
imported application (analysis_repository.py took MarketConditions from
application/metrics), and nothing noticed, because nothing looked. The first
contracts still missed two leaks, because neither module was in them: domain
scoring imported src.config (ScoringWeights), and src.utils.concurrent_scanner
imported the scripts tree (scripts.scan.date_utils). src.config is a layer
since, just above domain, and src and common may import nothing from scripts.

Two halves. The contracts hold on this tree -- CI also runs `lint-imports` as its
own step. And the contracts bite: each is run against a throwaway package tree
with one planted import, so reordering the layers or loosening a contract fails
here rather than passing silently on a tree that happens to be clean.
"""

import os
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
LINT_IMPORTS = Path(sys.executable).with_name("lint-imports")


def _lint_imports(cwd: Path, config: Path, pythonpath: str = None):
    assert LINT_IMPORTS.exists(), (
        f"{LINT_IMPORTS} missing -- install requirements.txt (import-linter is pinned there)")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return subprocess.run([str(LINT_IMPORTS), "--config", str(config), "--no-cache"],
                          cwd=cwd, env=env, capture_output=True, text=True)


def test_the_contracts_hold_on_this_tree():
    # This checkout's src and common, not whichever one the editable install maps
    # them to (a git worktree on the shared venv would lint the main checkout).
    r = _lint_imports(ROOT, PYPROJECT, pythonpath=os.pathsep.join([str(ROOT), str(ROOT / "2.0")]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BROKEN" not in r.stdout and "Contracts: 3 kept, 0 broken." in r.stdout, r.stdout


def test_ci_runs_the_contracts_as_their_own_step():
    workflow = (ROOT / ".github" / "workflows" / "pr-tests.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s*run: lint-imports\b", workflow, flags=re.M), (
        "pr-tests.yml no longer runs lint-imports")
    pins = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^import-linter==\S+$", pins, flags=re.M), "import-linter is not pinned"


def test_the_contracts_are_the_ones_the_layering_rule_names():
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["importlinter"]
    assert config["root_packages"] == ["src", "common"]
    assert config["include_external_packages"] is True     # `scripts` is not a root package
    shapes = sorted((c["type"], c.get("layers"), c.get("source_modules"), c.get("forbidden_modules"))
                    for c in config["contracts"])
    assert shapes == [
        ("forbidden", None, ["common"], ["src"]),
        ("forbidden", None, ["src", "common"], ["scripts"]),
        ("layers", ["src.application", "src.infrastructure", "src.config", "src.domain"], None, None),
    ]


# (module that imports, module it imports, whether the contracts allow it)
PLANTED = [
    ("src.infrastructure.repo", "src.application.service", False),
    ("src.domain.types", "src.application.service", False),
    ("src.domain.types", "src.infrastructure.repo", False),
    ("src.domain.types", "src.config.config", False),
    ("src.config.config", "src.infrastructure.repo", False),
    ("src.utils.scanner", "scripts.scan", False),
    ("src.domain.types", "scripts.scan", False),
    ("common.db", "scripts.scan", False),
    ("common.db", "src.domain.types", False),
    # Allowed, so the contracts are not simply refusing every import:
    ("src.application.service", "src.infrastructure.repo", True),
    ("src.infrastructure.repo", "src.domain.types", True),
    ("src.application.service", "src.config.config", True),
    ("src.infrastructure.repo", "src.config.config", True),
    ("src.config.config", "src.domain.types", True),
    ("src.utils.scanner", "src.domain.types", True),
    ("src.domain.types", "common.db", True),
]

MODULES = {"src.application.service", "src.infrastructure.repo", "src.config.config",
           "src.domain.types", "src.utils.scanner", "common.db", "scripts.scan"}


@pytest.mark.parametrize("importer,imported,allowed", PLANTED,
                         ids=[f"{a}->{b}" for a, b, _ in PLANTED])
def test_the_contracts_refuse_exactly_the_imports_the_layering_rule_forbids(
        tmp_path, importer, imported, allowed):
    for name in MODULES:
        parts = name.split(".")
        for i in range(1, len(parts)):
            package = tmp_path.joinpath(*parts[:i])
            package.mkdir(exist_ok=True)
            (package / "__init__.py").touch()
        body = f"import {imported}\n" if name == importer else ""
        tmp_path.joinpath(*parts[:-1], parts[-1] + ".py").write_text(body)

    # The real contracts, verbatim: everything from [tool.importlinter] to the end.
    text = PYPROJECT.read_text(encoding="utf-8")
    config = tmp_path / "contracts.toml"
    config.write_text(text[text.index("[tool.importlinter]"):])

    r = _lint_imports(tmp_path, config, pythonpath=str(tmp_path))
    # src's eleven files and common's two, plus one node for `scripts` when it is
    # imported (an external package is squashed to its name): the throwaway tree,
    # not the installed one, and the planted import is the only dependency.
    external = imported.split(".")[0] == "scripts"
    assert f"Analyzed {13 + external} files, 1 dependencies." in r.stdout, r.stdout + r.stderr
    if allowed:
        assert r.returncode == 0, r.stdout
    else:
        assert r.returncode != 0 and "BROKEN" in r.stdout, r.stdout
        shown = "scripts" if external else imported
        assert f"{importer} -> {shown}" in textwrap.dedent(r.stdout).replace("\n", " "), r.stdout
