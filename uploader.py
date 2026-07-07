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
from renderer import find_images, render_shorts, render_video

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
    if SCHEDULE_CONFIG.exists():
        global_shorts = json.loads(SCHEDULE_CONFIG.read_text(encoding="utf-8")).get("shorts", {})

    return {
        "enabled": config.get("shorts_enabled", global_shorts.get("enabled", True)),
        "delay_minutes": config.get("shorts_delay_minutes", global_shorts.get("delay_minutes", 3)),
        "duration_seconds": config.get("shorts_duration_seconds", global_shorts.get("duration_seconds", 45)),
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


def cleanup_after_publish(audio_path: Path, image_path: Path, video_path: Path | None = None) -> None:
    if audio_path.exists():
        audio_path.unlink()
        print(f"    Deleted beat: {audio_path.name}")
    if image_path.exists():
        image_path.unlink()
        print(f"    Deleted image: {image_path.name}")
    if video_path and video_path.exists():
        video_path.unlink()
        print(f"    Deleted video: {video_path.name}")


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
            print(f"    [dry-run] Shorts in {shorts['delay_minutes']} min: {shorts_title}")
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

            shorts_path = OUTPUT_DIR / artist / f"{safe_title}_{audio_path.stem}_shorts.mp4"
            print(f"    Rendering Shorts → {shorts_path.name}")
            render_shorts(
                image_path,
                audio_path,
                shorts_path,
                duration=shorts["duration_seconds"],
            )

            shorts_title = fill_template(
                config.get("shorts_title_template", '{artist_cap} Type Beat "{title}" #Shorts'),
                metadata, config, tags_line,
            )
            shorts_description = fill_template(
                config.get(
                    "shorts_description",
                    "{artist_cap} Type Beat | Check Full Beat On My Channel\n\n{full_video_url}\n\n{shorts_hashtags}",
                ),
                metadata, config, tags_line,
                extra={"{full_video_url}": full_url},
            )
            shorts_tags = config.get("shorts_tags", api_tags[:3] + ["shorts"])

            print(f"    Shorts title: {shorts_title}")
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
                "key": f"{upload_key(artist, audio_path, title)}:shorts",
                "artist": artist,
                "title": title,
                "audio_file": audio_path.name,
                "video_id": shorts_video_id,
                "youtube_title": shorts_title,
                "type": "shorts",
                "full_video_id": video_id,
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
            })

            if shorts_path.exists():
                shorts_path.unlink()

    archive_title(artist_dir, config, title, video_id=video_id)
    remove_first_title(artist_dir, config, title)
    print(f"    Archived title: {title}")
    cleanup_after_publish(audio_path, image_path, video_path)

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
