"""
MIDI-driven piano animation engine.

Takes parsed MIDI data, a rigged hand armature, and a piano keyboard model,
then generates keyframe animation of the fingers playing the correct keys.

Finger assignment uses a simplified piano fingering algorithm that considers
hand span, note distance, and avoids collisions between fingers.
"""

import math
from typing import Dict, List, Optional, Tuple

import bpy
from mathutils import Vector, Quaternion, Euler

from .midi_parser import MidiData, NoteEvent
from .piano_builder import PianoBuilder, _note_is_black


# ---------------------------------------------------------------------------
# Finger assignment
# ---------------------------------------------------------------------------

FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]

# Natural reach offsets from the wrist center in semitones
# (how far each finger naturally reaches on the keyboard)
FINGER_REACH_RIGHT = {
    "thumb":  0,
    "index":  2,
    "middle": 4,
    "ring":   5,
    "pinky":  7,
}

FINGER_REACH_LEFT = {
    "pinky":  0,
    "ring":   2,
    "middle": 4,
    "index":  5,
    "thumb":  7,
}


class FingerAssigner:
    """Assign fingers to notes using a greedy cost-based approach."""

    def __init__(self, hand: str = "RIGHT"):
        self.hand = hand
        self.reach = FINGER_REACH_RIGHT if hand == "RIGHT" else FINGER_REACH_LEFT

    def assign(self, notes: List[NoteEvent]) -> List[Tuple[NoteEvent, str]]:
        """Return a list of (note, finger_name) pairs."""
        if not notes:
            return []

        result = []
        # Track which fingers are currently busy and when they free up
        finger_busy_until: Dict[str, float] = {f: -1.0 for f in FINGER_NAMES}
        # Track the last note each finger played (for distance cost)
        finger_last_note: Dict[str, Optional[int]] = {f: None for f in FINGER_NAMES}

        # Sort by time
        sorted_notes = sorted(notes, key=lambda n: (n.time_on, n.note))

        # Handle simultaneous notes (chords)
        i = 0
        while i < len(sorted_notes):
            # Collect all notes starting at roughly the same time
            chord = [sorted_notes[i]]
            j = i + 1
            while j < len(sorted_notes) and abs(sorted_notes[j].time_on - chord[0].time_on) < 0.02:
                chord.append(sorted_notes[j])
                j += 1

            # Sort chord notes by pitch
            chord.sort(key=lambda n: n.note)
            if self.hand == "LEFT":
                chord.sort(key=lambda n: n.note, reverse=True)

            # Assign fingers to chord notes
            available = [f for f in FINGER_NAMES
                         if finger_busy_until[f] <= chord[0].time_on + 0.001]
            if not available:
                # All fingers busy - force-free the one that freed earliest
                available = sorted(FINGER_NAMES,
                                   key=lambda f: finger_busy_until[f])

            for ci, note in enumerate(chord):
                best_finger = None
                best_cost = float("inf")

                for finger in available:
                    if finger_busy_until[finger] > note.time_on + 0.001:
                        continue

                    cost = 0.0
                    # Distance cost: prefer fingers close to the note
                    if finger_last_note[finger] is not None:
                        dist = abs(note.note - finger_last_note[finger])
                        cost += dist * 0.5

                    # Natural position cost
                    reach_offset = self.reach[finger]
                    # Lower cost for fingers near their natural reach
                    cost += abs(ci - list(FINGER_NAMES).index(finger)) * 2.0

                    # Thumb crossing penalty
                    if finger == "thumb" and ci > 0:
                        cost += 3.0
                    if finger == "pinky" and ci < len(chord) - 1:
                        cost += 3.0

                    if cost < best_cost:
                        best_cost = cost
                        best_finger = finger

                if best_finger is None:
                    best_finger = available[ci % len(available)]

                result.append((note, best_finger))
                finger_busy_until[best_finger] = note.time_off
                finger_last_note[best_finger] = note.note

                # Remove from available for this chord
                if best_finger in available:
                    available.remove(best_finger)

            i = j

        return result


# ---------------------------------------------------------------------------
# Animation engine
# ---------------------------------------------------------------------------

class PianoAnimator:
    """Generate keyframe animation from MIDI data."""

    def __init__(self, fps: int = 30, key_press_depth: float = 0.015,
                 finger_lift_height: float = 0.03):
        self.fps = fps
        self.key_press_depth = key_press_depth
        self.finger_lift_height = finger_lift_height
        # Anticipation frames before a note plays
        self.anticipation_frames = 2
        # Ease-out frames after a note releases
        self.release_frames = 3

    def animate(self, midi_data: MidiData,
                rig_left: Optional[bpy.types.Object],
                rig_right: Optional[bpy.types.Object],
                piano_builder: Optional[PianoBuilder]):
        """Generate the full animation."""
        scene = bpy.context.scene
        scene.render.fps = self.fps
        scene.frame_start = 1
        scene.frame_end = max(
            int(midi_data.duration_seconds * self.fps) + self.fps,
            scene.frame_end,
        )

        # Split notes between hands
        left_notes = midi_data.notes_for_hand("LEFT")
        right_notes = midi_data.notes_for_hand("RIGHT")

        if rig_right and right_notes:
            assigner = FingerAssigner("RIGHT")
            assignments = assigner.assign(right_notes)
            self._animate_hand(rig_right, assignments, piano_builder, "RIGHT")

        if rig_left and left_notes:
            assigner = FingerAssigner("LEFT")
            assignments = assigner.assign(left_notes)
            self._animate_hand(rig_left, assignments, piano_builder, "LEFT")

        # Animate piano key presses
        if piano_builder:
            self._animate_keys(midi_data, piano_builder)

    def _animate_hand(self, rig: bpy.types.Object,
                      assignments: List[Tuple[NoteEvent, str]],
                      piano_builder: Optional[PianoBuilder],
                      hand: str):
        """Keyframe the IK targets for one hand."""
        bpy.context.view_layer.objects.active = rig

        # Position the wrist/hand near the piano
        self._position_hand_on_piano(rig, assignments, piano_builder, hand)

        for note_event, finger_name in assignments:
            ik_bone_name = f"{finger_name}_ik_target"
            pbone = rig.pose.bones.get(ik_bone_name)
            if pbone is None:
                # Fall back to direct distal bone rotation
                self._animate_finger_rotation(
                    rig, finger_name, note_event
                )
                continue

            # Compute target position over the correct piano key
            if piano_builder and note_event.note in piano_builder.key_objects:
                key_pos = piano_builder.get_key_top_position(note_event.note)
                # Convert to armature-local space
                local_pos = rig.matrix_world.inverted() @ key_pos
            else:
                # Estimate position based on note number
                local_pos = self._estimate_key_position(note_event.note, hand)

            frame_on = int(note_event.time_on * self.fps) + 1
            frame_off = int(note_event.time_off * self.fps) + 1

            # Rest position (slightly above the key)
            rest_pos = local_pos.copy()
            rest_pos.z += self.finger_lift_height

            # Pressed position (on the key, slightly depressed)
            press_pos = local_pos.copy()
            press_pos.z -= self.key_press_depth * 0.5

            # Anticipation: move to rest position above key
            antic_frame = max(1, frame_on - self.anticipation_frames)
            pbone.location = rest_pos
            pbone.keyframe_insert(data_path="location", frame=antic_frame)

            # Key press
            pbone.location = press_pos
            pbone.keyframe_insert(data_path="location", frame=frame_on)

            # Hold (for long notes, add a mid-hold keyframe)
            if frame_off - frame_on > 4:
                mid_frame = (frame_on + frame_off) // 2
                pbone.location = press_pos
                pbone.keyframe_insert(data_path="location", frame=mid_frame)

            # Release
            pbone.location = rest_pos
            pbone.keyframe_insert(data_path="location",
                                  frame=frame_off + self.release_frames)

        # Smooth the F-curves
        self._smooth_fcurves(rig)

    def _animate_finger_rotation(self, rig: bpy.types.Object,
                                 finger_name: str,
                                 note_event: NoteEvent):
        """Animate a finger by rotating the phalanx bones (fallback if no IK)."""
        frame_on = int(note_event.time_on * self.fps) + 1
        frame_off = int(note_event.time_off * self.fps) + 1

        for seg in ["proximal", "middle", "distal"]:
            bone_name = f"{finger_name}_{seg}"
            pbone = rig.pose.bones.get(bone_name)
            if pbone is None:
                continue

            # Rest rotation
            rest_angle = 0.0
            # Curl angles per segment for a key press
            curl_angles = {"proximal": 15, "middle": 25, "distal": 35}
            press_angle = math.radians(curl_angles.get(seg, 20))

            # Velocity-based intensity
            intensity = note_event.velocity / 127.0
            press_angle *= (0.7 + 0.3 * intensity)

            # Before note: rest
            antic_frame = max(1, frame_on - self.anticipation_frames)
            pbone.rotation_euler.x = rest_angle
            pbone.keyframe_insert(data_path="rotation_euler",
                                  index=0, frame=antic_frame)

            # On note: curl
            pbone.rotation_euler.x = press_angle
            pbone.keyframe_insert(data_path="rotation_euler",
                                  index=0, frame=frame_on)

            # Release: uncurl
            pbone.rotation_euler.x = rest_angle
            pbone.keyframe_insert(data_path="rotation_euler",
                                  index=0, frame=frame_off + self.release_frames)

    def _position_hand_on_piano(self, rig: bpy.types.Object,
                                assignments: List[Tuple[NoteEvent, str]],
                                piano_builder: Optional[PianoBuilder],
                                hand: str):
        """Position and keyframe the wrist bone to follow the playing range."""
        wrist = rig.pose.bones.get("wrist")
        if wrist is None:
            return

        if not assignments:
            return

        # Group notes into time windows and compute average position
        window_size = 2.0  # seconds per window
        max_time = max(n.time_off for n, _ in assignments)
        t = 0.0

        while t <= max_time:
            window_notes = [(n, f) for n, f in assignments
                            if n.time_on >= t and n.time_on < t + window_size]
            if window_notes:
                avg_note = sum(n.note for n, _ in window_notes) / len(window_notes)
                target_pos = self._estimate_key_position(int(avg_note), hand)
                # Offset for wrist (behind and above the keys)
                target_pos.y -= 0.12
                target_pos.z += 0.08

                frame = int(t * self.fps) + 1
                wrist.location = target_pos
                wrist.keyframe_insert(data_path="location", frame=frame)

            t += window_size

    def _estimate_key_position(self, note: int, hand: str) -> Vector:
        """Estimate a key's position without a piano model."""
        # Rough layout: each semitone ~1.4cm apart, middle C at x=0
        x = (note - 60) * 0.014
        y = 0.07  # ~60% along key
        z = 0.025  # white key top height
        if _note_is_black(note):
            z += 0.015
        return Vector((x, y, z))

    def _animate_keys(self, midi_data: MidiData,
                      piano_builder: PianoBuilder):
        """Animate piano key depressions from MIDI data."""
        for note_event in midi_data.notes:
            obj = piano_builder.key_objects.get(note_event.note)
            if obj is None:
                continue

            frame_on = int(note_event.time_on * self.fps) + 1
            frame_off = int(note_event.time_off * self.fps) + 1

            # Rest: no rotation
            antic_frame = max(1, frame_on - 1)
            obj.rotation_euler.x = 0
            obj.keyframe_insert(data_path="rotation_euler",
                                index=0, frame=antic_frame)

            # Pressed: slight rotation around back edge
            # Velocity affects how much the key depresses
            intensity = note_event.velocity / 127.0
            press_angle = math.radians(2.0 + 1.5 * intensity)
            obj.rotation_euler.x = -press_angle
            obj.keyframe_insert(data_path="rotation_euler",
                                index=0, frame=frame_on)

            # Hold
            if frame_off - frame_on > 2:
                obj.rotation_euler.x = -press_angle
                obj.keyframe_insert(data_path="rotation_euler",
                                    index=0, frame=frame_off - 1)

            # Release
            obj.rotation_euler.x = 0
            obj.keyframe_insert(data_path="rotation_euler",
                                index=0, frame=frame_off + 2)

    def _smooth_fcurves(self, obj: bpy.types.Object):
        """Set all F-curves to use bezier interpolation for smooth motion."""
        if obj.animation_data and obj.animation_data.action:
            for fcurve in obj.animation_data.action.fcurves:
                for kp in fcurve.keyframe_points:
                    kp.interpolation = "BEZIER"
                    kp.handle_left_type = "AUTO_CLAMPED"
                    kp.handle_right_type = "AUTO_CLAMPED"
