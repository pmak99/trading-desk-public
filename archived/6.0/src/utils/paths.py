"""Shared path utilities for 6.0 integration modules.

Provides common path resolution functions used across integration modules.
"""

import sys
import subprocess
from pathlib import Path


def find_main_repo(reference_path: Path = None) -> Path:
    """
    Find main repository root, handling git worktrees correctly.

    Args:
        reference_path: Path to use as reference for git commands.
                       Defaults to this file's parent directory.

    Returns:
        Path to main repository root (where .git directory lives)

    Example:
        main_repo = find_main_repo()
        db_path = main_repo / "2.0" / "data" / "ivcrush.db"
    """
    if reference_path is None:
        reference_path = Path(__file__).parent

    try:
        # Get git common dir (works in both main repo and worktrees)
        result = subprocess.run(
            ['git', 'rev-parse', '--git-common-dir'],
            capture_output=True,
            text=True,
            check=True,
            cwd=reference_path
        )
        git_common_dir = Path(result.stdout.strip())

        # If commondir path is relative, make it absolute
        if not git_common_dir.is_absolute():
            git_common_dir = (reference_path / git_common_dir).resolve()

        # Main repo is parent of .git directory
        main_repo = git_common_dir.parent
        return main_repo

    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as e:
        # Fallback: assume we're in main repo structure
        # Navigate up from 6.0/src/utils to find project root
        fallback = Path(__file__).parent.parent.parent.parent
        return fallback


# Pre-compute main repo path for module-level use
MAIN_REPO = find_main_repo()

# Add root to sys.path for common/ access
_root_str = str(MAIN_REPO)
if _root_str not in sys.path:
    sys.path.insert(0, _root_str)

# Common paths used by integration modules
REPO_2_0 = MAIN_REPO / "2.0"
REPO_4_0 = MAIN_REPO / "4.0"
REPO_5_0 = MAIN_REPO / "5.0"
DB_PATH = REPO_2_0 / "data" / "ivcrush.db"
SENTIMENT_CACHE_DB = REPO_4_0 / "data" / "sentiment_cache.db"
