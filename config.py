"""
Shared constants and utility functions for the MIDI-to-Piano animation pipeline.

All scripts should import from this file:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import *
"""

import os

import bpy
import numpy as np


# === PATHS (user must update these) ===
MIDI_PATH       = "/Users/rishii3/Downloads/MIDI A Major.mid"
MANO_RIGHT_PATH = "/Users/rishii3/Downloads/mano_v1_2/models/MANO_RIGHT.pkl"
MANO_LEFT_PATH  = "/Users/rishii3/Downloads/mano_v1_2/models/MANO_LEFT.pkl"
OUTPUT_DIR      = "/Users/rishii3/Desktop/piano_render/"

# === Piano geometry (metres) ===
W_WIDTH   = 0.0222   # white key width
W_LEN     = 0.150    # white key length (front to back)
W_THICK   = 0.02     # white key thickness
B_WIDTH   = 0.012    # black key width
B_LEN     = 0.095    # black key length
B_THICK   = 0.02     # black key thickness
B_HEIGHT  = 0.012    # black key Z offset above white keys
KEY_PRESS_DEPTH = 0.01  # how far keys depress in Z

# === MIDI range ===
MIDI_LOW  = 21   # A0
MIDI_HIGH = 108  # C8
BLACK_INDICES = [1, 3, 6, 8, 10]  # semitone indices where C=0

# === Animation ===
FPS = 30
ANTICIPATION_FRAMES = 3   # finger starts moving N frames before note_on
RELEASE_FRAMES      = 2   # finger lifts N frames after note_off
HOVER_HEIGHT        = 0.035  # finger hover Z above key surface

# === Hand positioning ===
RIGHT_HAND_CENTER_NOTE = 72  # C5 -- default right hand centre
LEFT_HAND_CENTER_NOTE  = 48  # C3 -- default left hand centre
WRIST_HEIGHT           = 0.06  # wrist Z above key surface
WRIST_Y_OFFSET         = -0.08  # wrist behind key front edge

# === Finger names ===
FINGER_NAMES = ["thumb", "index", "middle", "ring", "pinky"]

# === MANO joint indices per finger (for skinning weights) ===
# MANO has 16 joints: 0=wrist, then 3 per finger (MCP, PIP, DIP)
# thumb: 1,2,3   index: 4,5,6   middle: 7,8,9   ring: 10,11,12   pinky: 13,14,15
MANO_JOINT_INDICES = {
    "wrist":  [0],
    "thumb":  [1, 2, 3],
    "index":  [4, 5, 6],
    "middle": [7, 8, 9],
    "ring":   [10, 11, 12],
    "pinky":  [13, 14, 15],
}

# Fingertip vertex indices in the MANO topology (approximate)
MANO_FINGERTIP_VERT = {
    "thumb":  744,
    "index":  320,
    "middle": 443,
    "ring":   555,
    "pinky":  672,
}


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def get_key_x_position(midi_note):
    """Get the X position of a piano key by MIDI note number."""
    obj = bpy.data.objects.get(f"Key_{midi_note}")
    if obj:
        return obj.location.x
    return None


def get_key_surface_z(midi_note):
    """Get the Z position of a key's playing surface."""
    is_black = (midi_note % 12) in BLACK_INDICES
    return B_HEIGHT if is_black else 0.0


def get_key_front_y(midi_note):
    """Get the Y position of the front-centre of a key."""
    is_black = (midi_note % 12) in BLACK_INDICES
    obj = bpy.data.objects.get(f"Key_{midi_note}")
    if obj:
        return obj.location.y + (B_LEN if is_black else W_LEN) * 0.4
    return 0.0


def get_key_position(midi_note):
    """Return (x, y, z) of the front-centre of a piano key's playing surface."""
    obj = bpy.data.objects.get(f"Key_{midi_note}")
    if obj is None:
        # Estimate from note number if key objects don't exist yet
        return estimate_key_position(midi_note)
    is_black = (midi_note % 12) in BLACK_INDICES
    x = obj.location.x
    y = obj.location.y + (B_LEN if is_black else W_LEN) * 0.4
    z = B_HEIGHT if is_black else 0.0
    return (x, y, z)


def estimate_key_position(midi_note):
    """Estimate key position without Key_ objects in the scene.

    Computes the X offset from middle-C (note 60) based on the number
    of white keys between the two notes.
    """
    def _white_key_count_below(note):
        """Count how many white keys are at or below *note* starting from MIDI 21."""
        count = 0
        for n in range(MIDI_LOW, note + 1):
            if (n % 12) not in BLACK_INDICES:
                count += 1
        return count

    ref = _white_key_count_below(60)
    cur = _white_key_count_below(midi_note)
    x = (cur - ref) * W_WIDTH

    is_black = (midi_note % 12) in BLACK_INDICES
    if is_black:
        # Black keys sit between adjacent white keys
        lower = midi_note - 1
        upper = midi_note + 1
        while (lower % 12) in BLACK_INDICES and lower >= MIDI_LOW:
            lower -= 1
        while (upper % 12) in BLACK_INDICES and upper <= MIDI_HIGH:
            upper += 1
        lx = (_white_key_count_below(lower) - ref) * W_WIDTH
        ux = (_white_key_count_below(upper) - ref) * W_WIDTH
        x = (lx + ux) / 2.0

    y = (B_LEN if is_black else W_LEN) * 0.4
    z = B_HEIGHT if is_black else 0.0
    return (x, y, z)


def midi_note_to_name(note):
    """Convert MIDI note number to name (e.g., 60 -> 'C4')."""
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    return f"{names[note % 12]}{(note // 12) - 1}"


def smooth_value(current, target, factor=0.3):
    """Simple exponential smoothing."""
    return current + (target - current) * factor


def note_is_white(midi_note):
    """Return True if the MIDI note is a white key."""
    return (midi_note % 12) not in BLACK_INDICES


def note_is_black(midi_note):
    """Return True if the MIDI note is a black key."""
    return (midi_note % 12) in BLACK_INDICES


def white_key_count_in_range(low, high):
    """Count white keys in [low, high] inclusive."""
    return sum(1 for n in range(low, high + 1) if note_is_white(n))


def ensure_output_dir():
    """Create the output directory if it doesn't exist."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
