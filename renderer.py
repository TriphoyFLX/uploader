"""Render static-image + audio into an MP4 video via FFmpeg."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
ASSETS_DIR = Path(__file__).parent / "assets"
DEFAULT_SHORTS_BANNER = ASSETS_DIR / "banner.svg"
SHORTS_WIDTH = 1080
SHORTS_HEIGHT = 1920


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


def _find_rsvg_convert() -> str | None:
    for candidate in (
        shutil.which("rsvg-convert"),
        "/opt/homebrew/bin/rsvg-convert",
        "/usr/bin/rsvg-convert",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def resolve_shorts_banner(banner_path: Path | None = None) -> Path | None:
    path = banner_path or DEFAULT_SHORTS_BANNER
    return path if path.is_file() else None


def rasterize_banner_svg(svg_path: Path, *, width: int = 960) -> Image.Image:
    rsvg = _find_rsvg_convert()
    if not rsvg:
        raise RuntimeError(
            "rsvg-convert not found. Install: brew install librsvg / apt install librsvg2-bin"
        )

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        output_path = Path(tmp.name)

    try:
        subprocess.run(
            [rsvg, "-w", str(width), str(svg_path), "-o", str(output_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return Image.open(output_path).convert("RGBA")
    finally:
        output_path.unlink(missing_ok=True)


def _draw_centered_text(canvas: Image.Image, text: str) -> None:
    font = _load_overlay_font()
    stroke = 4
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (canvas.width - text_w) // 2
    y = (canvas.height - text_h) // 2 - bbox[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=stroke,
        stroke_fill=(0, 0, 0, 215),
    )


def make_shorts_overlay_png(
    *,
    overlay_text: str | None = None,
    banner_path: Path | None = None,
    width: int = SHORTS_WIDTH,
    height: int = SHORTS_HEIGHT,
    banner_bottom_margin: int = 56,
) -> Path | None:
    """Full-frame transparent overlay: centered type-beat label + bottom banner."""
    banner_svg = resolve_shorts_banner(banner_path)
    if not overlay_text and not banner_svg:
        return None

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    if overlay_text:
        _draw_centered_text(canvas, overlay_text)

    if banner_svg:
        banner = rasterize_banner_svg(banner_svg, width=min(960, width - 80))
        x = (width - banner.width) // 2
        y = height - banner.height - banner_bottom_margin
        canvas.alpha_composite(banner, (x, y))

    overlay_path = Path(tempfile.mkstemp(suffix=".png")[1])
    canvas.save(overlay_path)
    return overlay_path


def make_text_overlay_png(text: str, *, width: int = 1080) -> Path:
    """Backward-compatible helper for centered label only."""
    overlay = make_shorts_overlay_png(overlay_text=text, width=width, height=SHORTS_HEIGHT)
    if overlay is None:
        raise RuntimeError("Failed to build shorts overlay")
    return overlay


def _render_shorts_with_overlay(
    *,
    video_input_args: list[str],
    audio_path: Path,
    output_path: Path,
    duration: int,
    overlay_path: Path,
    base_video_filter: str,
) -> None:
    vf = (
        f"[0:v]{base_video_filter}[base];"
        "[2:v]format=rgba,fps=30[ovl];"
        "[base][ovl]overlay=0:0[v]"
    )
    cmd = [
        "ffmpeg", "-y",
        *video_input_args,
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
    subprocess.run(cmd, check=True, capture_output=True, text=True)


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
    banner_path: Path | None = None,
) -> Path:
    """Vertical 9:16 Shorts clip (square cover centered, trimmed audio)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg not found. Install: brew install ffmpeg")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    banner_svg = resolve_shorts_banner(banner_path)
    overlay_path = make_shorts_overlay_png(banner_path=banner_svg) if banner_svg else None

    if overlay_path:
        cover_filter = (
            "crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2,"
            "scale=1080:1080,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
            "fps=30"
        )
        try:
            _render_shorts_with_overlay(
                video_input_args=["-loop", "1", "-i", str(image_path)],
                audio_path=audio_path,
                output_path=output_path,
                duration=duration,
                overlay_path=overlay_path,
                base_video_filter=cover_filter,
            )
        finally:
            overlay_path.unlink(missing_ok=True)
        return output_path

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
    banner_path: Path | None = None,
) -> Path:
    """Vertical 9:16 clip: mute visual, loop if shorter than duration, overlay beat."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg not found. Install: brew install ffmpeg")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    banner_svg = resolve_shorts_banner(banner_path)
    overlay_path = make_shorts_overlay_png(
        overlay_text=overlay_text,
        banner_path=banner_svg,
    ) if (overlay_text or banner_svg) else None

    if overlay_path:
        visual_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30"
        )
        try:
            _render_shorts_with_overlay(
                video_input_args=["-stream_loop", "-1", "-i", str(visual_path)],
                audio_path=audio_path,
                output_path=output_path,
                duration=duration,
                overlay_path=overlay_path,
                base_video_filter=visual_filter,
            )
        finally:
            overlay_path.unlink(missing_ok=True)
        return output_path

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

    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output_path
