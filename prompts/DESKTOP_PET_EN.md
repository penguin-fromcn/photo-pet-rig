# End-to-end prompt: existing photo → layered rig → running desktop pet

Give this whole file to a coding agent that can **see images and run terminal
commands**. Replace the inputs below, then require it to actually execute.

```text
PHOTO_PATH: C:\absolute\path\to\your\full-body-photo.jpg
OUTPUT_DIR: C:\Users\<you>\Desktop
RIGHTS: I own this photo / I have permission to process and use it.
RUNTIME: Windows 10/11
```

## Cost and privacy rules (mandatory)

- The runtime calls **no LLM/image API**. Rendering, physics, walking, clicks and
  the tray are local.
- Use the deterministic Python builder. Do **not** redraw or “beautify” the
  person's face, hands or clothes with a generative model.
- Do **one** visual-analysis pass (grid + reference crops). Do not re-upload the
  full photo repeatedly, do not call a vision model per frame, and do not loop
  paid services.
- `rembg` is optional and downloads weights on first use. Ask first, or use a
  transparent PNG supplied by the user.
- Never commit the user's photo, alpha, `project.json` or outputs to Git.
- If the photo is unsuitable (heavy occlusion, side/back view, long skirt hiding
  legs, motion blur, multiple people), state the limits instead of inventing
  limbs.

## 1. Check the environment

```powershell
python --version
python -m pip install -e ".[pet]"
photopet --version
```

Python 3.10+, Windows 10/11. `[pet]` installs PySide6; without it you can still
build assets but cannot open the desktop window.

## 2. Inspect before cutting

```powershell
photopet inspect "<PHOTO_PATH>" -o inspect
```

Open `inspect/grid.png` and `inspect/crops/*_grid.png`. Check framing, visibility
of head/shoulders/elbows/hips/knees/ankles, merged limbs, hidden hands and face
quality. Record the verdict.

## 3. Transparent matte

Prefer a transparent PNG from the user. Otherwise:

```powershell
photopet cutout "<PHOTO_PATH>" -o cutout.png
```

Inspect the alpha. Report background remnants, hair gaps or missing fingers.
Do not use a generative model to repair them.

## 4. Write `project.json`

Follow [`PROJECT_SCHEMA.md`](PROJECT_SCHEMA.md) and [`AGENT_EN.md`](AGENT_EN.md):
absolute source-pixel pivots, 14 part polygons, face polygon, and explicit
empty/hidden parts where the photo has no visible hand or limb. Never invent
hidden anatomy.

```powershell
photopet validate project.json
```

## 5. Build and verify

```powershell
photopet build project.json --out "<OUTPUT_DIR>"
```

Check `qa_report.json.summary` (all four booleans true), `preview.png`,
`preview_poses.png`, and the recommended angle limits in `rig.json`. Iterate at
most three times and report each change honestly.

## 6. Run the real pet

```powershell
photopet pet --assets "<OUTPUT_DIR>\<slug>_pet_assets" --config pet_config.json
```

Verify: transparent background, drag/throw/landing bounce, autonomous walking
and edge turns, click/double-click feedback, right-click menu, tray pause/exit,
and visible limb alternation when layered assets exist. If only a flat PNG is
available, the app must say it is in degraded mode rather than rotating the whole
photo as fake limb animation.

Self-check:

```powershell
photopet pet --assets "<OUTPUT_DIR>\<slug>_pet_assets" --smoke-test 3 --smoke-report smoke.json
```

## 7. Deliver

Report: suitability and limits; absolute output/`rig.json` paths; commands run;
key `qa_report.json` results; screenshot/preview path; and known limitations.
