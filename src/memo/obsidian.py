"""Obsidian vaults and the ``obsidian://`` URL that opens a note.

A vault is just a directory with a `.obsidian/` in it, so pointing
``journal_dir`` at a folder inside one is all it takes to have the daily
journals show up in Obsidian. memo only needs to recognise that situation so
`memo open` hands the file to Obsidian instead of `$EDITOR`.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def vault_root(path: Path) -> Path | None:
    """The Obsidian vault containing ``path``, or ``None`` if it is not in one."""
    for candidate in (path, *path.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def uri(path: Path) -> str:
    """The ``obsidian://`` URL that opens ``path`` in Obsidian."""
    return "obsidian://open?path=" + quote(str(path), safe="")
