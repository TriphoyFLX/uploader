"""TikTok demo: creator info + render shorts + Direct Post (for app review video)."""

from __future__ import annotations

import sys
from pathlib import Path

from renderer import find_image, render_shorts
from tiktok import is_configured, query_creator_info, upload_video

ROOT = Path(__file__).resolve().parent
CREDENTIALS_DIR = ROOT / "credentials"
ARTIST = "che"
BEAT = ROOT / "artists/che/beats/450. 149Bpm F#minor @triphoy.mp3"
OUTPUT = ROOT / "output" / "demo_tiktok_shorts.mp4"
CAPTION = 'Che Type Beat "demo" | Full beat on YouTube #typebeat #che'


def main() -> None:
    if not is_configured(CREDENTIALS_DIR):
        print("TikTok not authorized. Run first:\n  python tiktok_auth.py")
        sys.exit(1)

    if not BEAT.exists():
        print(f"Beat not found: {BEAT}")
        sys.exit(1)

    image = find_image(ROOT / "artists" / ARTIST / "image")
    if not image:
        print("No cover image in artists/che/image/")
        sys.exit(1)

    print("=== Step 1: Creator info (Login Kit + user.info.basic) ===")
    query_creator_info(CREDENTIALS_DIR)

    print("\n=== Step 2: Render 9:16 shorts ===")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    render_shorts(image, BEAT, OUTPUT, duration=20)
    print(f"  Rendered: {OUTPUT}")

    print("\n=== Step 3: Direct Post (video.publish) ===")
    publish_id = upload_video(OUTPUT, caption=CAPTION, credentials_dir=CREDENTIALS_DIR)
    print(f"\nDone. publish_id: {publish_id}")
    print("Check TikTok app — video will be Private until app audit passes.")


if __name__ == "__main__":
    main()
