"""The only module that talks to the TP-7, and it does so only through ``tp7``.

Anything about USB or MTP belongs in the Rust CLI; here we shell out and parse
its ``--json`` reports, tolerating fields it may gain or lose.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Substring tp7 prints on stderr when a remote path does not exist.
REMOTE_MISSING = "remote path was not found"


class DeviceError(Exception):
    """A ``tp7`` invocation failed for a reason we cannot handle."""


@dataclass(frozen=True)
class Device:
    serial: str | None
    mode: str
    product: str | None
    vendor_id: int | None
    product_id: int | None
    #: macOS IORegistry entry id; a new one means the device re-enumerated.
    registry_id: str | None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def audio_mode(self) -> bool:
        return self.mode == "audio-midi"

    @classmethod
    def from_dict(cls, data: dict) -> "Device":
        return cls(
            serial=data.get("serial_number"),
            mode=str(data.get("mode") or "unknown"),
            product=data.get("product"),
            vendor_id=_int_or_none(data.get("vendor_id")),
            product_id=_int_or_none(data.get("product_id")),
            registry_id=data.get("registry_entry_id"),
            raw=data,
        )


@dataclass(frozen=True)
class PullFile:
    remote_path: str
    local_path: str
    size: int
    #: ``downloaded``, ``dry-run``, ``skipped-exists`` or ``skipped-too-large``.
    status: str

    @property
    def downloaded(self) -> bool:
        return self.status == "downloaded"

    @property
    def too_large(self) -> bool:
        return self.status == "skipped-too-large"

    @classmethod
    def from_dict(cls, data: dict) -> "PullFile":
        return cls(
            remote_path=str(data.get("remote_path") or ""),
            local_path=str(data.get("local_path") or ""),
            size=int(data.get("size") or 0),
            status=str(data.get("status") or "skipped"),
        )


@dataclass(frozen=True)
class PullReport:
    remote_path: str
    local_path: str
    downloaded: int
    skipped: int
    total_bytes: int
    files: tuple[PullFile, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "PullReport":
        files = tuple(PullFile.from_dict(item) for item in data.get("files") or [])
        return cls(
            remote_path=str(data.get("remote_path") or ""),
            local_path=str(data.get("local_path") or ""),
            downloaded=int(data.get("downloaded") or 0),
            skipped=int(data.get("skipped") or 0),
            total_bytes=int(data.get("total_bytes") or 0),
            files=files,
        )


def devices(tp7: str = "tp7") -> list[Device]:
    """Connected TP-7 devices (``tp7 -j devices``)."""
    result = _run([tp7, "-j", "devices"], tp7)
    if result.returncode != 0:
        raise DeviceError(_stderr(result) or f"tp7 devices exited {result.returncode}")
    payload = _json(result.stdout, "devices")
    if isinstance(payload, dict):
        payload = payload.get("devices") or []
    if not isinstance(payload, list):
        raise DeviceError("tp7 devices returned an unexpected JSON shape")
    return [Device.from_dict(item) for item in payload if isinstance(item, dict)]


def pull(
    remote_dir: str,
    local_dir: Path,
    max_size: str,
    tp7: str = "tp7",
) -> PullReport | None:
    """Pull ``remote_dir`` into ``local_dir``; ``None`` if the remote dir is absent.

    One invocation, one full MIDI -> MTP -> close lifecycle: call this once per
    remote directory per sync.
    """
    command = [
        tp7,
        "-a",
        "--no-progress",
        "-j",
        "pull",
        remote_dir,
        str(local_dir),
        "--recursive",
        "--skip-existing",
        "--max-size",
        max_size,
    ]
    result = _run(command, tp7)
    if result.returncode != 0:
        stderr = _stderr(result)
        if REMOTE_MISSING in stderr.lower():
            return None
        raise DeviceError(stderr or f"tp7 pull exited {result.returncode}")
    payload = _json(result.stdout, "pull")
    if not isinstance(payload, dict):
        raise DeviceError("tp7 pull returned an unexpected JSON shape")
    return PullReport.from_dict(payload)


def _run(command: list[str], tp7: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise DeviceError(f"{tp7}: not found (set `tp7` in the config)") from error
    except OSError as error:
        raise DeviceError(f"{tp7}: {error}") from error


def _json(text: str, what: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise DeviceError(f"tp7 {what} returned invalid JSON: {error}") from error


def _stderr(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or "").strip()


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
