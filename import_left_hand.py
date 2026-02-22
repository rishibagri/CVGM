"""
Script 5: import_left_hand.py

Import and rig a LEFT hand (mirrored from MANO_LEFT.pkl or MANO_RIGHT.pkl),
then reposition both hands above their default centre notes on the piano.

Assumes scripts 1-4 have already run, producing:
    - Key_21 .. Key_108 objects (piano)
    - MANO_RightHand mesh
    - MANO_Rig armature with IK empties (IK_thumb, IK_index, ...)
"""

import os
import sys
import math
import pickle

import bpy
import bmesh
import numpy as np
from mathutils import Vector, Matrix, Euler

# ---------------------------------------------------------------------------
# Import shared config
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config import (
    MANO_LEFT_PATH, MANO_RIGHT_PATH,
    LEFT_HAND_CENTER_NOTE, RIGHT_HAND_CENTER_NOTE,
    WRIST_HEIGHT, WRIST_Y_OFFSET,
    FINGER_NAMES, MANO_JOINT_INDICES,
    get_key_position,
)

# ---------------------------------------------------------------------------
# MANO pkl loader (handles chumpy stubs)
# ---------------------------------------------------------------------------

class ChumObject:
    """Stub for chumpy arrays stored inside MANO pickle files."""
    def __init__(self, *args, **kwargs):
        pass

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update(state)

    def __array__(self):
        # Attempt to extract underlying numpy data
        if hasattr(self, "x"):
            return np.array(self.x)
        return np.zeros(0)


class MANOUnpickler(pickle.Unpickler):
    """Unpickler that replaces chumpy references with plain stubs."""

    def find_class(self, module, name):
        if "chumpy" in module:
            return ChumObject
        if module == "__builtin__":
            import builtins
            return getattr(builtins, name)
        return super().find_class(module, name)


def _load_mano(pkl_path):
    """Load a MANO .pkl file and return the data dict."""
    with open(pkl_path, "rb") as f:
        data = MANOUnpickler(f, encoding="latin1").load()
    return data


def _extract_numpy(val):
    """Convert a value (possibly chumpy) to a plain numpy array."""
    if isinstance(val, np.ndarray):
        return val
    if hasattr(val, "r"):
        return np.array(val.r)
    if hasattr(val, "__array__"):
        return np.array(val)
    return np.array(val)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def build_left_hand():
    """Create the left-hand mesh, armature, and IK empties."""

    print("  [import_left_hand] Loading MANO data ...")

    # ------------------------------------------------------------------
    # 1. Load vertices and faces
    # ------------------------------------------------------------------
    mirror_from_right = False

    if os.path.isfile(MANO_LEFT_PATH):
        mano = _load_mano(MANO_LEFT_PATH)
    elif os.path.isfile(MANO_RIGHT_PATH):
        print("    MANO_LEFT.pkl not found — mirroring from MANO_RIGHT.pkl")
        mano = _load_mano(MANO_RIGHT_PATH)
        mirror_from_right = True
    else:
        raise FileNotFoundError(
            f"Neither {MANO_LEFT_PATH} nor {MANO_RIGHT_PATH} found."
        )

    v_template = _extract_numpy(mano["v_template"])  # (778, 3)
    faces = _extract_numpy(mano["f"]).astype(int)     # (1538, 3)
    joints_regressor = _extract_numpy(mano.get("J_regressor",
                                                mano.get("J")))
    weights = _extract_numpy(mano["weights"])          # (778, 16)

    if mirror_from_right:
        v_template = v_template.copy()
        v_template[:, 0] *= -1  # negate X
        # Reverse face winding so normals flip correctly
        faces = faces[:, ::-1].copy()

    # Compute joint positions from regressor or directly
    if joints_regressor is not None and joints_regressor.ndim == 2:
        if joints_regressor.shape[0] == 16 and joints_regressor.shape[1] == v_template.shape[0]:
            joints = joints_regressor @ v_template  # (16, 3)
        elif joints_regressor.shape[0] == v_template.shape[0]:
            joints = joints_regressor
        else:
            joints = joints_regressor @ v_template
    else:
        # Fallback: estimate joints from vertex groups
        joints = np.zeros((16, 3))
        for jidx in range(16):
            w_col = weights[:, jidx]
            if w_col.sum() > 0:
                joints[jidx] = (v_template * w_col[:, None]).sum(axis=0) / w_col.sum()

    if mirror_from_right:
        joints = joints.copy()
        joints[:, 0] *= -1

    print(f"    Vertices: {v_template.shape[0]}, Faces: {faces.shape[0]}")

    # ------------------------------------------------------------------
    # 2. Create mesh object
    # ------------------------------------------------------------------
    mesh_data = bpy.data.meshes.new("MANO_LeftHand_Mesh")
    mesh_obj = bpy.data.objects.new("MANO_LeftHand", mesh_data)
    bpy.context.collection.objects.link(mesh_obj)

    bm = bmesh.new()
    bm_verts = [bm.verts.new(Vector(v)) for v in v_template]
    bm.verts.ensure_lookup_table()

    for face in faces:
        try:
            bm.faces.new([bm_verts[i] for i in face])
        except ValueError:
            pass

    bm.to_mesh(mesh_data)
    bm.free()

    # Smooth shading
    for poly in mesh_data.polygons:
        poly.use_smooth = True

    # Rotation: align palm-down, fingers pointing +Y
    mesh_obj.rotation_euler = Euler((math.radians(90), 0, math.radians(90)), 'XYZ')
    bpy.context.view_layer.objects.active = mesh_obj
    mesh_obj.select_set(True)
    bpy.ops.object.transform_apply(rotation=True)
    mesh_obj.select_set(False)

    print("    Mesh 'MANO_LeftHand' created.")

    # ------------------------------------------------------------------
    # 3. Create armature
    # ------------------------------------------------------------------
    arm_data = bpy.data.armatures.new("MANO_Rig_Left_Data")
    arm_data.display_type = "OCTAHEDRAL"
    arm_obj = bpy.data.objects.new("MANO_Rig_Left", arm_data)
    bpy.context.collection.objects.link(arm_obj)

    # Apply same rotation to joint positions
    rot_mat = Euler((math.radians(90), 0, math.radians(90)), 'XYZ').to_matrix()

    rotated_joints = np.array([(rot_mat @ Vector(j)).to_tuple() for j in joints])

    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')

    # Bone creation
    bone_names = {}

    # Wrist bone
    wrist_pos = Vector(rotated_joints[0])
    # Average of MCP joints for palm end
    mcp_indices = [1, 4, 7, 10, 13]
    palm_end = Vector(np.mean(rotated_joints[mcp_indices], axis=0).tolist())

    wrist_bone = arm_data.edit_bones.new("L_wrist")
    wrist_bone.head = wrist_pos
    wrist_bone.tail = palm_end
    bone_names["L_wrist"] = wrist_bone

    # Finger bones: 3 segments + tip per finger
    seg_names = ["01", "02", "03"]
    for finger in FINGER_NAMES:
        joint_ids = MANO_JOINT_INDICES[finger]  # [MCP, PIP, DIP]
        parent = wrist_bone

        for si, seg in enumerate(seg_names):
            bone_name = f"L_{finger}_{seg}"
            bone = arm_data.edit_bones.new(bone_name)
            head_pos = Vector(rotated_joints[joint_ids[si]].tolist())
            if si < len(joint_ids) - 1:
                tail_pos = Vector(rotated_joints[joint_ids[si + 1]].tolist())
            else:
                # DIP to estimated fingertip: extend 60% of previous segment length
                prev_len = (head_pos - bone.parent.head if parent else Vector((0, 0.02, 0))).length
                direction = (head_pos - Vector(rotated_joints[joint_ids[si - 1]].tolist())).normalized()
                tail_pos = head_pos + direction * prev_len * 0.6

            bone.head = head_pos
            bone.tail = tail_pos
            bone.parent = parent
            bone.use_connect = (si > 0)
            bone_names[bone_name] = bone
            parent = bone

        # Tip bone (non-deforming, for IK)
        tip_name = f"L_{finger}_tip"
        tip_bone = arm_data.edit_bones.new(tip_name)
        tip_bone.head = parent.tail.copy()
        direction = (parent.tail - parent.head).normalized()
        tip_bone.tail = parent.tail + direction * 0.01
        tip_bone.parent = parent
        tip_bone.use_connect = True
        tip_bone.use_deform = False
        bone_names[tip_name] = tip_bone

    bpy.ops.object.mode_set(mode='OBJECT')

    print("    Armature 'MANO_Rig_Left' created.")

    # ------------------------------------------------------------------
    # 4. Create IK target empties
    # ------------------------------------------------------------------
    ik_empties = {}
    for finger in FINGER_NAMES:
        ik_name = f"IK_L_{finger}"
        tip_bone_name = f"L_{finger}_tip"

        # Get tip position from armature
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode='EDIT')
        tip_bone = arm_data.edit_bones.get(tip_bone_name)
        tip_pos = tip_bone.head.copy() if tip_bone else Vector((0, 0, 0))
        bpy.ops.object.mode_set(mode='OBJECT')

        ik_empty = bpy.data.objects.new(ik_name, None)
        ik_empty.empty_display_type = 'SPHERE'
        ik_empty.empty_display_size = 0.005
        ik_empty.location = tip_pos
        bpy.context.collection.objects.link(ik_empty)
        ik_empties[finger] = ik_empty

    # Wrist IK empty
    ik_wrist = bpy.data.objects.new("IK_L_wrist", None)
    ik_wrist.empty_display_type = 'CUBE'
    ik_wrist.empty_display_size = 0.008
    ik_wrist.location = wrist_pos
    bpy.context.collection.objects.link(ik_wrist)
    ik_empties["wrist"] = ik_wrist

    print("    IK empties created (IK_L_thumb ... IK_L_pinky, IK_L_wrist).")

    # ------------------------------------------------------------------
    # 5. Add IK constraints
    # ------------------------------------------------------------------
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='POSE')

    for finger in FINGER_NAMES:
        tip_bone_name = f"L_{finger}_tip"
        ik_name = f"IK_L_{finger}"

        pbone = arm_obj.pose.bones.get(tip_bone_name)
        if pbone is None:
            print(f"    Warning: pose bone '{tip_bone_name}' not found, skipping IK.")
            continue

        ik_con = pbone.constraints.new('IK')
        ik_con.name = "FingerIK"
        ik_con.target = ik_empties[finger]
        ik_con.chain_count = 3
        ik_con.use_rotation = False

    bpy.ops.object.mode_set(mode='OBJECT')

    print("    IK constraints applied.")

    # ------------------------------------------------------------------
    # 6. Skinning weights
    # ------------------------------------------------------------------
    # Create vertex groups on the mesh matching bone names
    weight_bone_map = {}
    weight_bone_map["L_wrist"] = 0
    for finger in FINGER_NAMES:
        jids = MANO_JOINT_INDICES[finger]
        for si, seg in enumerate(seg_names):
            bone_name = f"L_{finger}_{seg}"
            weight_bone_map[bone_name] = jids[si]

    for bone_name, joint_idx in weight_bone_map.items():
        vg = mesh_obj.vertex_groups.new(name=bone_name)
        for vi in range(len(v_template)):
            w = float(weights[vi, joint_idx])
            if w > 0.001:
                vg.add([vi], w, 'REPLACE')

    # Parent mesh to armature
    bpy.ops.object.select_all(action='DESELECT')
    mesh_obj.select_set(True)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.parent_set(type='ARMATURE_NAME')

    bpy.ops.object.select_all(action='DESELECT')

    print("    Skinning weights applied and mesh parented to armature.")

    # ------------------------------------------------------------------
    # 7. Position left hand over LEFT_HAND_CENTER_NOTE
    # ------------------------------------------------------------------
    left_key_pos = get_key_position(LEFT_HAND_CENTER_NOTE)
    arm_obj.location = Vector((
        left_key_pos[0],
        left_key_pos[1] + WRIST_Y_OFFSET,
        left_key_pos[2] + WRIST_HEIGHT,
    ))

    print(f"    Left hand positioned above note {LEFT_HAND_CENTER_NOTE} "
          f"({left_key_pos[0]:.3f}, {left_key_pos[1]:.3f}).")

    # ------------------------------------------------------------------
    # 8. Reposition right hand over RIGHT_HAND_CENTER_NOTE
    # ------------------------------------------------------------------
    right_rig = bpy.data.objects.get("MANO_Rig")
    if right_rig:
        right_key_pos = get_key_position(RIGHT_HAND_CENTER_NOTE)
        right_rig.location = Vector((
            right_key_pos[0],
            right_key_pos[1] + WRIST_Y_OFFSET,
            right_key_pos[2] + WRIST_HEIGHT,
        ))
        print(f"    Right hand repositioned above note {RIGHT_HAND_CENTER_NOTE}.")
    else:
        print("    Warning: 'MANO_Rig' not found — skipping right hand repositioning.")

    print("  [import_left_hand] Done.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__" or True:
    build_left_hand()
