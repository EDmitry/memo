"""Local transcription: ffmpeg to 16 kHz mono, then mlx-whisper."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from .store import Transcript, recorded_at_for


class TranscribeError(Exception):
    """Conversion or transcription failed."""


class Transcriber:
    """Holds the model choice; loads the model on first use, once per process."""

    def __init__(
        self,
        model: str,
        language: str = "",
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
    ) -> None:
        self.model = model
        self.language = language or ""
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def transcribe(self, wav_path: Path, duration_s: float | None = None) -> Transcript:
        wav_path = Path(wav_path)
        # Imported here so `memo show`/`ls`/`status` never pay for mlx.
        import mlx_whisper

        duration = self.duration(wav_path) if duration_s is None else duration_s
        with tempfile.TemporaryDirectory(prefix="memo-") as tmp:
            prepared = Path(tmp) / "audio.wav"
            self._to_16k_mono(wav_path, prepared)
            options = {"path_or_hf_repo": self.model}
            if self.language:
                options["language"] = self.language
            try:
                result = mlx_whisper.transcribe(str(prepared), **options)
            except Exception as error:  # noqa: BLE001 - surfaced as a memo error
                raise TranscribeError(f"{wav_path.name}: {error}") from error

        return Transcript(
            name=wav_path.stem,
            recorded_at=recorded_at_for(wav_path),
            duration_s=duration,
            language=str(result.get("language") or self.language),
            model=self.model,
            text=str(result.get("text") or "").strip(),
            segments=[
                {
                    "start": float(segment.get("start", 0.0)),
                    "end": float(segment.get("end", 0.0)),
                    "text": str(segment.get("text", "")).strip(),
                }
                for segment in result.get("segments") or []
            ],
            transcribed_at=datetime.now(),
        )

    def duration(self, path: Path) -> float:
        """Length of the original file in seconds; 0.0 if ffprobe cannot tell."""
        command = [
            self.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as error:
            raise TranscribeError(f"{self.ffprobe}: {error}") from error
        try:
            return float(result.stdout.strip())
        except ValueError:
            return 0.0

    def _to_16k_mono(self, source: Path, destination: Path) -> None:
        command = [
            self.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as error:
            raise TranscribeError(f"{self.ffmpeg}: {error}") from error
        if result.returncode != 0 or not destination.exists():
            detail = (result.stderr or "").strip().splitlines()
            raise TranscribeError(
                f"{source.name}: ffmpeg failed" + (f": {detail[-1]}" if detail else "")
            )


def missing_tools(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> list[str]:
    """Which of the external tools are not on PATH."""
    return [tool for tool in (ffmpeg, ffprobe) if shutil.which(tool) is None]
