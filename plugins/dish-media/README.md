# dish-media

Turns a phone shoot of restaurant dishes and drinks — two photos and one turntable clip
per item, 50–200 items per menu — into what the tablets on the tables show: sRGB JPEGs and
seamless SDR H.264 loops, with one measured look for the whole menu and not one generated
pixel.

The edits are the ones a careful editor would make and nothing else: exposure, white
balance, contrast, shadows and highlights, modest saturation, crop and straighten, an
optional plain background with a soft contact shadow, and a loop cut from the real
rotation. Consistency across the menu matters more than perfection on one dish, so the
pipeline measures what can be measured (a grey card gives white balance and exposure by
arithmetic), has Claude judge one contact sheet of reference dishes to choose **one**
parameter set, applies that set to every file deterministically, and uses further
contact sheets and frame grids only to catch outliers, which get per-file overrides.

## Install

```bash
/plugin marketplace add https://github.com/maksymskuibida/claude-plugins
/plugin install dish-media@mskuibida-tooling
```

Then, once per machine, from a shell:

```bash
bash ~/.claude/plugins/marketplaces/mskuibida-tooling/plugins/dish-media/skills/dish-media/scripts/setup.sh
```

It checks ffmpeg/ffprobe (Homebrew), Pillow, numpy, OpenCV and rembg, prints exactly what
it would install into the active `python3`, and asks before installing. The cutout step
needs the `isnet-general-use` rembg model, about 170 MB, fetched once.

## Capture prerequisites (the shoot decides 80% of the result)

1. **Turntable + soft side light**, the same place, height and framing for every dish; one
   grey-card frame at the start of every session, under that light.
2. **HDR video off** in the camera app (iPhone: Settings → Camera → Record Video → HDR
   Video; Android: the HDR10+ or HDR video switch in the camera settings), and JPEG or
   "most compatible" stills if offered (or let the ingest convert HEIC). HDR video and
   wide-gamut (Display P3) stills are the two things that look wrong on cheap Android tablets.
3. **One colour style, never changed** (iPhone: one Photographic Style with Preserve
   Settings → Creative Controls on; Android: one picture profile, no scene optimiser);
   AE/AF/WB locked on the card before each dish. Blackmagic Camera (iOS and Android) set
   to Rec.709 does all of this explicitly.
4. **4K30, 1× lens**, two photos per dish (tablet angle and top-down) and one rotation of
   8–12 s with two seconds of handle on each end.
5. Wipe the plate, centre it, keep cutlery and napkins out unless they are the dish.

## Quick start

A session, as Claude drives it through the skill (`S` is the skill's `scripts/` directory,
`P` the project folder):

```
> /dish-media calibrate

  python3 $S/ingest.py --in $P/raw/photos --out $P/work/photos --manifest $P/work/manifest.csv --video-in $P/raw/video --qa $P/qa
    ingested 84/84 photos, listed 42 clips
  python3 $S/measure.py $P/work/photos/IMG_0001.jpg --auto --out $P/work/measure/card.json --preview $P/qa/card-box.jpg
    card mean sRGB [131.2, 121.0, 104.7]  R/G 1.19  B/G 0.72
  [Claude reads qa/card-box.jpg: the box is on the card]
  python3 $S/calibrate.py --measure $P/work/measure/card.json --out $P/work/session.json
    wb_gains [0.84, 1.0, 1.39]  exposure x1.31 (+0.39 EV)  predicted card [122.4, 122.4, 122.4]
  python3 $S/grade.py --session $P/work/session.json --variants $P/work/photos/IMG_0012.jpg --out $P/qa/variants
  python3 $S/contact_sheet.py --in $P/qa/variants --out $P/qa/variants.jpg --variants
  [Claude reads qa/variants.jpg once: the plate clips at +0.3, the dark sauce loses texture at c4.5]
  python3 $S/calibrate.py --from $P/work/session.json --measure $P/work/measure/card.json --out $P/work/session.json --contrast-strength 3.0

> /dish-media grade
  python3 $S/grade.py --session $P/work/session.json --in $P/work/photos --out $P/out/photos --qa $P/qa --workers 4
    graded 84 file(s); 3 flag(s)

> /dish-media sheet
  python3 $S/contact_sheet.py --in $P/out/photos --out $P/qa/sheets --reference IMG_0012 --stats $P/qa/grade_stats.json
  [Claude reads 5 sheets: IMG_0031 is a stop too bright, IMG_0044's bowl is crushed, IMG_0050 sits off-centre]
  python3 $S/calibrate.py --from $P/work/session.json --out $P/work/session.json \
      --override IMG_0031 exposure_ev=-0.3 --override IMG_0044 shadows=0.25 --override IMG_0050 crop_center=0.55,0.5
  python3 $S/grade.py --session $P/work/session.json --in $P/work/photos/IMG_0031.jpg --out $P/out/photos --qa $P/qa   (and the other two)

> /dish-media loops
  python3 $S/video_grade.py --session $P/work/session.json --in $P/raw/video --out $P/out/loops --qa $P/qa
    IMG_0013.mov: revolution start 1.0s duration 8.533s (256 frames)  period 8.533s score 0.97
    ...
  python3 $S/frame_grid.py $P/out/loops/IMG_0013.mp4 --out $P/qa/grids/IMG_0013.jpg --qa $P/qa   (for each)
  [Claude reads the grids: one seam flagged on a symmetric plate → --loop pingpong for that clip]

> /dish-media qa
  python3 $S/qa_report.py --project $P     → qa/report.md: 0 error, 2 warn, 84 info

> /dish-media deliver
  python3 $S/deliver.py --project $P       → deliver/photos, deliver/loops, deliver/manifest.csv
```

## Rules the skill enforces

- **Never generative.** Colour and light are arithmetic on the real pixels; the cutout is a
  segmentation mask; the loop is a cut of the real rotation.
- **Grey card first**; white balance and exposure are derived, not eyeballed. No card:
  measure a white plate rim instead (`--auto --white`, `--target-luminance 0.75`), per
  photo when the sources differ (`--file-measure`).
- **One parameter set per session** in `work/session.json`; differences between dishes are
  per-file overrides in that file.
- **Claude judges contact sheets and frame grids only**, never single files, and only the
  exported sRGB JPEG or MP4 frames — never HEIC, HDR or wide-gamut (Display P3) originals.
- **Only light and colour.** Never "improve" the food.
- **Originals untouched**; `raw/` is read-only to every script.
- **Deterministic**: same inputs and same session → byte-identical outputs.

## What is in the box

| Script | Does |
|---|---|
| `setup.sh` | toolchain check; installs only after printing the plan and asking |
| `ingest.py` | HEIC/JPEG/PNG → 2400 px sRGB JPEG, orientation normalised, manifest CSV; lists clips and flags HDR ones |
| `measure.py` | grey-card region (explicit, `--auto`, or `--auto --white` for a plate rim when there is no card) → mean RGB, R/G, B/G, luminance percentiles, clipping; preview JPEG that turns red when the patch is not flat |
| `calibrate.py` | measurement + targets → `session.json` (gains, exposure solved through the tone curve, look, per-file overrides and measurements, exclude list) |
| `grade.py` | applies the session: linear light, gains, exposure, tone curve, OKLab saturation, straighten, crop, resize, JPEG q90 + sRGB profile; `--variants` |
| `cutout.py` | rembg matte → erode/feather → composite on the background with a contact shadow; confidence score and flags |
| `contact_sheet.py` | 5 × 4 thumbnails with diagnostics, reference first, ≤ 1568 px; `--variants` mode |
| `loop_period.py` | revolution period by normalised cross-correlation against the reference frame, turning direction from optical flow |
| `video_grade.py`, `video_loop.sh` | trim, LUT from the session, optional vidstab, `revolution` or `pingpong` loop, 1080p H.264 yuv420p CRF 20 faststart |
| `tonemap_hdr.sh` | HLG/PQ → SDR bt709 (zscale linear → Mobius → bt709; `TONEMAP=hable` to override) for clips shot in HDR by mistake |
| `frame_grid.py` | 3 × 3 frames of an exported loop with a seam score, ≤ 1568 px |
| `qa_report.py` | merges every flag plus batch outliers, direction mismatches and missing outputs into `qa/report.md` |
| `deliver.py` | assembles `deliver/` from cutouts or photos and loops; refuses while errors remain |

All Python 3, Pillow, numpy and OpenCV; rembg only for `cutout.py`; ffmpeg for video.
Every script is runnable from any directory and takes explicit paths.

## Tests

```bash
bash plugins/dish-media/skills/dish-media/scripts/tests/run_tests.sh
```

Generates a synthetic shoot in a temp dir (white plates with coloured food and a grey card
under a warm cast at −1 EV; a patterned disc turning exactly once every 8 s at 30 fps for
12 s; an HLG-tagged clip), runs every script twice and asserts: the card comes out neutral
within ±2/255 and at the target luminance within ±3%, `loop_period.py` finds 8.0 ± 0.1 s,
both loop modes have first/last-frame similarity above 0.95, sheets and grids fit in
1568 px, the HDR clip is refused and then tone-mapped and looped, the outlier dish is
flagged and reported, and the second run is byte-identical to the first. `py_compile`
runs on every script and `shellcheck` on every shell script first. rembg is used when
importable and the cutout checks are skipped otherwise (`--skip-cutout`).

## Troubleshooting

- **HEIC does not convert** — on macOS the ingest uses `sips`; if it fails on one file the
  file is flagged `unreadable` and the run continues. Export that one from the phone's
  gallery as JPEG.
  Off macOS, `pip install pillow-heif`.
- **rembg model download** — first use fetches `isnet-general-use` (~170 MB) into
  `~/.rembg/models/`; behind a proxy set `HTTPS_PROXY`, or run `setup.sh` on a good
  connection. If the name is unknown to your rembg version it falls back to `u2net`.
- **The clip looks grey and flat** — it was shot in HDR (Dolby Vision on an iPhone, HDR10+
  or HLG on Android). The ingest
  flags it `hdr_source` and `video_grade.py` refuses it; run `tonemap_hdr.sh IN OUT` and
  grade the output. Turn HDR Video off on the phone; that is the real fix.
- **Reds and greens look neon on the tablet** — the tablet app ignores ICC profiles and
  the file was wide-gamut (Display P3, as iPhones and some Android phones write). Everything
  the pipeline writes is sRGB; if a file went straight
  from the phone to the tablet, it bypassed the pipeline.
- **`--auto` put the card box on the table or the plate** — pass `--card X,Y,W,H` in pixels
  of the working image; always look at `qa/card-box.jpg`.
- **The loop jumps at the seam** — the dish is symmetric (`period_ambiguous`), or the
  turntable was still accelerating at `--start`. Set `--start 2` or `--duration` to the
  full turn read off the grid, or use `--loop pingpong`.
- **One loop turns the other way** — `qa/report.md` says `direction_mismatch`; re-export
  that clip with `video_grade.py --reverse`.
- **A photo is not a dish (interior, table shot, the card frame)** — put it on the
  session's exclude list: `calibrate.py --from … --exclude IMG_0008`; grading, the report
  and delivery skip it.
- **Outputs changed between runs** — they do not, given the same inputs and session. Diff
  `work/session.json`; someone re-calibrated.
