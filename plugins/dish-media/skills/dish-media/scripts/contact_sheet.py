#!/usr/bin/env python3
"""Contact sheets Claude can judge in one look (long edge <= 1568 px).

    contact_sheet.py --in out/photos --out qa/sheets --reference dish-0412
    contact_sheet.py --in qa/variants --out qa/variants-sheet.jpg --variants
    contact_sheet.py --in out/photos/a.jpg out/photos/b.jpg --before work/photos --out qa/pairs.jpg

Default mode: pages of up to 20 thumbnails (5 x 4) on a neutral grey, the
reference dish in the first cell of every page, and under each thumbnail
the file name plus median luminance and clipping. --variants lays out the
six calibration renders of one dish side by side with their settings.
--before DIR puts the same-named file from DIR (the working copy, as shot)
left of each thumbnail: before | after pairs, 10 files a page.
Stats come from --stats (grade_stats.json) when present, else are computed.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

GREY = (128, 128, 128)
BAND = (38, 38, 38)
TEXT = (235, 235, 235)
DIM = (170, 170, 170)


def font(size: int):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def fit_text(draw, text: str, f, max_w: int) -> str:
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text + "…"


def thumb(rgb8: np.ndarray, w: int, h: int):
    from PIL import Image
    im = Image.fromarray(rgb8)
    im.thumbnail((w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), GREY)
    canvas.paste(im, ((w - im.size[0]) // 2, (h - im.size[1]) // 2))
    return canvas


def render_sheet(items: list[dict], cols: int, max_edge: int, header: str, aspect: float, out: Path) -> tuple[int, int]:
    from PIL import Image, ImageDraw
    gutter, label_h, header_h = 8, 34, 24
    cell_w = (max_edge - gutter * (cols + 1)) // cols
    thumb_h = int(round(cell_w / aspect))
    rows = max(1, math.ceil(len(items) / cols))
    W = max_edge
    H = header_h + rows * (thumb_h + label_h + gutter) + gutter
    sheet = Image.new("RGB", (W, H), GREY)
    d = ImageDraw.Draw(sheet)
    f_small, f_head = font(13), font(14)
    d.rectangle([0, 0, W, header_h], fill=BAND)
    d.text((gutter, 5), header, fill=TEXT, font=f_head)
    for i, it in enumerate(items):
        r, c = divmod(i, cols)
        x = gutter + c * (cell_w + gutter)
        y = header_h + gutter + r * (thumb_h + label_h + gutter)
        sheet.paste(thumb(it["rgb8"], cell_w, thumb_h), (x, y))
        d.rectangle([x, y + thumb_h, x + cell_w, y + thumb_h + label_h], fill=BAND)
        d.text((x + 4, y + thumb_h + 2), fit_text(d, it["title"], f_small, cell_w - 8), fill=TEXT, font=f_small)
        d.text((x + 4, y + thumb_h + 18), fit_text(d, it["sub"], f_small, cell_w - 8), fill=DIM, font=f_small)
        if it.get("ref"):
            d.rectangle([x, y, x + cell_w - 1, y + thumb_h - 1], outline=(255, 210, 0), width=3)
    if max(W, H) > max_edge:  # portrait cells could push the height over
        s = max_edge / max(W, H)
        sheet = sheet.resize((round(W * s), round(H * s)), Image.Resampling.LANCZOS)
    C.save_jpeg(out, np.asarray(sheet), quality=85)
    return sheet.size


def stats_for(path: Path, rgb8: np.ndarray, table: dict) -> dict:
    st = table.get(path.stem)
    if st and "y_median" in st:
        return st
    return C.image_stats(rgb8)


def sub_line(st: dict) -> str:
    return f"Y50 {st['y_median']:.2f}  p99 {st['y_p99']:.2f}  clip {st['clip_high_pct']:.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True, nargs="+", help="folders of JPEGs and/or single files")
    ap.add_argument("--out", required=True, help="a .jpg for one sheet, or a folder for paginated sheets")
    ap.add_argument("--reference", help="file (or stem) to show first on every page")
    ap.add_argument("--stats", help="grade_stats.json / cutout_stats.json for the labels")
    ap.add_argument("--per-sheet", type=int, help="files per page (default 20, or 10 with --before)")
    ap.add_argument("--cols", type=int, help="thumbnails per row (default 5, or 4 with --before)")
    ap.add_argument("--before", help="folder holding the as-shot version of each file (work/photos): render before | after pairs")
    ap.add_argument("--max-edge", type=int, default=1568)
    ap.add_argument("--aspect", type=float, default=4 / 3, help="thumbnail cell aspect (w/h)")
    ap.add_argument("--variants", action="store_true", help="lay out grade.py --variants renders, 3 per row")
    ap.add_argument("--title", default="", help="text for the header")
    args = ap.parse_args()

    files = [f for src in args.inp for f in C.list_images(src)]
    if not files:
        C.die(f"no images in {' '.join(args.inp)}")
    before = {q.stem: q for q in C.list_images(args.before)} if args.before else None
    cols = args.cols or (4 if before is not None else 5)
    per_sheet = args.per_sheet or (10 if before is not None else 20)
    table = {}
    if args.stats and Path(args.stats).is_file():
        table = json.loads(Path(args.stats).read_text())

    ref = None
    if args.reference:
        cand = Path(args.reference)
        ref = cand if cand.is_file() else next((f for f in files if f.stem == C.stem_of(args.reference)), None)
        if ref is None:
            C.die(f"reference {args.reference} not found")
        files = [f for f in files if f.resolve() != ref.resolve()]

    if args.variants:
        items = []
        for f in files:
            rgb8 = C.load_rgb8(f)
            st = C.image_stats(rgb8)
            tag = f.stem.split("__", 1)[1] if "__" in f.stem else f.stem
            items.append({"rgb8": rgb8, "title": tag.replace("_", "  "), "sub": sub_line(st)})
        out = Path(args.out)
        if out.suffix.lower() not in (".jpg", ".jpeg"):
            out = out / "variants.jpg"
        size = render_sheet(items, 3, args.max_edge, args.title or f"calibration variants: {files[0].stem.split('__')[0]}  (EV / contrast)", args.aspect, out)
        C.log(f"{out} {size[0]}x{size[1]} ({len(items)} variants)")
        return 0

    per = per_sheet - (1 if ref else 0)
    pages = [files[i:i + per] for i in range(0, len(files), per)] or [[]]
    out = Path(args.out)
    single = out.suffix.lower() in (".jpg", ".jpeg")
    if single:
        pages = pages[:1]
    else:
        out.mkdir(parents=True, exist_ok=True)
    ref_item = None
    if ref:
        rgb8 = C.load_rgb8(ref)
        ref_item = {"rgb8": rgb8, "title": f"REF {ref.stem}", "sub": sub_line(stats_for(ref, rgb8, table)), "ref": True}
    for n, page in enumerate(pages, 1):
        items = [ref_item] if ref_item else []
        for f in page:
            rgb8 = C.load_rgb8(f)
            if before is not None:
                b = before.get(f.stem)
                brgb = C.load_rgb8(b) if b else np.full((3, 4, 3), GREY, dtype=np.uint8)
                items.append({"rgb8": brgb, "title": f"before  {f.stem}", "sub": sub_line(C.image_stats(brgb)) if b else "(no file in --before)"})
            items.append({"rgb8": rgb8, "title": f.stem, "sub": sub_line(stats_for(f, rgb8, table))})
        dst = out if single else out / f"sheet-{n:02d}.jpg"
        head = args.title or (f"before | after, {len(page)} dishes  grey = neutral" if before is not None
                              else f"sheet {n}/{len(pages)}  {len(page)} dishes" + ("  + reference (yellow frame)" if ref else "") + "  grey = neutral")
        size = render_sheet(items, cols, args.max_edge, head, args.aspect, dst)
        C.log(f"{dst} {size[0]}x{size[1]}: {page[0].stem if page else '-'} .. {page[-1].stem if page else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
