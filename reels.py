"""Upload videos to Instagram Reels via Graph API."""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

GRAPH_API = "https://graph.facebook.com/v21.0"
CONFIG_FILE = "instagram.json"


def load_config(credentials_dir: Path) -> dict:
    path = credentials_dir / CONFIG_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def is_configured(credentials_dir: Path) -> bool:
    cfg = load_config(credentials_dir)
    return bool(cfg.get("access_token") and cfg.get("ig_user_id"))


def upload_reel(
    video_path: Path,
    *,
    caption: str,
    credentials_dir: Path,
) -> str:
    cfg = load_config(credentials_dir)
    token = cfg.get("access_token")
    ig_user_id = cfg.get("ig_user_id")
    if not token or not ig_user_id:
        raise FileNotFoundError(
            f"Instagram not configured. Add {credentials_dir / CONFIG_FILE}\n"
            'Fields: {"access_token": "...", "ig_user_id": "..."}'
        )

    # Step 1: create resumable upload container
    params = {
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": caption[:2200],
        "access_token": token,
    }
    resp = requests.post(f"{GRAPH_API}/{ig_user_id}/media", params=params, timeout=60)
    resp.raise_for_status()
    container = resp.json()
    creation_id = container["id"]
    upload_url = container.get("uri") or container.get("upload_url")

    if not upload_url:
        raise RuntimeError(f"Instagram container failed: {container}")

    # Step 2: upload video binary
    video_data = video_path.read_bytes()
    up_headers = {
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(len(video_data)),
        "Content-Type": "application/octet-stream",
    }
    up = requests.post(upload_url, headers=up_headers, data=video_data, timeout=300)
    up.raise_for_status()

    # Step 3: wait for processing
    for _ in range(30):
        status = requests.get(
            f"{GRAPH_API}/{creation_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=30,
        ).json()
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"Instagram processing error: {status}")
        time.sleep(2)

    # Step 4: publish
    pub = requests.post(
        f"{GRAPH_API}/{ig_user_id}/media_publish",
        params={"creation_id": creation_id, "access_token": token},
        timeout=60,
    )
    pub.raise_for_status()
    media_id = pub.json().get("id", creation_id)
    print(f"  Reels upload OK (media_id: {media_id})")
    return media_id
