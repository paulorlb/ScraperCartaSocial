"""Create, list, and resolve versioned collection snapshots.

Each collection run stores its HTML pages, parsed JSON, and per-ID request
state in its own folder under raw/scraping/snapshots/. Snapshot folders are
never reused for a different collection, so earlier captures stay intact.
Tables derived from a snapshot are written separately under interim/scraping/.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import warnings

import pandas as pd

from .utils import INTERIM, RAW, write_json

SNAPSHOT_ROOT = RAW / "scraping" / "snapshots"
EXPORT_ROOT = INTERIM / "scraping"
SNAPSHOT_SUBDIRS = ("html", "json", "state")
RESUMABLE_STATUSES = ("in_progress", "partial")
_SNAPSHOT_ID = re.compile(r"[0-9]{8}T[0-9]{6}Z(?:_[A-Za-z0-9-]+)?")
_LABEL = re.compile(r"[A-Za-z0-9-]+")


def create_snapshot(label=None, root=SNAPSHOT_ROOT, now=None):
    """Create an empty snapshot folder with an in-progress manifest.

    Args:
        label: Optional suffix of letters, digits, and hyphens, e.g. "pilot".
        root: Folder holding all snapshots.
        now: Optional timezone-aware creation time, mainly for tests.

    Returns:
        Path: New snapshot folder, named YYYYMMDDTHHMMSSZ[_label].

    Raises:
        ValueError: The label contains unsupported characters.
        FileExistsError: A snapshot with the same identifier already exists.
    """
    if label is not None and not _LABEL.fullmatch(str(label)):
        raise ValueError("Snapshot labels may contain only letters, digits, and hyphens.")
    created = now or datetime.now(timezone.utc)
    snapshot_id = created.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if label is not None:
        snapshot_id += f"_{label}"
    path = Path(root) / snapshot_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=False)
    for name in SNAPSHOT_SUBDIRS:
        (path / name).mkdir()
    write_json({"snapshot_id": snapshot_id, "created_at": created.isoformat(),
                "finished_at": None, "status": "in_progress", "parameters": {},
                "counts": {}}, path / "manifest.json")
    return path


def read_manifest(snapshot_dir):
    """Read a snapshot manifest.

    Args:
        snapshot_dir: Snapshot folder.

    Returns:
        dict: Manifest contents.

    Raises:
        FileNotFoundError: The folder is not a snapshot.
    """
    path = Path(snapshot_dir) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"{snapshot_dir} is not a collection snapshot (no manifest.json).")
    return json.loads(path.read_text(encoding="utf-8"))


def update_manifest(snapshot_dir, **changes):
    """Merge top-level fields into a snapshot manifest and save it atomically.

    Args:
        snapshot_dir: Snapshot folder.
        **changes: Manifest fields to replace.

    Returns:
        dict: Updated manifest.
    """
    manifest = read_manifest(snapshot_dir)
    manifest.update(changes)
    write_json(manifest, Path(snapshot_dir) / "manifest.json")
    return manifest


def export_dir_for(snapshot_dir, export_root=EXPORT_ROOT):
    """Return the folder holding tables derived from a snapshot.

    Args:
        snapshot_dir: Snapshot folder.
        export_root: Folder holding all derived snapshot tables.

    Returns:
        Path: export_root / snapshot identifier (not created here).
    """
    return Path(export_root) / Path(snapshot_dir).name


def _snapshot_dirs(root):
    root = Path(root)
    if not root.is_dir():
        return []
    paths = [path for path in root.iterdir()
             if path.is_dir() and _SNAPSHOT_ID.fullmatch(path.name)
             and (path / "manifest.json").is_file()]
    return sorted(paths, key=lambda path: path.name)


def list_snapshots(root=SNAPSHOT_ROOT, export_root=EXPORT_ROOT):
    """Summarize available snapshots, oldest first.

    Args:
        root: Folder holding all snapshots.
        export_root: Folder holding derived tables, used for csv_built.

    Returns:
        pandas.DataFrame: snapshot_id, status, created_at, finished_at, first_id,
            last_id, ok, no_data, error, and csv_built.
    """
    columns = ["snapshot_id", "status", "created_at", "finished_at", "first_id",
               "last_id", "ok", "no_data", "error", "csv_built"]
    rows = []
    for path in _snapshot_dirs(root):
        manifest = read_manifest(path)
        parameters = manifest.get("parameters", {})
        counts = manifest.get("counts", {})
        rows.append({
            "snapshot_id": path.name, "status": manifest.get("status"),
            "created_at": manifest.get("created_at"),
            "finished_at": manifest.get("finished_at"),
            "first_id": parameters.get("first_id"), "last_id": parameters.get("last_id"),
            "ok": counts.get("ok", 0), "no_data": counts.get("no_data", 0),
            "error": counts.get("error", 0),
            "csv_built": (export_dir_for(path, export_root) / "equipments.csv").is_file(),
        })
    return pd.DataFrame(rows, columns=columns)


def resolve_snapshot(snapshot_id="latest", root=SNAPSHOT_ROOT):
    """Find a snapshot folder by identifier.

    Args:
        snapshot_id: Exact snapshot identifier, or "latest" for the newest one.
        root: Folder holding all snapshots.

    Returns:
        Path: Existing snapshot folder. A warning is issued when "latest"
            resolves to a snapshot whose collection did not finish cleanly.

    Raises:
        FileNotFoundError: No snapshot exists, or the identifier is unknown.
    """
    paths = _snapshot_dirs(root)
    if snapshot_id == "latest":
        if not paths:
            raise FileNotFoundError("No collection snapshots exist yet; collect one with collection_mode='new'.")
        path = paths[-1]
        status = read_manifest(path).get("status")
        if status != "complete":
            warnings.warn(f"Latest snapshot {path.name} has status '{status}'.", stacklevel=2)
        return path
    for path in paths:
        if path.name == snapshot_id:
            return path
    raise FileNotFoundError(f"Snapshot {snapshot_id!r} was not found in {root}.")
