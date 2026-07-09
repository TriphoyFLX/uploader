"""Upload videos to TikTok via Content Posting API."""

from __future__ import annotations

import json
from pathlib import Path

import requests

TIKTOK_API = "https://open.tiktokapis.com/v2"
CONFIG_FILE = "tiktok.json"


def load_config(credentials_dir: Path) -> dict:
    path = credentials_dir / CONFIG_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def is_configured(credentials_dir: Path) -> bool:
    cfg = load_config(credentials_dir)
    return bool(cfg.get("access_token"))


def upload_video(
    video_path: Path,
    *,
    caption: str,
    credentials_dir: Path,
    privacy_level: str = "PUBLIC_TO_EVERYONE",
) -> str:
    cfg = load_config(credentials_dir)
    token = cfg.get("access_token")
    if not token:
        raise FileNotFoundError(
            f"TikTok not configured. Add {credentials_dir / CONFIG_FILE}\n"
            "Get token: https://developers.tiktok.com/"
        )

    video_size = video_path.stat().st_size
    chunk_size = min(video_size, 10 * 1024 * 1024)
    total_chunks = max(1, (video_size + chunk_size - 1) // chunk_size)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}

    init_body = {
        "post_info": {
            "title": caption[:150],
            "privacy_level": privacy_level,
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": chunk_size,
            "total_chunk_count": total_chunks,
        },
    }

    resp = requests.post(f"{TIKTOK_API}/post/publish/video/init/", headers=headers, json=init_body, timeout=60)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")

    if not upload_url:
        raise RuntimeError(f"TikTok init failed: {resp.text}")

    with video_path.open("rb") as f:
        video_data = f.read()

    upload_headers = {
        "Content-Type": "video/mp4",
        "Content-Length": str(len(video_data)),
        "Content-Range": f"bytes 0-{len(video_data) - 1}/{len(video_data)}",
    }
    up = requests.put(upload_url, headers=upload_headers, data=video_data, timeout=300)
    up.raise_for_status()

    print(f"  TikTok upload OK (publish_id: {publish_id})")
    return publish_id or "ok"
