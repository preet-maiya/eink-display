#!/usr/bin/env python3
"""Download Space Grotesk Bold and IBM Plex Mono Regular TTF files to function-app/fonts/.

Uses the Google Fonts download/list API to get the current versioned gstatic.com URL,
then downloads the TTF directly — no zip parsing, no fragile user-agent tricks.
"""
import json
import os
import sys
import urllib.request

FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "function-app", "fonts")

# (Google Fonts family name, exact filename inside the manifest)
DOWNLOADS = [
    ("Space Grotesk",  "static/SpaceGrotesk-Bold.ttf",  "SpaceGrotesk-Bold.ttf"),
    ("IBM Plex Mono",  "IBMPlexMono-Regular.ttf",        "IBMPlexMono-Regular.ttf"),
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def get_gstatic_url(family: str, manifest_filename: str) -> str:
    """Call the Google Fonts download/list API and return the gstatic.com URL."""
    api = f"https://fonts.google.com/download/list?family={urllib.request.quote(family)}"
    req = urllib.request.Request(api, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode()
    # Response starts with ")]}'\n" — strip that before JSON parsing
    payload = json.loads(raw.lstrip(")]}'\n"))
    refs = payload["manifest"]["fileRefs"]
    for ref in refs:
        if ref["filename"] == manifest_filename:
            return ref["url"]
    available = [r["filename"] for r in refs]
    raise RuntimeError(f"{manifest_filename!r} not found. Available: {available}")


def download(url: str, dest: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    with open(dest, "wb") as f:
        f.write(data)
    print(f"  saved ({len(data):,} bytes)")


def main() -> None:
    os.makedirs(FONTS_DIR, exist_ok=True)
    for family, manifest_name, local_name in DOWNLOADS:
        dest = os.path.join(FONTS_DIR, local_name)
        if os.path.exists(dest):
            print(f"  {local_name} already exists, skipping")
            continue
        print(f"Fetching {local_name}...")
        try:
            url = get_gstatic_url(family, manifest_name)
            download(url, dest)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            sys.exit(1)
    print("Done. Fonts saved to function-app/fonts/")


if __name__ == "__main__":
    main()
