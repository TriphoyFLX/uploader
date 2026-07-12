"""Auto-publisher: daily full videos + Shorts every N hours."""

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
    list_shorts_artists,
    load_artist_config,
    load_shorts_settings,
    load_titles,
    publish_interval_shorts,
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
        "weekly_quota": {"osamason": 3, "che": 2, "ninevicious": 1, "osamason+che": 1},
        "shorts": {"enabled": True, "interval_hours": 2, "delay_minutes": 3},
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "week_id": "",
        "weekly_published": {},
        "last_publish_date": None,
        "history": [],
        "shorts": {
            "last_publish_at": None,
            "artist_index": 0,
            "use_visual_next": True,
            "publish_count": 0,
        },
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


def get_shorts_state(state: dict) -> dict:
    shorts = state.setdefault("shorts", {})
    shorts.setdefault("last_publish_at", None)
    shorts.setdefault("artist_index", 0)
    shorts.setdefault("use_visual_next", True)
    shorts.setdefault("publish_count", 0)
    return shorts


def mark_shorts_published(state: dict, now: datetime) -> None:
    shorts = get_shorts_state(state)
    shorts["last_publish_at"] = now.isoformat()
    shorts["publish_count"] = shorts.get("publish_count", 0) + 1
    state["shorts"] = shorts


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

    for artist in quota:
        if artist in ready:
            return artist

    return next(iter(sorted(ready)))


def next_publish_datetime(now: datetime, publish_time: str) -> datetime:
    hour, minute = map(int, publish_time.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def parse_state_time(value: str | None, tz: ZoneInfo) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def next_shorts_datetime(now: datetime, state: dict, config: dict) -> datetime | None:
    shorts_cfg = load_shorts_settings(config)
    if not shorts_cfg.get("enabled"):
        return None

    interval = max(1, int(shorts_cfg.get("interval_hours", 2)))
    tz = ZoneInfo(config.get("timezone", "Europe/Moscow"))
    shorts_state = get_shorts_state(state)
    last_at = parse_state_time(shorts_state.get("last_publish_at"), tz)

    if last_at is None:
        return now

    return last_at + timedelta(hours=interval)


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
            "type": "full",
        })
        state["history"] = state["history"][-100:]

        if result.get("shorts_video_id"):
            mark_shorts_published(state, moscow_now())
            shorts = get_shorts_state(state)
            shorts["artist_index"] = (shorts.get("artist_index", 0) + 1) % max(len(list_shorts_artists()), 1)
            if load_shorts_settings(config).get("alternate_visual_cover", True):
                shorts["use_visual_next"] = not shorts.get("use_visual_next", True)
            state["shorts"] = shorts

    save_state(state)
    print(f"  Done: {result['youtube_title']}")
    return True


def run_shorts_cycle(*, dry_run: bool = False) -> bool:
    config = load_schedule_config()
    state = load_state()
    now = moscow_now()
    reset_week_if_needed(state, now)

    shorts_cfg = load_shorts_settings(config)
    if not shorts_cfg.get("enabled"):
        return False

    shorts_state = get_shorts_state(state)
    print(
        f"\n[{now.strftime('%Y-%m-%d %H:%M MSK')}] Interval Shorts "
        f"(every {shorts_cfg.get('interval_hours', 2)}h)"
    )

    result = publish_interval_shorts(
        artist_index=shorts_state.get("artist_index", 0),
        publish_count=shorts_state.get("publish_count", 0),
        prefer_visual=shorts_state.get("use_visual_next", True),
        dry_run=dry_run,
        use_audio_analysis=False,
    )

    if not result:
        save_state(state)
        return False

    if not dry_run:
        mark_shorts_published(state, now)
        shorts_state["artist_index"] = result.get("artist_index", 0) + 1
        shorts_state["use_visual_next"] = result.get(
            "next_prefer_visual",
            shorts_state.get("use_visual_next", True),
        )
        state["shorts"] = shorts_state
        state.setdefault("history", []).append({
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "artist": result["artist"],
            "title": result["title"],
            "mode": result.get("mode"),
            "video_id": result.get("shorts_video_id"),
            "type": "shorts_interval",
        })
        state["history"] = state["history"][-100:]

    save_state(state)
    print(f"  Done: {result.get('title')} ({result.get('mode')})")
    return True


def seconds_until(target: datetime | None, now: datetime) -> int | None:
    if target is None:
        return None
    return max(1, int((target - now).total_seconds()))


def daemon_loop() -> None:
    config = load_schedule_config()
    publish_time = config.get("publish_time", "21:00")
    shorts_cfg = load_shorts_settings(config)

    while True:
        now = moscow_now()
        today = now.strftime("%Y-%m-%d")
        state = load_state()
        reset_week_if_needed(state, now)

        hour, minute = map(int, publish_time.split(":"))
        daily_target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if already_published_today(state, today):
            daily_target = next_publish_datetime(now, publish_time)
        elif now < daily_target:
            pass
        elif not already_published_today(state, today):
            print(f"\n{'='*50}")
            print(f"Scheduled publish triggered at {now.strftime('%Y-%m-%d %H:%M MSK')}")
            run_publish_cycle()
            time.sleep(60)
            continue

        shorts_due = False
        if shorts_cfg.get("enabled"):
            next_shorts = next_shorts_datetime(now, state, config)
            if next_shorts is None or now >= next_shorts:
                print(f"\n{'='*50}")
                print(f"Interval Shorts triggered at {now.strftime('%Y-%m-%d %H:%M MSK')}")
                run_shorts_cycle()
                time.sleep(30)
                continue

        daily_wait = seconds_until(daily_target, now)
        next_shorts = next_shorts_datetime(now, state, config)
        shorts_wait = seconds_until(next_shorts, now)

        waits = [w for w in (daily_wait, shorts_wait) if w is not None]
        wait_seconds = min(waits) if waits else 300
        wait_seconds = min(wait_seconds, 300)

        parts = []
        if daily_wait is not None:
            parts.append(f"full video {daily_target.strftime('%Y-%m-%d %H:%M MSK')}")
        if shorts_wait is not None and next_shorts is not None:
            parts.append(f"Shorts {next_shorts.strftime('%Y-%m-%d %H:%M MSK')}")
        next_label = " | ".join(parts) if parts else "idle"

        print(
            f"[{now.strftime('%H:%M MSK')}] Next: {next_label} "
            f"(in {wait_seconds // 3600}h {(wait_seconds % 3600) // 60}m)"
        )
        time.sleep(wait_seconds)


def main():
    parser = argparse.ArgumentParser(description="BeatMachine Scheduler — daily videos + interval Shorts")
    parser.add_argument("--daemon", action="store_true", help="Run forever")
    parser.add_argument("--now", action="store_true", help="Publish one full beat immediately")
    parser.add_argument("--shorts-now", action="store_true", help="Publish one interval Shorts immediately")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading/deleting")
    parser.add_argument("--status", action="store_true", help="Show schedule state")
    args = parser.parse_args()

    if args.status:
        config = load_schedule_config()
        state = load_state()
        now = moscow_now()
        reset_week_if_needed(state, now)
        shorts_cfg = load_shorts_settings(config)
        shorts_state = get_shorts_state(state)
        next_shorts = next_shorts_datetime(now, state, config)

        print(f"Time now:     {now.strftime('%Y-%m-%d %H:%M MSK')}")
        print(f"Full video:   {config.get('publish_time')} MSK (daily)")
        print(f"Shorts:       every {shorts_cfg.get('interval_hours', 2)}h")
        print(f"Shorts delay: {shorts_cfg.get('delay_minutes', 3)} min after full video")
        print(f"Shorts mode:  alternate visual/cover = {shorts_cfg.get('alternate_visual_cover', True)}")
        print(f"Week:         {state.get('week_id', current_week_id(now))}")
        print(f"Last full:    {state.get('last_publish_date', 'never')}")
        last_shorts = shorts_state.get("last_publish_at", "never")
        print(f"Last Shorts:  {last_shorts}")
        if next_shorts:
            print(f"Next Shorts:  {next_shorts.strftime('%Y-%m-%d %H:%M MSK')}")
        print(f"Shorts count: {shorts_state.get('publish_count', 0)}")
        print(f"Next visual:  {shorts_state.get('use_visual_next', True)}")
        print(f"\nWeekly quota / published:")
        for artist, limit in config.get("weekly_quota", {}).items():
            done = state.get("weekly_published", {}).get(artist, 0)
            ready = "ready" if artist_is_ready(artist) else "empty"
            print(f"  {artist}: {done}/{limit} ({ready})")
        print(f"\nReady full:   {', '.join(get_ready_artists()) or 'none'}")
        print(f"Ready Shorts: {', '.join(list_shorts_artists()) or 'none'}")
        next_artist = pick_artist(state, config)
        print(f"Next full:    {next_artist or 'nothing'}")
        sys.exit(0)

    if args.shorts_now:
        ok = run_shorts_cycle(dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    if args.now:
        ok = run_publish_cycle(dry_run=args.dry_run)
        sys.exit(0 if ok else 1)

    if args.daemon:
        config = load_schedule_config()
        shorts_cfg = load_shorts_settings(config)
        print("BeatMachine Scheduler started")
        print(f"Config: {SCHEDULE_CONFIG}")
        print(f"Full video daily at {config.get('publish_time')} MSK")
        print(f"Shorts every {shorts_cfg.get('interval_hours', 2)} hours")
        print(f"Weekly: {config.get('weekly_quota')}")
        print("Press Ctrl+C to stop\n")
        try:
            daemon_loop()
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
            sys.exit(0)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
