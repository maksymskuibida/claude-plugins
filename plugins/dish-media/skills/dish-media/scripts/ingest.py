#!/usr/bin/env python3
"""Ingest originals into working sRGB JPEGs plus a manifest.

    ingest.py --in raw/photos --out work/photos --manifest work/manifest.csv
              [--video-in raw/video] [--long-edge 2400] [--qa qa/]

HEIC/HEIF and any file carrying a non-sRGB ICC profile go through `sips`
(macOS) or pillow-heif/ImageCms elsewhere, so every working file is sRGB.
EXIF orientation is applied and dropped. Originals are never written to.
Videos are listed in the manifest (and checked for HDR) but not copied.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

MAC_SRGB = "/System/Library/ColorSync/Profiles/sRGB Profile.icc"


def profile_is_srgb(icc: bytes | None) -> bool:
    if not icc:
        return True  # untagged: assume sRGB, which is what every viewer does
    try:
        from PIL import ImageCms
        desc = ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(__import__("io").BytesIO(icc)))
    except Exception:
        return False
    return "srgb" in desc.lower()


def sips_to_srgb_jpeg(src: Path, dst: Path) -> None:
    cmd = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "100", "--matchTo", MAC_SRGB, str(src), "--out", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError(f"sips failed on {src.name}: {r.stderr.strip() or r.stdout.strip()}")


def sips_creation(src: Path) -> str:
    r = subprocess.run(["sips", "-g", "creation", str(src)], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "creation:" in line:
            val = line.split("creation:", 1)[1].strip()
            return "" if val in ("<nil>", "nil", "") else val
    return ""


def open_any(src: Path, tmpdir: Path):
    """Return (PIL image in sRGB, source-profile note). Never modifies src."""
    from PIL import Image, ImageOps
    ext = src.suffix.lower()
    have_sips = shutil.which("sips") is not None
    note = "srgb"
    if ext in (".heic", ".heif"):
        if have_sips:
            tmp = tmpdir / (src.stem + ".jpg")
            sips_to_srgb_jpeg(src, tmp)
            im = Image.open(tmp)
            note = "heic->sips->srgb"
        else:
            try:
                import pillow_heif  # type: ignore
                pillow_heif.register_heif_opener()
            except ImportError:
                raise RuntimeError("HEIC needs `sips` (macOS) or `pip install pillow-heif`")
            im = Image.open(src)
            note = "heic->pillow-heif"
    else:
        im = Image.open(src)
    im.load()
    icc = im.info.get("icc_profile")
    if not profile_is_srgb(icc):
        if have_sips and ext not in (".heic", ".heif"):
            tmp = tmpdir / (src.stem + ".sips.jpg")
            sips_to_srgb_jpeg(src, tmp)
            im = Image.open(tmp)
            im.load()
            note = "icc->sips->srgb"
        else:
            from PIL import ImageCms
            src_prof = ImageCms.ImageCmsProfile(__import__("io").BytesIO(icc))
            im = ImageCms.profileToProfile(im.convert("RGB"), src_prof, ImageCms.createProfile("sRGB"))
            note = "icc->lcms->srgb"
    im = ImageOps.exif_transpose(im)
    return im, note


def capture_time(im, src: Path) -> str:
    try:
        exif = im.getexif()
        val = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if val:
            return str(val)
    except Exception:
        pass
    if shutil.which("sips") and src.suffix.lower() in (".heic", ".heif"):
        return sips_creation(src)
    return ""


def probe_video(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,r_frame_rate,color_transfer,color_primaries,pix_fmt:format=duration",
        "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        return {}
    d = json.loads(r.stdout)
    s = (d.get("streams") or [{}])[0]
    num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {
        "width": s.get("width"), "height": s.get("height"), "fps": round(fps, 3),
        "duration": round(float(d.get("format", {}).get("duration") or 0), 3),
        "transfer": s.get("color_transfer") or "", "primaries": s.get("color_primaries") or "",
        "pix_fmt": s.get("pix_fmt") or "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, help="folder of originals (HEIC/JPEG/PNG)")
    ap.add_argument("--out", required=True, help="folder for working sRGB JPEGs")
    ap.add_argument("--manifest", required=True, help="CSV to write")
    ap.add_argument("--video-in", help="folder of original clips to list in the manifest")
    ap.add_argument("--long-edge", type=int, default=2400)
    ap.add_argument("--qa", help="qa folder for flags.json")
    args = ap.parse_args()

    from PIL import Image
    src_dir = Path(args.inp)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    photos = C.list_images(src_dir)
    videos = C.list_videos(args.video_in) if args.video_in else []
    if not photos and not videos:
        C.die(f"nothing to ingest in {src_dir}")

    rows, flags, used = [], [], {}
    with tempfile.TemporaryDirectory(prefix="dish-media-ingest-") as td:
        tmpdir = Path(td)
        for src in photos:
            stem = src.stem
            if stem in used:
                stem = f"{src.stem}-{src.suffix.lower().lstrip('.')}"
            used[stem] = src
            dst = out_dir / f"{stem}.jpg"
            try:
                im, note = open_any(src, tmpdir)
            except Exception as e:  # unreadable file: flag, keep going
                C.log(f"  ! {src.name}: {e}")
                flags.append({"file": src.name, "code": "unreadable", "severity": "error", "detail": str(e)})
                rows.append({"work_file": "", "original_file": src.name, "kind": "photo", "captured_at": "",
                             "orig_width": "", "orig_height": "", "work_width": "", "work_height": "", "source_profile": "error"})
                continue
            ow, oh = im.size
            when = capture_time(im, src)
            im = im.convert("RGB")
            scale = args.long_edge / max(ow, oh)
            if scale < 1.0:
                im = im.resize((max(1, round(ow * scale)), max(1, round(oh * scale))), Image.Resampling.LANCZOS)
            arr = __import__("numpy").asarray(im)
            C.save_jpeg(dst, arr, quality=95, subsampling=0)
            if max(ow, oh) < 1.2 * args.long_edge:
                flags.append({"file": dst.name, "code": "small_source", "severity": "info",
                              "detail": f"original is only {ow}x{oh}"})
            if not when:
                flags.append({"file": dst.name, "code": "no_capture_time", "severity": "info", "detail": "no EXIF date"})
            rows.append({"work_file": dst.name, "original_file": src.name, "kind": "photo", "captured_at": when,
                         "orig_width": ow, "orig_height": oh, "work_width": im.size[0], "work_height": im.size[1],
                         "source_profile": note})
            C.log(f"  {src.name} -> {dst.name} ({ow}x{oh} -> {im.size[0]}x{im.size[1]}, {note}, {when or 'no date'})")

    for v in videos:
        info = probe_video(v)
        transfer = info.get("transfer", "")
        if transfer in ("arib-std-b67", "smpte2084"):
            flags.append({"file": v.name, "code": "hdr_source", "severity": "warn",
                          "detail": f"clip is HDR ({transfer}); tonemap_hdr.sh it into work/sdr/ before video_grade.py, and turn HDR Video off on the phone"})
        rows.append({"work_file": "", "original_file": v.name, "kind": "video", "captured_at": "",
                     "orig_width": info.get("width", ""), "orig_height": info.get("height", ""),
                     "work_width": "", "work_height": "",
                     "source_profile": f"{info.get('transfer','?')}/{info.get('pix_fmt','?')} {info.get('duration','?')}s @{info.get('fps','?')}fps"})
        C.log(f"  {v.name}: {info.get('width')}x{info.get('height')} {info.get('duration')}s {transfer or 'untagged'}")

    mpath = Path(args.manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    with mpath.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["work_file", "original_file", "kind", "captured_at", "orig_width", "orig_height",
                                           "work_width", "work_height", "source_profile"])
        w.writeheader()
        w.writerows(rows)
    if args.qa:
        C.write_flags(args.qa, "ingest", [r["original_file"] for r in rows] + [r["work_file"] for r in rows if r["work_file"]], flags)
    n_ok = sum(1 for r in rows if r["kind"] == "photo" and r["work_file"])
    C.log(f"ingested {n_ok}/{len(photos)} photos, listed {len(videos)} clips -> {mpath}")
    return 0 if n_ok == len(photos) else 1


if __name__ == "__main__":
    sys.exit(main())
