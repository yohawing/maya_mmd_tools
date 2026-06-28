"""MMD standard bone to Maya HumanIK bone mapping.

This module keeps the HumanIK naming table separate from scene mutation code so
the resolver and UI layers can share one audited source of truth.
"""

from types import MappingProxyType
from typing import Mapping


HIK_BONE_INDICES: Mapping[str, int] = MappingProxyType(
    {
        "Reference": 0,
        "Hips": 1,
        "LeftUpLeg": 2,
        "LeftLeg": 3,
        "LeftFoot": 4,
        "RightUpLeg": 5,
        "RightLeg": 6,
        "RightFoot": 7,
        "Spine": 8,
        "LeftArm": 9,
        "LeftForeArm": 10,
        "LeftHand": 11,
        "RightArm": 12,
        "RightForeArm": 13,
        "RightHand": 14,
        "Head": 15,
        "LeftToeBase": 16,
        "RightToeBase": 17,
        "LeftShoulder": 18,
        "RightShoulder": 19,
        "Neck": 20,
        "Spine1": 23,
        "Spine2": 24,
        "Spine3": 25,
        "Spine4": 26,
        "Spine5": 27,
        "Spine6": 28,
        "Spine7": 29,
        "Spine8": 30,
        "Spine9": 31,
        "Neck1": 32,
        "Neck2": 33,
        "Neck3": 34,
        "Neck4": 35,
        "Neck5": 36,
        "Neck6": 37,
        "Neck7": 38,
        "Neck8": 39,
        "Neck9": 40,
        "LeftUpLegRoll": 41,
        "LeftLegRoll": 42,
        "RightUpLegRoll": 43,
        "RightLegRoll": 44,
        "LeftArmRoll": 45,
        "LeftForeArmRoll": 46,
        "RightArmRoll": 47,
        "RightForeArmRoll": 48,
        "LeftHandThumb1": 50,
        "LeftHandThumb2": 51,
        "LeftHandThumb3": 52,
        "LeftHandThumb4": 53,
        "LeftHandIndex1": 54,
        "LeftHandIndex2": 55,
        "LeftHandIndex3": 56,
        "LeftHandIndex4": 57,
        "LeftHandMiddle1": 58,
        "LeftHandMiddle2": 59,
        "LeftHandMiddle3": 60,
        "LeftHandMiddle4": 61,
        "LeftHandRing1": 62,
        "LeftHandRing2": 63,
        "LeftHandRing3": 64,
        "LeftHandRing4": 65,
        "LeftHandPinky1": 66,
        "LeftHandPinky2": 67,
        "LeftHandPinky3": 68,
        "LeftHandPinky4": 69,
        "RightHandThumb1": 74,
        "RightHandThumb2": 75,
        "RightHandThumb3": 76,
        "RightHandThumb4": 77,
        "RightHandIndex1": 78,
        "RightHandIndex2": 79,
        "RightHandIndex3": 80,
        "RightHandIndex4": 81,
        "RightHandMiddle1": 82,
        "RightHandMiddle2": 83,
        "RightHandMiddle3": 84,
        "RightHandMiddle4": 85,
        "RightHandRing1": 86,
        "RightHandRing2": 87,
        "RightHandRing3": 88,
        "RightHandRing4": 89,
        "RightHandPinky1": 90,
        "RightHandPinky2": 91,
        "RightHandPinky3": 92,
        "RightHandPinky4": 93,
    }
)


MMD_TO_HIK_BONE: Mapping[str, str] = MappingProxyType(
    {
        "下半身": "Hips",
        "上半身": "Spine",
        "上半身2": "Spine1",
        "首": "Neck",
        "頭": "Head",
        "左目": "LeftEye",
        "右目": "RightEye",
        "左肩": "LeftShoulder",
        "左腕": "LeftArm",
        "左ひじ": "LeftForeArm",
        "左手首": "LeftHand",
        "右肩": "RightShoulder",
        "右腕": "RightArm",
        "右ひじ": "RightForeArm",
        "右手首": "RightHand",
        "左足": "LeftUpLeg",
        "左ひざ": "LeftLeg",
        "左足首": "LeftFoot",
        "左つま先": "LeftToeBase",
        "右足": "RightUpLeg",
        "右ひざ": "RightLeg",
        "右足首": "RightFoot",
        "右つま先": "RightToeBase",
        "左親指０": "LeftHandThumb1",
        "左親指１": "LeftHandThumb2",
        "左親指２": "LeftHandThumb3",
        "左人指１": "LeftHandIndex1",
        "左人指２": "LeftHandIndex2",
        "左人指３": "LeftHandIndex3",
        "左中指１": "LeftHandMiddle1",
        "左中指２": "LeftHandMiddle2",
        "左中指３": "LeftHandMiddle3",
        "左薬指１": "LeftHandRing1",
        "左薬指２": "LeftHandRing2",
        "左薬指３": "LeftHandRing3",
        "左小指１": "LeftHandPinky1",
        "左小指２": "LeftHandPinky2",
        "左小指３": "LeftHandPinky3",
        "右親指０": "RightHandThumb1",
        "右親指１": "RightHandThumb2",
        "右親指２": "RightHandThumb3",
        "右人指１": "RightHandIndex1",
        "右人指２": "RightHandIndex2",
        "右人指３": "RightHandIndex3",
        "右中指１": "RightHandMiddle1",
        "右中指２": "RightHandMiddle2",
        "右中指３": "RightHandMiddle3",
        "右薬指１": "RightHandRing1",
        "右薬指２": "RightHandRing2",
        "右薬指３": "RightHandRing3",
        "右小指１": "RightHandPinky1",
        "右小指２": "RightHandPinky2",
        "右小指３": "RightHandPinky3",
        "左腕捻": "LeftArmRoll",
        "左手捻": "LeftForeArmRoll",
        "右腕捻": "RightArmRoll",
        "右手捻": "RightForeArmRoll",
    }
)


MMD_TO_HIK_BONE_INDEX: Mapping[str, int] = MappingProxyType(
    {
        mmd_bone: HIK_BONE_INDICES[hik_bone]
        for mmd_bone, hik_bone in MMD_TO_HIK_BONE.items()
        if hik_bone in HIK_BONE_INDICES
    }
)


MMD_TO_HIK_UNINDEXED_BONES: Mapping[str, str] = MappingProxyType(
    {
        mmd_bone: hik_bone
        for mmd_bone, hik_bone in MMD_TO_HIK_BONE.items()
        if hik_bone not in HIK_BONE_INDICES
    }
)
