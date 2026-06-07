"""Media extraction helpers for OBS recordings."""

from __future__ import annotations

import shutil
import subprocess
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaArtifacts:
    wav_path: Path | None
    frame_paths: list[Path]
    duration_s: int
    work_dir: Path


def _binary(name: str) -> str | None:
    ffmpeg_dir = os.environ.get("JARVIS_FFMPEG_DIR", "").strip()
    if ffmpeg_dir:
        candidate = Path(ffmpeg_dir) / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


def require_ffmpeg() -> tuple[str, str]:
    ffmpeg = _binary("ffmpeg")
    ffprobe = _binary("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError(
            "ffmpeg/ffprobe no estan en PATH. Instala con: winget install Gyan.FFmpeg"
        )
    return ffmpeg, ffprobe


def video_duration_s(video_path: Path) -> int:
    _ffmpeg, ffprobe = require_ffmpeg()
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return max(0, int(float((result.stdout or "0").strip() or "0")))


def extract_audio(video_path: Path, wav_path: Path) -> Path:
    ffmpeg, _ffprobe = require_ffmpeg()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(wav_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return wav_path


def extract_audio_segment(video_path: Path, wav_path: Path, *, start_s: int, length_s: int) -> Path:
    ffmpeg, _ffprobe = require_ffmpeg()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-ss",
            str(start_s),
            "-i",
            str(video_path),
            "-t",
            str(length_s),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(wav_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return wav_path


def extract_keyframes(
    video_path: Path,
    frames_dir: Path,
    *,
    count: int,
    duration_s: int | None = None,
) -> list[Path]:
    ffmpeg, _ffprobe = require_ffmpeg()
    if count <= 0:
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    duration = duration_s if duration_s is not None else video_duration_s(video_path)
    if duration <= 0:
        return []
    step = max(1, duration // (count + 1))
    frames: list[Path] = []
    for idx in range(count):
        ts = min(duration, (idx + 1) * step)
        out = frames_dir / f"frame_{idx + 1:02d}_{ts:06d}s.png"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                str(ts),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=1600:-1",
                str(out),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def extract_keyframes_segment(
    video_path: Path,
    frames_dir: Path,
    *,
    start_s: int,
    length_s: int,
    count: int,
) -> list[Path]:
    ffmpeg, _ffprobe = require_ffmpeg()
    if count <= 0 or length_s <= 0:
        return []
    frames_dir.mkdir(parents=True, exist_ok=True)
    step = max(1, length_s // count)
    frames: list[Path] = []
    for idx in range(count):
        ts = start_s + min(length_s - 1, idx * step)
        out = frames_dir / f"frame_{idx + 1:02d}_{ts:06d}s.png"
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-ss",
                str(ts),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=1600:-1",
                str(out),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def extract_artifacts(video_path: Path, work_dir: Path, keyframes: int) -> MediaArtifacts:
    duration = video_duration_s(video_path)
    wav_path = extract_audio(video_path, work_dir / "audio.wav")
    frame_paths = extract_keyframes(
        video_path,
        work_dir / "frames",
        count=keyframes,
        duration_s=duration,
    )
    return MediaArtifacts(
        wav_path=wav_path,
        frame_paths=frame_paths,
        duration_s=duration,
        work_dir=work_dir,
    )
