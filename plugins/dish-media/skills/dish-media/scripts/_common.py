"""Shared code for the dish-media scripts. Not a CLI.

Everything here is deterministic: the same pixels and the same session
parameters give the same bytes. Nothing writes timestamps into outputs.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
from pathlib import Path

import numpy as np

SCRIPT_VERSION = "1.0.0"
SESSION_SCHEMA = "dish-media.session/1"

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}

# Delivery encoding. One place, so photos, cutouts and sheets agree.
JPEG_QUALITY = 90
JPEG_SUBSAMPLING = 2  # 4:2:0, what every camera and tablet decoder expects

# Default look. calibrate.py writes these into session.json; grade.py and
# video_grade.py read them back. Change them there, not here.
DEFAULT_LOOK = {
    "contrast": {"strength": 2.5, "midpoint": 0.48},
    "shadows": 0.10,
    "highlights": 0.15,
    "saturation": 1.05,
    "crop_ratio": "4:3",
    "straighten_deg": 0.0,
    "output_long_edge": 1600,
    "background": None,
    "shadow": {"blur": 40, "offset": 14, "opacity": 0.35},
}

# Flat override keys that map onto nested session values.
OVERRIDE_KEYS = {
    "exposure", "exposure_ev", "wb_gains", "contrast_strength", "contrast_midpoint",
    "shadows", "highlights", "saturation", "crop_ratio", "straighten_deg",
    "crop_center", "crop_scale", "crop_box", "output_long_edge", "background", "shadow",
    # video-only
    "start", "duration", "loop", "reverse",
}


def die(msg: str, code: int = 2) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


# --------------------------------------------------------------------------
# Colour math (numpy, float32 in [0, 1] unless stated)
# --------------------------------------------------------------------------

def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def linear_to_srgb(y: np.ndarray) -> np.ndarray:
    y = np.clip(np.asarray(y, dtype=np.float32), 0.0, 1.0)
    return np.where(y <= 0.0031308, y * 12.92, 1.055 * np.power(y, 1 / 2.4) - 0.055).astype(np.float32)


LUMA_709 = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def luminance(lin_rgb: np.ndarray) -> np.ndarray:
    return lin_rgb @ LUMA_709


# OKLab (Björn Ottosson, 2020). Linear sRGB in, Lab out.
_M1 = np.array([
    [0.4122214708, 0.5363325363, 0.0514459929],
    [0.2119034982, 0.6806995451, 0.1073969566],
    [0.0883024619, 0.2817188376, 0.6299787005],
], dtype=np.float32)
_M2 = np.array([
    [0.2104542553, 0.7936177850, -0.0040720468],
    [1.9779984951, -2.4285922050, 0.4505937099],
    [0.0259040371, 0.7827717662, -0.8086757660],
], dtype=np.float32)
_M1_INV = np.linalg.inv(_M1.astype(np.float64)).astype(np.float32)
_M2_INV = np.linalg.inv(_M2.astype(np.float64)).astype(np.float32)


def linear_to_oklab(lin: np.ndarray) -> np.ndarray:
    lms = lin @ _M1.T
    lms = np.cbrt(np.clip(lms, 0.0, None))
    return lms @ _M2.T


def oklab_to_linear(lab: np.ndarray) -> np.ndarray:
    lms = lab @ _M2_INV.T
    lms = lms * lms * lms
    return lms @ _M1_INV.T


# --------------------------------------------------------------------------
# Tone curve, defined on the sRGB-encoded [0, 1] domain
# --------------------------------------------------------------------------

_SHADOW_PEAK = 0.25 * (0.75 ** 3)  # peak of v(1-v)^3, at v = 0.25
_KNEE = 0.55


def shadows_lift(v: np.ndarray, amount: float) -> np.ndarray:
    """Lift (amount > 0) or deepen (amount < 0) the shadows.

    amount 1.0 raises the 0.25 level by 0.20 and leaves black and white
    untouched. Monotonic for |amount| <= 1.
    """
    if amount == 0:
        return v
    w = v * (1.0 - v) ** 3 / _SHADOW_PEAK
    return v + amount * 0.20 * w


def highlights_knee(v: np.ndarray, amount: float) -> np.ndarray:
    """Soft knee above 0.55 that keeps bright plates off the clip.

    amount 0 is identity; amount 1 is the full knee (white lands at 0.83).
    """
    if amount == 0:
        return v
    k = _KNEE
    over = np.clip(v - k, 0.0, None)
    knee = k + (1.0 - k) * (1.0 - np.exp(-over / (1.0 - k)))
    return np.where(v > k, v + amount * (knee - v), v)


def sigmoid_contrast(v: np.ndarray, strength: float, midpoint: float) -> np.ndarray:
    """Normalised logistic S-curve. strength 0 is identity; 6 is strong."""
    if strength < 1e-6:
        return v
    g0 = 1.0 / (1.0 + math.exp(strength * midpoint))
    g1 = 1.0 / (1.0 + math.exp(-strength * (1.0 - midpoint)))
    g = 1.0 / (1.0 + np.exp(-strength * (v - midpoint)))
    return (g - g0) / (g1 - g0)


def tone_curve(v, look: dict):
    """Shadows lift, then highlight knee, then sigmoid contrast."""
    v = np.asarray(v, dtype=np.float32)
    c = look["contrast"]
    v = shadows_lift(v, float(look["shadows"]))
    v = highlights_knee(v, float(look["highlights"]))
    v = sigmoid_contrast(v, float(c["strength"]), float(c["midpoint"]))
    return np.clip(v, 0.0, 1.0).astype(np.float32)


def invert_tone(target: float, look: dict, iters: int = 60) -> float:
    """Encoded input value whose tone_curve output is `target` (bisection)."""
    lo, hi = 0.0, 1.0
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if float(tone_curve(np.float32(mid), look)) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------------------------
# The look: linear sRGB in, linear sRGB out
# --------------------------------------------------------------------------

def apply_look_linear(lin: np.ndarray, look: dict) -> np.ndarray:
    """WB gains, exposure, tone (ratio-preserving on luminance), saturation."""
    gains = np.asarray(look["wb_gains"], dtype=np.float32)
    lin = lin * gains * np.float32(look["exposure"])
    y = luminance(lin)
    y_c = np.clip(y, 1e-6, 1.0)
    y_out = srgb_to_linear(tone_curve(linear_to_srgb(y_c), look))
    scale = (y_out / y_c)[..., None]
    lin = lin * scale
    sat = float(look["saturation"])
    if abs(sat - 1.0) > 1e-6:
        lab = linear_to_oklab(np.clip(lin, 0.0, 1.0))
        lab[..., 1:] *= sat
        lin = oklab_to_linear(lab)
    return np.clip(lin, 0.0, 1.0).astype(np.float32)


def apply_look_rgb8(rgb8: np.ndarray, look: dict) -> np.ndarray:
    """uint8 sRGB in, uint8 sRGB out (no geometry)."""
    lin = srgb_to_linear(rgb8.astype(np.float32) / 255.0)
    out = linear_to_srgb(apply_look_linear(lin, look))
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def write_cube_lut(path: Path, look: dict, size: int = 33, title: str = "dish-media") -> None:
    """3D LUT of the look (no geometry) in Adobe .cube format, red fastest."""
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    out = linear_to_srgb(apply_look_linear(srgb_to_linear(rgb), look))
    lines = [f"TITLE \"{title}\"", f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0.0 0.0 0.0", "DOMAIN_MAX 1.0 1.0 1.0"]
    lines += [f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}" for v in out]
    Path(path).write_text("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# Session files
# --------------------------------------------------------------------------

def default_session() -> dict:
    s = {"schema": SESSION_SCHEMA, "provenance": {}, "wb_gains": [1.0, 1.0, 1.0], "exposure": 1.0}
    s.update(json.loads(json.dumps(DEFAULT_LOOK)))
    s["exclude"] = []
    s["overrides"] = {}
    return s


def load_session(path) -> dict:
    p = Path(path)
    if not p.is_file():
        die(f"session file not found: {p}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        die(f"session file is not valid JSON: {p}: {e}")
    if data.get("schema") != SESSION_SCHEMA:
        die(f"session file {p} has schema {data.get('schema')!r}, expected {SESSION_SCHEMA!r}")
    base = default_session()
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict) and k != "overrides":
            base[k].update(v)
        else:
            base[k] = v
    if len(base["wb_gains"]) != 3:
        die("wb_gains must have three values")
    return base


def save_session(path, session: dict) -> None:
    Path(path).write_text(json.dumps(session, indent=2, sort_keys=False) + "\n")


def stem_of(name) -> str:
    return Path(str(name)).stem


def params_for(session: dict, filename) -> dict:
    """Effective parameters for one file: the session plus its override."""
    p = json.loads(json.dumps({k: v for k, v in session.items() if k not in ("overrides", "provenance", "schema", "exclude")}))
    p["crop_center"] = [0.5, 0.5]
    p["crop_scale"] = 1.0
    stem = stem_of(filename)
    ov = None
    for key, val in (session.get("overrides") or {}).items():
        if key == stem or stem_of(key) == stem:
            ov = val
            break
    if not ov:
        return p
    for k, v in ov.items():
        if k not in OVERRIDE_KEYS:
            die(f"override for {stem}: unknown key {k!r} (allowed: {', '.join(sorted(OVERRIDE_KEYS))})")
        if k == "exposure_ev":
            p["exposure"] = float(p["exposure"]) * (2.0 ** float(v))
        elif k == "contrast_strength":
            p["contrast"]["strength"] = float(v)
        elif k == "contrast_midpoint":
            p["contrast"]["midpoint"] = float(v)
        elif k == "shadow" and isinstance(v, dict):
            p["shadow"].update(v)
        else:
            p[k] = v
    return p


def is_excluded(session: dict, filename) -> bool:
    stem = stem_of(filename)
    return any(stem_of(x) == stem for x in (session.get("exclude") or []))


def parse_ratio(text) -> float | None:
    """Crop ratio as long:short (4:3 fits landscape and portrait alike)."""
    if text in (None, "", "none", "null"):
        return None
    if isinstance(text, (int, float)):
        return float(text)
    if ":" in text:
        a, b = text.split(":", 1)
        return float(a) / float(b)
    return float(text)


def parse_colour(text) -> tuple[int, int, int]:
    t = str(text).strip().lstrip("#")
    if len(t) != 6:
        die(f"background colour must be #RRGGBB, got {text!r}")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


# --------------------------------------------------------------------------
# Image I/O
# --------------------------------------------------------------------------

_MAC_SRGB = Path("/System/Library/ColorSync/Profiles/sRGB Profile.icc")
_srgb_cache: bytes | None = None


def srgb_profile_bytes() -> bytes:
    """An sRGB ICC profile with a fixed header date, so files stay byte-stable."""
    global _srgb_cache
    if _srgb_cache is None:
        if _MAC_SRGB.is_file():
            _srgb_cache = _MAC_SRGB.read_bytes()
        else:
            from PIL import ImageCms
            b = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
            b[24:36] = bytes(12)  # dateTimeNumber in the header would otherwise change per run
            _srgb_cache = bytes(b)
    return _srgb_cache


def load_rgb8(path) -> np.ndarray:
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        return np.asarray(im.convert("RGB"))


def save_jpeg(path, rgb8: np.ndarray, quality: int = JPEG_QUALITY, subsampling: int = JPEG_SUBSAMPLING) -> None:
    from PIL import Image
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(rgb8), "RGB").save(
        str(path), "JPEG", quality=quality, subsampling=subsampling, optimize=True,
        icc_profile=srgb_profile_bytes(),
    )


def save_png(path, arr: np.ndarray) -> None:
    from PIL import Image
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.ascontiguousarray(arr)).save(str(path), "PNG", optimize=True)


def list_images(folder) -> list[Path]:
    p = Path(folder)
    if p.is_file():
        return [p]
    if not p.is_dir():
        die(f"not a file or directory: {p}")
    return sorted(q for q in p.iterdir() if q.suffix.lower() in PHOTO_EXTS and not q.name.startswith(("_", ".")))


def list_videos(folder) -> list[Path]:
    p = Path(folder)
    if p.is_file():
        return [p]
    if not p.is_dir():
        die(f"not a file or directory: {p}")
    return sorted(q for q in p.iterdir() if q.suffix.lower() in VIDEO_EXTS and not q.name.startswith(("_", ".")))


# --------------------------------------------------------------------------
# Diagnostics shared by measure, grade and the contact sheet
# --------------------------------------------------------------------------

def image_stats(rgb8: np.ndarray) -> dict:
    """Encoded-luminance percentiles, clipping and a plate-cast indicator."""
    f = rgb8.astype(np.float32) / 255.0
    y = f @ LUMA_709
    pct = np.percentile(y, [1, 5, 50, 95, 99])
    hi = rgb8 >= 254
    lo = rgb8 <= 1
    n = float(rgb8.shape[0] * rgb8.shape[1])
    bright = y > 0.6
    if bright.sum() > 100:
        lab = linear_to_oklab(srgb_to_linear(f[bright]))
        cast = [round(float(lab[:, 1].mean()), 4), round(float(lab[:, 2].mean()), 4)]
    else:
        cast = None
    return {
        "width": int(rgb8.shape[1]),
        "height": int(rgb8.shape[0]),
        "y_p1": round(float(pct[0]), 4),
        "y_p5": round(float(pct[1]), 4),
        "y_median": round(float(pct[2]), 4),
        "y_p95": round(float(pct[3]), 4),
        "y_p99": round(float(pct[4]), 4),
        "clip_high_pct": round(100.0 * float(hi.any(axis=2).sum()) / n, 3),
        "clip_low_pct": round(100.0 * float(lo.all(axis=2).sum()) / n, 3),
        "clip_high_pct_rgb": [round(100.0 * float(hi[..., i].sum()) / n, 3) for i in range(3)],
        "bright_ab": cast,
    }


# --------------------------------------------------------------------------
# QA flags: qa/flags.json, merged per step so re-runs are idempotent
# --------------------------------------------------------------------------

def flags_path(qa_dir) -> Path:
    return Path(qa_dir) / "flags.json"


def load_flags(qa_dir) -> list[dict]:
    p = flags_path(qa_dir)
    if not p.is_file():
        return []
    try:
        return list(json.loads(p.read_text()).get("flags", []))
    except (json.JSONDecodeError, AttributeError):
        die(f"{p} is not a valid flags file")


def write_flags(qa_dir, step: str, processed_files: list[str], new_flags: list[dict]) -> None:
    """Replace this step's flags for the processed files, keep everything else."""
    if qa_dir is None:
        return
    Path(qa_dir).mkdir(parents=True, exist_ok=True)
    processed = {stem_of(f) for f in processed_files}
    kept = [f for f in load_flags(qa_dir) if not (f.get("step") == step and stem_of(f.get("file", "")) in processed)]
    for f in new_flags:
        f.setdefault("step", step)
        f.setdefault("severity", "warn")
    merged = sorted(kept + new_flags, key=lambda f: (f.get("step", ""), f.get("file", ""), f.get("code", "")))
    flags_path(qa_dir).write_text(json.dumps({"schema": "dish-media.flags/1", "flags": merged}, indent=2) + "\n")


def update_stats(path, step_stats: dict) -> None:
    """Merge per-file stats into a JSON map keyed by stem."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text())
        except json.JSONDecodeError:
            data = {}
    data.update(step_stats)
    p.write_text(json.dumps(dict(sorted(data.items())), indent=2) + "\n")


def run(cmd: list[str], **kw):
    """subprocess.run with the command echoed to stderr."""
    import subprocess
    log("$ " + " ".join(_quote(c) for c in cmd))
    return subprocess.run(cmd, **kw)


def _quote(s: str) -> str:
    return s if all(ch.isalnum() or ch in "-_./:=+,@%" for ch in s) else "'" + s.replace("'", "'\\''") + "'"


def which(name: str) -> str | None:
    import shutil
    return shutil.which(name)
