"""
Script 6: finger_assigner.py

Parse a MIDI file and assign each note event to a specific finger of a
specific hand using a rule-based engine.  Outputs:
    1. A JSON file at OUTPUT_DIR/finger_assignments.json
    2. A Blender empty 'FingerAssignments' with the JSON stored as a
       custom property for downstream pipeline scripts.
"""

import os
import sys
import json

import bpy

# ---------------------------------------------------------------------------
# Import shared config
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config import (
    MIDI_PATH, OUTPUT_DIR, FPS,
    MIDI_LOW, MIDI_HIGH, BLACK_INDICES,
    RIGHT_HAND_CENTER_NOTE, LEFT_HAND_CENTER_NOTE,
    FINGER_NAMES,
    note_is_white, note_is_black, white_key_count_in_range,
    ensure_output_dir,
)

# ---------------------------------------------------------------------------
# MIDI parsing (lightweight, using mido if available, else built-in parser)
# ---------------------------------------------------------------------------

def _parse_midi(midi_path):
    """Return a list of note-event dicts sorted by time_on.

    Each dict: {note, velocity, time_on, time_off, channel, hand, finger}
    """
    events = []

    try:
        import mido
        mid = mido.MidiFile(midi_path)
        abs_time = 0.0
        active = {}  # (channel, note) -> {event dict}

        for msg in mid:
            abs_time += msg.time
            if msg.type == 'note_on' and msg.velocity > 0:
                key = (getattr(msg, 'channel', 0), msg.note)
                active[key] = {
                    "note": msg.note,
                    "velocity": msg.velocity,
                    "time_on": abs_time,
                    "time_off": None,
                    "channel": getattr(msg, 'channel', 0),
                    "hand": None,
                    "finger": None,
                }
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                key = (getattr(msg, 'channel', 0), msg.note)
                if key in active:
                    evt = active.pop(key)
                    evt["time_off"] = abs_time
                    events.append(evt)

        # Close any remaining active notes
        for key, evt in active.items():
            evt["time_off"] = abs_time + 0.1
            events.append(evt)

    except ImportError:
        # Fallback: use the built-in parser from hand_generator
        _hg_dir = os.path.join(_SCRIPT_DIR, "hand_generator")
        if _hg_dir not in sys.path:
            sys.path.insert(0, _hg_dir)
        try:
            from midi_parser import parse_midi as _parse
            midi_data = _parse(midi_path)
            for n in midi_data.notes:
                events.append({
                    "note": n.note,
                    "velocity": n.velocity,
                    "time_on": n.time_on,
                    "time_off": n.time_off,
                    "channel": getattr(n, "channel", 0),
                    "hand": None,
                    "finger": None,
                })
        except ImportError:
            raise ImportError(
                "Neither 'mido' nor the built-in midi_parser could be imported.  "
                "Install mido: <blender-python> -m pip install mido"
            )

    events.sort(key=lambda e: e["time_on"])
    return events


# ---------------------------------------------------------------------------
# Hand splitting
# ---------------------------------------------------------------------------

def _assign_hands(events):
    """Assign each event to 'R' or 'L' based on median pitch splitting."""
    if not events:
        return

    pitches = [e["note"] for e in events]
    median_pitch = sorted(pitches)[len(pitches) // 2]

    # Check if all notes fit in a single hand span (10 white keys ~ octave+2)
    lo, hi = min(pitches), max(pitches)
    span_whites = white_key_count_in_range(lo, hi)
    if span_whites <= 10:
        for e in events:
            e["hand"] = "R"
        return

    for e in events:
        e["hand"] = "L" if e["note"] <= median_pitch else "R"


# ---------------------------------------------------------------------------
# Finger assignment
# ---------------------------------------------------------------------------

# Default rest-position offsets (in semitones from hand centre note):
# Right hand: thumb on lowest, pinky on highest.
# Left hand:  pinky on lowest, thumb on highest (mirrored).
_REST_OFFSETS_R = {1: 0, 2: 2, 3: 4, 4: 5, 5: 7}  # thumb..pinky
_REST_OFFSETS_L = {5: 0, 4: 2, 3: 4, 2: 5, 1: 7}   # pinky..thumb


def _assign_fingers(events):
    """Assign a finger (1-5) to each event per hand.

    Finger numbering: thumb=1, index=2, middle=3, ring=4, pinky=5.
    """
    # Separate by hand
    right_events = [e for e in events if e["hand"] == "R"]
    left_events = [e for e in events if e["hand"] == "L"]

    _assign_fingers_hand(right_events, hand="R")
    _assign_fingers_hand(left_events, hand="L")


def _assign_fingers_hand(events, hand="R"):
    """Assign fingers for a single hand's events."""
    if not events:
        return

    rest_offsets = _REST_OFFSETS_R if hand == "R" else _REST_OFFSETS_L
    center_note = RIGHT_HAND_CENTER_NOTE if hand == "R" else LEFT_HAND_CENTER_NOTE

    # Track finger availability (time when finger becomes free)
    finger_free_at = {f: -1.0 for f in range(1, 6)}
    # Track last note each finger played
    finger_last_note = {f: center_note + rest_offsets[f] for f in range(1, 6)}

    i = 0
    while i < len(events):
        # Collect chord: notes within 0.02 seconds of each other
        chord = [events[i]]
        j = i + 1
        while j < len(events) and abs(events[j]["time_on"] - chord[0]["time_on"]) < 0.02:
            chord.append(events[j])
            j += 1

        # Sort chord by pitch
        if hand == "R":
            chord.sort(key=lambda e: e["note"])   # ascending
        else:
            chord.sort(key=lambda e: e["note"], reverse=True)  # descending for left

        # Check if chord exceeds hand span
        if len(chord) > 1:
            chord_pitches = [c["note"] for c in chord]
            chord_span = white_key_count_in_range(min(chord_pitches), max(chord_pitches))
            if chord_span > 10:
                # Split across both hands — reassign some notes
                mid = len(chord) // 2
                for ci in range(mid, len(chord)):
                    other_hand = "L" if hand == "R" else "R"
                    chord[ci]["hand"] = other_hand
                chord = chord[:mid]

        # Determine available fingers
        t_now = chord[0]["time_on"] if chord else 0.0
        available = [f for f in range(1, 6) if finger_free_at[f] <= t_now + 0.001]
        if not available:
            available = sorted(range(1, 6), key=lambda f: finger_free_at[f])

        # Assign fingers to chord notes
        used = set()
        for ci, evt in enumerate(chord):
            best_finger = None
            best_cost = float("inf")

            for finger in range(1, 6):
                if finger in used:
                    continue
                if finger not in available and len(available) > len(chord):
                    continue

                cost = 0.0

                # Natural position cost: prefer the finger whose rest position
                # is closest to the note
                rest_note = center_note + rest_offsets[finger]
                cost += abs(evt["note"] - rest_note) * 1.0

                # Distance from last note played by this finger
                dist = abs(evt["note"] - finger_last_note[finger])
                cost += dist * 0.5

                # Ordering cost: within a chord, fingers should be in order
                expected_finger_idx = ci  # 0-indexed position in chord
                actual_idx = finger - 1
                cost += abs(expected_finger_idx - actual_idx) * 2.0

                # Thumb/pinky edge penalty in middle of chord
                if finger == 1 and ci > 0 and len(chord) > 2:
                    cost += 3.0
                if finger == 5 and ci < len(chord) - 1 and len(chord) > 2:
                    cost += 3.0

                if cost < best_cost:
                    best_cost = cost
                    best_finger = finger

            if best_finger is None:
                best_finger = (ci % 5) + 1

            evt["finger"] = best_finger
            used.add(best_finger)
            finger_free_at[best_finger] = evt["time_off"]
            finger_last_note[best_finger] = evt["note"]

        i = j  # move past the chord


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_finger_assigner():
    """Parse MIDI, assign hands and fingers, save results."""

    print("  [finger_assigner] Parsing MIDI ...")
    events = _parse_midi(MIDI_PATH)
    print(f"    Parsed {len(events)} note events.")

    if not events:
        print("    WARNING: No note events found in MIDI file.")
        return

    # Assign hands
    print("  [finger_assigner] Assigning hands ...")
    _assign_hands(events)

    r_count = sum(1 for e in events if e["hand"] == "R")
    l_count = sum(1 for e in events if e["hand"] == "L")
    print(f"    Right hand: {r_count} notes, Left hand: {l_count} notes.")

    # Assign fingers
    print("  [finger_assigner] Assigning fingers ...")
    _assign_fingers(events)

    # Also handle any events that were re-assigned to the other hand during
    # chord splitting
    unassigned = [e for e in events if e["finger"] is None]
    if unassigned:
        _assign_fingers_hand([e for e in unassigned if e["hand"] == "R"], "R")
        _assign_fingers_hand([e for e in unassigned if e["hand"] == "L"], "L")

    max_time = max(e["time_off"] for e in events) if events else 0.0

    # Build output structure
    assignments = {
        "events": events,
        "metadata": {
            "midi_path": MIDI_PATH,
            "total_notes": len(events),
            "duration_seconds": max_time,
            "right_hand_notes": r_count,
            "left_hand_notes": l_count,
        },
    }

    # Save to JSON file
    ensure_output_dir()
    json_path = os.path.join(OUTPUT_DIR, "finger_assignments.json")
    with open(json_path, "w") as f:
        json.dump(assignments, f, indent=2)
    print(f"    Saved assignments to {json_path}")

    # Store on a Blender empty for pipeline continuity
    existing = bpy.data.objects.get("FingerAssignments")
    if existing:
        bpy.data.objects.remove(existing, do_unlink=True)

    empty = bpy.data.objects.new("FingerAssignments", None)
    empty.empty_display_type = 'PLAIN_AXES'
    empty.empty_display_size = 0.01
    bpy.context.collection.objects.link(empty)
    empty["assignments_json"] = json.dumps(assignments)

    print("    Stored assignments on Blender empty 'FingerAssignments'.")

    # Print summary
    finger_counts = {}
    for e in events:
        key = (e["hand"], e["finger"])
        finger_counts[key] = finger_counts.get(key, 0) + 1

    finger_labels = {1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "pinky"}
    print("\n    Finger assignment summary:")
    for hand in ["R", "L"]:
        for f in range(1, 6):
            count = finger_counts.get((hand, f), 0)
            if count > 0:
                print(f"      {hand} {finger_labels[f]:>8}: {count} notes")

    print(f"\n  [finger_assigner] Done.  Duration: {max_time:.2f}s\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__" or True:
    run_finger_assigner()
