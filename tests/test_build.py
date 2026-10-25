from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from PIL import Image

from photopet.builder import BuildError, build
from photopet.project import load_project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "mannequin" / "project.json"


class ExampleBuildTest(unittest.TestCase):
    def test_example_project_validates(self):
        project = load_project(EXAMPLE)
        self.assertEqual(project.width, 900)
        self.assertEqual(project.height, 1600)
        self.assertEqual(len(project.parts), 14)
        self.assertIsNotNone(project.face_polygon)
        self.assertTrue(project.parts["torso"].fallback)

    def test_face_polygon_outside_foreground_is_rejected(self):
        tmp = ROOT / "tests" / "_face_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
            data["image"] = str((EXAMPLE.parent / "photo.png").resolve())
            data["alpha"] = str((EXAMPLE.parent / "alpha.png").resolve())
            # Top-left is empty background in the mannequin example.
            data["face"] = {"polygon": [[0, 0], [80, 0], [80, 80], [0, 80]]}
            project_path = tmp / "project.json"
            project_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(BuildError):
                build(project_path, tmp / "out", make_zip=False, force=False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_example_builds_and_reassembles(self):
        tmp = ROOT / "tests" / "_build_tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            result = build(
                EXAMPLE,
                tmp,
                make_zip=False,
                force=False,
            )
            folder = result.folder
            self.assertTrue(folder.is_dir())
            self.assertTrue((folder / "rig.json").is_file())
            self.assertTrue((folder / "qa_report.json").is_file())
            self.assertTrue((folder / "preview.png").is_file())
            self.assertTrue((folder / "preview_layers.png").is_file())
            self.assertTrue((folder / "preview_rig.png").is_file())
            self.assertTrue((folder / "preview_poses.png").is_file())

            rig = json.loads((folder / "rig.json").read_text(encoding="utf-8"))
            qa = json.loads((folder / "qa_report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rig["layers"]), 25)  # 14 body + 11 patches
            self.assertEqual(len(rig["draw_order"]), 25)
            self.assertEqual(rig["layers"][0]["source_kind"], "synthetic_repair_patch")
            self.assertTrue(qa["summary"]["exact_layer_reassembly"])
            self.assertTrue(qa["summary"]["patches_hidden_at_rest"])
            self.assertTrue(qa["summary"]["zero_enclosed_holes_at_rest"])
            self.assertTrue(qa["summary"]["face_pixels_unchanged"])
            self.assertEqual(qa["checks"]["face"]["alpha_min"], 255)

            # Every body PNG is the same full canvas and RGBA.
            for name in [
                "character_original.png", "head.png", "torso.png",
                "upper_arm_left.png", "forearm_left.png", "hand_left.png",
                "upper_arm_right.png", "forearm_right.png", "hand_right.png",
                "thigh_left.png", "shin_left.png", "foot_left.png",
                "thigh_right.png", "shin_right.png", "foot_right.png",
            ]:
                with Image.open(folder / name) as im:
                    self.assertEqual(im.size, (900, 1600))
                    self.assertEqual(im.mode, "RGBA")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
