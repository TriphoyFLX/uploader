"""Authorize TikTok via Login Kit (Desktop OAuth + PKCE)."""

from __future__ import annotations

import hashlib
import json
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

CREDENTIALS_DIR = Path(__file__).resolve().parent / "credentials"
CONFIG_FILE = "tiktok.json"
REDIRECT_URI = "http://127.0.0.1:8765/callback/"
AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
SCOPES_BASIC = "user.info.basic"
SCOPES_FULL = "user.info.basic,video.publish"


def _pkce_pair() -> tuple[str, str]:
    verifier = "".join(secrets.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~") for _ in range(64))
    challenge = hashlib.sha256(verifier.encode()).hexdigest()
    return verifier, challenge


def _load_config() -> dict:
    path = CREDENTIALS_DIR / CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Create {path} with client_key and client_secret from TikTok Developer Portal."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _save_config(cfg: dict) -> None:
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    (CREDENTIALS_DIR / CONFIG_FILE).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def authorize(*, port: int = 8765, scopes: str = SCOPES_FULL) -> dict:
    cfg = _load_config()
    client_key = cfg.get("client_key")
    client_secret = cfg.get("client_secret")
    if not client_key or not client_secret:
        raise ValueError("tiktok.json must include client_key and client_secret")

    redirect_uri = cfg.get("redirect_uri", REDIRECT_URI)
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    params = urlencode({
        "client_key": client_key,
        "scope": scopes,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"{AUTHORIZE_URL}?{params}"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            self.server.auth_code = query.get("code", [None])[0]
            self.server.auth_error = query.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            if self.server.auth_code:
                body = "<h1>TikTok connected</h1><p>You can close this tab.</p>"
            else:
                body = f"<h1>TikTok error</h1><p>{self.server.auth_error or 'No code received'}</p>"
            self.wfile.write(body.encode())

    class ReuseHTTPServer(HTTPServer):
        allow_reuse_address = True

    try:
        server = ReuseHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        if exc.errno != 48:
            raise
        raise SystemExit(
            f"Port {port} is busy. Free it with:\n"
            f"  lsof -ti :{port} | xargs kill -9\n"
            f"Then run: python tiktok_auth.py"
        ) from exc
    server.auth_code = None
    server.auth_error = None

    print(f"Open this URL if the browser does not start:\n{auth_url}\n", flush=True)
    webbrowser.open(auth_url)
    print(f"Waiting for callback on {redirect_uri} ...", flush=True)
    server.handle_request()

    if server.auth_error:
        raise RuntimeError(f"TikTok authorization failed: {server.auth_error}")
    if not server.auth_code:
        raise RuntimeError("No authorization code received")

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": server.auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    resp.raise_for_status()
    token_data = resp.json()

    cfg.update({
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "open_id": token_data.get("open_id"),
        "expires_in": token_data.get("expires_in"),
        "scope": token_data.get("scope"),
        "redirect_uri": redirect_uri,
    })
    _save_config(cfg)

    print("TikTok authorized successfully.", flush=True)
    print(f"  open_id: {cfg.get('open_id')}", flush=True)
    print(f"  scopes:  {cfg.get('scope')}", flush=True)
    return cfg


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Authorize TikTok via Login Kit")
    parser.add_argument("--port", type=int, default=8765, help="Local callback port (default: 8765)")
    parser.add_argument(
        "--basic-only",
        action="store_true",
        help="Request only user.info.basic (use if full scopes fail in sandbox)",
    )
    args = parser.parse_args()
    scopes = SCOPES_BASIC if args.basic_only else SCOPES_FULL
    print("Scopes:", scopes, flush=True)
    print(
        "If TikTok shows 'client_key' error: add your TikTok account in "
        "Developer Portal → your app → Sandbox (Target users), then retry.\n",
        flush=True,
    )
    authorize(port=args.port, scopes=scopes)
