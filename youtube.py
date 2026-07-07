"""Upload videos to YouTube via Data API v3."""

from __future__ import annotations

import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.json"
CLIENT_SECRETS = "client_secrets.json"


def get_authenticated_service(credentials_dir: Path):
    credentials_dir.mkdir(parents=True, exist_ok=True)
    token_path = credentials_dir / TOKEN_FILE
    secrets_path = credentials_dir / CLIENT_SECRETS

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets_path.exists():
                raise FileNotFoundError(
                    f"Place OAuth client secrets at {secrets_path}\n"
                    "Get them from Google Cloud Console → YouTube Data API v3"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
            creds = flow.run_local_server(port=0)

        token_path.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_video(
    service,
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "10",  # Music
    privacy: str = "public",
    playlist_id: str | None = None,
) -> str:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,
    )

    request = service.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  Upload: {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"  Upload successful: https://youtu.be/{video_id}")

    if playlist_id:
        service.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        print(f"  Added to playlist: {playlist_id}")

    return video_id


def save_upload_log(log_path: Path, entry: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    if log_path.exists():
        entries = json.loads(log_path.read_text())
    entries.append(entry)
    log_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
