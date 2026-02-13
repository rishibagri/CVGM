"""
Hand armature / rig builder.

Creates a fully articulated skeleton for the procedural hand mesh, with:
- Correct bone hierarchy (wrist -> palm -> fingers)
- IK-ready chain for each finger
- Bone constraints for realistic curl / splay limits
- Automatic weight-paint binding to the hand mesh
"""

import math
from typing import Dict, Tuple

import bpy
from mathutils import Vector, Euler

from .mesh_builder import FINGER_DATA, age_profile


# ---------------------------------------------------------------------------
# Bone layout constants
# ---------------------------------------------------------------------------

# Rotation limits per joint (min, max) in degrees.  Negative = extension,
# positive = flexion for the primary curl axis.
JOINT_LIMITS = {
    # (curl_min, curl_max, spread_min, spread_max)
    "thumb_proximal":  (-10, 60,  -30, 30),
    "thumb_middle":    (-5,  80,   -5,  5),
    "thumb_distal":    (-5,  70,   -5,  5),
    "index_proximal":  (-15, 90,  -15, 20),
    "index_middle":    (0,   110,  -2,  2),
    "index_distal":    (0,   80,   -2,  2),
    "middle_proximal": (-15, 90,  -10, 10),
    "middle_middle":   (0,   110,  -2,  2),
    "middle_distal":   (0,   80,   -2,  2),
    "ring_proximal":   (-15, 90,  -15, 10),
    "ring_middle":     (0,   110,  -2,  2),
    "ring_distal":     (0,   80,   -2,  2),
    "pinky_proximal":  (-15, 90,  -20, 15),
    "pinky_middle":    (0,   110,  -2,  2),
    "pinky_distal":    (0,   80,   -2,  2),
}


class HandRigBuilder:
    """Build a complete hand armature and bind it to a mesh."""

    def __init__(self, age: int, side: str = "RIGHT", scale: float = 1.0):
        self.profile = age_profile(age)
        self.side = side
        self.scale = scale * self.profile.size_factor * 0.18
        self.mirror = -1.0 if side == "LEFT" else 1.0

    def build(self, mesh_obj: bpy.types.Object) -> bpy.types.Object:
        """Create the armature, parent the mesh, and bind weights."""
        arm_data = bpy.data.armatures.new(f"Armature_{self.side}")
        arm_data.display_type = "OCTAHEDRAL"
        arm_obj = bpy.data.objects.new(f"Rig_{self.side}", arm_data)
        bpy.context.collection.objects.link(arm_obj)

        # Enter edit mode on the armature
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="EDIT")

        bones_info: Dict[str, bpy.types.EditBone] = {}

        # --- Wrist bone ---
        wrist = arm_data.edit_bones.new("wrist")
        wrist.head = Vector((0, -0.02 * self.scale / 0.18, 0))
        wrist.tail = Vector((0, 0, 0))
        wrist.roll = 0
        bones_info["wrist"] = wrist

        # --- Palm bone ---
        palm = arm_data.edit_bones.new("palm")
        palm.head = Vector((0, 0, 0))
        palm.tail = Vector((0, 0.45 * self.scale, 0))
        palm.parent = wrist
        palm.use_connect = True
        bones_info["palm"] = palm

        # --- Finger bones ---
        for fname, (bx, by, plens, _) in FINGER_DATA.items():
            parent_bone = palm
            s = self.scale

            # Finger base position
            fx = bx * s * self.mirror
            fy = by * s

            if fname == "thumb":
                fwd = Vector((self.mirror * -0.5, 0.75, 0.15)).normalized()
            else:
                fwd = Vector((0, 1, 0))

            cursor = Vector((fx, fy, 0))

            for pi, (seg_name, plen) in enumerate(
                zip(["proximal", "middle", "distal"], plens)
            ):
                seg_len = plen * s
                bone_name = f"{fname}_{seg_name}"

                bone = arm_data.edit_bones.new(bone_name)
                bone.head = cursor.copy()
                bone.tail = cursor + fwd * seg_len
                bone.parent = parent_bone
                bone.use_connect = (pi > 0)

                # Roll: fingers aligned with Z-up curl axis
                if fname == "thumb":
                    bone.roll = math.radians(45 * self.mirror)
                else:
                    bone.roll = 0

                bones_info[bone_name] = bone
                parent_bone = bone
                cursor = bone.tail.copy()

            # Tip bone (non-deforming, for IK target)
            tip = arm_data.edit_bones.new(f"{fname}_tip")
            tip.head = cursor.copy()
            tip.tail = cursor + fwd * (0.02 * s)
            tip.parent = parent_bone
            tip.use_connect = True
            tip.use_deform = False
            bones_info[f"{fname}_tip"] = tip

        bpy.ops.object.mode_set(mode="OBJECT")

        # --- Pose-mode constraints ---
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="POSE")

        for bone_name, limits in JOINT_LIMITS.items():
            pbone = arm_obj.pose.bones.get(bone_name)
            if pbone is None:
                continue

            curl_min, curl_max, spread_min, spread_max = limits

            # Limit rotation constraint
            con = pbone.constraints.new("LIMIT_ROTATION")
            con.name = "JointLimits"
            con.owner_space = "LOCAL"

            con.use_limit_x = True
            con.min_x = math.radians(curl_min)
            con.max_x = math.radians(curl_max)

            con.use_limit_z = True
            con.min_z = math.radians(spread_min)
            con.max_z = math.radians(spread_max)

            con.use_limit_y = True
            con.min_y = math.radians(-5)
            con.max_y = math.radians(5)

        bpy.ops.object.mode_set(mode="OBJECT")

        # --- Parent mesh to armature with automatic weights ---
        mesh_obj.select_set(True)
        arm_obj.select_set(True)
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")

        # Deselect all
        bpy.ops.object.select_all(action="DESELECT")

        return arm_obj

    def add_piano_ik_targets(self, arm_obj: bpy.types.Object):
        """Add IK targets for each fingertip to enable piano playing."""
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="EDIT")
        arm_data = arm_obj.data

        ik_targets = {}
        for fname in ["thumb", "index", "middle", "ring", "pinky"]:
            tip_bone = arm_data.edit_bones.get(f"{fname}_tip")
            if tip_bone is None:
                continue

            # Create an IK target bone (free-floating)
            ik_name = f"{fname}_ik_target"
            ik_bone = arm_data.edit_bones.new(ik_name)
            ik_bone.head = tip_bone.head.copy()
            ik_bone.tail = tip_bone.head + Vector((0, 0, -0.02 * self.scale / 0.18))
            ik_bone.use_deform = False
            # No parent - free to be keyframed independently
            ik_targets[fname] = ik_name

        bpy.ops.object.mode_set(mode="POSE")

        # Add IK constraints to the distal bones
        for fname in ["thumb", "index", "middle", "ring", "pinky"]:
            distal_name = f"{fname}_distal"
            ik_target_name = f"{fname}_ik_target"

            pbone = arm_obj.pose.bones.get(distal_name)
            if pbone is None:
                continue

            ik = pbone.constraints.new("IK")
            ik.name = "PianoIK"
            ik.target = arm_obj
            ik.subtarget = ik_target_name
            ik.chain_count = 3  # proximal -> middle -> distal
            ik.use_rotation = True

        bpy.ops.object.mode_set(mode="OBJECT")
        return ik_targets
