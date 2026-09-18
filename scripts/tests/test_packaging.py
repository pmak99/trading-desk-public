"""`common` and `src` are installed packages, and nothing edits sys.path to reach them.

Until 2026-09-14 (audit 5.2) `common/` was importable only because six modules in
2.0/src -- the domain layer's enums and types among them -- pushed the repo root
onto sys.path at import time, and some sixty scripts, tests and conftests did the
same. Which checkout's `common` a process got depended on which of those ran
first. Now the repo root is a distribution (pyproject.toml, `common*`) beside the
engine's (2.0/pyproject.toml, `src*`), installed editable into 2.0/venv, CI and
the server alike.

The sys.path edits that remain are launch shims for names no distribution
provides: the `scripts` namespace of either tree (2.0/scripts/..., scripts/...),
or a bare module under a scripts directory (`research.events`, `scan.cli`,
`journal.models`, `bootstrap_sharpe`). Each is listed in KEPT with that reason; a
new edit anywhere fails until it is listed or removed. Tests rely on the
documented invocation, `python -m pytest` from the suite's root, which puts that
root on sys.path.
"""

import ast
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / "2.0"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-tests.yml"

# Every Python tree in the repo that runs. docs/ holds plans quoting old code.
PYTHON_TREES = [ENGINE / "src", ENGINE / "scripts", ENGINE / "tests", ROOT / "scripts",
                ROOT / "common", ROOT / "tests"]
PYTHON_FILES = [ENGINE / "conftest.py"]


def _is_sys_path(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "path"
            and isinstance(node.value, ast.Name) and node.value.id in {"sys", "_sys"})


def sys_path_edits(path: Path) -> list:
    """Line numbers of every sys.path.insert/append/extend and sys.path assignment."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"insert", "append", "extend"}
                and _is_sys_path(node.func.value)):
            lines.append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_is_sys_path(t) or (isinstance(t, ast.Subscript) and _is_sys_path(t.value))
                   for t in targets):
                lines.append(node.lineno)
    return sorted(lines)


def python_files():
    files = [p for p in PYTHON_FILES if p.exists()]
    for tree in PYTHON_TREES:
        files += [p for p in tree.rglob("*.py") if "__pycache__" not in p.parts]
    return sorted(files)


def test_the_repo_root_packages_common_and_only_common():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == ["common*"]
    engine = tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))
    assert engine["tool"]["setuptools"]["packages"]["find"]["include"] == ["src*"]


def test_common_and_src_import_from_outside_the_repo(tmp_path):
    """The property the sys.path edits used to provide, provided by the venv: from a
    directory that is neither the repo root nor 2.0/, with no PYTHONPATH, both
    packages import. Fails in a venv that has not run
    `pip install --no-deps -e . -e 2.0/` from the repo root."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run(
        [sys.executable, "-c",
         "import common.db, src.domain.enums; print(common.db.__file__); "
         "print(src.domain.enums.__file__)"],
        cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, (
        "common/src are not installed in this interpreter's environment -- run "
        "`2.0/venv/bin/pip install --no-deps -e . -e 2.0/` from the repo root:\n" + r.stderr)
    common_file, src_file = r.stdout.splitlines()
    assert Path(common_file).parts[-2:] == ("common", "db.py")
    assert Path(src_file).parts[-4:] == ("2.0", "src", "domain", "enums.py")


def test_ci_installs_both_distributions_before_the_tests_run():
    text = WORKFLOW.read_text(encoding="utf-8")
    installs = re.findall(r"pip install (.+)", text)
    assert any(re.search(r"(^|\s)-e \.(\s|$)", i) for i in installs), installs
    assert any(re.search(r"(^|\s)-e 2\.0/?(\s|$)", i) for i in installs), installs


def test_the_engine_package_never_edits_sys_path():
    """2.0/src is imported, never run by path, so it has no launch to shim."""
    offenders = {str(p.relative_to(ROOT)): lines
                 for p in sorted((ENGINE / "src").rglob("*.py"))
                 if (lines := sys_path_edits(p))}
    assert not offenders, offenders


_BY_PATH_SCRIPTS = "run by path, so 2.0/ is not on sys.path: 2.0/ for the `scripts.` namespace"
_BARE_2_0 = "a bare module under 2.0/scripts (research.*, ab_evaluate_live, scan.*, vix)"
_BARE_JOURNAL = "bare `journal.*` under scripts/"

# file -> (number of sys.path edits, why it cannot go)
KEPT = {
    "2.0/scripts/ab_test_vrp_baseline.py": (1, _BY_PATH_SCRIPTS),
    "2.0/scripts/analyze.py": (1, _BY_PATH_SCRIPTS + " (trade.sh TICKER)"),
    "2.0/scripts/backfill_historical.py": (1, _BY_PATH_SCRIPTS + " (trade.sh, /maintenance Step 7b)"),
    "2.0/scripts/jobs/morning_digest.py": (1, _BY_PATH_SCRIPTS + " (a direct run; the unit uses -m)"),
    "2.0/scripts/jobs/record_moves.py": (1, _BY_PATH_SCRIPTS + " (`--help` runs the file)"),
    "2.0/scripts/jobs/telegram_bot.py": (1, _BY_PATH_SCRIPTS + " (a direct run; the unit uses -m)"),
    "2.0/scripts/research/ab_vrp_power.py": (1, _BARE_2_0),
    "2.0/scripts/research/compound_risk_recalibration.py": (1, _BARE_2_0),
    "2.0/scripts/research/percentile_vrp.py": (1, _BARE_2_0),
    "2.0/scripts/research/placebo.py": (1, _BARE_2_0),
    "2.0/scripts/research/scoring_factors.py": (2, _BARE_2_0 + "; and 2.0/ for scripts.scan"),
    "2.0/scripts/research/small_sample_vrp.py": (1, _BARE_2_0),
    "2.0/scripts/research/vix_adaptive_thresholds.py": (1, _BARE_2_0),
    "2.0/scripts/research/vrp_close_calibration.py": (1, _BARE_2_0),
    "2.0/scripts/rule_evidence.py": (1, _BY_PATH_SCRIPTS),
    "2.0/scripts/scan.py": (1, _BY_PATH_SCRIPTS + " (the bare `scan` package imports "
                               "scripts.track_iv_weekly and scripts.store_bias_prediction)"),
    "2.0/scripts/store_bias_prediction.py": (1, _BY_PATH_SCRIPTS + " (trade.sh TICKER)"),
    "2.0/scripts/taco/calibrate.py": (1, _BY_PATH_SCRIPTS),
    "2.0/scripts/taco_cli.py": (1, _BY_PATH_SCRIPTS + " (trade.sh taco, /taco)"),
    "2.0/scripts/track_iv_weekly.py": (1, _BY_PATH_SCRIPTS + " (/maintenance Step 6)"),
    "scripts/mcp/finnhub_server.py": (1, "launched by path from a worktree on the main checkout's "
                                         "venv: puts its own checkout's src ahead of the editable one"),
    "scripts/reconcile_journal.py": (2, _BARE_JOURNAL + "; and the repo root for "
                                        "scripts.strategy_grouper when run by path (/journal)"),
    "scripts/reenrich_journal_earnings.py": (1, _BARE_JOURNAL),
    "scripts/refresh_risk_metrics.py": (1, "bare `bootstrap_sharpe` under 2.0/scripts"),
    "scripts/refresh_rule_evidence.py": (1, "bare `rule_evidence` under 2.0/scripts"),
    "scripts/regroup_strategies.py": (1, "run by path (/journal): the repo root for "
                                         "scripts.strategy_grouper"),
    "2.0/tests/unit/test_vrp_display.py": (1, "bare `analyze`, loaded as trade.sh runs it"),
    "2.0/tests/unit/test_container_startup.py": (1, "loads entry files as `python scripts/<name>.py` "
                                                    "runs them"),
    "2.0/tests/unit/test_silent_exceptions.py": (1, _BARE_2_0),
    "2.0/tests/unit/test_workflows_vix.py": (1, _BARE_2_0),
    "scripts/tests/test_ab_vrp_power.py": (1, _BARE_2_0),
    "scripts/tests/test_baseline_schema.py": (1, "the engine's scripts.taco from the root suite"),
    "scripts/tests/test_compound_risk_recalibration.py": (1, _BARE_2_0),
    "scripts/tests/test_percentile_vrp.py": (1, _BARE_2_0),
    "scripts/tests/test_pg_loader.py": (1, "bare migrate_to_postgres under 2.0/scripts"),
    "scripts/tests/test_placebo.py": (1, _BARE_2_0),
    "scripts/tests/test_research_events.py": (1, _BARE_2_0),
    "scripts/tests/test_research_scoring_factors.py": (1, _BARE_2_0),
    "scripts/tests/test_rule_evidence_snapshot.py": (1, "bare `rule_evidence` under "
                                                        "2.0/scripts, for its band tuples"),
    "scripts/tests/test_small_sample_vrp.py": (1, _BARE_2_0),
    "scripts/tests/test_vix_adaptive_thresholds.py": (1, _BARE_2_0),
    "scripts/tests/test_vrp_close_calibration.py": (1, _BARE_2_0),
    "tests/journal/test_csv_reader.py": (1, _BARE_JOURNAL),
    "tests/journal/test_journal_parsing.py": (1, _BARE_JOURNAL),
    "tests/journal/test_journal_statistics.py": (1, _BARE_JOURNAL),
    "tests/journal/test_parse_fidelity_csv.py": (1, _BARE_JOURNAL),
    "tests/journal/test_vrp_correlation.py": (1, _BARE_JOURNAL),
}


def test_every_remaining_sys_path_edit_is_a_listed_launch_shim():
    """None reaches `common` or `src` -- those are installed. A new edit fails here
    until it is removed, or listed with the name it provides that no package does."""
    found = {str(p.relative_to(ROOT)): len(lines)
             for p in python_files() if (lines := sys_path_edits(p))}
    assert found == {path: count for path, (count, _) in KEPT.items()}


def test_common_never_edits_sys_path():
    offenders = {str(p.relative_to(ROOT)): lines
                 for p in sorted((ROOT / "common").rglob("*.py"))
                 if (lines := sys_path_edits(p))}
    assert not offenders, offenders
