"""Configuration: ``~/.config/memo/config.toml`` plus ``MEMO_*`` env overrides."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

APP_NAME = "memo"

DEFAULT_DIR = "~/Memos"
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
DEFAULT_REMOTE_DIRS = ("/recordings", "/memo")
DEFAULT_MAX_MINUTES = 30.0
DEFAULT_MAX_PULL_MB = 512
DEFAULT_TP7 = "tp7"


class ConfigError(Exception):
    """Raised when the config file is unreadable or holds a bad value."""


@dataclass(frozen=True)
class Config:
    dir: Path
    model: str = DEFAULT_MODEL
    language: str = ""
    remote_dirs: tuple[str, ...] = DEFAULT_REMOTE_DIRS
    max_minutes: float = DEFAULT_MAX_MINUTES
    max_pull_mb: int = DEFAULT_MAX_PULL_MB
    tp7: str = DEFAULT_TP7
    source: Path | None = None

    @property
    def max_pull_size(self) -> str:
        """``--max-size`` argument for ``tp7 pull``."""
        return f"{self.max_pull_mb}M"


def config_path() -> Path:
    override = os.environ.get("MEMO_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / APP_NAME / "config.toml"


def load(path: Path | None = None) -> Config:
    """Load config from disk, then apply ``MEMO_*`` environment overrides."""
    path = path or config_path()
    data: dict[str, object] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ConfigError(f"{path}: {error}") from error

    cfg = _from_mapping(data, source=path if path.is_file() else None)
    return _apply_env(cfg, os.environ)


def _from_mapping(data: dict[str, object], source: Path | None) -> Config:
    return Config(
        dir=_path(data.get("dir", DEFAULT_DIR), "dir"),
        model=_str(data.get("model", DEFAULT_MODEL), "model"),
        language=_str(data.get("language", ""), "language"),
        remote_dirs=_str_tuple(data.get("remote_dirs", DEFAULT_REMOTE_DIRS), "remote_dirs"),
        max_minutes=_float(data.get("max_minutes", DEFAULT_MAX_MINUTES), "max_minutes"),
        max_pull_mb=_int(data.get("max_pull_mb", DEFAULT_MAX_PULL_MB), "max_pull_mb"),
        tp7=_str(data.get("tp7", DEFAULT_TP7), "tp7"),
        source=source,
    )


def _apply_env(cfg: Config, env: dict[str, str] | os._Environ[str]) -> Config:
    changes: dict[str, object] = {}
    if value := env.get("MEMO_DIR"):
        changes["dir"] = _path(value, "MEMO_DIR")
    if value := env.get("MEMO_MODEL"):
        changes["model"] = value
    if (value := env.get("MEMO_LANGUAGE")) is not None:
        changes["language"] = value
    if value := env.get("MEMO_REMOTE_DIRS"):
        changes["remote_dirs"] = tuple(part.strip() for part in value.split(",") if part.strip())
    if value := env.get("MEMO_MAX_MINUTES"):
        changes["max_minutes"] = _float(value, "MEMO_MAX_MINUTES")
    if value := env.get("MEMO_MAX_PULL_MB"):
        changes["max_pull_mb"] = _int(value, "MEMO_MAX_PULL_MB")
    if value := env.get("MEMO_TP7"):
        changes["tp7"] = value
    return replace(cfg, **changes) if changes else cfg


def _str(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key}: expected a string, got {type(value).__name__}")
    return value


def _path(value: object, key: str) -> Path:
    return Path(_str(value, key)).expanduser()


def _str_tuple(value: object, key: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ConfigError(f"{key}: expected a list of strings")


def _float(value: object, key: str) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{key}: expected a number") from error


def _int(value: object, key: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{key}: expected an integer") from error
