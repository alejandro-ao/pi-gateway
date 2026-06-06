from __future__ import annotations

import asyncio
import importlib.metadata
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import __version__

PACKAGE_NAME = "pi-gateway"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
DEFAULT_CACHE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True, slots=True)
class VersionStatus:
    current: str
    latest: str | None
    checked_at: float
    error: str | None = None

    @property
    def update_available(self) -> bool:
        return bool(self.latest and version_key(self.latest) > version_key(self.current))


_cached_status: VersionStatus | None = None


def current_version() -> str:
    try:
        return importlib.metadata.version(PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        return __version__


def version_key(version: str) -> tuple[int, ...]:
    """Return a small comparison key for normal release versions.

    pi-gateway currently publishes simple numeric versions like 0.1.1. This is
    intentionally lightweight so update checks do not add a packaging runtime
    dependency.
    """
    release = version.split("+", 1)[0].split("-", 1)[0].split(".dev", 1)[0]
    parts: list[int] = []
    for part in release.split("."):
        digits = ""
        for char in part:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits or 0))
    return tuple(parts)


def _fetch_latest_version(timeout: float) -> str:
    request = urllib.request.Request(PYPI_JSON_URL, headers={"User-Agent": f"pi-gateway/{current_version()}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload: dict[str, Any] = json.load(response)
    latest = payload.get("info", {}).get("version")
    if not latest:
        raise ValueError("PyPI response did not include info.version")
    return str(latest)


async def check_version(*, cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> VersionStatus:
    """Return installed/latest version information using a short cached PyPI check."""
    global _cached_status
    now = time.monotonic()
    if _cached_status and now - _cached_status.checked_at < cache_ttl_seconds:
        return _cached_status

    current = current_version()
    try:
        latest = await asyncio.to_thread(_fetch_latest_version, timeout)
        status = VersionStatus(current=current, latest=latest, checked_at=now)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        status = VersionStatus(current=current, latest=None, checked_at=now, error=str(exc))
    _cached_status = status
    return status


def format_update_notice(status: VersionStatus) -> str | None:
    if not status.update_available:
        return None
    return (
        f"⬆️ pi-gateway {status.latest} is available. Current: {status.current}.\n"
        "Upgrade with:\n"
        "uv tool upgrade pi-gateway\n"
        "sudo systemctl restart pi-gateway.service"
    )


def format_status_line(status: VersionStatus) -> str:
    if status.update_available:
        return f"pi-gateway: {status.current} (update available: {status.latest})"
    if status.latest:
        return f"pi-gateway: {status.current} (up to date)"
    return f"pi-gateway: {status.current} (update check unavailable)"
