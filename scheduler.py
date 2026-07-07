"""Daily auto-publisher: 21:00 MSK, weekly quotas, cleanup after upload."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from analyzer import find_audio_files
from renderer import find_images
from uploader import (
    ARTISTS_DIR,
    load_artist_config,
    load_titles,
    publish_single_beat,
)

ROOT = Path(__file__).parent
SCHEDULE_CONFIG = ROOT / "schedule_config.json"
STATE_FILE = ROOT / "scheduler_state.json"


def load_schedule_config() -> dict:
    if SCHEDULE_CONFIG.exists():
        return json.loads(SCHEDULE_CONFIG.read_text(encoding="utf-8"))
    return {
        "publish_time": "21:00",
        "timezone": "Europe/Moscow",
        "weekly_quota": {"osamason": 4, "che": 2, "osamason+che": 1},
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "week_id": "",
        "weekly_published": {},
        "last_publish_date": None,
        "history": [],
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def moscow_now() -> datetime:
    tz = ZoneInfo(load_schedule_config().get("timezone", "Europe/Moscow"))
    return datetime.now(tz)


def current_week_id(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def reset_week_if_needed(state: dict, now: datetime) -> None:
    week_id = current_week_id(now)
    if state.get("week_id") != week_id:
        state["week_id"] = week_id
        state["weekly_published"] = {}


def artist_is_ready(artist_name: str) -> bool:
    artist_dir = ARTISTS_DIR / artist_name
    if not artist_dir.is_dir():
        return False

    config = load_artist_config(artist_dir)
    has_beats = bool(find_audio_files(artist_dir / "beats"))
    has_images = bool(find_images(artist_dir / "image"))
    has_titles = bool(load_titles(artist_dir, config))
    return has_beats and has_images and has_titles


def get_ready_artists() -> list[str]:
    if not ARTISTS_DIR.is_dir():
        return []
    return sorted(
        d.name for d in ARTISTS_DIR.iterdir()
        if d.is_dir() and artist_is_ready(d.name)
    )


def pick_artist(state: dict, config: dict) -> str | None:
    """Pick artist by weekly quota; fallback to any ready artist."""
    quota: dict[str, int] = config.get("weekly_quota", {})
    published: dict[str, int] = state.get("weekly_published", {})
    ready = set(get_ready_artists())

    if not ready:
        return None

    # Prefer artists with remaining weekly quota (highest remaining first)
    candidates: list[tuple[int, str]] = []
    for artist, limit in quota.items():
        if artist not in ready:
            continue
        remaining = limit - published.get(artist, 0)
        if remaining > 0:
            candidates.append((remaining, artist))

    if candidates:
        candidates.sort(reverse=True)
        return candidates[0][1]

    # Quota filled for scheduled artists — still publish if content exists
    for artist in quota:
        if artist in ready:
            return artist

    # Any ready artist not in quota map
    return next(iter(sorted(ready)))


def next_publish_datetime(now: datetime, publish_time: str) -> datetime:
    hour, minute = map(int, publish_time.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def already_published_today(state: dict, today: str) -> bool:
    return state.get("last_publish_date") == today


def run_publish_cycle(*, dry_run: bool = False) -> bool:
    config = load_schedule_config()
    state = load_state()
    now = moscow_now()
    reset_week_if_needed(state, now)

    artist = pick_artist(state, config)
    if artist is None:
        print(f"[{now.strftime('%Y-%m-%d %H:%M')}] Nothing to publish — no beats/images/titles ready")
        save_state(state)
        return False

    published_counts = state.get("weekly_published", {})
    quota = config.get("weekly_quota", {})
    remaining = quota.get(artist, "?") - published_counts.get(artist, 0) if artist in quota else "?"
    print(f"\n[{now.strftime('%Y-%m-%d %H:%M MSK')}] Publishing: {artist} (weekly remaining: {remaining})")

    result = publish_single_beat(
        ARTISTS_DIR / artist,
        dry_run=dry_run,
        use_audio_analysis=False,
    )

    if not result:
        print(f"  Failed to publish {artist}")
        save_state(state)
        return False

    if not dry_run:
        published_counts[artist] = published_counts.get(artist, 0) + 1
        state["weekly_published"] = published_counts
        state["last_publish_date"] = now.strftime("%Y-%m-%d")
        state.setdefault("history", []).append({
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "artist": artist,
            "title": result["title"],
            "youtube_title": result["youtube_title"],
            "video_id": result.get("video_id"),
        })
        # keep last 100 entries
        state["history"] = state["history"][-100:]

    save_state(state)
    print(f"  Done: {result['youtube_title']}")
    return True


def wait_until_publish_time() -> None:
    config = load_schedule_config()
    publish_time = config.get("publish_time", "21:00")

    while True:
        now = moscow_now()
        today = now.strftime("%Y-%m-%d")
        state = load_state()
        reset_week_if_needed(state, now)

        hour, minute = map(int, publish_time.split(":"))
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if not already_published_today(state, today) and now >= target:
            print(f"\n{'='*50}")
            print(f"Scheduled publish triggered at {now.strftime('%Y-%m-%d %H:%M MSK')}")
            run_publish_cycle()
            # after publish, wait until tomorrow
            time.sleep(60)
            continue

        if already_published_today(state, today):
            target = next_publish_datetime(now, publish_time)
        elif now < target:
            pass  # target is today 21:00
        else:
            target = next_publish_datetime(now, publish_time)

        wait_seconds = max(1, int((target - now).total_seconds()))
        print(
            f"[{now.strftime('%H:%M MSK')}] Next publish: "
            f"{target.strftime('%Y-%m-%d %H:%M MSK')} "
            f"(in {wait_seconds // 3600}h {(wait_seconds % 3600) // 60}m)"
        )
        time.sleep(min(wait_seconds, 300))  # check every 5 min max


def main():
    parser = argparse.ArgumentParser(description="BeatMachine Scheduler — daily auto-publish")
    parser.add_argument("--daemon", action="store_true", help="Run forever, publish daily at 21:00 MSK")
    parser.add_argument("--now", action="store_true", help="Publish one beat immediately (test)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading/deleting")
    parser.add_argument("--status", action="store_true", help="Show schedule state")
    args = parser.parse_args()

    if args.status:
        config = load_schedule_config()
        state = load_state()
        now = moscow_now()
        reset_week_if_needed(state, now)
        print(f"Time now:    {now.strftime('%Y-%m-%d %H:%M MSK')}")
        print(f"Publish at:  {config.get('publish_time')} MSK")
        print(f"Week:        {state.get('week_id', current_week_id(now))}")
        print(f"Last publish:{state.get('last_publish_date', 'never')}")
        print(f"\nWeekly quota / published:")
        for artist, limit in config.get("weekly_quota", {}).items():
            done = state.get("weekly_published", {}).get(artist, 0)
            ready = "ready" if artist_is_ready(artist) else "empty"
            print(f"  {artist}: {done}/{limit} ({ready})")
        print(f"\nReady artists: {', '.join(get_ready_artists()) or 'none'}")
        next_artist = pick_artist(state, config)
        print(f"Next pick:     {next_artist or 'nothing'}")
        sys.exit(0)

    if args.now or args.dry_run:
        ok = run_publish_cycle(dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    if args.daemon:
        print("BeatMachine Scheduler started")
        print(f"Config: {SCHEDULE_CONFIG}")
        config = load_schedule_config()
        print(f"Daily at {config.get('publish_time')} MSK")
        print(f"Weekly: {config.get('weekly_quota')}")
        print("Press Ctrl+C to stop\n")
        try:
            wait_until_publish_time()
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
            sys.exit(0)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
