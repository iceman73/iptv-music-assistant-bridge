\
#!/usr/bin/env python3
import argparse
import json
import os
import sys
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8088").rstrip("/")
BRIDGE_INTERNAL_URL = os.getenv("BRIDGE_INTERNAL_URL", "http://127.0.0.1:8088").rstrip("/")
MUSIC_ASSISTANT_URL = os.getenv("MUSIC_ASSISTANT_URL", "http://music-assistant:8095").rstrip("/")
MUSIC_ASSISTANT_TOKEN = os.getenv("MUSIC_ASSISTANT_TOKEN", "").strip()
IMPORT_FORMAT = os.getenv("IMPORT_FORMAT", "aac").strip().lower()
IMPORT_LOGOS = os.getenv("IMPORT_LOGOS", "true").lower() in {"1", "true", "yes", "on"}


def fetch_json(url: str):
    req = Request(url, headers={"User-Agent": "Dispatcharr-XC-MA-Bridge/4.4"})
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def ma_add_radio(item: dict):
    url = f"{PUBLIC_BASE_URL}/stream/{item['id']}.{IMPORT_FORMAT}"
    args = {"url": url, "name": item["name"]}
    if IMPORT_LOGOS and item.get("logo"):
        args["image_url"] = item["logo"]

    payload = json.dumps({
        "command": "builtin/add_radio",
        "message_id": uuid.uuid4().hex,
        "args": args,
    }).encode()

    req = Request(
        f"{MUSIC_ASSISTANT_URL}/api",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {MUSIC_ASSISTANT_TOKEN}",
            "User-Agent": "Dispatcharr-XC-MA-Bridge/4.4",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(
        description="Import bridge channels into Music Assistant as radio stations."
    )
    parser.add_argument("--apply", action="store_true", help="Actually add stations; default is dry-run.")
    args = parser.parse_args()

    if IMPORT_FORMAT not in {"mp3", "aac"}:
        print("IMPORT_FORMAT must be mp3 or aac", file=sys.stderr)
        return 2

    try:
        items = fetch_json(f"{BRIDGE_INTERNAL_URL}/channels")
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        print(f"Failed to fetch bridge catalog: {exc}", file=sys.stderr)
        return 2

    if not isinstance(items, list) or not items:
        print("No matching channels found in the bridge catalog.")
        return 1

    print(f"Found {len(items)} matching channel(s).")
    for item in items:
        radio_url = f"{PUBLIC_BASE_URL}/stream/{item['id']}.{IMPORT_FORMAT}"
        print(f"- {item['name']} [{item.get('group', '')}] -> {radio_url}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to add them to Music Assistant.")
        return 0
    if not MUSIC_ASSISTANT_TOKEN:
        print("MUSIC_ASSISTANT_TOKEN is required with --apply", file=sys.stderr)
        return 2

    failures = 0
    for item in items:
        try:
            status, _ = ma_add_radio(item)
            if 200 <= status < 300:
                print(f"ADDED: {item['name']}")
            else:
                failures += 1
                print(f"FAILED ({status}): {item['name']}", file=sys.stderr)
        except (HTTPError, URLError, TimeoutError) as exc:
            failures += 1
            print(f"FAILED: {item['name']}: {exc}", file=sys.stderr)

    print(f"\nCompleted: {len(items) - failures} added, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())