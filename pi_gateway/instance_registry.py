"""Per-user index of gateway configs; Pi sessions and process state live elsewhere."""

from __future__ import annotations

import fcntl
import json
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TypedDict


class RegisteredInstance(TypedDict):
    name: str | None
    config: str


_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?\Z")


def normalize_name(value: str) -> str:
    name = value.strip().lower()
    if not _NAME_PATTERN.fullmatch(name):
        raise ValueError("Instance name must be 1-64 letters, digits, or hyphens; no leading/trailing hyphen.")
    return name


class InstanceRegistry:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.with_suffix(".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def load(self) -> list[RegisteredInstance]:
        """Call with locked(). Upgrade the original list-of-paths registry in place."""
        if not self.path.exists():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        legacy = isinstance(raw, list)
        if legacy:
            raw = [{"config": config, "name": None} for config in raw]
        elif not isinstance(raw, dict) or raw.get("version") != 2:
            raise ValueError(f"Unsupported instance registry: {self.path}")
        rows = raw if legacy else raw.get("instances")
        if not isinstance(rows, list):
            raise ValueError(f"Invalid instance registry: {self.path}")
        entries: list[RegisteredInstance] = []
        names: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("config"), str):
                raise ValueError(f"Invalid instance registry: {self.path}")
            name = row.get("name")
            if name is not None:
                if not isinstance(name, str) or normalize_name(name) != name or name in names:
                    raise ValueError(f"Invalid or duplicate instance name in registry: {self.path}")
                names.add(name)
            entries.append({"name": name, "config": str(Path(row["config"]).expanduser().resolve())})
        if legacy:
            self.save(entries)
        return entries

    def save(self, entries: list[RegisteredInstance]) -> None:
        """Call with locked(). Replace the index atomically (never store bot tokens)."""
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, delete=False, encoding="utf-8") as tmp:
            json.dump({"version": 2, "instances": entries}, tmp, indent=2)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        try:
            tmp_path.replace(self.path)
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    def upsert(entries: list[RegisteredInstance], config: Path, name: str | None) -> list[RegisteredInstance]:
        path = str(config.resolve())
        if name and any(entry["name"] == name and entry["config"] != path for entry in entries):
            raise ValueError(f"Instance name {name!r} is already assigned to another config.")
        updated = [entry for entry in entries if entry["config"] != path]
        updated.append({"name": name, "config": path})
        return updated
