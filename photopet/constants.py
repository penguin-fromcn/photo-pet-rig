"""Shared schema constants for the photo-pet-rig builder."""

SCHEMA = "photopet/project@1"
VERSION = "0.1.0"

STANDARD_PARTS = [
    "head",
    "torso",
    "upper_arm_left",
    "forearm_left",
    "hand_left",
    "upper_arm_right",
    "forearm_right",
    "hand_right",
    "thigh_left",
    "shin_left",
    "foot_left",
    "thigh_right",
    "shin_right",
    "foot_right",
]

REQUIRED_BODY_PARTS = [p for p in STANDARD_PARTS if not p.startswith("hand_")]

STANDARD_BONES = [
    "root",
    "torso",
    "head",
    "upper_arm_left",
    "forearm_left",
    "hand_left",
    "upper_arm_right",
    "forearm_right",
    "hand_right",
    "thigh_left",
    "shin_left",
    "foot_left",
    "thigh_right",
    "shin_right",
    "foot_right",
]

# Back-to-front defaults. Patches are inserted below all body layers by the builder.
DEFAULT_Z = {
    "thigh_right": 20,
    "thigh_left": 21,
    "shin_right": 22,
    "shin_left": 23,
    "foot_right": 24,
    "foot_left": 25,
    "torso": 30,
    "upper_arm_right": 40,
    "upper_arm_left": 41,
    "forearm_right": 42,
    "forearm_left": 43,
    "hand_right": 44,
    "hand_left": 45,
    "head": 50,
}

DEFAULT_ROTATION_RECOMMENDED = {
    "root": [-2.0, 2.0],
    "torso": [-2.0, 2.0],
    "head": [-3.0, 3.0],
    "upper_arm_left": [-2.0, 2.0],
    "upper_arm_right": [-2.0, 2.0],
    "forearm_left": [-1.5, 1.5],
    "forearm_right": [-1.5, 1.5],
    "hand_left": [0.0, 0.0],
    "hand_right": [0.0, 0.0],
    "thigh_left": [-4.5, 4.5],
    "thigh_right": [-4.5, 4.5],
    "shin_left": [0.0, 8.0],
    "shin_right": [0.0, 8.0],
    "foot_left": [-2.5, 2.5],
    "foot_right": [-2.5, 2.5],
}

DEFAULT_ROTATION_SAFE = {
    "root": [-5.0, 5.0],
    "torso": [-3.0, 3.0],
    "head": [-5.0, 5.0],
    "upper_arm_left": [-5.0, 5.0],
    "upper_arm_right": [-5.0, 5.0],
    "forearm_left": [-4.0, 4.0],
    "forearm_right": [-4.0, 4.0],
    "hand_left": [0.0, 0.0],
    "hand_right": [0.0, 0.0],
    "thigh_left": [-7.0, 7.0],
    "thigh_right": [-7.0, 7.0],
    "shin_left": [-2.0, 14.0],
    "shin_right": [-2.0, 14.0],
    "foot_left": [-5.0, 5.0],
    "foot_right": [-5.0, 5.0],
}

DEFAULT_MIRROR_PAIRS = [
    ["upper_arm_left", "upper_arm_right"],
    ["forearm_left", "forearm_right"],
    ["hand_left", "hand_right"],
    ["thigh_left", "thigh_right"],
    ["shin_left", "shin_right"],
    ["foot_left", "foot_right"],
]

DEFAULT_PATCH_JOINTS = [
    "head",
    "upper_arm_left",
    "upper_arm_right",
    "forearm_left",
    "forearm_right",
    "thigh_left",
    "thigh_right",
    "shin_left",
    "shin_right",
    "foot_left",
    "foot_right",
]

# bone -> (patch_id, parent body part, parent bone)
JOINT_INFO = {
    "head": ("neck", "torso", "torso"),
    "upper_arm_left": ("shoulder_left", "torso", "torso"),
    "upper_arm_right": ("shoulder_right", "torso", "torso"),
    "forearm_left": ("elbow_left", "upper_arm_left", "upper_arm_left"),
    "forearm_right": ("elbow_right", "upper_arm_right", "upper_arm_right"),
    "thigh_left": ("hip_left", "torso", "torso"),
    "thigh_right": ("hip_right", "torso", "torso"),
    "shin_left": ("knee_left", "thigh_left", "thigh_left"),
    "shin_right": ("knee_right", "thigh_right", "thigh_right"),
    "foot_left": ("ankle_left", "shin_left", "shin_left"),
    "foot_right": ("ankle_right", "shin_right", "shin_right"),
}

# Radii in pixels at a 2000 px tall canvas; scaled to the actual canvas.
PATCH_RADII = {
    "head": (0.075, 0.034),
    "upper_arm_left": (0.050, 0.035),
    "upper_arm_right": (0.050, 0.035),
    "forearm_left": (0.033, 0.027),
    "forearm_right": (0.033, 0.027),
    "thigh_left": (0.042, 0.032),
    "thigh_right": (0.042, 0.032),
    "shin_left": (0.031, 0.027),
    "shin_right": (0.031, 0.027),
    "foot_left": (0.028, 0.022),
    "foot_right": (0.028, 0.022),
}
