"""The ``memos:`` frontmatter property: which recordings a day file already holds.

The journal is the one file two machines are guaranteed to share, so it has to
say which recordings it covers. Obsidian keeps that kind of metadata in YAML
frontmatter and shows it in the note's properties panel, so a day file lists
the audio stems of its entries::

    ---
    created: 2026-09-12 13:17
    modified: 2026-09-12 13:17
    subjects:
    memos:
      - 2026-09-12_131726_000
      - 2026-09-12_131837_000
    ---

memo only ever appends one item to that list. Every other property, its
formatting, and the whole body are preserved byte for byte — which is why this
is a line-based reader and not a YAML round-trip: no YAML library gives a
hand-written file back unchanged, and Obsidian's frontmatter is not general
YAML anyway. Reading is deliberately generous (the block form above, the inline
``memos: [a, b]`` Obsidian also accepts, a bare scalar, quoted items); writing
always produces the block form.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: The frontmatter key memo owns. Everything else in the block belongs to the
#: user and to Obsidian's plugins.
KEY = "memos"

FENCE = "---"
INDENT = "  "

_KEY_RE = re.compile(rf"^{KEY}:[ \t]*(?P<rest>.*?)[ \t]*$")
_ITEM_RE = re.compile(r"^(?P<indent>[ \t]*)-[ \t]*(?P<value>.*?)[ \t]*$")


class _Property(NamedTuple):
    """Where the ``memos:`` property sits in a file's lines, and what it lists."""

    key: int  #: index of the ``memos:`` line
    end: int  #: index one past the last line the property occupies
    items: list[str]
    inline: bool  #: written as ``memos: [a, b]`` rather than as a block


def read(text: str) -> list[str]:
    """The recording names listed in ``text``'s ``memos:`` property, in order."""
    lines = text.split("\n")
    bounds = _bounds(lines)
    if bounds is None:
        return []
    found = _find(lines, bounds)
    return list(found.items) if found else []


def add(text: str, name: str) -> str:
    """``text`` with ``name`` appended to its ``memos:`` list.

    The list is created — and, if the file has no frontmatter at all, so is the
    frontmatter — but nothing that was already there is rewritten. Adding a
    name the list already has changes nothing.
    """
    lines = text.split("\n")
    bounds = _bounds(lines)
    if bounds is None:
        return "\n".join([FENCE, f"{KEY}:", f"{INDENT}- {name}", FENCE, ""]) + text

    _start, close = bounds
    found = _find(lines, bounds)
    if found is None:
        # No ledger yet: start one just above the closing fence.
        lines[close:close] = [f"{KEY}:", f"{INDENT}- {name}"]
    elif name in found.items:
        return text
    elif found.inline:
        # Reading accepts the inline form; writing only ever emits the block one.
        lines[found.key : found.end] = _block(found.items + [name], INDENT)
    else:
        lines.insert(found.end, f"{_indent_of(lines, found)}- {name}")
    return "\n".join(lines)


def _bounds(lines: list[str]) -> tuple[int, int] | None:
    """The indices of the opening and closing fence of the frontmatter, if any."""
    if not lines or lines[0].strip() != FENCE:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() in (FENCE, "..."):
            return 0, index
    return None  # an unterminated fence is not frontmatter


def _find(lines: list[str], bounds: tuple[int, int]) -> _Property | None:
    """The ``memos:`` property inside the frontmatter, or ``None``."""
    start, close = bounds
    for index in range(start + 1, close):
        match = _KEY_RE.match(lines[index])
        if match is None:
            continue
        rest = match.group("rest")
        if rest:
            return _Property(index, index + 1, _parse_inline(rest), inline=True)
        end, items = index + 1, []
        while end < close and (item := _ITEM_RE.match(lines[end])) is not None:
            if value := _unquote(item.group("value")):
                items.append(value)
            end += 1
        return _Property(index, end, items, inline=False)
    return None


def _parse_inline(rest: str) -> list[str]:
    """``[a, b]``, or a bare scalar, as a list of names."""
    if rest.startswith("[") and rest.endswith("]"):
        rest = rest[1:-1]
    return [name for part in rest.split(",") if (name := _unquote(part.strip()))]


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _block(items: list[str], indent: str) -> list[str]:
    return [f"{KEY}:", *(f"{indent}- {item}" for item in items)]


def _indent_of(lines: list[str], found: _Property) -> str:
    """Indent the new item the way the list already indents its items."""
    if found.end > found.key + 1 and (last := _ITEM_RE.match(lines[found.end - 1])):
        return last.group("indent")
    return INDENT
