# `project.json` schema

`photo-pet-rig` separates **AI interpretation** from **pixel processing**:

- A vision-capable agent (or a human) writes `project.json`: joints, part polygons, optional face polygon, and patch options.
- The Python builder does all cutting, masking, patching, preview and QA deterministically.

This file is the contract. The complete working example is
[`examples/mannequin/project.json`](../examples/mannequin/project.json).

---

## 1. Minimal skeleton

```json
{
  "schema": "photopet/project@1",
  "name": "my_pet",
  "image": "photo.jpg",
  "alpha": "cutout.png",
  "canvas": { "width": 900, "height": 1600 },
  "bones": {
    "root":             { "parent": null,   "pivot": [450, 815] },
    "torso":            { "parent": "root", "pivot": [450, 815] },
    "head":             { "parent": "torso", "pivot": [450, 350] },
    "upper_arm_left":   { "parent": "torso", "pivot": [588, 392] },
    "forearm_left":     { "parent": "upper_arm_left", "pivot": [686, 590] },
    "hand_left":        { "parent": "forearm_left", "pivot": [712, 790] },
    "upper_arm_right":  { "parent": "torso", "pivot": [312, 392] },
    "forearm_right":    { "parent": "upper_arm_right", "pivot": [214, 590] },
    "hand_right":       { "parent": "forearm_right", "pivot": [188, 790] },
    "thigh_left":       { "parent": "root", "pivot": [498, 815] },
    "shin_left":        { "parent": "thigh_left", "pivot": [532, 1080] },
    "foot_left":        { "parent": "shin_left", "pivot": [535, 1400] },
    "thigh_right":      { "parent": "root", "pivot": [402, 815] },
    "shin_right":       { "parent": "thigh_right", "pivot": [368, 1080] },
    "foot_right":       { "parent": "shin_right", "pivot": [365, 1400] }
  },
  "face": {
    "polygon": [[372, 202], [440, 184], [512, 202], [540, 270],
                 [500, 318], [450, 330], [392, 316], [360, 270]]
  },
  "parts": {
    "head":            { "bone": "head", "z": 50, "polygons": [[[329, 109], [571, 109], [571, 380], [329, 380]]] },
    "torso":           { "bone": "torso", "z": 30, "fallback": true, "polygons": [[[285, 338], [614, 338], [614, 837], [285, 837]]] },
    "upper_arm_left":  { "bone": "upper_arm_left", "z": 41, "polygons": [[[541, 342], [735, 342], [735, 641], [541, 641]]] },
    "forearm_left":    { "bone": "forearm_left", "z": 43, "polygons": [[[647, 571], [764, 571], [764, 805], [647, 805]]] },
    "hand_left":       { "bone": "hand_left", "z": 45, "polygons": [[[677, 786], [764, 786], [764, 877], [677, 877]]] },
    "upper_arm_right": { "bone": "upper_arm_right", "z": 40, "polygons": [[[164, 342], [359, 342], [359, 640], [164, 640]]] },
    "forearm_right":   { "bone": "forearm_right", "z": 42, "polygons": [[[135, 571], [253, 571], [253, 805], [135, 805]]] },
    "hand_right":      { "bone": "hand_right", "z": 44, "polygons": [[[135, 786], [222, 786], [222, 877], [135, 877]]] },
    "thigh_left":      { "bone": "thigh_left", "z": 21, "polygons": [[[434, 811], [594, 811], [594, 1147], [434, 1147]]] },
    "shin_left":       { "bone": "shin_left", "z": 23, "polygons": [[[479, 1030], [587, 1030], [587, 1395], [479, 1395]]] },
    "foot_left":       { "bone": "foot_left", "z": 25, "polygons": [[[467, 1387], [622, 1387], [622, 1537], [467, 1537]]] },
    "thigh_right":     { "bone": "thigh_right", "z": 20, "polygons": [[[305, 811], [448, 811], [448, 1147], [305, 1147]]] },
    "shin_right":      { "bone": "shin_right", "z": 22, "polygons": [[[312, 1030], [421, 1030], [421, 1395], [312, 1395]]] },
    "foot_right":      { "bone": "foot_right", "z": 24, "polygons": [[[277, 1387], [432, 1387], [432, 1537], [277, 1537]]] }
  },
  "patches": {
    "enabled": true,
    "joints": ["head", "upper_arm_left", "upper_arm_right", "forearm_left",
               "forearm_right", "thigh_left", "thigh_right", "shin_left",
               "shin_right", "foot_left", "foot_right"],
    "scale": 1.0,
    "overlap_px": 8
  },
  "preview": { "title": "My Pet" }
}
```

The example above uses **rectangles only for readability**; real annotations must follow the visible silhouette and clothing boundaries.

---

## 2. Top-level fields

| Field | Required | Meaning |
|---|---|---|
| `schema` | yes | Must be exactly `photopet/project@1`. |
| `name` | recommended | Display name. The output folder uses a sanitized slug. |
| `image` | yes | Source photo path, relative to `project.json` or absolute. |
| `alpha` | optional | Transparent cutout / grayscale matte, same canvas size. If omitted, embedded alpha is used, otherwise rembg is attempted. |
| `canvas` | optional | `{width, height}`. If present it must match the oriented image. |
| `bones` | yes | Standard 15-bone skeleton; see below. |
| `parts` | yes | Standard 14 body layers with polygons. |
| `face` | strongly recommended | Polygon around visible face/mask. Enables alpha fixing and identity QA. |
| `patches` | optional | Auto joint-patch configuration. Defaults shown above. |
| `rotation_ranges` | optional | Per-bone `{recommended: {bone: [min,max]}, safe: {bone: [min,max]}}`. |
| `mirror_pairs` | optional | Left/right pairing metadata; do not mirror source pixels. |
| `notes` | optional | Strings appended to the generated limitations. |
| `preview` | optional | `{ "title": "..." }` for `preview.png`. |

All coordinates are **absolute pixels in the original image canvas**, origin top-left, X right, Y down. Do not resize the photo for annotation.

The image is loaded with EXIF orientation applied. If a camera stores rotation in EXIF, annotate the **oriented** image (the same dimensions that `photopet inspect` reports).

---

## 3. Bones

Supported IDs (all required):

```text
root
torso
head
upper_arm_left   forearm_left   hand_left
upper_arm_right  forearm_right  hand_right
thigh_left       shin_left      foot_left
thigh_right      shin_right     foot_right
```

Each bone:

```json
"upper_arm_left": {
  "parent": "torso",
  "pivot": [588, 392]
}
```

- `root.parent` must be `null`; it is the virtual pelvis root.
- `pivot` is the joint center.
- `head` pivots at the **neck base**, not at the face center.
- `upper_arm_*` pivots at the shoulder; `forearm_*` at the elbow; `hand_*` at the wrist.
- `thigh_*` pivots at the hip; `shin_*` at the knee; `foot_*` at the ankle.
- Left/right means **the character's left/right**, not the viewer's. Character-left is on the screen right for a front-facing photo.

### Pivot placement matters

Place a pivot at the **visible seam** between the two parts, not merely at an anatomical guess. If the shin polygon starts 60 px above the visual knee, place the knee pivot near that start, or adjust the polygon. The builder cannot find joints for you.

For crossed arms, the forearms overlap and the hands may be fully hidden. Put the elbow pivot at the visible bend and keep the hand layer empty if no hand pixels exist.

---

## 4. Parts and polygons

All 14 part IDs are required. Each part is one or more polygons:

```json
"head": {
  "bone": "head",
  "z": 50,
  "polygons": [
    [[420, 120], [560, 120], [580, 300], [520, 390], [420, 390], [380, 280]]
  ]
}
```

- `polygons`: list of polygons; each polygon is a list of `[x, y]`. Holes are not supported; use multiple polygons to approximate disjoint pieces.
- `z`: draw order and mask priority. **Larger z is closer to the camera / wins overlaps.** Defaults:
  - thighs 20/21, shins 22/23, feet 24/25
  - torso 30
  - upper arms 40/41, forearms 42/43, hands 44/45
  - head 50
- `fallback: true` must be set on `torso`. Unassigned silhouette fringes are assigned to the **nearest part**; torso is the required fallback layer, not a dumping ground for entire limbs.
- `empty: true` may be used for hands that are completely occluded. An empty part must have `polygons: []`.
- Non-empty parts must have at least one polygon and must capture at least one foreground pixel.

### What to annotate

| Part | Visible region |
|---|---|
| `head` | hair, face, ears, hood, mask; stop at the neck/collar. Never cut through visible eyes, nose, mouth or mask. |
| `torso` | clothing body/shoulders/hem; exclude sleeves and legs that are separate layers. |
| `upper_arm_*` | visible upper sleeve from shoulder to elbow seam. |
| `forearm_*` | visible lower sleeve and exposed wrist; stop before a hidden hand. |
| `hand_*` | visible hand only. If no real hand pixels, mark empty. |
| `thigh_*` | trouser/dress thigh from hip seam to knee seam. |
| `shin_*` | lower leg from knee seam to ankle seam. |
| `foot_*` | shoe/foot from ankle seam down. |

For a crossed-arm pose, define the **visible bands** of each sleeve rather than imaginary full arms. The hidden arm behind another arm cannot be recovered. If the overlap is severe, document that only small arm rotations are supported.

### Coverage and nearest-part filling

The builder requires polygons to cover at least **85%** of the foreground alpha. Remaining fringes (typically 0–3%) are assigned to the nearest part by a distance transform. If coverage is lower, `build` fails and asks you to expand the polygons.

### Face safety

If `face` is supplied:

- The builder forces every pixel under `face.polygon` to `alpha = 255`.
- It forces the face pixels to belong to `head`.
- It verifies that the head layer RGB at those pixels is byte-identical to the source.
- A non-empty face polygon that captures no foreground pixels is an error.
- A face polygon that includes more than 20% fully transparent background is rejected; shrink it to the visible face/mask.

Draw the face polygon **inside** the visible face area; do not include hair, hood, background or clothing. It is fine to include the surgical mask if the whole lower face is masked; it is part of the photo identity.

---

## 5. Patches

```json
"patches": {
  "enabled": true,
  "joints": ["head", "upper_arm_left", "upper_arm_right",
             "forearm_left", "forearm_right",
             "thigh_left", "thigh_right", "shin_left", "shin_right",
             "foot_left", "foot_right"],
  "scale": 1.0,
  "overlap_px": 8,
  "radii": {
    "shin_left": [45, 35]
  }
}
```

- `joints` uses the **child bone at that joint**:
  - `head` → neck patch
  - `upper_arm_*` → shoulder patch
  - `forearm_*` → elbow patch
  - `thigh_*` → hip patch
  - `shin_*` → knee patch
  - `foot_*` → ankle patch
- `scale` multiplies the default patch radii.
- `radii` overrides individual patches in **pixels** as `[rx, ry]`.
- `overlap_px` copies a small band of parent pixels into the child layer around the pivot. This reduces holes at rotations. Static QA is still exact because the copied pixels are original pixels. Set `0` to disable.
- Patches are synthetic fabric fills. They are drawn behind every body layer and clipped to `alpha == 255`, so they do not change the rest silhouette. They are invisible at rest by design.
- Patches are **not** recovered hidden anatomy. Do not claim otherwise.

A crossed-arm chest occlusion may need a larger custom torso underlay than the automatic patches can provide. The safe approach is to restrict arm rotation; the builder does not invent a hidden chest.

---

## 6. Rotation ranges

Optional; defaults are reasonable and conservative:

```json
"rotation_ranges": {
  "recommended": {
    "head": [-3, 3],
    "torso": [-2, 2],
    "thigh_left": [-4.5, 4.5],
    "thigh_right": [-4.5, 4.5],
    "shin_left": [0, 8],
    "shin_right": [0, 8]
  },
  "safe": {
    "upper_arm_left": [-5, 5],
    "upper_arm_right": [-5, 5]
  }
}
```

Angles are degrees. Positive follows OpenCV `getRotationMatrix2D` (counter-clockwise in image coordinates with +Y down). In engines whose positive rotation is clockwise, invert signs.

The builder only writes these values into `rig.json`; it does not enforce them at runtime.

---

## 7. Mirror pairs

```json
"mirror_pairs": [
  ["upper_arm_left", "upper_arm_right"],
  ["forearm_left", "forearm_right"],
  ["hand_left", "hand_right"],
  ["thigh_left", "thigh_right"],
  ["shin_left", "shin_right"],
  ["foot_left", "foot_right"]
]
```

This is **metadata only**. Do not mirror the actual source textures: the pose is asymmetric, and mirroring changes clothing folds and identity details.

---

## 8. Validation rules

`photopet validate project.json` checks:

- schema string;
- image exists and canvas matches the oriented image;
- alpha path (if supplied) exists;
- all standard bones exist, parents are valid, no cycles, pivots inside canvas;
- all standard parts exist, each bone referenced, z unique, only torso is fallback;
- non-empty parts have at least one ≥3-point polygon;
- empty parts have no polygons;
- all polygon points inside the canvas;
- face polygon (if present) has at least 3 points and is inside canvas;
- patch joints and radii are supported;
- mirror pairs and rotation ranges are structurally valid.

`photopet build` adds runtime checks:

- every foreground pixel is assigned to a part (at least 85% polygon coverage, nearest-part fill for fringes);
- every non-empty part captures pixels;
- layer reassembly is pixel-exact;
- patches do not change the rest pose;
- face RGB is byte-identical and face alpha is 255;
- no enclosed transparent holes at rest;
- preview and QA generation succeed.

---

## 9. Annotation workflow for an AI agent

1. Run `photopet inspect photo.jpg -o inspect/`. Use `inspect/grid.png` and the gridded crops; all labels are absolute pixel coordinates.
2. Create the transparent cutout with `photopet cutout` or an external matting tool. Inspect it for extra people/objects/halos. Use `--keep-largest` only when appropriate.
3. Use the example project as a schema template. Replace the canvas, all pivots and all polygons with coordinates for this photo.
4. Keep the full body in the original canvas. Do not resize the source.
5. Run `photopet validate project.json`.
6. Run `photopet build project.json --out <desktop> --force`.
7. Open `preview.png`, `preview_layers.png`, `preview_poses.png`, and read `qa_report.json`.
8. If the QA fails, fix the alpha or polygons and rebuild. Do not hide failures.
9. Report what the source cannot support (hidden hands, crossed arms, side/back views, facial expressions).
