---
name: dish-media
description: Turn a phone shoot of restaurant dishes and drinks into tablet-ready menu media with only truthful edits — exposure, white balance, contrast, shadows and highlights, modest saturation, crop and straighten, an optional plain background with a soft contact shadow, and a seamless turntable loop — delivered as sRGB JPEG and SDR H.264 MP4 that cheap Android tablets show correctly. Use it whenever someone wants to edit, grade, colour-correct, fix, batch-process or "make consistent" dish, food, drink or menu photos, make turntable or rotation clips loop, prepare or export menu media for the tablets, calibrate a shoot from a grey card, check or judge a contact sheet or frame grid, cut dishes out onto a plain background, or convert HEIC or HDR phone files for a menu — and for `/dish-media` with a subcommand (setup, ingest, calibrate, grade, cutout, sheet, loops, qa, deliver). It measures what can be measured (grey card), lets Claude judge one contact sheet of reference dishes to pick ONE parameter set per session, applies that set to every file deterministically, and uses further contact sheets only to catch outliers. It never generates, adds, removes or retouches food. Do NOT use it for generative edits ("add steam", "make the sauce glossier", "remove the fork"), for photos that are not menu media, or for video beyond trimming, grading, stabilising and looping a real rotation.
---

# Dish media: from the plate to the tablet

A restaurant shoots every dish and drink on a phone: two photos and one 8–12 s clip of
the dish turning on a turntable, 50–200 items per menu. The tablets on the tables show
the photo and the loop. This skill takes the shoot from `raw/` to a delivery folder with
edits a careful human editor would make and nothing else, and it does it the same way
for every file so the menu looks like one menu.

## Hard rules

1. **Never generative.** No model paints a pixel. Colour and light are arithmetic on the
   real pixels; the cutout is a segmentation mask; the loop is a cut of the real
   rotation. If a request needs invented pixels ("add garnish", "fill the gap where the
   fork was", "make it look juicier"), say that this pipeline does not do that and stop.
2. **Grey card first.** Every session starts with a frame of the grey card under the
   session's light. White balance and exposure come from that measurement, by
   arithmetic, not by eye.
3. **One parameter set per session**, saved to `work/session.json`. Every photo and every
   clip in the session is rendered from it. Differences between dishes are per-file
   `overrides` inside that same file, never a second look.
4. **Judge on contact sheets and frame grids only**, never file by file. Claude's eye is
   good at "too dark", "too warm", "the matte failed", and poor at "200 K warmer" or "a
   third of a stop". Looking at 200 files one by one costs 200 looks and produces 200
   slightly different opinions. A sheet of 20 thumbnails, compared against the reference
   dish in the first cell, gives one opinion per batch and shows outliers as outliers.
5. **Look only at exported sRGB JPEGs and MP4 frame grids** — the thing the tablet will
   show. Never open a HEIC, an HDR clip or a wide-gamut (Display P3) file to judge it: the viewer
   downscales to about 1.1 MP and does not colour-manage, so what you would see is not
   what is there.
6. **Only light and colour.** Exposure, white balance, contrast, shadows, highlights,
   saturation, crop, straighten, background, shadow, loop. Never "improve" the food.
7. **Originals are untouched.** Scripts read `raw/` and write `work/`, `out/`, `qa/`.
   Nothing in the pipeline modifies or deletes a file under `raw/`.
8. **Deterministic.** The same inputs and the same `session.json` produce byte-identical
   outputs. If a re-run changes a file, something changed in the inputs or the session,
   and that is worth knowing.

## Why it is built this way

The investigation of 25 Sep 2026 ("Plate to Tablet",
https://claude.ai/artifact/WtSMd9EEqLpLhUH28CXxqE, notes in
`~/photo-video-ai-edits-investigation/`) compared chat models, photo and video editors,
generated orbits and 3D capture for this exact job. Its conclusions drive every choice
here: generative editors re-render the dish and cannot give one look to 200 items; the
edits that are safe are parametric; consistency across dishes matters more than
perfection on one; a locked-off phone in SDR with a grey-card frame removes most of the
editing; HDR video and wide-gamut (Display P3) stills are the two things that look wrong on cheap
Android tablets, so delivery is sRGB JPEG and SDR H.264. The parts that need a person's
eye (which of five looks, is this matte broken, is that seam visible) are the parts
Claude does here — on sheets, once per batch.

## Project layout

Pick a project folder per menu or per shoot (`P` below). Every script takes explicit paths;
this is the layout the commands assume and `qa_report.py --project` expects:

```
P/
  raw/photos/     originals: HEIC, JPEG, PNG        (never written)
  raw/video/      originals: MOV, MP4               (never written)
  work/photos/    ingested sRGB JPEG, 2400 px       work/manifest.csv
  work/measure/   grey-card measurements            work/session.json, session.cube
  work/mattes/    cutout alphas (PNG)               work/sdr/ tone-mapped HDR clips
  out/photos/     graded delivery JPEG, 1600 px     out/cutouts/  out/loops/
  qa/             flags.json, grade_stats.json, video_stats.json, sheets/, grids/,
                  variants/, report.md
  deliver/        what goes to the tablets (deliver.py)
```

`S` below is this skill's `scripts/` directory (this file's directory + `/scripts`). Set both
once per session:

```bash
S=/path/to/skills/dish-media/scripts     # the base directory shown when this skill loaded
P=~/menus/trattoria-2026-10               # the project folder the user named
```

## Subcommands

`/dish-media <subcommand>` jumps into the workflow at that step. Each step assumes the
earlier ones are done and says so if not.

| Subcommand | Does | Step |
|---|---|---|
| `setup` | Check ffmpeg, Python modules, rembg model; offer to install what is missing | 0 |
| `ingest` | HEIC/JPEG/PNG → working sRGB JPEG + manifest; list clips, flag HDR | 1 |
| `calibrate` | Measure the card, derive the session, render variants, pick one set | 2–5 |
| `grade` | Apply the session to every photo | 6 |
| `cutout` | Plain background + contact shadow, with confidence flags | 7 |
| `sheet` | Contact sheets of graded photos (or cutouts) for QA; overrides | 8–9 |
| `loops` | Trim, grade and loop every clip; frame grids | 10–11 |
| `qa` | Merge every flag into `qa/report.md` | 12 |
| `deliver` | Assemble `deliver/` from cutouts or photos and loops | 13 |

## Workflow

### 0. Setup (once per machine)

```bash
bash "$S/setup.sh" --check          # reports; installs nothing
bash "$S/setup.sh"                  # prints the plan, asks, then pip-installs into the active python3 and fetches the rembg model (~170 MB)
```

The pipeline needs ffmpeg/ffprobe (Homebrew), Pillow, numpy, OpenCV and, for cutouts only,
rembg with its `isnet-general-use` model. On macOS `sips` does HEIC and ICC conversion;
elsewhere `pillow-heif` is installed too.

### 1. Ingest

```bash
python3 "$S/ingest.py" --in "$P/raw/photos" --out "$P/work/photos" \
  --manifest "$P/work/manifest.csv" --video-in "$P/raw/video" --qa "$P/qa"
```

Working files are sRGB JPEG at 2400 px, orientation applied and dropped, one per original.
The manifest records original name, capture time and sizes. Clips are listed, not copied;
an HDR clip is flagged `hdr_source` (see step 10).

### 2. Measure the grey card

Find the card frame in `work/photos/` (the first file of the session, usually). Then:

```bash
python3 "$S/measure.py" "$P/work/photos/IMG_0001.jpg" --auto \
  --out "$P/work/measure/card.json" --preview "$P/qa/card-box.jpg"
```

**Look at `qa/card-box.jpg`** (it is an sRGB JPEG under 1568 px, so one Read). The magenta
box must sit entirely on the card. If it does not, pass the region yourself in pixels of
the working image: `--card X,Y,W,H`. `--auto` picks the flattest mid-grey patch; a matte
grey table or a shadowed plate can fool it.

The JSON has the card's mean RGB, R/G and B/G in linear light, luminance percentiles and
clipping. `neutral_error_255` is how far from neutral the card is *before* correction —
a healthy warm cast reads 5–15.

### 3. Calibrate

```bash
python3 "$S/calibrate.py" --measure "$P/work/measure/card.json" --out "$P/work/session.json"
```

This writes the full parameter set: `wb_gains` that make the card neutral, an `exposure`
that puts the card at 0.48 (sRGB, 0–1) **in the output JPEG, after the tone curve**, and
the default look: contrast `{strength 2.5, midpoint 0.48}`, `shadows 0.10`,
`highlights 0.15`, `saturation 1.05`, `crop_ratio 4:3`, `output_long_edge 1600`, no
background. Any value can be set with a flag (`--contrast-strength 3.5 --saturation 1.0
--crop-ratio 1:1 --background '#F6F4EF'`); re-running with `--from` keeps what is not
re-specified, including overrides. Because exposure is solved through the curve, changing
contrast or shadows here keeps the card on target; hand-editing the JSON does not.

If the clips were shot with different exposure than the photos (video ISO and shutter
differ), shoot the card on video too, grab a frame and calibrate a second session from it
while keeping the look: `ffmpeg -ss 1 -i card.mov -frames:v 1 card-video.jpg`, then
`measure.py`, then `calibrate.py --from "$P/work/session.json" --measure card-video.json
--out "$P/work/session-video.json"`. Use that file for step 10.

### 4. Variants sheet: the one place Claude chooses a look

Pick 3–5 **reference dishes** that span the menu: a white plate with pale food, a dark
sauce, something red or green, a drink in glass. Render the variants of the most
representative one and look at them side by side:

```bash
python3 "$S/grade.py" --session "$P/work/session.json" --variants "$P/work/photos/IMG_0012.jpg" --out "$P/qa/variants"
python3 "$S/contact_sheet.py" --in "$P/qa/variants" --out "$P/qa/variants.jpg" --variants
```

The sheet shows −0.3 / 0 / +0.3 EV at the session contrast and at a stronger one, with
median luminance, 99th percentile and clipping under each. Read it once. Choose by
these questions, in this order: is the plate white without being clipped (p99 below
~0.98, clip under 1%)? does the darkest food still show texture? is the card-corrected
colour believable (tomato red, not orange; herbs green, not yellow)? which contrast keeps
sauces glossy without crushing the shadows? Then set the choice on the session:

```bash
python3 "$S/calibrate.py" --from "$P/work/session.json" --measure "$P/work/measure/card.json" \
  --out "$P/work/session.json" --exposure-ev -0.3 --contrast-strength 4.5
```

If two references disagree (the pale dish wants −0.3, the dark one +0.3), the session
takes the middle and the outliers get per-file overrides in step 9. Do not average by
rendering more variants; one round is the budget.

### 5. Grade everything

```bash
python3 "$S/grade.py" --session "$P/work/session.json" --in "$P/work/photos" --out "$P/out/photos" --qa "$P/qa" --workers 4
```

Per file: sRGB → linear, gains, exposure, tone curve on luminance (ratio-preserving, so
hue and chroma are untouched), saturation as OKLab chroma scaling, back to sRGB,
straighten, crop, resize, JPEG q90 with the sRGB profile embedded and no EXIF. Files
with more than 1% of pixels at 254+ are flagged `clip_high`; `qa/grade_stats.json`
holds every file's median luminance and clipping for the sheets and the report.

### 6. Cutout (only if the menu wants a plain background)

Set the colour on the session (`calibrate.py --from ... --background '#F6F4EF'`), then:

```bash
python3 "$S/cutout.py" --session "$P/work/session.json" --in "$P/out/photos" \
  --out "$P/out/cutouts" --mattes "$P/work/mattes" --qa "$P/qa"
```

rembg (`isnet-general-use`, falling back to `u2net`) gives the matte; it is eroded one
pixel and feathered, composited in linear light, and a contact shadow is made from the
blurred, offset alpha with the session's `shadow` settings. Every file gets a confidence
score from alpha coverage, soft-edge area, holes, border contact and faint ghosts away
from the dish; below 0.8 it is flagged `matte_low_confidence`, so any one clear defect
puts the file on the list. Glass, steam, thin herbs
and cutlery are the usual reasons. `--reuse-mattes` recomposites after a change of
background or shadow without running the model again.

### 7. Contact-sheet QA

```bash
python3 "$S/contact_sheet.py" --in "$P/out/photos" --out "$P/qa/sheets" \
  --reference IMG_0012 --stats "$P/qa/grade_stats.json"
# and, if cutouts were made:
python3 "$S/contact_sheet.py" --in "$P/out/cutouts" --out "$P/qa/sheets-cutouts" --reference IMG_0012 --stats "$P/qa/cutout_stats.json"
```

One sheet per 19 dishes plus the reference in the first cell (yellow frame), on neutral
grey, 1568 px wide, with `Y50`, `p99` and `clip` under each thumbnail. Read each sheet
once and apply the checklist below. Write down the file names that need something, with
the reason, before touching any parameter.

### Judging checklist (for every sheet and grid)

Compare against the reference cell and the grey surround, not against your memory of the
previous sheet.

- **Exposure outliers**: a thumbnail clearly lighter or darker than its neighbours, or
  `Y50` more than ~0.06 from the reference's. The report computes this too; the eye
  catches the ones the number misses (dark food on a white plate reads "dark" while the
  median says "fine").
- **Colour cast**: a plate that is warmer, cooler, greener or magenta next to the
  reference's plate. Whole-batch casts mean the card was wrong: re-measure and
  re-calibrate rather than overriding 40 files.
- **Blown highlights on white plates**: `clip` above 1% or a plate rim that has lost its
  edge. Fix with `exposure_ev=-0.2` on that file, or raise `highlights` for the session
  if it is most of them.
- **Crushed shadows**: dark sauces or bowls with no texture at all. `shadows=0.2` or
  `exposure_ev=+0.2` on that file.
- **Failed mattes** (cutout sheets): a bite out of the plate rim, a halo of table around
  thin herbs, a glass that went transparent, the shadow of a fork left floating. Options
  in order: `cutout.py --erode 2 --feather 2` on that file, a different `--model`, or
  deliver the graded photo instead of the cutout for that dish.
- **Dust, smudges, reflections, a hand, a stray napkin**: nothing here fixes those and
  nothing should. Name the file for a reshoot.
- **Straightness and framing**: a tilted plate rim gets `straighten_deg`; a dish sitting
  off-centre gets `crop_center=[0.55,0.5]`.
- **Loop seam** (frame grids): the first and last cells should look like consecutive
  frames. A jump means the period was wrong (`period_ambiguous` on a symmetric dish) or
  the turntable was not up to speed at `--start`.

### 8. Per-file overrides, then re-grade only those files

```bash
python3 "$S/calibrate.py" --from "$P/work/session.json" --out "$P/work/session.json" \
  --override IMG_0031 exposure_ev=-0.3 \
  --override IMG_0044 shadows=0.25 straighten_deg=1.5 \
  --override IMG_0050 crop_center=0.55,0.5
python3 "$S/grade.py" --session "$P/work/session.json" --in "$P/work/photos/IMG_0031.jpg" --out "$P/out/photos" --qa "$P/qa"
```

Override keys: `exposure_ev` (stops, relative), `exposure` (absolute multiplier),
`wb_gains`, `contrast_strength`, `contrast_midpoint`, `shadows`, `highlights`,
`saturation`, `crop_ratio`, `straighten_deg`, `crop_center`, `output_long_edge`,
`background`, `shadow`, and for clips `start`, `duration`, `loop`. Then re-run the sheet
for the affected page. Two rounds of overrides is normal; a third means the session is
wrong — go back to step 4.

### 9. Loops

```bash
python3 "$S/video_grade.py" --session "$P/work/session.json" --in "$P/raw/video" --out "$P/out/loops" --qa "$P/qa"
# one clip, ping-pong, explicit cut:
bash "$S/video_loop.sh" "$P/work/session.json" "$P/raw/video/IMG_0013.mov" "$P/out/loops/IMG_0013.mp4" pingpong 1.5 5
```

The session look is baked into `work/session.cube` (a 33³ LUT from the same maths as
`grade.py`) and applied with `lut3d`, so a dish's clip matches its photo. `revolution`
(default) runs `loop_period.py` to find one turn from `--start` (default 1.0 s, after the
turntable is up to speed) and cuts exactly that many frames, so the last frame is one
step before the first: seamless when the turntable is steady. `pingpong` plays 5 s
forward then backward; use it when a revolution was not found or the dish is symmetric.
`--stabilize` runs two-pass vidstab for a clip that was bumped. Output: long edge 1920,
yuv420p, CRF 20, bt709 tags, faststart, no audio, no metadata, timestamps exactly on the
frame grid.

An HDR clip (`hdr_source` in the flags: HLG or PQ, which is what a phone writes with HDR
video on — Dolby Vision on an iPhone, HDR10+ on most Android phones) is refused per clip.
Tone-map it first, then grade the SDR intermediate:

```bash
bash "$S/tonemap_hdr.sh" "$P/raw/video/IMG_0013.mov" "$P/work/sdr/IMG_0013.mov"
python3 "$S/video_grade.py" --session "$P/work/session.json" --in "$P/work/sdr/IMG_0013.mov" --out "$P/out/loops/IMG_0013.mp4" --qa "$P/qa"
```

That is a repair. Turn HDR video off in the camera app before the next shoot (iPhone:
Settings → Camera → Record Video → HDR Video; Android: the camera app's HDR10+ or HDR
video switch), or use Blackmagic Camera (iOS and Android) set to Rec.709, and no clip
needs it.

### 10. Frame-grid QA

```bash
for f in "$P"/out/loops/*.mp4; do
  python3 "$S/frame_grid.py" "$f" --out "$P/qa/grids/$(basename "${f%.mp4}").jpg" --qa "$P/qa"
done
```

Nine frames (first, seven evenly spaced, last) on one 1568 px image with the seam score
in the header: last-frame-to-first-frame similarity against the typical
frame-to-frame similarity. Below 0.95, or clearly worse than a normal step, is flagged
`loop_seam`. Read the grids like sheets: the look (is the clip as bright and as neutral as
the photo?), the seam (first vs last cell), and anything the checklist names.

### 11. QA report

```bash
python3 "$S/qa_report.py" --project "$P"
```

Merges every step's flags with the batch-level checks (exposure and cast outliers
against the batch median, a card that did not come out neutral, missing outputs) into
`qa/report.md`, grouped by severity, with one line per code saying what to do. Exit code
1 while any error remains. Fix, re-run the affected step, re-run the report.

### 12. Deliver

```bash
python3 "$S/deliver.py" --project "$P"
```

Copies the cutout where one exists, else the graded photo, and every loop into
`deliver/photos` and `deliver/loops` with `deliver/manifest.csv`. Refuses while the report
has errors (`--force` overrides, and say so). Then the one check no script can do: open two
or three files on the cheapest tablet that will run the menu, in the ordering app, under
the restaurant's lights.

## The session file

```json
{
  "schema": "dish-media.session/1",
  "provenance": {"created": "...", "script_version": "1.0.0", "card": {...}, "targets": {...}, "predicted_card_srgb255": [122.4, 122.4, 122.4]},
  "wb_gains": [0.80, 1.0, 1.28],   "exposure": 2.02,
  "contrast": {"strength": 2.5, "midpoint": 0.48},
  "shadows": 0.10, "highlights": 0.15, "saturation": 1.05,
  "crop_ratio": "4:3", "straighten_deg": 0.0, "output_long_edge": 1600,
  "background": "#F6F4EF", "shadow": {"blur": 40, "offset": 14, "opacity": 0.35},
  "overrides": {"IMG_0031": {"exposure_ev": -0.3}}
}
```

Gains and exposure are linear multipliers. The tone curve lives on the sRGB-encoded 0–1
axis: `shadows` lifts the 0.25 level by 0.20 per unit, `highlights` is a soft knee above
0.55 (0 off, 1 full), contrast is a normalised logistic around `midpoint` (0 off, 6
strong). Saturation scales OKLab chroma, so 1.0 changes nothing and neutrals stay neutral.
Blur and offset of the shadow are in pixels at 1600 px and scale with the output size.

## Things that bite

- **The grey card is the session's only truth.** A card in shadow, at an angle to the
  light, or measured on a white plate by `--auto`, puts every file off by the same
  amount. Check `qa/card-box.jpg` every time.
- **Photos and clips are exposed differently by the phone.** Same light, different
  gain. If the loops look darker than the photos on the grids, calibrate a video session
  from a card frame (step 3) instead of nudging `exposure_ev` per clip.
- **Symmetric dishes fool the period finder.** A plain round plate or two identical items
  match at half a turn; the clip loops twice as fast. `period_ambiguous` says so; set
  `--duration` to the full turn from the grid, or use `pingpong`.
- **HEIC needs `sips` or pillow-heif; Display P3 must be converted, not relabelled.** The
  ingest does both; a working file that still looks over-saturated on the sheet is a
  file that came in without a profile and was P3 anyway — rare, but reshoot with the
  camera set to JPEG / "most compatible", or export it from the phone's gallery as JPEG.
- **rembg is segmentation, not magic.** Glass, clear sauces, steam and single herb leaves
  are exactly what it gets wrong. The confidence score catches most of it; the cutout
  sheet catches the rest. Delivering the plain graded photo for those dishes is a fine
  answer.
- **Do not hand-edit `exposure` after changing the curve.** `calibrate.py` solves exposure
  through the tone curve; change the look through its flags so the card stays on target.
- **Viewing an image costs tokens and is not deterministic.** That is why the sheets exist.
  If you find yourself opening single files, stop and make a sheet.

## Model note

Everything after step 4 is mechanical: run a command, read a sheet against a checklist,
write an override, repeat. It suits Sonnet, and Haiku for the runs with no judgement in
them (ingest, grade, loops, report, deliver). Choosing the look in step 4 and judging
mattes in step 7 are the two places a stronger eye earns its cost.

## Tests

```bash
bash "$S/tests/run_tests.sh"
```

Renders a synthetic shoot (plates with coloured food and a grey card under a warm cast at
−1 EV; a patterned disc turning once every 8 s at 30 fps for 12 s; an HLG-tagged clip),
runs every script twice and asserts the card comes out neutral within 2/255 and at the
target within 3%, the period is 8.0 ± 0.1 s, both loop modes seam above 0.95, sheets and
grids fit in 1568 px, HDR is refused then tone-mapped, and the second run is
byte-identical. `py_compile` and `shellcheck` run first.
