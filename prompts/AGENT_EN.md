# AI agent prompt — photo → pivot-rigged desktop pet

You are a vision-capable coding agent working inside a checkout of
[`photo-pet-rig`](../README.md). Your job is to turn **one real full-body photo** into a
validated, pivot-rigged 2D desktop-pet asset pack, delivered to the user's Desktop.

This prompt is meant to be pasted into an agent that can:

- inspect images visually;
- edit files;
- run Python/terminal commands.

If you cannot see images or cannot run commands, stop and say so instead of pretending.

---

## Copy-paste assignment

Replace the placeholders and send the whole file or this block plus the repository context.

```text
PHOTO_PATH: <absolute path to the user's full-body photo>
OUTPUT_DIR: <optional; default = the user's Desktop>
RIGHTS: I own this photo / I have permission to process and use it.

Follow prompts/AGENT.md exactly. Read prompts/PROJECT_SCHEMA.md before writing
project.json. Build the assets with the repository tools; do not merely describe
what could be done. Preserve the face pixels exactly. Never regenerate, redraw,
beautify or restyle the person with a generative image model. If hands or body
parts are not visible, use empty layers and report the limitation. Deliver the
final folder and ZIP to the Desktop, then report the exact paths and QA results.
```

---

## Non-negotiable rules

1. **The uploaded photo is the only identity source.** The visible face pixels in
   `head.png` must be byte-identical to the source image. Do not retouch, redraw,
   beautify, morph or restyle the face, eyes, nose, mouth, hairline, skin, mask or hood.
2. **No generative redraw of the person.** The build pipeline only cuts pixels,
   cleans matte edges, adds joint-overlap pixels and creates synthetic fabric patches.
   Do not call an image-generation model to make a hand, arm, torso, clothing or face.
3. **Do not invent hidden anatomy.** If both hands are hidden, mark the hand parts
   `empty: true`. If a limb is completely outside the frame or fully occluded, mark
   it empty and state the limitation. A patch is not recovered truth.
4. **Do not resize the photo for annotation.** All project coordinates are absolute
   pixels in the original oriented canvas. The builder does not support per-layer
   re-centering.
5. **Do not hide QA failures.** If face equality, layer reassembly, patch hiding or
   static hole checks fail, fix the project or report the failure honestly.
6. **Do not commit the user's photo, alpha, project, or output folder to Git.**
   They are personal data. Keep them out of `examples/`, which is public.
7. **Get consent.** Only process photos the user owns or has permission to use.

---

## Step 0 — Inspect the repository

Read, at minimum:

- `README.md`
- `prompts/PROJECT_SCHEMA.md`
- `examples/mannequin/project.json`
- `photopet/project.py` (validation rules)
- `photopet/builder.py` (build output and QA)

Install the package:

```bash
python -m pip install -e .
# optional first-pass matting; downloads model weights on first use
python -m pip install -e ".[rembg]"
```

Verify:

```bash
photopet --version
```

If the `photopet` executable is not on PATH, use `python -m photopet` everywhere.

---

## Step 1 — Suitability gate

Look at the photo. Report a short verdict before spending time on layers.

### Good candidates

- Full body (or at least all parts you intend to animate) is visible.
- Head, shoulders, elbows, hips, knees, ankles and feet are at least partly visible.
- Limbs are not all fused into a single silhouette.
- There is enough separation between arms, torso and legs to annotate polygons.
- Face is visible and in reasonable focus.
- No severe motion blur or clothing that hides every joint.

### Poor candidates / limitations

- Arms crossed and hands hidden → you can still make a front-view rig, but
  natural arm swing is impossible; only small rotations.
- One side/back view only → state that the rig is one-sided.
- Legs hidden by a long skirt → the tool can only build a partial or approximate
  lower-body rig; do not invent legs.
- Face turned away or covered beyond the source → identity verification is limited.
- Multiple people or a busy cutout → clean the alpha or use another photo.
- Cropped feet → foot pivots and ground anchoring may be approximate.

If the photo is fundamentally unsuitable (e.g., no visible joints at all), stop and
ask for a new photo. If it is partially suitable, build what is real and report what
is missing.

---

## Step 2 — Create annotation references

```bash
photopet inspect "$PHOTO_PATH" -o inspect
```

Open `inspect/grid.png` and the gridded crops. All grid labels are in the original
full-image coordinate system. Use them to choose pivots and polygon points.

Check the image dimensions from the `inspect` output; if your viewer/EXIF shows
different dimensions, use the dimensions reported by `inspect`.

---

## Step 3 — Transparent cutout

```bash
photopet cutout "$PHOTO_PATH" -o cutout.png
```

Inspect `cutout.png` carefully over a dark and a light checker background.

- Remove a second person/object if present. You may use
  `photopet cutout ... --keep-largest`, but check that it did not remove a real
  disconnected body part.
- Repair holes/halos only by **masking**, never by repainting the face.
- If the automatic matte is poor, use another matting tool or a hand-authored alpha
  and save it as a grayscale PNG or RGBA cutout.

Put the chosen matte in the project as `alpha`.

---

## Step 4 — Write `project.json`

Use `examples/mannequin/project.json` as the structural template. Read
`prompts/PROJECT_SCHEMA.md` for every field.

Hard requirements:

- `schema` is exactly `photopet/project@1`.
- All 15 standard bones exist.
- All 14 standard parts exist.
- The canvas is the original oriented image size.
- `face.polygon` follows the visible face/mask area.
- The 14 parts' polygons cover at least **85%** of the foreground alpha.
  Aim for 99%+.

### Pivot placement

Put each pivot at the center of the visible joint seam:

| Joint | Pivot |
|---|---|
| neck | base of the head / top of the torso |
| shoulders | where the sleeve meets the torso |
| elbows | visible bend between upper sleeve and forearm |
| wrists | cuff/wrist end; hand pivot if visible |
| hips | where the trouser/leg meets the torso |
| knees | visible knee seam |
| ankles | shoe/trouser seam |

The skeleton hierarchy must stay:

```text
root
├─ torso
│  ├─ head
│  ├─ upper_arm_left → forearm_left → hand_left
│  └─ upper_arm_right → forearm_right → hand_right
├─ thigh_left → shin_left → foot_left
└─ thigh_right → shin_right → foot_right
```

Character-left is on the **screen right** for a front-facing photo. Keep that
convention in the JSON.

### Polygon placement

- Polygons are the **visible regions** of each part, not idealized full anatomy.
- Use multiple polygons for a part split by an occlusion.
- Higher `z` wins overlaps. Defaults match the standard draw order; keep them unless
  the photo requires otherwise.
- Head polygon: include all visible head/hair/hood/face; stop at the neck. Never cut
  through visible eyes, nose or mouth.
- Face polygon: inside the visible face/mask only. It must not include hair, hood,
  background or clothing.
- Torso polygon: clothing body, shoulders and hem; exclude sleeve and leg regions
  that have their own parts. Do not draw a giant torso polygon around the whole body.
- Upper arm / forearm: follow the visible sleeve/cuff. Do not extend a forearm into
  an occluding crossed arm.
- Legs: split at the visible knee seam; feet at the visible ankle.
- Hands: if there are real hand pixels, draw their polygons. If not, set
  `"empty": true, "polygons": []`.
- Mark `torso` with `"fallback": true`.

### Crossed arms and severe occlusion

The builder can still calculate layers and joint patches, but it cannot recover the
surface behind the crossing arm. Keep the arm rotation ranges very small, and state
in `notes` that natural arm swing is unsupported. Do not fabricate a chest/hand.

### Face protection

The face polygon is the identity contract:

- every face pixel gets forced to alpha 255;
- the face is forced into the `head` layer;
- the builder verifies the head layer RGB is byte-identical to the source.

If this verification fails, fix the project; do not suppress the error.

---

## Step 5 — Validate before building

```bash
photopet validate project.json
```

Fix every error. Then build:

```bash
photopet build project.json --out "$OUTPUT_DIR" --force
```

If `OUTPUT_DIR` is omitted, the CLI defaults to the user's Desktop. The build creates:

```text
<Desktop>/<slug>_pet_assets/
<Desktop>/<slug>_pet_assets.zip
```

---

## Step 6 — Inspect the actual result (mandatory)

Open:

- `preview.png` — main cutout on a background.
- `preview_layers.png` — every layer on the same full canvas.
- `preview_poses.png` — rest + small pivot tests.
- `preview_rig.png` — joint map.
- `rig.json` — pivots, draw order, angles.
- `qa_report.json` — machine checks.

Check at least:

1. `summary.exact_layer_reassembly` is `true`.
2. `summary.face_pixels_unchanged` is `true`.
3. `summary.patches_hidden_at_rest` is `true`.
4. `summary.zero_enclosed_holes_at_rest` is `true`.
5. The torso layer bbox is not accidentally the whole character.
6. Head/neck, shoulders, hips and knees look like clean cuts rather than random chunks.
7. There are no background stripes, white halos or shoe/ground fragments.
8. In `preview_poses.png`, there are no obvious missing chunks at small angles.
   Narrow gaps between the legs/arms can look enclosed at some angles; judge the
   image visually, not only the raw pixel count.

### Iterate

If something is wrong, fix the smallest cause and rebuild:

| Symptom | Likely fix |
|---|---|
| Torso layer covers the whole character | Torso polygon covers arms/legs; shrink it. |
| A part captures no pixels | Polygon is off the body or alpha is wrong. |
| Faint white outline | Matte edge includes background; improve alpha or use `--keep-largest`. |
| Visible patch at rest | Build must fail; inspect patch clipping/z. |
| Face check false | Face polygon includes hair/background or a non-head cut; fix it. |
| Hands are floating/empty | Mark hidden hands empty; do not invent them. |
| Shoulder/knee hole under small pose | Adjust pivot to the visible seam or increase patch radius/overlap. |
| Double person/object | Clean alpha or choose a better photo. |

Do not simply delete QA output or lower checks to make the build pass.

---

## Step 7 — Final report

Report in the user's language:

- final folder path and ZIP path;
- original photo size and output canvas;
- number of visible body layers and patches;
- QA numbers for reassembly, face equality, patch hiding, static holes;
- what works (idle, small walk, click, drag/landing);
- what does **not** work (hidden hands, crossed arms, profile, expressions, large
  rotations);
- any approximations/pivots that were estimated;
- the exact command to rebuild.

Never claim:

- “fully rigged” if hands/limbs are missing;
- “no holes at any angle”;
- “natural arm swing” when the source has crossed/hidden arms;
- “face preserved” without the QA value;
- “AI-generated replacement” — the build step is deterministic and non-generative.

---

## Final checklist

- [ ] Read `prompts/PROJECT_SCHEMA.md`.
- [ ] Inspected the photo and stated suitability/limitations.
- [ ] Created and inspected the cutout alpha.
- [ ] Used the original canvas coordinates, no resizing.
- [ ] Filled all 15 bones and 14 parts.
- [ ] Face polygon is inside the visible face; no head cut through face.
- [ ] Hidden hands are empty; no invented hands.
- [ ] `photopet validate` passes.
- [ ] `photopet build` succeeds.
- [ ] Read `qa_report.json`; all static gates are true.
- [ ] Looked at `preview.png`, `preview_layers.png`, `preview_poses.png`, `preview_rig.png`.
- [ ] Delivered folder and ZIP to the Desktop.
- [ ] Stated limitations honestly.
- [ ] Did not commit the user's photo or outputs to Git.
