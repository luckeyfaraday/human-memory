"""Resolve human-memory project storage paths.

The default storage is central, under $AGENT_MEMORY_HOME/projects/, so installing
the shim does not create files inside every repository a user works in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MEMORY_FILE = "HUMAN_MEMORY.md"
VALID_STORAGE = {"central", "project-file"}


@dataclass(frozen=True)
class MemoryLocation:
    root: Path
    path: Path
    storage: str
    project_id: str
    metadata_path: Path | None = None


def agent_memory_home() -> Path:
    home = os.environ.get("AGENT_MEMORY_HOME") or os.path.join(Path.home(), ".agent-memory")
    return Path(home)


def default_config_path() -> Path:
    return agent_memory_home() / "config.toml"


def project_id(root: Path) -> str:
    resolved = root.resolve()
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", resolved.name).strip("-._").lower()
    if not slug:
        slug = "project"
    digest = hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def resolve(root: Path, storage: str = "central") -> MemoryLocation:
    if storage not in VALID_STORAGE:
        raise ValueError(f"memory.storage must be one of: {', '.join(sorted(VALID_STORAGE))}")
    root = root.resolve()
    pid = project_id(root)
    if storage == "project-file":
        return MemoryLocation(root=root, path=root / MEMORY_FILE, storage=storage, project_id=pid)
    project_dir = agent_memory_home() / "projects" / pid
    return MemoryLocation(
        root=root,
        path=project_dir / MEMORY_FILE,
        storage=storage,
        project_id=pid,
        metadata_path=project_dir / "metadata.json",
    )


def ensure_location(location: MemoryLocation) -> None:
    location.path.parent.mkdir(parents=True, exist_ok=True)
    if location.metadata_path is None:
        return
    payload = {
        "project_id": location.project_id,
        "cwd": str(location.root),
        "memory_file": str(location.path),
        "storage": location.storage,
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=location.metadata_path.name + ".",
        suffix=".tmp",
        dir=str(location.metadata_path.parent),
        text=True,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(payload, indent=2) + "\n")
        tmp.replace(location.metadata_path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def load_storage(path: Path | None = None) -> tuple[str, str | None]:
    path = path or default_config_path()
    if not path.exists():
        return "central", f"no config at {path}; using central memory storage"
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return "central", f"config {path} unreadable ({e}); using central memory storage"
    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        return "central", f"config {path} has bad [memory] table; using central"
    storage = memory.get("storage", "central")
    if not isinstance(storage, str):
        return "central", (f"config {path} has bad memory.storage ({storage!r}); "
                           "expected string; using central")
    if storage not in VALID_STORAGE:
        return "central", f"config {path} has bad memory.storage ({storage}); using central"
    return storage, f"loaded memory storage from {path}: {storage}"
