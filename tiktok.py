"""Upload videos to TikTok via Content Posting API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

TIKTOK_API = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{TIKTOK_API}/oauth/token/"
CONFIG_FILE = "tiktok.json"


def load_config(credentials_dir: Path) -> dict:
    path = credentials_dir / CONFIG_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_config(credentials_dir: Path, cfg: dict) -> None:
    (credentials_dir / CONFIG_FILE).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def refresh_access_token(credentials_dir: Path) -> str:
    cfg = load_config(credentials_dir)
    refresh_token = cfg.get("refresh_token")
    client_key = cfg.get("client_key")
    client_secret = cfg.get("client_secret")
    if not all([refresh_token, client_key, client_secret]):
        return cfg.get("access_token", "")

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    cfg["access_token"] = data["access_token"]
    if data.get("refresh_token"):
        cfg["refresh_token"] = data["refresh_token"]
    cfg["expires_in"] = data.get("expires_in")
    _save_config(credentials_dir, cfg)
    return cfg["access_token"]


def get_access_token(credentials_dir: Path) -> str:
    cfg = load_config(credentials_dir)
    token = cfg.get("access_token")
    if not token:
        raise FileNotFoundError(
            f"TikTok not configured. Run: python tiktok_auth.py\n"
            f"Config: {credentials_dir / CONFIG_FILE}"
        )
    return token


def is_configured(credentials_dir: Path) -> bool:
    cfg = load_config(credentials_dir)
    return bool(cfg.get("access_token") and cfg.get("client_key"))


def query_creator_info(credentials_dir: Path) -> dict:
    token = get_access_token(credentials_dir)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    resp = requests.post(f"{TIKTOK_API}/post/publish/creator_info/query/", headers=headers, json={}, timeout=60)
    if resp.status_code == 401:
        token = refresh_access_token(credentials_dir)
        headers["Authorization"] = f"Bearer {token}"
        resp = requests.post(f"{TIKTOK_API}/post/publish/creator_info/query/", headers=headers, json={}, timeout=60)
    resp.raise_for_status()
    data = resp.json().get("data", {})
    print(f"  TikTok creator: @{data.get('creator_username')} ({data.get('creator_nickname')})")
    return data


def _wait_for_publish(credentials_dir: Path, publish_id: str, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
    for _ in range(30):
        resp = requests.post(
            f"{TIKTOK_API}/post/publish/status/fetch/",
            headers=headers,
            json={"publish_id": publish_id},
            timeout=60,
        )
        resp.raise_for_status()
        status = resp.json().get("data", {}).get("status", "")
        print(f"  TikTok status: {status or 'processing'}")
        if status in {"PUBLISH_COMPLETE", "FAILED"}:
            if status == "FAILED":
                raise RuntimeError(f"TikTok publish failed: {resp.text}")
            return
        time.sleep(2)


def upload_video(
    video_path: Path,
    *,
    caption: str,
    credentials_dir: Path,
    privacy_level: str | None = None,
) -> str:
    creator = query_creator_info(credentials_dir)
    privacy_options = creator.get("privacy_level_options") or ["SELF_ONLY"]
    if privacy_level is None:
        # Unaudited/sandbox apps must post as private (SELF_ONLY).
        privacy_level = "SELF_ONLY" if "SELF_ONLY" in privacy_options else privacy_options[0]

    token = get_access_token(credentials_dir)
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

    if publish_id:
        _wait_for_publish(credentials_dir, publish_id, token)

    print(f"  TikTok upload OK (publish_id: {publish_id}, privacy: {privacy_level})")
    return publish_id or "ok"
