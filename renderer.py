"""Render static-image + audio into an MP4 video via FFmpeg."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def find_images(image_dir: Path) -> list[Path]:
    if not image_dir.is_dir():
        return []
    return sorted(
        f for f in image_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )


def find_image(image_dir: Path) -> Path | None:
    files = find_images(image_dir)
    return files[0] if files else None


def get_audio_duration(audio_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(result.stdout.strip())


def render_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    resolution: str = "1920x1080",
) -> Path:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg not found. Install: brew install ffmpeg")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Center-crop to square → scale to 1080×1080 → pad to 1920×1080 (centered)
    # Commas inside crop expressions must be escaped for ffmpeg's filter parser
    vf = (
        "crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,"
        "scale=1080:1080,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-shortest",
        str(output_path),
    ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output_path


def render_shorts(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    duration: int = 45,
    start: int = 0,
) -> Path:
    """Vertical 9:16 Shorts clip (square cover centered, trimmed audio)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg not found. Install: brew install ffmpeg")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    vf = (
        "crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,"
        "scale=1080:1080,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-i", str(audio_path),
        "-t", str(duration),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-vf", vf,
        "-shortest",
        str(output_path),
    ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output_path
