"""Render static-image + audio into an MP4 video via FFmpeg."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}


def find_images(image_dir: Path) -> list[Path]:
    if not image_dir.is_dir():
        return []
    return sorted(
        f for f in image_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    )


def find_visuals(visuals_dir: Path) -> list[Path]:
    if not visuals_dir.is_dir():
        return []
    return sorted(
        f for f in visuals_dir.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )


def find_image(image_dir: Path) -> Path | None:
    files = find_images(image_dir)
    return files[0] if files else None


def shorts_overlay_text(artist_slug: str, *, display_name: str | None = None) -> str:
    """Label for Shorts visual overlay, e.g. 'osamason type beat'."""
    if "+" in artist_slug:
        name = artist_slug.split("+", 1)[0].strip()
    elif display_name:
        name = display_name.strip()
    else:
        name = artist_slug.replace("_", " ").strip()
    return f"{name.lower()} type beat"


def _drawtext_font() -> str | None:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ):
        if Path(path).is_file():
            return path
    return None


def _load_overlay_font(size: int = 56) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_path = _drawtext_font()
    if font_path:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def make_text_overlay_png(text: str, *, width: int = 1080) -> Path:
    """Transparent PNG with centered label for Shorts overlay."""
    font = _load_overlay_font()
    stroke = 4
    pad_y = 24

    measure = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(measure)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    img = Image.new("RGBA", (width, text_h + pad_y * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    x = (width - text_w) // 2
    y = pad_y - bbox[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, 215),
    )

    overlay_path = Path(tempfile.mkstemp(suffix=".png")[1])
    img.save(overlay_path)
    return overlay_path


def pick_visual(visuals_dir: Path, *, seed: str = "") -> Path | None:
    """Pick a visual (reusable — same file can be used for many beats)."""
    files = find_visuals(visuals_dir)
    if not files:
        return None
    if len(files) == 1:
        return files[0]
    return files[hash(seed) % len(files)]


def find_visual(visuals_dir: Path) -> Path | None:
    return pick_visual(visuals_dir)


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
    duration: int = 25,
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


def render_shorts_visual(
    visual_path: Path,
    audio_path: Path,
    output_path: Path,
    *,
    duration: int = 25,
    overlay_text: str | None = None,
) -> Path:
    """Vertical 9:16 clip: mute visual, loop if shorter than duration, overlay beat."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg not found. Install: brew install ffmpeg")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    overlay_path: Path | None = None
    if overlay_text:
        overlay_path = make_text_overlay_png(overlay_text)
        vf = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30[base];"
            "[2:v]format=rgba,fps=30[ovl];"
            "[base][ovl]overlay=(W-w)/2:(H-h)/2[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(visual_path),
            "-i", str(audio_path),
            "-loop", "1",
            "-i", str(overlay_path),
            "-filter_complex", vf,
            "-map", "[v]",
            "-map", "1:a:0",
            "-t", str(duration),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]
    else:
        vf = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30[v]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(visual_path),
            "-i", str(audio_path),
            "-filter_complex", vf,
            "-map", "[v]",
            "-map", "1:a:0",
            "-t", str(duration),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(output_path),
        ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    finally:
        if overlay_path:
            overlay_path.unlink(missing_ok=True)
    return output_path
