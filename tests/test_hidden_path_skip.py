"""Discovery walks must ignore hidden dot-directories.

Tooling routinely parks a *second copy of the catalog* inside the catalog: a
git worktree under ``.claude/worktrees/<branch>/``, a ``.venv`` holding
vendored fixtures, an editor cache. Those copies carry the same spec files with
the same ``listing.name``, so a walk that descends into them yields every
service twice — and a consumer that uploads what it discovers races the stale
copy onto the real service's backend id.

The skip is computed **relative to the walk root**, which is the other half of
the contract: a catalog that legitimately lives under a hidden directory
(``~/.cache/repo/specs/…``) must still be discovered in full.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from unitysvc_core.utils import find_data_files, find_files_by_pattern, is_hidden_path
from unitysvc_core.validator import DataValidator


@pytest.fixture
def schema_dir() -> Path:
    return Path(__file__).parent.parent / "src" / "unitysvc_core" / "schema"


def _write_listing(folder: Path, name: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    listing = folder / "listing.json"
    listing.write_text(json.dumps({"name": name}))
    return listing


# --- the predicate ---------------------------------------------------------


def test_is_hidden_path_detects_a_dot_directory_below_the_root(tmp_path: Path) -> None:
    assert is_hidden_path(tmp_path / ".claude" / "worktrees" / "wt" / "listing.json", tmp_path)
    assert is_hidden_path(tmp_path / "specs" / ".git" / "listing.json", tmp_path)


def test_is_hidden_path_ignores_dots_above_the_root(tmp_path: Path) -> None:
    """A repo cloned into ``.cache/`` is a normal catalog, not a hidden one."""
    root = tmp_path / ".cache" / "repo"
    assert not is_hidden_path(root / "specs" / "svc" / "listing.json", root)


def test_is_hidden_path_keeps_paths_outside_the_root(tmp_path: Path) -> None:
    """Unrelatable paths were named explicitly, not found by walking."""
    assert not is_hidden_path(Path("/elsewhere/listing.json"), tmp_path)


# --- the walks ------------------------------------------------------------


def test_find_data_files_skips_hidden_directories(tmp_path: Path) -> None:
    real = _write_listing(tmp_path / "specs" / "svc", "labs/svc")
    worktree = _write_listing(tmp_path / ".claude" / "worktrees" / "wt" / "specs" / "svc", "labs/svc")

    found = find_data_files(tmp_path)

    assert real in found
    assert worktree not in found


def test_find_data_files_keeps_a_catalog_under_a_hidden_root(tmp_path: Path) -> None:
    root = tmp_path / ".cache" / "repo"
    real = _write_listing(root / "specs" / "svc", "labs/svc")

    assert find_data_files(root) == [real]


def test_find_files_by_pattern_skips_hidden_directories(tmp_path: Path) -> None:
    """The seller/admin upload path: one service, discovered once."""
    real = _write_listing(tmp_path / "specs" / "svc", "labs/svc")
    worktree = _write_listing(tmp_path / ".claude" / "worktrees" / "wt" / "specs" / "svc", "labs/svc")

    found = [p for p, _fmt, _data in find_files_by_pattern(tmp_path, "listing_v1")]

    assert found == [real]
    assert worktree not in found


def test_validate_all_reaches_a_catalog_under_a_hidden_root(tmp_path: Path, schema_dir: Path) -> None:
    """``validate_all`` skipped dot-parts of the *absolute* path, so a catalog
    under ``~/.cache/`` validated zero files and reported success."""
    root = tmp_path / ".cache" / "repo"
    _write_listing(root / "specs" / "svc", "labs/svc")

    results = DataValidator(root, schema_dir).validate_all()

    assert [Path(k).as_posix() for k in results] == ["specs/svc/listing.json"]


def test_validate_all_skips_hidden_directories(tmp_path: Path, schema_dir: Path) -> None:
    _write_listing(tmp_path / "specs" / "svc", "labs/svc")
    _write_listing(tmp_path / ".claude" / "worktrees" / "wt" / "specs" / "svc", "labs/svc")

    results = DataValidator(tmp_path, schema_dir).validate_all()

    assert [Path(k).as_posix() for k in results] == ["specs/svc/listing.json"]
