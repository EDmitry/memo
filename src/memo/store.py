"""The memo directory: audio files, transcripts, and the metadata under ``.memo/``.

The filesystem is the state. A memo is *pulled* when its audio file exists and
*transcribed* when its transcript JSON exists; everything is keyed by the audio
file stem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Config
from .obsidian import block_id

AUDIO_SUFFIXES = (".wav", ".WAV")

#: TP-7 recordings are named ``YYYY-MM-DD_HHMMSS_NNN.wav``.
NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})(?:_(\d+))?$")


@dataclass
class Transcript:
    """The durable record for one memo; the journal is a rendered view of these."""

    name: str
    recorded_at: datetime
    duration_s: float
    language: str
    model: str
    text: str
    segments: list[dict] = field(default_factory=list)
    transcribed_at: datetime = field(default_factory=datetime.now)
    #: True when reconstructed from a shared journal entry another machine wrote.
    adopted: bool = False

    @property
    def date(self) -> str:
        return self.recorded_at.strftime("%Y-%m-%d")

    @property
    def time(self) -> str:
        return self.recorded_at.strftime("%H:%M")

    @property
    def duration(self) -> str:
        total = int(round(self.duration_s))
        return f"{total // 60}:{total % 60:02d}"

    def to_dict(self) -> dict:
        data = {
            "name": self.name,
            "recorded_at": self.recorded_at.isoformat(timespec="seconds"),
            "duration_s": round(self.duration_s, 3),
            "language": self.language or None,
            "model": self.model or None,
            "text": self.text,
            "segments": self.segments,
            "transcribed_at": self.transcribed_at.isoformat(timespec="seconds"),
        }
        if self.adopted:
            data["adopted"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Transcript":
        return cls(
            name=str(data.get("name", "")),
            recorded_at=_parse_dt(data.get("recorded_at")),
            duration_s=float(data.get("duration_s") or 0.0),
            language=str(data.get("language") or ""),
            model=str(data.get("model") or ""),
            text=str(data.get("text") or "").strip(),
            segments=list(data.get("segments") or []),
            transcribed_at=_parse_dt(data.get("transcribed_at")),
            adopted=bool(data.get("adopted")),
        )


class Store:
    """Paths and lookups for one memo directory.

    The durable data (audio, transcripts, sync state, ``CLAUDE.md``) always
    lives under ``root``. Only the rendered journals can be sent elsewhere,
    via ``journal_dir`` — typically a folder inside an Obsidian vault.
    """

    def __init__(
        self,
        root: Path,
        journal_dir: Path | None = None,
        journal_template: Path | None = None,
    ) -> None:
        self.root = Path(root).expanduser()
        self.journal_dir = Path(journal_dir).expanduser() if journal_dir else self.root
        self.journal_template = journal_template

    @classmethod
    def from_config(cls, cfg: Config) -> "Store":
        return cls(cfg.dir, cfg.journal_dir, cfg.journal_template)

    # -- layout ---------------------------------------------------------
    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def meta_dir(self) -> Path:
        return self.root / ".memo"

    @property
    def transcripts_dir(self) -> Path:
        return self.meta_dir / "transcripts"

    @property
    def sync_log(self) -> Path:
        return self.meta_dir / "sync.log"

    @property
    def last_sync(self) -> Path:
        return self.meta_dir / "last-sync"

    @property
    def launchd_log(self) -> Path:
        return self.meta_dir / "launchd.log"

    @property
    def claude_md(self) -> Path:
        return self.root / "CLAUDE.md"

    def ensure_dirs(self) -> None:
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)

    # -- audio ----------------------------------------------------------
    def audio_files(self) -> list[Path]:
        """Every audio file, oldest recording first."""
        if not self.audio_dir.is_dir():
            return []
        files = [
            path
            for path in self.audio_dir.rglob("*")
            if path.is_file() and path.suffix in AUDIO_SUFFIXES
        ]
        return sorted(files, key=lambda path: (recorded_at_for(path), path.name))

    def untranscribed(self) -> list[Path]:
        """Audio files that have no transcript yet, oldest first.

        Matched by block id rather than by file name: a transcript adopted
        from the shared journal before this machine had the recording is
        named after the block id, and must still count as transcribed.
        """
        known = self.transcript_ids()
        return [path for path in self.audio_files() if block_id(path.stem) not in known]

    # -- transcripts ------------------------------------------------------
    def transcript_ids(self) -> set[str]:
        """The block id of every memo this machine has a transcript for."""
        if not self.transcripts_dir.is_dir():
            return set()
        return {block_id(path.stem) for path in self.transcripts_dir.glob("*.json")}

    def transcript_path(self, stem: str) -> Path:
        return self.transcripts_dir / f"{stem}.json"

    def write_transcript(self, transcript: Transcript) -> Path:
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_path(transcript.name)
        path.write_text(
            json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def read_transcript(self, stem: str) -> Transcript | None:
        path = self.transcript_path(stem)
        if not path.is_file():
            return None
        try:
            return Transcript.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def transcripts(self) -> list[Transcript]:
        """Every transcript, oldest recording first."""
        if not self.transcripts_dir.is_dir():
            return []
        found = [self.read_transcript(path.stem) for path in sorted(self.transcripts_dir.glob("*.json"))]
        items = [item for item in found if item is not None]
        return sorted(items, key=lambda item: (item.recorded_at, item.name))

    # -- journals ---------------------------------------------------------
    def journal_path(self, date: str) -> Path:
        """The Markdown file for one ``YYYY-MM-DD``, wherever journals live."""
        return self.journal_dir / f"{date}.md"

    def journal_files(self) -> list[Path]:
        if not self.journal_dir.is_dir():
            return []
        return sorted(
            path
            for path in self.journal_dir.glob("*.md")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.stem)
        )


def recorded_at_for(path: Path) -> datetime:
    """Timestamp of a recording: from the TP-7 filename, else the file mtime."""
    parsed = parse_recorded_at(path.stem)
    if parsed is not None:
        return parsed
    return datetime.fromtimestamp(path.stat().st_mtime)


def parse_recorded_at(stem: str) -> datetime | None:
    """Parse ``YYYY-MM-DD_HHMMSS_NNN`` into a datetime, or ``None``."""
    match = NAME_RE.match(stem)
    if not match:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _parse_dt(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.fromtimestamp(0)
