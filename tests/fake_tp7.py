#!/usr/bin/env python3
"""A stand-in for the `tp7` binary, driven by environment variables.

FAKE_TP7_DEVICES   number of connected devices to report (default 1)
FAKE_TP7_DIRS      comma-separated remote dirs that exist (default /recordings)
FAKE_TP7_SOURCE    directory whose *.wav files a pull copies into the local dir
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

DEVICE = {
    "vendor_id": 9063,
    "product_id": 25,
    "vendor_id_hex": "0x2367",
    "product_id_hex": "0x0019",
    "manufacturer": "teenage engineering",
    "product": "TP-7",
    "serial_number": "TP7FAKE0001",
    "mode": "mtp",
    "speed": "high",
    "usb_version": "2.0.0",
    "device_version": "2.5.0",
    "class": 0,
    "subclass": 0,
    "protocol": 0,
    "bus_id": None,
    "device_address": 3,
    "port_chain": [1],
    "location_id": "0x01100000",
    "registry_entry_id": "4295000000",
    "interfaces": [],
}


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if not arg.startswith("-")]
    if not args:
        return 2
    command = args[0]

    if command == "devices":
        count = int(os.environ.get("FAKE_TP7_DEVICES", "1"))
        json.dump([DEVICE for _ in range(count)], sys.stdout)
        print()
        return 0

    if command == "pull":
        return pull(args[1], args[2])

    print(f"error: {command} is not implemented yet", file=sys.stderr)
    return 2


def pull(remote: str, local: str) -> int:
    available = os.environ.get("FAKE_TP7_DIRS", "/recordings").split(",")
    if remote not in available:
        print(f"error: remote path was not found: {remote}", file=sys.stderr)
        return 1

    source = Path(os.environ.get("FAKE_TP7_SOURCE", ""))
    destination = Path(local)
    destination.mkdir(parents=True, exist_ok=True)

    files = []
    downloaded = 0
    skipped = 0
    total = 0
    for item in sorted(source.glob("*.wav")) if source.is_dir() else []:
        target = destination / item.name
        if target.exists():
            files.append(entry(remote, item, target, "skipped-exists"))
            skipped += 1
            continue
        if item.name.startswith("huge"):  # stands in for --max-size
            files.append(entry(remote, item, target, "skipped-too-large"))
            skipped += 1
            continue
        shutil.copyfile(item, target)
        files.append(entry(remote, item, target, "downloaded"))
        downloaded += 1
        total += item.stat().st_size

    report = {
        "remote_path": remote,
        "local_path": str(destination),
        "dry_run": False,
        "downloaded": downloaded,
        "skipped": skipped,
        "total_bytes": total,
        "files": files,
    }
    json.dump(report, sys.stdout)
    print()
    return 0


def entry(remote: str, source: Path, target: Path, status: str) -> dict:
    return {
        "remote_path": f"{remote.rstrip('/')}/{source.name}",
        "local_path": str(target),
        "size": source.stat().st_size,
        "status": status,
    }


if __name__ == "__main__":
    sys.exit(main(sys.argv))
