"""Main pipeline: scan artists → analyze → render → upload."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from analyzer import analyze_beat, find_audio_files, format_key, format_producers
from renderer import (
    find_images,
    find_visuals,
    pick_visual,
    render_shorts,
    render_shorts_visual,
    render_video,
    shorts_overlay_text,
)

ROOT = Path(__file__).parent
ARTISTS_DIR = ROOT / "artists"
OUTPUT_DIR = ROOT / "output"
CREDENTIALS_DIR = ROOT / "credentials"
UPLOAD_LOG = ROOT / "uploaded.json"
SCHEDULE_CONFIG = ROOT / "schedule_config.json"


def load_artist_config(artist_dir: Path) -> dict:
    config_path = artist_dir / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        config = {}

    config.setdefault("artist", artist_dir.name)
    config.setdefault("producer", "triphoy")
    config.setdefault("tags", [f"{artist_dir.name} type beat"])
    config.setdefault("title_template", '(free) {artist} type beat "{title}"')
    config.setdefault("privacy", "public")
    config.setdefault("playlist_id", None)
    config.setdefault("category_id", "10")
    config.setdefault("purchase_link", "instagram.com/triphoy_prod")
    config.setdefault("sounds_link", "instagram.com/triphoy_prod")
    config.setdefault("hashtags", f"#{artist_dir.name}typebeat #{artist_dir.name} #typebeat")
    return config


def load_shorts_settings(config: dict) -> dict:
    global_shorts = {}
    global_social = {}
    if SCHEDULE_CONFIG.exists():
        data = json.loads(SCHEDULE_CONFIG.read_text(encoding="utf-8"))
        global_shorts = data.get("shorts", {})
        global_social = data.get("social", {})

    return {
        "enabled": config.get("shorts_enabled", global_shorts.get("enabled", True)),
        "interval_hours": config.get("shorts_interval_hours", global_shorts.get("interval_hours", 2)),
        "delay_minutes": config.get("shorts_delay_minutes", global_shorts.get("delay_minutes", 3)),
        "duration_seconds": config.get("shorts_duration_seconds", global_shorts.get("duration_seconds", 25)),
        "use_visuals": config.get("shorts_use_visuals", global_shorts.get("use_visuals", True)),
        "alternate_visual_cover": config.get(
            "shorts_alternate_visual_cover",
            global_shorts.get("alternate_visual_cover", True),
        ),
        "tiktok": config.get("tiktok_enabled", global_social.get("tiktok", False)),
        "reels": config.get("reels_enabled", global_social.get("reels", False)),
    }


def load_titles(artist_dir: Path, config: dict) -> list[str]:
    titles_file = artist_dir / config.get("titles_file", "titles.txt")
    if titles_file.exists():
        lines = titles_file.read_text(encoding="utf-8").splitlines()
        titles = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
        if titles:
            return titles

    return config.get("titles", [])


def load_tags_line(artist_dir: Path, config: dict) -> str:
    tags_file = artist_dir / config.get("tags_file", "tags.txt")
    if tags_file.exists():
        return tags_file.read_text(encoding="utf-8").strip()

    if "tags_description" in config:
        return config["tags_description"]

    return ", ".join(config.get("tags", []))


def load_description(artist_dir: Path) -> str:
    for name in ("description.txt", "description_name.txt"):
        path = artist_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8")

    config = load_artist_config(artist_dir)
    return config.get("default_description", "Produced by {producer}\n\nBPM - {bpm}\nKEY - {key}")


def title_case(name: str) -> str:
    return " ".join(word.capitalize() for word in re.split(r"[\s_-]+", name))


def fill_template(
    template: str,
    metadata,
    config: dict,
    tags_line: str,
    *,
    extra: dict[str, str] | None = None,
) -> str:
    artist = config.get("artist", metadata.artist)
    title = metadata.title

    producer = format_producers(metadata.producers, config.get("producer", "triphoy"))

    replacements = {
        "{artist}": artist,
        "{artist_cap}": title_case(artist),
        "{beat_name}": metadata.beat_name,
        "{beat_name_cap}": title_case(metadata.beat_name),
        "{title}": title,
        "{title_cap}": title_case(title),
        "{bpm}": str(metadata.bpm) if metadata.bpm else "N/A",
        "{key}": format_key(metadata.key),
        "{producer}": producer,
        "{producers}": producer,
        "{purchase_link}": config.get("purchase_link", ""),
        "{sounds_link}": config.get("sounds_link", ""),
        "{tags_line}": tags_line,
        "{hashtags}": config.get("hashtags", ""),
        "{shorts_hashtags}": config.get("shorts_hashtags", "#typebeat #shorts"),
        "{full_video_url}": "",
    }
    if extra:
        replacements.update(extra)

    result = template
    for key, value in replacements.items():
        result = result.replace(key, value)
    return result


def upload_key(artist: str, audio_path: Path, title: str) -> str:
    return f"{artist}:{audio_path.name}:{title}"


def is_already_uploaded(artist: str, audio_path: Path, title: str) -> bool:
    if not UPLOAD_LOG.exists():
        return False
    entries = json.loads(UPLOAD_LOG.read_text())
    key = upload_key(artist, audio_path, title)
    return any(e.get("key") == key for e in entries)


def save_titles(artist_dir: Path, config: dict, titles: list[str]) -> None:
    titles_file = artist_dir / config.get("titles_file", "titles.txt")
    header = []
    if titles_file.exists():
        for line in titles_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("#"):
                header.append(line)
            elif not line.strip():
                header.append("")
    body = "\n".join(titles)
    content = "\n".join(header).rstrip()
    if content and body:
        content += "\n" + body
    elif body:
        content = body
    titles_file.write_text(content + ("\n" if content else ""), encoding="utf-8")


def archive_title(artist_dir: Path, config: dict, title: str, *, video_id: str | None = None) -> None:
    archive_file = artist_dir / config.get("archive_titles_file", "archive_titles.txt")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"{now} | {title}"
    if video_id:
        line += f" | https://youtu.be/{video_id}"

    existing = ""
    if archive_file.exists():
        existing = archive_file.read_text(encoding="utf-8").rstrip() + "\n"
    archive_file.write_text(existing + line + "\n", encoding="utf-8")


def remove_first_title(artist_dir: Path, config: dict, title: str) -> None:
    titles = load_titles(artist_dir, config)
    if titles and titles[0] == title:
        save_titles(artist_dir, config, titles[1:])
    elif title in titles:
        save_titles(artist_dir, config, [t for t in titles if t != title])


def cleanup_after_publish(
    audio_path: Path,
    image_path: Path,
    video_path: Path | None = None,
    visual_path: Path | None = None,
) -> None:
    if audio_path.exists():
        audio_path.unlink()
        print(f"    Deleted beat: {audio_path.name}")
    if image_path.exists():
        image_path.unlink()
        print(f"    Deleted image: {image_path.name}")
    if visual_path and visual_path.exists():
        print(f"    Kept visual: {visual_path.name} (reusable)")
    if video_path and video_path.exists():
        video_path.unlink()
        print(f"    Deleted video: {video_path.name}")


def find_full_video_url(artist: str, audio_path: Path) -> str:
    if not UPLOAD_LOG.exists():
        return ""
    for entry in reversed(json.loads(UPLOAD_LOG.read_text())):
        if entry.get("type") in ("shorts", "shorts_interval"):
            continue
        if entry.get("artist") == artist and entry.get("audio_file") == audio_path.name:
            video_id = entry.get("video_id")
            if video_id:
                return f"https://youtu.be/{video_id}"
    return ""


def render_shorts_clip(
    *,
    artist: str,
    audio_path: Path,
    image_path: Path,
    output_path: Path,
    config: dict,
    shorts: dict,
    visual_path: Path | None = None,
    use_visual: bool = False,
) -> str:
    """Render Shorts clip. Returns mode used: 'visual' or 'cover'."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if use_visual and visual_path:
        print(f"    Rendering Shorts (visual) → {output_path.name}")
        render_shorts_visual(
            visual_path,
            audio_path,
            output_path,
            duration=shorts["duration_seconds"],
            overlay_text=shorts_overlay_text(
                artist,
                display_name=config.get("artist"),
            ),
        )
        return "visual"

    print(f"    Rendering Shorts (cover) → {output_path.name}")
    render_shorts(
        image_path,
        audio_path,
        output_path,
        duration=shorts["duration_seconds"],
    )
    return "cover"


def upload_shorts_clip(
    *,
    artist_dir: Path,
    audio_path: Path,
    image_path: Path | None,
    title: str,
    metadata,
    config: dict,
    tags_line: str,
    api_tags: list[str],
    shorts: dict,
    service,
    full_video_url: str = "",
    visual_path: Path | None = None,
    use_visual: bool = False,
    log_type: str = "shorts",
    log_key_suffix: str = "shorts",
    full_video_id: str | None = None,
) -> dict | None:
    artist = artist_dir.name
    safe_title = re.sub(r'[^\w\-]', '_', title)
    shorts_path = OUTPUT_DIR / artist / f"{safe_title}_{audio_path.stem}_{log_key_suffix}.mp4"

    if not use_visual and not image_path:
        raise ValueError("Cover Shorts require image_path")

    mode = render_shorts_clip(
        artist=artist,
        audio_path=audio_path,
        image_path=image_path,
        output_path=shorts_path,
        config=config,
        shorts=shorts,
        visual_path=visual_path,
        use_visual=use_visual,
    )

    if full_video_url:
        description_template = config.get(
            "shorts_description",
            "{artist_cap} Type Beat | Check Full Beat On My Channel\n\n{full_video_url}\n\n{shorts_hashtags}",
        )
    else:
        description_template = config.get(
            "shorts_interval_description",
            "{artist_cap} Type Beat \"{title}\"\n\n{shorts_hashtags}",
        )

    shorts_title = fill_template(
        config.get("shorts_title_template", '{artist_cap} Type Beat "{title}" #Shorts'),
        metadata, config, tags_line,
    )
    shorts_description = fill_template(
        description_template,
        metadata, config, tags_line,
        extra={"{full_video_url}": full_video_url},
    )
    shorts_tags = config.get("shorts_tags", api_tags[:3] + ["shorts"])

    print(f"    Shorts title: {shorts_title}")
    from youtube import save_upload_log, upload_video

    shorts_video_id = upload_video(
        service,
        shorts_path,
        title=shorts_title,
        description=shorts_description,
        tags=shorts_tags,
        category_id=config.get("category_id", "10"),
        privacy=config.get("privacy", "public"),
    )

    save_upload_log(UPLOAD_LOG, {
        "key": f"{upload_key(artist, audio_path, title)}:{log_key_suffix}",
        "artist": artist,
        "title": title,
        "audio_file": audio_path.name,
        "video_id": shorts_video_id,
        "youtube_title": shorts_title,
        "type": log_type,
        "mode": mode,
        "full_video_id": full_video_id,
        "visual_file": visual_path.name if mode == "visual" and visual_path else None,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    })

    if full_video_url:
        social_caption = fill_template(
            config.get(
                "social_caption",
                "{artist_cap} Type Beat \"{title}\" | Full beat on YouTube\n\n{full_video_url}\n\n{shorts_hashtags}",
            ),
            metadata, config, tags_line,
            extra={"{full_video_url}": full_video_url},
        )
    else:
        social_caption = fill_template(
            config.get(
                "social_interval_caption",
                "{artist_cap} Type Beat \"{title}\"\n\n{shorts_hashtags}",
            ),
            metadata, config, tags_line,
        )

    if shorts.get("tiktok"):
        try:
            from tiktok import is_configured as tiktok_ready, upload_video as tiktok_upload
            if tiktok_ready(CREDENTIALS_DIR):
                print("    Uploading TikTok...")
                tiktok_upload(shorts_path, caption=social_caption, credentials_dir=CREDENTIALS_DIR)
            else:
                print("    TikTok skipped (no credentials/tiktok.json)")
        except Exception as exc:
            print(f"    TikTok error: {exc}")

    if shorts.get("reels"):
        try:
            from reels import is_configured as reels_ready, upload_reel
            if reels_ready(CREDENTIALS_DIR):
                print("    Uploading Instagram Reels...")
                upload_reel(shorts_path, caption=social_caption, credentials_dir=CREDENTIALS_DIR)
            else:
                print("    Reels skipped (no credentials/instagram.json)")
        except Exception as exc:
            print(f"    Reels error: {exc}")

    if shorts_path.exists():
        shorts_path.unlink()

    return {
        "video_id": shorts_video_id,
        "title": shorts_title,
        "mode": mode,
    }


def artist_can_shorts(artist_name: str, mode: str) -> bool:
    artist_dir = ARTISTS_DIR / artist_name
    if not artist_dir.is_dir():
        return False
    config = load_artist_config(artist_dir)
    has_beats = bool(find_audio_files(artist_dir / "beats"))
    has_titles = bool(load_titles(artist_dir, config))
    if not (has_beats and has_titles):
        return False
    if mode == "visual":
        return bool(find_visuals(artist_dir / "visuals"))
    return bool(find_images(artist_dir / "image"))


def list_shorts_artists() -> list[str]:
    if not ARTISTS_DIR.is_dir():
        return []
    artists = sorted(d.name for d in ARTISTS_DIR.iterdir() if d.is_dir())
    return [
        name for name in artists
        if artist_can_shorts(name, "visual") or artist_can_shorts(name, "cover")
    ]


def pick_interval_shorts_target(
    *,
    artist_index: int,
    publish_count: int,
    prefer_visual: bool,
    alternate: bool,
) -> tuple[str, str, int] | None:
    artists = list_shorts_artists()
    if not artists:
        return None

    preferred_mode = "visual" if prefer_visual else "cover"
    start = artist_index % len(artists)

    for offset in range(len(artists)):
        idx = (start + offset) % len(artists)
        artist = artists[idx]
        if artist_can_shorts(artist, preferred_mode):
            return artist, preferred_mode, idx
        fallback = "cover" if preferred_mode == "visual" else "visual"
        if artist_can_shorts(artist, fallback):
            return artist, fallback, idx

    return None


def publish_interval_shorts(
    *,
    artist_index: int = 0,
    publish_count: int = 0,
    prefer_visual: bool = True,
    dry_run: bool = False,
    use_audio_analysis: bool = False,
) -> dict | None:
    """Upload a Shorts clip every N hours — rotates artists and visual/cover."""
    global_shorts = load_shorts_settings({})
    if not global_shorts["enabled"]:
        return None

    target = pick_interval_shorts_target(
        artist_index=artist_index,
        publish_count=publish_count,
        prefer_visual=prefer_visual,
        alternate=global_shorts.get("alternate_visual_cover", True),
    )
    if not target:
        print("  No content ready for interval Shorts")
        return None

    artist, mode, picked_index = target
    artist_dir = ARTISTS_DIR / artist
    config = load_artist_config(artist_dir)
    shorts = load_shorts_settings(config)

    audio_files = find_audio_files(artist_dir / "beats")
    images = find_images(artist_dir / "image")
    titles = load_titles(artist_dir, config)
    if not audio_files or not titles:
        return None

    slot = publish_count
    audio_path = audio_files[slot % len(audio_files)]
    title = titles[slot % len(titles)]
    image_path = images[slot % len(images)] if images else None

    visual_path = None
    if mode == "visual":
        visual_path = pick_visual(
            artist_dir / "visuals",
            seed=f"{artist}:{slot}:{audio_path.name}",
        )
        if not visual_path:
            if not image_path:
                return None
            mode = "cover"
    elif not image_path:
        return None

    metadata = analyze_beat(
        artist,
        audio_path,
        title=title,
        use_audio_analysis=use_audio_analysis,
    )
    tags_line = load_tags_line(artist_dir, config)
    api_tags = config.get("tags", [])
    full_video_url = find_full_video_url(artist, audio_path)

    print(f"  [{artist}] interval Shorts \"{title}\" ({mode})")
    print(f"    Audio: {audio_path.name}")
    if mode == "visual" and visual_path:
        print(f"    Visual: {visual_path.name}")
    elif image_path:
        print(f"    Cover: {image_path.name}")

    if dry_run:
        print("    [dry-run] Would upload interval Shorts (files kept)")
        return {
            "artist": artist,
            "title": title,
            "mode": mode,
            "artist_index": picked_index,
            "next_prefer_visual": not prefer_visual if shorts.get("alternate_visual_cover", True) else prefer_visual,
        }

    from youtube import get_authenticated_service

    service = get_authenticated_service(CREDENTIALS_DIR)
    result = upload_shorts_clip(
        artist_dir=artist_dir,
        audio_path=audio_path,
        image_path=image_path,
        title=title,
        metadata=metadata,
        config=config,
        tags_line=tags_line,
        api_tags=api_tags,
        shorts=shorts,
        service=service,
        full_video_url=full_video_url,
        visual_path=visual_path,
        use_visual=mode == "visual",
        log_type="shorts_interval",
        log_key_suffix=f"shorts_interval_{slot}",
    )
    if not result:
        return None

    print(f"    Done: {result['title']} ({result['mode']})")
    return {
        "artist": artist,
        "title": title,
        "mode": result["mode"],
        "shorts_video_id": result["video_id"],
        "artist_index": picked_index,
        "next_prefer_visual": not prefer_visual if shorts.get("alternate_visual_cover", True) else prefer_visual,
    }


def publish_single_beat(
    artist_dir: Path,
    *,
    dry_run: bool = False,
    skip_upload: bool = False,
    use_audio_analysis: bool = True,
) -> dict | None:
    """Publish exactly one beat (first in queue). Archive title and delete files after."""
    artist = artist_dir.name
    config = load_artist_config(artist_dir)

    audio_files = find_audio_files(artist_dir / "beats")
    images = find_images(artist_dir / "image")
    titles = load_titles(artist_dir, config)

    if not audio_files:
        print(f"  [{artist}] No beats")
        return None
    if not images:
        print(f"  [{artist}] No images")
        return None
    if not titles:
        print(f"  [{artist}] No titles in titles.txt")
        return None

    audio_path = audio_files[0]
    image_path = images[0]
    title = titles[0]

    visual_path = None
    shorts_settings = load_shorts_settings(config)
    if shorts_settings.get("use_visuals", True):
        visual_path = pick_visual(artist_dir / "visuals", seed=audio_path.name)

    metadata = analyze_beat(
        artist,
        audio_path,
        title=title,
        use_audio_analysis=use_audio_analysis,
    )

    description_template = load_description(artist_dir)
    tags_line = load_tags_line(artist_dir, config)
    api_tags = config.get("tags", [])

    print(f"  [{artist}] \"{title}\"")
    print(f"    Audio: {audio_path.name}")
    print(f"    Image: {image_path.name}")
    if visual_path:
        print(f"    Visual: {visual_path.name} (Shorts/TikTok/Reels)")
    print(f"    BPM: {metadata.bpm} | Key: {format_key(metadata.key)}")
    print(f"    Prod: {format_producers(metadata.producers, config.get('producer', 'triphoy'))}")

    description = fill_template(description_template, metadata, config, tags_line)
    youtube_title = fill_template(
        config.get("title_template", '(free) {artist} type beat "{title}"'),
        metadata,
        config,
        tags_line,
    )
    print(f"    YouTube: {youtube_title}")

    if dry_run:
        shorts = load_shorts_settings(config)
        shorts_title = fill_template(
            config.get("shorts_title_template", '{artist_cap} Type Beat "{title}" #Shorts'),
            metadata, config, tags_line,
            extra={"{full_video_url}": "https://youtu.be/XXXXXXXX"},
        )
        print("    [dry-run] Would upload and cleanup")
        if shorts["enabled"]:
            src = visual_path.name if visual_path else "cover image"
            print(f"    [dry-run] Shorts in {shorts['delay_minutes']} min ({src}): {shorts_title}")
            if shorts.get("tiktok"):
                print("    [dry-run] TikTok upload after Shorts render")
            if shorts.get("reels"):
                print("    [dry-run] Instagram Reels upload after Shorts render")
        return {
            "artist": artist,
            "title": title,
            "youtube_title": youtube_title,
            "audio_file": audio_path.name,
            "image_file": image_path.name,
        }

    safe_title = re.sub(r'[^\w\-]', '_', title)
    video_path = OUTPUT_DIR / artist / f"{safe_title}_{audio_path.stem}.mp4"
    print(f"    Rendering → {video_path.name}")
    render_video(image_path, audio_path, video_path)

    video_id = None
    shorts_video_id = None
    if not skip_upload:
        from youtube import get_authenticated_service, save_upload_log, upload_video

        service = get_authenticated_service(CREDENTIALS_DIR)
        video_id = upload_video(
            service,
            video_path,
            title=youtube_title,
            description=description,
            tags=api_tags,
            category_id=config.get("category_id", "10"),
            privacy=config.get("privacy", "public"),
            playlist_id=config.get("playlist_id"),
        )

        save_upload_log(UPLOAD_LOG, {
            "key": upload_key(artist, audio_path, title),
            "artist": artist,
            "beat_name": metadata.beat_name,
            "title": title,
            "audio_file": audio_path.name,
            "video_id": video_id,
            "youtube_title": youtube_title,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        })

        shorts = load_shorts_settings(config)
        if shorts["enabled"] and video_id:
            full_url = f"https://youtu.be/{video_id}"
            delay = shorts["delay_minutes"]
            print(f"    Waiting {delay} min before Shorts...")
            time.sleep(delay * 60)

            use_visual = bool(visual_path and shorts.get("use_visuals", True))
            shorts_result = upload_shorts_clip(
                artist_dir=artist_dir,
                audio_path=audio_path,
                image_path=image_path,
                title=title,
                metadata=metadata,
                config=config,
                tags_line=tags_line,
                api_tags=api_tags,
                shorts=shorts,
                service=service,
                full_video_url=full_url,
                visual_path=visual_path,
                use_visual=use_visual,
                log_type="shorts",
                log_key_suffix="shorts",
                full_video_id=video_id,
            )
            if shorts_result:
                shorts_video_id = shorts_result["video_id"]

    archive_title(artist_dir, config, title, video_id=video_id)
    remove_first_title(artist_dir, config, title)
    print(f"    Archived title: {title}")
    cleanup_after_publish(audio_path, image_path, video_path, visual_path)

    return {
        "artist": artist,
        "title": title,
        "youtube_title": youtube_title,
        "video_id": video_id,
        "shorts_video_id": shorts_video_id,
        "audio_file": audio_path.name,
        "image_file": image_path.name,
    }


def process_artist(
    artist_dir: Path,
    *,
    dry_run: bool = False,
    skip_upload: bool = False,
    use_audio_analysis: bool = True,
    force: bool = False,
) -> int:
    artist = artist_dir.name
    config = load_artist_config(artist_dir)

    audio_files = find_audio_files(artist_dir / "beats")
    if not audio_files:
        print(f"[{artist}] No audio in beats/ — skip")
        return 0

    images = find_images(artist_dir / "image")
    if not images:
        print(f"[{artist}] No images in image/ — skip")
        return 0

    titles = load_titles(artist_dir, config)
    if not titles:
        print(f"[{artist}] Warning: no titles in titles.txt — using filenames")

    description_template = load_description(artist_dir)
    tags_line = load_tags_line(artist_dir, config)
    api_tags = config.get("tags", [])

    print(f"\n[{artist}] {len(audio_files)} beat(s), {len(images)} image(s), {len(titles)} title(s)")

    processed = 0
    for i, audio_path in enumerate(audio_files):
        image_path = images[i % len(images)]
        title = titles[i % len(titles)] if titles else audio_path.stem

        metadata = analyze_beat(
            artist,
            audio_path,
            title=title,
            use_audio_analysis=use_audio_analysis,
        )

        print(f"\n  [{i + 1}/{len(audio_files)}] \"{title}\"")
        print(f"    Audio: {audio_path.name}")
        print(f"    Image: {image_path.name}")
        print(f"    BPM: {metadata.bpm} | Key: {format_key(metadata.key)} ({metadata.source})")
        print(f"    Prod: {format_producers(metadata.producers, config.get('producer', 'triphoy'))}")

        if not force and is_already_uploaded(artist, audio_path, title):
            print("    Already uploaded — skip (use --force)")
            continue

        description = fill_template(description_template, metadata, config, tags_line)
        youtube_title = fill_template(
            config.get("title_template", '(free) {artist} type beat "{title}"'),
            metadata,
            config,
            tags_line,
        )

        print(f"    Title: {youtube_title}")

        if dry_run:
            print("    [dry-run] Would render and upload")
            processed += 1
            continue

        safe_title = re.sub(r'[^\w\-]', '_', title)
        video_path = OUTPUT_DIR / artist / f"{safe_title}_{audio_path.stem}.mp4"
        print(f"    Rendering → {video_path.name}")
        render_video(image_path, audio_path, video_path)

        if skip_upload:
            print(f"    Video saved: {video_path}")
            processed += 1
            continue

        from youtube import get_authenticated_service, save_upload_log, upload_video

        service = get_authenticated_service(CREDENTIALS_DIR)
        video_id = upload_video(
            service,
            video_path,
            title=youtube_title,
            description=description,
            tags=api_tags,
            category_id=config.get("category_id", "10"),
            privacy=config.get("privacy", "public"),
            playlist_id=config.get("playlist_id"),
        )

        save_upload_log(UPLOAD_LOG, {
            "key": upload_key(artist, audio_path, title),
            "artist": artist,
            "beat_name": metadata.beat_name,
            "title": title,
            "audio_file": audio_path.name,
            "video_id": video_id,
            "youtube_title": youtube_title,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        })
        processed += 1

    return processed


def scan_and_upload(
    *,
    artist_filter: str | None = None,
    dry_run: bool = False,
    skip_upload: bool = False,
    use_audio_analysis: bool = True,
    force: bool = False,
) -> int:
    if not ARTISTS_DIR.is_dir():
        print(f"Create artists folder: {ARTISTS_DIR}")
        return 1

    artists = sorted(d for d in ARTISTS_DIR.iterdir() if d.is_dir())
    if artist_filter:
        artists = [d for d in artists if d.name == artist_filter]

    if not artists:
        print("No artist folders found.")
        return 1

    print(f"Found {len(artists)} artist(s): {', '.join(d.name for d in artists)}")

    total = 0
    for artist_dir in artists:
        total += process_artist(
            artist_dir,
            dry_run=dry_run,
            skip_upload=skip_upload,
            use_audio_analysis=use_audio_analysis,
            force=force,
        )

    print(f"\nDone: {total} beat(s) processed")
    return 0 if total > 0 else 1


def main():
    parser = argparse.ArgumentParser(description="BeatMachine — auto upload beats to YouTube")
    parser.add_argument("--artist", help="Process only this artist folder")
    parser.add_argument("--dry-run", action="store_true", help="Preview without rendering/uploading")
    parser.add_argument("--render-only", action="store_true", help="Render video but don't upload")
    parser.add_argument("--no-audio-analysis", action="store_true", help="Only parse filename/tags")
    parser.add_argument("--force", action="store_true", help="Re-upload even if already done")
    args = parser.parse_args()

    sys.exit(scan_and_upload(
        artist_filter=args.artist,
        dry_run=args.dry_run,
        skip_upload=args.render_only,
        use_audio_analysis=not args.no_audio_analysis,
        force=args.force,
    ))


if __name__ == "__main__":
    main()
