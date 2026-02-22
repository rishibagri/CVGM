"""
Script 7: ik_animator.py

Read finger assignments, compute 3D fingertip target positions over time,
and keyframe the IK empties to animate both hands playing the piano.

This is the most complex script in the pipeline.  It drives all animation
by moving IK target empties; the IK constraints on each hand's armature
automatically solve the finger bone chain.
"""

import os
import sys
import json
import math

import bpy
from mathutils import Vector

# ---------------------------------------------------------------------------
# Import shared config
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config import (
    FPS, ANTICIPATION_FRAMES, RELEASE_FRAMES, HOVER_HEIGHT,
    KEY_PRESS_DEPTH, WRIST_HEIGHT, WRIST_Y_OFFSET, OUTPUT_DIR,
    RIGHT_HAND_CENTER_NOTE, LEFT_HAND_CENTER_NOTE,
    W_WIDTH, BLACK_INDICES, FINGER_NAMES,
    get_key_position, note_is_white, white_key_count_in_range,
    smooth_value,
)


# ---------------------------------------------------------------------------
# Finger ↔ IK empty name mapping
# ---------------------------------------------------------------------------

FINGER_NUM_TO_NAME = {1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "pinky"}

FINGER_IK_MAP_R = {
    1: "IK_thumb",
    2: "IK_index",
    3: "IK_middle",
    4: "IK_ring",
    5: "IK_pinky",
}

FINGER_IK_MAP_L = {
    1: "IK_L_thumb",
    2: "IK_L_index",
    3: "IK_L_middle",
    4: "IK_L_ring",
    5: "IK_L_pinky",
}

# Default rest offsets (semitones from hand centre) — same as finger_assigner
_REST_OFFSETS_R = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7}
_REST_OFFSETS_L = {5: 0, 4: 2, 3: 4, 2: 5, 1: 7}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_assignments():
    """Load finger assignments from the FingerAssignments empty or JSON."""
    # Try Blender empty first
    empty = bpy.data.objects.get("FingerAssignments")
    if empty and "assignments_json" in empty:
        data = json.loads(empty["assignments_json"])
        return data

    # Fallback: JSON file
    json_path = os.path.join(OUTPUT_DIR, "finger_assignments.json")
    if os.path.isfile(json_path):
        with open(json_path, "r") as f:
            return json.load(f)

    raise FileNotFoundError(
        "No finger assignments found.  Run finger_assigner.py first."
    )


def _time_to_frame(t):
    """Convert seconds to Blender frame number (1-based)."""
    return int(round(t * FPS)) + 1


def _set_ik_keyframe(obj, frame, x, y, z, interp='BEZIER'):
    """Insert a location keyframe on an object at the given frame."""
    obj.location = (x, y, z)
    obj.keyframe_insert(data_path="location", frame=frame)

    # Set interpolation type
    if obj.animation_data and obj.animation_data.action:
        for fc in obj.animation_data.action.fcurves:
            if fc.data_path == "location":
                for kp in fc.keyframe_points:
                    if abs(kp.co[0] - frame) < 0.5:
                        kp.interpolation = interp
                        if interp == 'BEZIER':
                            kp.handle_left_type = 'AUTO_CLAMPED'
                            kp.handle_right_type = 'AUTO_CLAMPED'


def _get_rest_position(finger_num, hand, center_note):
    """Compute default hover position for a finger not currently playing."""
    offsets = _REST_OFFSETS_R if hand == "R" else _REST_OFFSETS_L
    rest_note = center_note + offsets.get(finger_num, 0)
    rest_note = max(21, min(108, rest_note))

    key_pos = get_key_position(rest_note)
    return (key_pos[0], key_pos[1], key_pos[2] + HOVER_HEIGHT)


# ---------------------------------------------------------------------------
# Main animation logic
# ---------------------------------------------------------------------------

def run_ik_animator():
    """Animate all IK empties from the finger assignments."""

    print("  [ik_animator] Loading finger assignments ...")
    data = _load_assignments()
    events = data["events"]
    metadata = data["metadata"]
    duration = metadata["duration_seconds"]

    print(f"    {metadata['total_notes']} notes, {duration:.2f}s duration.")

    # Separate events by hand
    right_events = [e for e in events if e.get("hand") == "R"]
    left_events = [e for e in events if e.get("hand") == "L"]

    # Set scene frame range
    total_frames = _time_to_frame(duration) + FPS * 2  # +2s padding
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = max(bpy.context.scene.frame_end, total_frames)
    bpy.context.scene.render.fps = FPS

    print(f"    Frame range: 1 – {total_frames}")

    # Animate each hand
    if right_events:
        print(f"  [ik_animator] Animating RIGHT hand ({len(right_events)} notes) ...")
        _animate_hand(right_events, hand="R")

    if left_events:
        print(f"  [ik_animator] Animating LEFT hand ({len(left_events)} notes) ...")
        _animate_hand(left_events, hand="L")

    # Smooth all IK F-curves
    print("  [ik_animator] Smoothing F-curves ...")
    _smooth_all_ik_fcurves()

    print("  [ik_animator] Done.\n")


def _animate_hand(events, hand="R"):
    """Keyframe the IK empties for one hand."""

    ik_map = FINGER_IK_MAP_R if hand == "R" else FINGER_IK_MAP_L
    rest_offsets = _REST_OFFSETS_R if hand == "R" else _REST_OFFSETS_L
    center_note = RIGHT_HAND_CENTER_NOTE if hand == "R" else LEFT_HAND_CENTER_NOTE
    wrist_ik_name = "IK_wrist" if hand == "R" else "IK_L_wrist"
    rig_name = "MANO_Rig" if hand == "R" else "MANO_Rig_Left"

    rig_obj = bpy.data.objects.get(rig_name)

    # ---- Verify IK empties exist ----
    for finger_num, ik_name in ik_map.items():
        obj = bpy.data.objects.get(ik_name)
        if obj is None:
            print(f"    Warning: IK empty '{ik_name}' not found — skipping.")
            return

    # ---- Group events by finger ----
    finger_events = {f: [] for f in range(1, 6)}
    for evt in events:
        fnum = evt.get("finger")
        if fnum and fnum in finger_events:
            finger_events[fnum].append(evt)

    # Sort each finger's events by time
    for f in finger_events:
        finger_events[f].sort(key=lambda e: e["time_on"])

    # ---- Set initial rest positions for all fingers (frame 1) ----
    for finger_num in range(1, 6):
        ik_name = ik_map[finger_num]
        ik_obj = bpy.data.objects.get(ik_name)
        if ik_obj is None:
            continue
        rest = _get_rest_position(finger_num, hand, center_note)
        _set_ik_keyframe(ik_obj, 1, rest[0], rest[1], rest[2], 'BEZIER')

    # ---- Animate each finger ----
    current_center = center_note

    for finger_num in range(1, 6):
        ik_name = ik_map[finger_num]
        ik_obj = bpy.data.objects.get(ik_name)
        if ik_obj is None:
            continue

        fing_events = finger_events[finger_num]
        if not fing_events:
            continue

        for ei, evt in enumerate(fing_events):
            note = evt["note"]
            t_on = evt["time_on"]
            t_off = evt["time_off"]

            frame_on = _time_to_frame(t_on)
            frame_off = _time_to_frame(t_off)

            # Key target position
            key_pos = get_key_position(note)
            press_x = key_pos[0]
            press_y = key_pos[1]
            press_z = key_pos[2] - KEY_PRESS_DEPTH

            hover_x = press_x
            hover_y = press_y
            hover_z = key_pos[2] + HOVER_HEIGHT

            # --- Approach phase ---
            antic_frame = max(1, frame_on - ANTICIPATION_FRAMES)

            # Where the finger was before this note
            if ei == 0:
                prev_rest = _get_rest_position(finger_num, hand, current_center)
                from_x, from_y, from_z = prev_rest
            else:
                # Hover above the previous note's key
                prev_note = fing_events[ei - 1]["note"]
                prev_pos = get_key_position(prev_note)
                from_x = prev_pos[0]
                from_y = prev_pos[1]
                from_z = prev_pos[2] + HOVER_HEIGHT

            # Keyframe: pre-approach (where finger is before moving)
            pre_antic = max(1, antic_frame - 2)
            _set_ik_keyframe(ik_obj, pre_antic, from_x, from_y, from_z, 'BEZIER')

            # Keyframe: hover above target key
            _set_ik_keyframe(ik_obj, antic_frame, hover_x, hover_y, hover_z, 'BEZIER')

            # --- Press phase ---
            _set_ik_keyframe(ik_obj, frame_on, press_x, press_y, press_z, 'BEZIER')

            # --- Hold phase ---
            if frame_off - frame_on > 3:
                mid_frame = (frame_on + frame_off) // 2
                _set_ik_keyframe(ik_obj, mid_frame, press_x, press_y, press_z, 'BEZIER')

            # Keyframe at note_off (still pressed)
            _set_ik_keyframe(ik_obj, frame_off, press_x, press_y, press_z, 'BEZIER')

            # --- Release phase ---
            release_frame = frame_off + RELEASE_FRAMES
            _set_ik_keyframe(ik_obj, release_frame, hover_x, hover_y, hover_z, 'BEZIER')

    # ---- Collision avoidance (simple Y offset) ----
    _apply_collision_offsets(events, ik_map, hand)

    # ---- Wrist tracking ----
    _animate_wrist(events, hand, ik_map, wrist_ik_name, rig_obj)

    # ---- Hand lateral sliding ----
    _animate_hand_slide(events, hand, rig_obj, center_note)


def _apply_collision_offsets(events, ik_map, hand):
    """If two fingers of the same hand target keys within 1 white key,
    nudge their Y positions slightly apart to avoid mesh overlap."""

    # Group simultaneous events
    from collections import defaultdict
    time_groups = defaultdict(list)
    for evt in events:
        # Quantise to frame
        frame = _time_to_frame(evt["time_on"])
        time_groups[frame].append(evt)

    for frame, group in time_groups.items():
        if len(group) < 2:
            continue

        group.sort(key=lambda e: e["note"])
        for i in range(len(group) - 1):
            a = group[i]
            b = group[i + 1]
            if a["finger"] == b["finger"]:
                continue

            note_diff = abs(a["note"] - b["note"])
            if note_diff <= 2:  # within 1-2 semitones
                # Apply small Y offsets
                ik_a = bpy.data.objects.get(ik_map.get(a["finger"], ""))
                ik_b = bpy.data.objects.get(ik_map.get(b["finger"], ""))
                if ik_a and ik_b:
                    # Nudge in Y by ±2mm
                    for kp_frame in [frame, frame + 1]:
                        loc_a = list(ik_a.location)
                        loc_a[1] -= 0.002
                        _set_ik_keyframe(ik_a, kp_frame, *loc_a, 'BEZIER')

                        loc_b = list(ik_b.location)
                        loc_b[1] += 0.002
                        _set_ik_keyframe(ik_b, kp_frame, *loc_b, 'BEZIER')


def _animate_wrist(events, hand, ik_map, wrist_ik_name, rig_obj):
    """Animate the wrist IK empty to follow the centroid of active fingers.

    The wrist X tracks a smoothed average of active fingertip X positions.
    Y and Z are held constant.
    """
    wrist_ik = bpy.data.objects.get(wrist_ik_name)
    if wrist_ik is None:
        print(f"    Warning: '{wrist_ik_name}' not found — skipping wrist animation.")
        return

    # Build a timeline of wrist X targets using a rolling average
    # Sample every N frames
    sample_interval = max(1, FPS // 6)  # ~5 samples per second
    frame_start = 1
    frame_end = bpy.context.scene.frame_end

    # Pre-compute: for each frame, find all notes that are active
    # and compute their average X position
    smoothed_x = None
    center_note = RIGHT_HAND_CENTER_NOTE if hand == "R" else LEFT_HAND_CENTER_NOTE
    initial_pos = get_key_position(center_note)
    smoothed_x = initial_pos[0]

    wrist_y = initial_pos[1] + WRIST_Y_OFFSET
    wrist_z = initial_pos[2] + WRIST_HEIGHT

    for frame in range(frame_start, frame_end + 1, sample_interval):
        t = (frame - 1) / FPS

        # Find active notes at this time
        active_notes = [
            e for e in events
            if e["time_on"] - 0.1 <= t <= e["time_off"] + 0.1
        ]

        if active_notes:
            avg_x = 0.0
            for e in active_notes:
                kp = get_key_position(e["note"])
                avg_x += kp[0]
            avg_x /= len(active_notes)
            target_x = avg_x
        else:
            # Look ahead for next note
            upcoming = [e for e in events if e["time_on"] > t]
            if upcoming:
                next_note = min(upcoming, key=lambda e: e["time_on"])
                kp = get_key_position(next_note["note"])
                target_x = kp[0]
            else:
                target_x = smoothed_x

        # Smooth the X value
        smoothed_x = smooth_value(smoothed_x, target_x, factor=0.2)

        _set_ik_keyframe(wrist_ik, frame, smoothed_x, wrist_y, wrist_z, 'BEZIER')


def _animate_hand_slide(events, hand, rig_obj, center_note):
    """If notes are far from the current hand centre, slide the armature.

    This simulates the pianist repositioning their hand along the keyboard.
    """
    if rig_obj is None:
        return

    # Threshold: if a note is more than 5 white keys from centre, slide
    slide_threshold_semitones = 9  # roughly 5 white keys

    current_center = center_note
    sample_interval = max(1, FPS // 4)
    frame_end = bpy.context.scene.frame_end

    # Start position
    initial_pos = get_key_position(current_center)
    smoothed_x = initial_pos[0]

    for frame in range(1, frame_end + 1, sample_interval):
        t = (frame - 1) / FPS

        # Find notes being played or about to be played
        window_events = [
            e for e in events
            if t - 0.5 <= e["time_on"] <= t + 1.0
        ]

        if window_events:
            avg_note = sum(e["note"] for e in window_events) / len(window_events)
            target_center = int(round(avg_note))

            if abs(target_center - current_center) > slide_threshold_semitones:
                current_center = target_center

            target_pos = get_key_position(current_center)
            target_x = target_pos[0]
        else:
            target_x = smoothed_x

        smoothed_x = smooth_value(smoothed_x, target_x, factor=0.1)

        # Only keyframe if there's meaningful change
        rig_obj.location.x = smoothed_x
        rig_obj.location.y = initial_pos[1] + WRIST_Y_OFFSET
        rig_obj.location.z = initial_pos[2] + WRIST_HEIGHT
        rig_obj.keyframe_insert(data_path="location", frame=frame)


# ---------------------------------------------------------------------------
# F-curve smoothing
# ---------------------------------------------------------------------------

def _smooth_all_ik_fcurves():
    """Set all IK empty F-curves to BEZIER with auto-clamped handles."""
    ik_prefixes = ("IK_", "IK_L_")

    for obj in bpy.data.objects:
        if not any(obj.name.startswith(p) for p in ik_prefixes):
            continue
        if obj.animation_data is None or obj.animation_data.action is None:
            continue

        for fc in obj.animation_data.action.fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'BEZIER'
                kp.handle_left_type = 'AUTO_CLAMPED'
                kp.handle_right_type = 'AUTO_CLAMPED'

    # Also smooth armature location curves
    for rig_name in ("MANO_Rig", "MANO_Rig_Left"):
        rig = bpy.data.objects.get(rig_name)
        if rig and rig.animation_data and rig.animation_data.action:
            for fc in rig.animation_data.action.fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation = 'BEZIER'
                    kp.handle_left_type = 'AUTO_CLAMPED'
                    kp.handle_right_type = 'AUTO_CLAMPED'


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__" or True:
    run_ik_animator()
