#!/usr/bin/env python3
"""Download the openly licensed real-world files the evals run on.

    fetch_data.py [--out evals/results/data/downloads]

Everything comes from Wikimedia Commons (licences in data-sources.md).
Nothing here is committed; the folder is git-ignored. Re-run to refetch.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = "dish-media-skill-eval/1.0 (https://github.com/maksymskuibida/claude-plugins; eval data fetch)"

HK = ("HK 上環 Sheung Wan 急庇利街 Clevely Street 鋿晶館 SC Cuisine Chinese Seafood Restaurant food "
      "乳豬全體 baby Suckling pig meat full dish siu yuk October 2025 N13P {:02d}.jpg")
PHOTOS = {  # local name -> Commons file name (without "File:")
    **{f"hk/N13P_{i:02d}.jpg": HK.format(i) for i in range(1, 12)},
    "mixed/khmer_01.jpg": "Khmer Family Restaurant, 2018 (01).jpg",
    "mixed/khmer_02.jpg": "Khmer Family Restaurant, 2018 (02).jpg",
    "mixed/khmer_03.jpg": "Khmer Family Restaurant, 2018 (03).jpg",
    "mixed/khmer_04.jpg": "Khmer Family Restaurant, 2018 (04).jpg",
    "mixed/turkey_starter.jpg": "Starter dish in Turkey.jpg",
    "mixed/lazarus_grilled_fish.jpg": "Grilled fish, Lazaru's Restaurant, New Urbane Hotel, 2025 (01).jpg",
    "mixed/meatloaf.jpg": "Dish of meatloaf served on a white plate with sauce and herbs in a restaurant setting.jpg",
}
VIDEOS = {  # local name -> (Commons file name, transcode key)
    "video/moon_turntable.webm": ("Moon Essentials- Turntable (SVS5319).webm", "1080p.vp9.webm"),
    "video/swift_360.webm": ("Swift Spacecraft Animations- 2025 (SVS14786 - SWIFT 360 4k 60fps, cropped).webm", "1080p.vp9.webm"),
    "video/al92_rotation.webm": ("Exposed Central Rotation of AL92 (CIRA 2024-06-21 - nolabels).webm", "1080p.vp9.webm"),
}


def commons_url(name: str, transcode: str | None = None) -> str:
    n = name.replace(" ", "_")
    m = hashlib.md5(n.encode("utf-8")).hexdigest()
    q = urllib.parse.quote(n)
    if transcode:
        return f"https://upload.wikimedia.org/wikipedia/commons/transcoded/{m[0]}/{m[:2]}/{q}/{q}.{transcode}"
    return f"https://upload.wikimedia.org/wikipedia/commons/{m[0]}/{m[:2]}/{q}"


def fetch(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_size > 0:
        print(f"  have {dst.name}")
        return
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as fh:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    print(f"  {dst.name}: {dst.stat().st_size // 1024} KB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results" / "data" / "downloads"))
    args = ap.parse_args()
    out = Path(args.out)
    for local, name in PHOTOS.items():
        fetch(commons_url(name), out / local)
    for local, (name, key) in VIDEOS.items():
        fetch(commons_url(name, key), out / local)
    print(f"done -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
