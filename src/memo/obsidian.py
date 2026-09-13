"""Obsidian: block ids that identify a memo, vaults, and the ``obsidian://`` URL.

A vault is just a directory with a `.obsidian/` in it, so pointing
``journal_dir`` at a folder inside one is all it takes to have the daily
journals show up in Obsidian. memo only needs to recognise that situation so
`memo open` hands the file to Obsidian instead of `$EDITOR`.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

#: Obsidian block ids allow only alphanumerics and dashes.
_NOT_IN_BLOCK_ID = re.compile(r"[^A-Za-z0-9-]")

BLOCK_ID_PREFIX = "memo-"


def block_id(name: str) -> str:
    """The Obsidian block id that identifies the memo ``name`` in a journal.

    The journal is the only file two machines are guaranteed to share, so it
    has to carry each memo's identity: ``^memo-2026-09-12-170312-000`` under an
    entry says which recording it came from, and makes the memo linkable as
    ``[[2026-09-12#^memo-2026-09-12-170312-000]]``.
    """
    return BLOCK_ID_PREFIX + _NOT_IN_BLOCK_ID.sub("-", name)


def name_from_block_id(value: str) -> str:
    """The best guess at a memo name for a block id with no local audio."""
    return value.removeprefix(BLOCK_ID_PREFIX)


def vault_root(path: Path) -> Path | None:
    """The Obsidian vault containing ``path``, or ``None`` if it is not in one."""
    for candidate in (path, *path.parents):
        if (candidate / ".obsidian").is_dir():
            return candidate
    return None


def uri(path: Path) -> str:
    """The ``obsidian://`` URL that opens ``path`` in Obsidian."""
    return "obsidian://open?path=" + quote(str(path), safe="")
