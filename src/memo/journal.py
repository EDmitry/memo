"""Daily Markdown journals rendered from transcripts.

Transcripts are the durable record; a journal file is a view that
:func:`rebuild` can always regenerate. Appending one entry produces exactly the
text a full render of that day would.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .store import Store, Transcript

CLAUDE_MD = """\
# Voice memo journal

This directory is a journal of voice memos recorded on a Teenage Engineering
TP-7 and transcribed locally with Whisper by the `memo` CLI.

## Layout

- `YYYY-MM-DD.md` — one file per day, one `##` section per memo, headed
  `## HH:MM · M:SS` (time of recording, duration). This is the thing to read.
- `audio/` — the original recordings, never modified.
- `.memo/transcripts/*.json` — per-memo transcripts (text, segments, model,
  timestamps). The durable record; the daily files are rendered from these.
- `.memo/sync.log`, `.memo/last-sync` — bookkeeping for automatic syncs.

## Reading these

Entries are spoken thoughts, usually short and unedited: notes to self, ideas,
reminders, half-formed reasoning. They are machine transcriptions, so expect
mishearings — names, jargon, and acronyms are the usual casualties — and
punctuation the speaker never intended. Read for intent rather than literally,
and say so when a passage looks like a transcription error rather than
inferring something confident from it.
"""


def entry(transcript: Transcript) -> str:
    """One journal section, ending in a single newline."""
    text = " ".join(transcript.text.split()) or "_(no speech detected)_"
    return f"## {transcript.time} · {transcript.duration}\n\n{text}\n"


def render_day(date: str, transcripts: list[Transcript]) -> str:
    """The full Markdown file for one day."""
    body = "\n".join(entry(item) for item in sorted(transcripts, key=_order))
    return f"# {date}\n\n{body}"


def append_entry(store: Store, transcript: Transcript) -> Path:
    """Append one memo to its day file, creating the file if it is new."""
    path = store.journal_path(transcript.date)
    block = entry(transcript)
    if path.exists():
        existing = path.read_text(encoding="utf-8").rstrip("\n")
        content = f"{existing}\n\n{block}" if existing else f"# {transcript.date}\n\n{block}"
    else:
        content = f"# {transcript.date}\n\n{block}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def rebuild(store: Store) -> list[Path]:
    """Rewrite every day file from the transcripts. Returns the files written."""
    by_day: dict[str, list[Transcript]] = defaultdict(list)
    for transcript in store.transcripts():
        by_day[transcript.date].append(transcript)

    written: list[Path] = []
    for date in sorted(by_day):
        path = store.journal_path(date)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_day(date, by_day[date]), encoding="utf-8")
        written.append(path)
    return written


def ensure_claude_md(store: Store) -> bool:
    """Write ``CLAUDE.md`` if absent so ``cd <memo dir> && claude`` has context."""
    path = store.claude_md
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CLAUDE_MD, encoding="utf-8")
    return True


def _order(transcript: Transcript) -> tuple:
    return (transcript.recorded_at, transcript.name)
