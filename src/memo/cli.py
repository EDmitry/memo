"""The `memo` command line."""

from __future__ import annotations

import contextlib
import fcntl
import functools
import json as jsonlib
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import click

from . import journal, launchd
from .config import Config, ConfigError, load as load_config
from .device import DeviceError, devices as list_devices, pull as pull_dir
from .store import Store, Transcript
from .transcribe import TranscribeError, Transcriber

APP_ERRORS = (ConfigError, DeviceError, TranscribeError, launchd.LaunchdError, OSError)

DEFAULT_SHOW_COUNT = 10
SINCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([mhdw])$", re.IGNORECASE)
SINCE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


# ---------------------------------------------------------------- plumbing


def handle_errors(command):
    """Turn library errors into `Error: ...` on stderr with exit code 1."""

    @functools.wraps(command)
    def wrapper(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except APP_ERRORS as error:
            raise click.ClickException(str(error)) from error

    return wrapper


def context() -> tuple[Config, Store]:
    cfg = load_config()
    return cfg, Store(cfg.dir)


def warn(message: str) -> None:
    click.echo(message, err=True)


def parse_since(value: str, now: datetime | None = None) -> datetime:
    """`3d`, `2w`, `12h`, `90m`, or an ISO date/datetime."""
    now = now or datetime.now()
    text = value.strip()
    match = SINCE_RE.match(text)
    if match:
        amount = float(match.group(1))
        unit = SINCE_UNITS[match.group(2).lower()]
        return now - timedelta(**{unit: amount})
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError(
            f"cannot read {value!r} as a time; use 3d, 2w, 12h, 90m, or 2026-09-01"
        ) from error


def notify(message: str) -> None:
    """Best-effort macOS notification; never fatal."""
    script = f'display notification {_osa(message)} with title "memo"'
    try:
        subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
    except OSError:
        pass


def _osa(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def log_line(store: Store, message: str) -> None:
    """Append one line to `.memo/sync.log`; never fatal."""
    stamp = datetime.now().isoformat(timespec="seconds")
    try:
        store.meta_dir.mkdir(parents=True, exist_ok=True)
        with store.sync_log.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


# -------------------------------------------------------------------- sync


@dataclass
class SyncResult:
    pulled: int = 0
    transcribed: int = 0
    too_long: list[tuple[str, float]] = field(default_factory=list)
    too_large: list[str] = field(default_factory=list)
    pull_error: str | None = None

    def summary(self) -> str:
        parts = [f"pulled={self.pulled}", f"transcribed={self.transcribed}"]
        if self.pull_error:
            parts.append(f"pull-error={self.pull_error!r}")
        if self.too_long:
            parts.append(f"too-long={len(self.too_long)}")
        if self.too_large:
            parts.append(f"too-large={len(self.too_large)}")
        return " ".join(parts)


def run_sync(
    cfg: Config,
    store: Store,
    *,
    do_pull: bool,
    max_minutes: float,
    quiet: bool,
) -> SyncResult:
    """Pull, transcribe, append. Every step is idempotent and re-runnable."""
    result = SyncResult()
    store.ensure_dirs()
    journal.ensure_claude_md(store)

    if do_pull:
        for remote in cfg.remote_dirs:
            try:
                report = pull_dir(remote, store.audio_dir, cfg.max_pull_size, tp7=cfg.tp7)
            except DeviceError as error:
                # A dropped MTP session must not cost us what was already
                # pulled: transcribe that, and surface the error at the end.
                result.pull_error = str(error)
                warn(f"pull from {remote} failed: {error}")
                break
            if report is None:  # remote dir absent on this device
                continue
            result.pulled += report.downloaded
            result.too_large.extend(
                f"{item.remote_path} ({item.size / 1_048_576:.0f} MB)"
                for item in report.files
                if item.too_large
            )
        if not quiet and result.pulled:
            click.echo(f"pulled {result.pulled} new {_plural(result.pulled, 'file')}")

    pending = store.untranscribed()
    if not pending:
        if not quiet:
            click.echo("nothing new to transcribe")
        return result

    transcriber = Transcriber(cfg.model, cfg.language)
    cap = max_minutes * 60 if max_minutes > 0 else 0.0

    for path in pending:
        duration = transcriber.duration(path)
        if cap and duration > cap:
            result.too_long.append((path.name, duration))
            if not quiet:
                click.echo(f"skipped {path.name} ({_clock(duration)}, over --max-minutes)")
            continue
        if not quiet:
            warn(f"transcribing {path.name} ({_clock(duration)})…")
        transcript = transcriber.transcribe(path, duration_s=duration)
        store.write_transcript(transcript)
        # Append per memo, so a crash mid-run loses nothing.
        journal.append_entry(store, transcript)
        result.transcribed += 1
        if not quiet:
            click.echo(_ls_line(transcript, _width()))

    return result


@click.command()
@click.option("--auto", is_flag=True, help="launchd mode: quiet, notifies, waits for unplug.")
@click.option("--no-pull", is_flag=True, help="Only transcribe audio already in audio/.")
@click.option(
    "--max-minutes",
    type=float,
    default=None,
    help="Skip recordings longer than this (0 lifts the cap).",
)
def sync(auto: bool, no_pull: bool, max_minutes: float | None) -> None:
    """Pull new memos from a connected TP-7, transcribe, append to the journal."""
    if auto:
        _sync_auto(no_pull=no_pull, max_minutes=max_minutes)
        return
    _sync_once(auto=False, no_pull=no_pull, max_minutes=max_minutes)


@handle_errors
def _sync_once(*, auto: bool, no_pull: bool, max_minutes: float | None) -> SyncResult | None:
    cfg, store = context()
    limit = cfg.max_minutes if max_minutes is None else max_minutes
    store.ensure_dirs()

    if not no_pull and not _wait_for_power_on(cfg, store, auto=auto):
        if not auto:
            click.echo("no TP-7 connected")
        return None

    with _sync_lock(store) as acquired:
        if not acquired:
            # Two syncs on one device time each other out: launchd fires on
            # every re-enumeration, and a plug-in can overlap a manual run.
            if auto:
                log_line(store, "skipped (another sync is running)")
                return None
            raise DeviceError("another memo sync is already running")

        result = run_sync(cfg, store, do_pull=not no_pull, max_minutes=limit, quiet=auto)
        store.last_sync.touch()  # informational only: `memo status` shows it

    if not auto and result.too_large:
        for item in result.too_large:
            warn(f"not pulled (over max_pull_mb): {item}")
    if not auto and result.transcribed:
        click.echo(f"{result.transcribed} {_plural(result.transcribed, 'memo')} transcribed")
    if result.pull_error and not auto:
        raise DeviceError(f"pull did not finish: {result.pull_error}; run `memo sync` again")
    return result


UNPLUG_POLL_SECONDS = 5.0
POWER_ON_POLL_SECONDS = 2.0
#: How long a manual `memo sync` waits for a powered-off recorder to be turned on.
POWER_ON_MANUAL_TIMEOUT = 60.0
#: Closing an MTP session takes the recorder off USB for ~8 s (measured on
#: firmware 2.5.7); a real unplug has to outlast that by a wide margin.
UNPLUG_ABSENT_POLLS = 6


def _sync_auto(*, no_pull: bool, max_minutes: float | None) -> None:
    """launchd entry point: never raises, logs everything, notifies on change.

    launchd treats an IOKit match as a level, not an edge: it re-spawns the job
    every ThrottleInterval for as long as a matching device is attached. So
    this process stays alive until the recorder is unplugged, whatever the
    sync itself did, and launchd sees exactly one run per plug-in.
    """
    cfg: Config | None = None
    store: Store | None = None
    try:
        cfg = load_config()
        store = Store(cfg.dir)
    except Exception:  # noqa: BLE001 - config may be broken; fall back to no log
        pass

    try:
        result = _sync_once(auto=True, no_pull=no_pull, max_minutes=max_minutes)
    except Exception as error:  # noqa: BLE001 - --auto must never raise
        message = str(error) or error.__class__.__name__
        if store is not None:
            log_line(store, f"error: {message}")
        notify(f"sync failed: {message}")
        click.echo(f"Error: {message}", err=True)
        _wait_for_unplug(cfg)
        sys.exit(1)

    _report_auto(result, store)
    _wait_for_unplug(cfg)


def _wait_for_power_on(cfg: Config, store: Store, *, auto: bool) -> bool:
    """True once a TP-7 is present and powered on; False if none is connected.

    A recorder plugged in while switched off enumerates as a bare USB
    mass-storage device with no MIDI, so the MTP switch cannot be sent and
    the state never changes on its own (measured on firmware 2.5.7). Turning
    it on re-enumerates it in audio mode, so wait for that: forever in --auto
    mode (the run lives until unplug anyway), briefly for a manual sync.
    """
    told = False
    deadline = None if auto else time.monotonic() + POWER_ON_MANUAL_TIMEOUT
    absent = 0
    while True:
        device = next(iter(list_devices(tp7=cfg.tp7)), None)
        if device is None:
            absent += 1
            if not told or absent >= UNPLUG_ABSENT_POLLS:
                return False
        elif not device.powered_off:
            return True
        else:
            absent = 0
            if not told:
                told = True
                if auto:
                    log_line(store, "waiting (TP-7 is powered off)")
                    notify("TP-7 is powered off; turn it on to sync")
                else:
                    warn("TP-7 is powered off; waiting for it to be turned on…")
            if deadline is not None and time.monotonic() >= deadline:
                raise DeviceError("TP-7 is still powered off; turn it on and run `memo sync` again")
        time.sleep(POWER_ON_POLL_SECONDS)


def _wait_for_unplug(cfg: Config | None) -> None:
    """Block until the recorder is physically unplugged.

    Absence alone is not enough: closing the MTP session re-enumerates the
    device (it vanishes for ~8 s), and a quick replug can fit inside one
    poll interval. So once the recorder has settled into its audio
    personality, remember its IORegistry id: a different id in audio mode
    can only come from a replug. Absence counts only once it has lasted
    longer than any re-enumeration.
    """
    if cfg is None:
        return
    settled_id: str | None = None
    absent = 0
    while True:
        try:
            device = next(iter(list_devices(tp7=cfg.tp7)), None)
        except DeviceError:
            return
        if device is None:
            absent += 1
            if absent >= UNPLUG_ABSENT_POLLS:
                return
        else:
            absent = 0
            if device.audio_mode:
                if settled_id is None:
                    settled_id = device.registry_id
                elif device.registry_id != settled_id:
                    return
        time.sleep(UNPLUG_POLL_SECONDS)


def _report_auto(result: SyncResult | None, store: Store | None) -> None:
    if result is None or store is None:
        return
    log_line(store, f"{'partial' if result.pull_error else 'ok'} {result.summary()}")
    if result.pull_error:
        notify(
            f"{result.transcribed} new {_plural(result.transcribed, 'memo')} transcribed; "
            "pull did not finish, replug or run `memo sync`"
        )
    elif result.transcribed:
        notify(f"{result.transcribed} new {_plural(result.transcribed, 'memo')} transcribed")
    elif result.too_long:
        notify(f"{len(result.too_long)} memos skipped (too long)")
    elif not (result.pulled or result.too_large):
        # A plug-in that finds nothing must still say so, or it looks broken.
        notify("no new memos")


@contextlib.contextmanager
def _sync_lock(store: Store):
    """Yield True while holding the memo dir's sync lock, False if someone else has it."""
    with open(store.meta_dir / "lock", "w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# -------------------------------------------------------------------- show


@click.command()
@click.option("-n", "count", type=int, default=None, help="Show the last N memos.")
@click.option("--today", is_flag=True, help="Show today's memos.")
@click.option("--since", "since", metavar="WHEN", help="3d, 2w, 12h, or an ISO date.")
@click.option("--all", "show_all", is_flag=True, help="Show everything.")
@click.option("--json", "as_json", is_flag=True, help="Print transcripts as JSON.")
@handle_errors
def show(
    count: int | None,
    today: bool,
    since: str | None,
    show_all: bool,
    as_json: bool,
) -> None:
    """Print recent memos, grouped by day, newest day last."""
    selectors = [count is not None, today, since is not None, show_all]
    if sum(1 for flag in selectors if flag) > 1:
        raise click.UsageError("use only one of -n, --today, --since, --all")

    _cfg, store = context()
    transcripts = _select(store.transcripts(), count, today, since, show_all)

    if as_json:
        click.echo(jsonlib.dumps([item.to_dict() for item in transcripts], ensure_ascii=False, indent=2))
        return

    if not transcripts:
        click.echo("no memos")
        return

    width = _width()
    day = None
    for index, transcript in enumerate(transcripts):
        if transcript.date != day:
            if index:
                click.echo()
            day = transcript.date
            click.echo(click.style(day, bold=True))
            click.echo()
        head = click.style(transcript.time, fg="cyan") + "  " + click.style(transcript.duration, dim=True)
        click.echo("  " + head)
        for line in _wrap(transcript.text or "(no speech detected)", width - 2):
            click.echo("  " + line)
        click.echo()


def _select(
    transcripts: list[Transcript],
    count: int | None,
    today: bool,
    since: str | None,
    show_all: bool,
) -> list[Transcript]:
    if show_all:
        return transcripts
    if today:
        stamp = datetime.now().strftime("%Y-%m-%d")
        return [item for item in transcripts if item.date == stamp]
    if since is not None:
        try:
            cutoff = parse_since(since)
        except ValueError as error:
            raise click.UsageError(str(error)) from error
        return [item for item in transcripts if item.recorded_at >= cutoff]
    limit = DEFAULT_SHOW_COUNT if count is None else count
    return transcripts[-limit:] if limit > 0 else []


# ---------------------------------------------------------------------- ls


@click.command(name="ls")
@handle_errors
def ls_command() -> None:
    """One line per memo: date, time, duration, first words."""
    _cfg, store = context()
    transcripts = store.transcripts()
    if not transcripts:
        click.echo("no memos")
        return
    width = _width()
    for transcript in transcripts:
        click.echo(_ls_line(transcript, width))


def _ls_line(transcript: Transcript, width: int) -> str:
    prefix = f"{transcript.date} {transcript.time}  {transcript.duration:>5}  "
    text = " ".join(transcript.text.split()) or "(no speech detected)"
    room = max(20, width - len(prefix))
    if len(text) > room:
        text = text[: room - 1].rstrip() + "…"
    return prefix + text


# -------------------------------------------------------------------- open


@click.command(name="open")
@click.option("--dir", "open_dir", is_flag=True, help="Open the memo directory instead.")
@handle_errors
def open_command(open_dir: bool) -> None:
    """Open today's journal (or the memo directory) in $EDITOR."""
    _cfg, store = context()
    if open_dir:
        target = store.root
    else:
        today = store.journal_path(datetime.now().strftime("%Y-%m-%d"))
        journals = store.journal_files()
        if today.exists():
            target = today
        elif journals:
            target = journals[-1]
            warn(f"no journal for today; opening {target.name}")
        else:
            raise click.ClickException(f"no journals in {store.root}")
    if not target.exists():
        raise click.ClickException(f"{target} does not exist")

    editor = os.environ.get("EDITOR")
    command = [editor, str(target)] if editor else ["open", str(target)]
    subprocess.run(command, check=False)


# ----------------------------------------------------------------- rebuild


@click.command()
@handle_errors
def rebuild() -> None:
    """Regenerate every journal file from the transcripts."""
    _cfg, store = context()
    journal.ensure_claude_md(store)
    written = journal.rebuild(store)
    count = len(store.transcripts())
    click.echo(
        f"rebuilt {len(written)} {_plural(len(written), 'day')} from "
        f"{count} {_plural(count, 'transcript')}"
    )


# ------------------------------------------------------- install/uninstall


@click.command()
@handle_errors
def install() -> None:
    """Write and load the LaunchAgent that syncs when the TP-7 is plugged in."""
    cfg, store = context()
    store.ensure_dirs()
    memo_exe = launchd.memo_executable()
    if ".venv" in Path(memo_exe).parts:
        warn(f"note: {memo_exe} is inside a project virtualenv; `uv tool install .` gives a stabler path")
    plist = launchd.build_plist(
        memo_exe=memo_exe,
        memo_dir=store.root,
        tp7=cfg.tp7,
        ffmpeg="ffmpeg",
    )
    path = launchd.write_plist(plist)
    launchd.bootstrap(path)
    click.echo(f"installed {launchd.LABEL}")
    click.echo(f"  plist: {path}")
    click.echo(f"  runs:  {' '.join(plist['ProgramArguments'])}")
    click.echo(f"  log:   {store.launchd_log}")


@click.command()
@handle_errors
def uninstall() -> None:
    """Unload and remove the LaunchAgent."""
    unloaded = launchd.bootout()
    path = launchd.plist_path()
    removed = path.exists()
    path.unlink(missing_ok=True)
    if unloaded or removed:
        click.echo(f"removed {launchd.LABEL}")
    else:
        click.echo(f"{launchd.LABEL} was not installed")


# ------------------------------------------------------------------ status


@click.command()
@handle_errors
def status() -> None:
    """Config, model, memo dir, LaunchAgent state, last sync."""
    cfg, store = context()
    audio = store.audio_files()
    transcripts = store.transcripts()
    pending = store.untranscribed()

    click.echo(f"config      {cfg.source or '(defaults)'}")
    click.echo(f"dir         {store.root}{'' if store.root.is_dir() else '  (missing)'}")
    click.echo(f"model       {cfg.model}")
    click.echo(f"language    {cfg.language or 'auto'}")
    click.echo(f"remote dirs {', '.join(cfg.remote_dirs)}")
    click.echo(f"limits      max {cfg.max_minutes:g} min, pull <= {cfg.max_pull_size}")
    click.echo(f"tp7         {shutil.which(cfg.tp7) or cfg.tp7 + '  (not found)'}")
    click.echo(f"ffmpeg      {shutil.which('ffmpeg') or 'ffmpeg  (not found)'}")
    click.echo(f"memos       {len(audio)} audio, {len(transcripts)} transcribed, {len(pending)} pending")
    click.echo(f"agent       {'loaded' if launchd.is_loaded() else 'not loaded'} ({launchd.LABEL})")
    click.echo(f"last sync   {_last_sync(store)}")


def _last_sync(store: Store) -> str:
    if not store.last_sync.exists():
        return "never"
    when = datetime.fromtimestamp(store.last_sync.stat().st_mtime)
    return when.strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ shared


def _width() -> int:
    return max(40, min(shutil.get_terminal_size(fallback=(80, 24)).columns, 100))


def _wrap(text: str, width: int) -> list[str]:
    clean = " ".join(text.split())
    return textwrap.wrap(clean, width=width) or [""]


def _clock(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else word + "s"


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="memo")
def cli() -> None:
    """Turn TP-7 voice memos into text you can read and feed to an AI."""


for command in (sync, show, ls_command, open_command, rebuild, install, uninstall, status):
    cli.add_command(command)


def main() -> None:
    cli()


__all__ = ["cli", "main", "parse_since", "run_sync"]
