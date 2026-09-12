"""The user LaunchAgent that runs `memo sync --auto` when the TP-7 appears."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

LABEL = "local.memo.sync"

TP7_VENDOR_ID = 0x2367  # 9063
#: The recorder enumerates as the audio/MIDI personality when plugged in, and
#: as the MTP personality while `tp7` holds a session. Match both.
#: `IOMatchLaunchStream` is deliberately absent: with it, launchd re-spawns the
#: job every ThrottleInterval for as long as the device stays attached.
TP7_PRODUCT_IDS = {"audio": 0x8019, "mtp": 0x0019}  # 32793, 25

THROTTLE_INTERVAL = 10


class LaunchdError(Exception):
    """A launchctl command failed."""


def plist_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"


def memo_executable() -> str:
    """Absolute path of the installed ``memo`` executable."""
    candidate = Path(sys.argv[0]).absolute()
    if candidate.name == "memo" and candidate.is_file():
        return str(candidate)
    found = shutil.which("memo")
    if found:
        return str(Path(found).absolute())
    # Running as `python -m memo` or via `uv run`: the console script sits
    # next to the interpreter.
    sibling = Path(sys.executable).absolute().parent / "memo"
    if sibling.is_file():
        return str(sibling)
    raise LaunchdError("cannot find the `memo` executable; install it with `uv tool install .`")


def build_plist(
    *,
    memo_exe: str,
    memo_dir: Path,
    tp7: str = "tp7",
    ffmpeg: str = "ffmpeg",
    home: str | None = None,
) -> dict:
    """The LaunchAgent definition, with every path resolved to an absolute one."""
    log = str(Path(memo_dir).expanduser() / ".memo" / "launchd.log")
    matching = {
        f"tp7-{name}": {
            "IOProviderClass": "IOUSBDevice",
            "idVendor": TP7_VENDOR_ID,
            "idProduct": product_id,
        }
        for name, product_id in TP7_PRODUCT_IDS.items()
    }
    return {
        "Label": LABEL,
        "ProgramArguments": [memo_exe, "sync", "--auto"],
        "LaunchEvents": {"com.apple.iokit.matching": matching},
        "EnvironmentVariables": {
            "PATH": agent_path(memo_exe, tp7, ffmpeg),
            "HOME": home or str(Path.home()),
        },
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "ThrottleInterval": THROTTLE_INTERVAL,
    }


def agent_path(*executables: str) -> str:
    """PATH for the agent: the dirs holding our tools, then the system ones.

    Symlinks are left alone on purpose: `/opt/homebrew/bin` survives a brew
    upgrade in a way the Cellar directory behind it does not.
    """
    dirs: list[str] = []
    for executable in executables:
        resolved = shutil.which(executable) or (executable if Path(executable).is_file() else None)
        if not resolved:
            continue
        parent = str(Path(resolved).absolute().parent)
        if parent not in dirs:
            dirs.append(parent)
    for fallback in ("/usr/bin", "/bin"):
        if fallback not in dirs:
            dirs.append(fallback)
    return ":".join(dirs)


def write_plist(plist: dict, path: Path | None = None) -> Path:
    path = path or plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(plist, sort_keys=True))
    return path


def domain() -> str:
    return f"gui/{os.getuid()}"


def is_loaded() -> bool:
    return _launchctl(["print", f"{domain()}/{LABEL}"]).returncode == 0


def bootstrap(path: Path) -> None:
    if is_loaded():
        _launchctl(["bootout", f"{domain()}/{LABEL}"])
        # bootout returns before the job is gone; bootstrapping over a job
        # that is still being torn down fails with an I/O error.
        for _ in range(20):
            if not is_loaded():
                break
            time.sleep(0.25)
    result = _launchctl(["bootstrap", domain(), str(path)])
    if result.returncode != 0:
        raise LaunchdError(_message(result) or f"launchctl bootstrap exited {result.returncode}")


def bootout() -> bool:
    """Unload the agent. False if it was not loaded."""
    if not is_loaded():
        return False
    result = _launchctl(["bootout", f"{domain()}/{LABEL}"])
    if result.returncode != 0:
        raise LaunchdError(_message(result) or f"launchctl bootout exited {result.returncode}")
    return True


def _launchctl(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
    except OSError as error:
        raise LaunchdError(f"launchctl: {error}") from error


def _message(result: subprocess.CompletedProcess[str]) -> str:
    return ((result.stderr or "") + (result.stdout or "")).strip()
