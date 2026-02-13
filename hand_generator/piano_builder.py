"""
Piano keyboard model builder.

Generates a realistic 88-key piano keyboard with individually animatable keys.
Each key is a separate object parented to a master empty, enabling per-key
keyframe animation driven by MIDI data.
"""

import math
from typing import Dict, Tuple

import bpy
import bmesh
from mathutils import Vector


# ---------------------------------------------------------------------------
# Piano key layout constants (in metres, real scale)
# ---------------------------------------------------------------------------

# A standard piano key is ~23.5mm wide for white keys
WHITE_KEY_WIDTH = 0.0235
WHITE_KEY_LENGTH = 0.15
WHITE_KEY_HEIGHT = 0.025

BLACK_KEY_WIDTH = 0.013
BLACK_KEY_LENGTH = 0.095
BLACK_KEY_HEIGHT = 0.015  # how far above white key surface

# Gap between white keys
KEY_GAP = 0.001

# Note layout: 12 semitones per octave.  0=C .. 11=B
# Black keys sit between certain white keys.
# White key indices in an octave: C=0, D=2, E=4, F=5, G=7, A=9, B=11
WHITE_NOTES = {0, 2, 4, 5, 7, 9, 11}
BLACK_NOTES = {1, 3, 6, 8, 10}

# Standard 88-key piano: A0 (MIDI 21) to C8 (MIDI 108)
PIANO_LOW = 21
PIANO_HIGH = 108


def _note_is_white(midi_note: int) -> bool:
    return (midi_note % 12) in WHITE_NOTES


def _note_is_black(midi_note: int) -> bool:
    return (midi_note % 12) in BLACK_NOTES


class PianoBuilder:
    """Build a piano keyboard model with individually controllable keys."""

    def __init__(self, low_note: int = PIANO_LOW, high_note: int = PIANO_HIGH,
                 scale: float = 1.0):
        self.low = max(low_note, PIANO_LOW)
        self.high = min(high_note, PIANO_HIGH)
        self.scale = scale
        self.key_objects: Dict[int, bpy.types.Object] = {}

    def build(self) -> bpy.types.Object:
        """Build the keyboard and return the parent empty."""
        # Master empty
        parent = bpy.data.objects.new("Piano", None)
        parent.empty_display_type = "CUBE"
        parent.empty_display_size = 0.02
        bpy.context.collection.objects.link(parent)

        # Materials
        white_mat = self._white_key_material()
        black_mat = self._black_key_material()

        # Lay out white keys first to determine x-positions
        white_x = 0.0
        note_x_positions: Dict[int, float] = {}

        # Pass 1: compute x positions for white keys
        x = 0.0
        for note in range(self.low, self.high + 1):
            if _note_is_white(note):
                note_x_positions[note] = x
                x += (WHITE_KEY_WIDTH + KEY_GAP) * self.scale

        # Pass 2: compute x positions for black keys (between white keys)
        for note in range(self.low, self.high + 1):
            if _note_is_black(note):
                # Black key sits between the white key below and above
                lower_white = note - 1
                upper_white = note + 1
                # Find the nearest white keys
                while lower_white >= self.low and _note_is_black(lower_white):
                    lower_white -= 1
                while upper_white <= self.high and _note_is_black(upper_white):
                    upper_white += 1
                if lower_white in note_x_positions and upper_white in note_x_positions:
                    note_x_positions[note] = (
                        note_x_positions[lower_white] + note_x_positions[upper_white]
                    ) / 2
                elif lower_white in note_x_positions:
                    note_x_positions[note] = (
                        note_x_positions[lower_white]
                        + (WHITE_KEY_WIDTH + KEY_GAP) * self.scale / 2
                    )

        # Pass 3: create key objects
        for note in range(self.low, self.high + 1):
            if note not in note_x_positions:
                continue

            nx = note_x_positions[note]
            is_black = _note_is_black(note)

            if is_black:
                obj = self._make_key_mesh(
                    f"Key_{note}",
                    BLACK_KEY_WIDTH * self.scale,
                    BLACK_KEY_LENGTH * self.scale,
                    (WHITE_KEY_HEIGHT + BLACK_KEY_HEIGHT) * self.scale,
                )
                obj.location = Vector((nx, 0, 0))
                obj.data.materials.append(black_mat)
            else:
                obj = self._make_key_mesh(
                    f"Key_{note}",
                    WHITE_KEY_WIDTH * self.scale,
                    WHITE_KEY_LENGTH * self.scale,
                    WHITE_KEY_HEIGHT * self.scale,
                )
                obj.location = Vector((nx, 0, 0))
                obj.data.materials.append(white_mat)

            obj.parent = parent
            # Set the pivot point to the back edge (hinge) of the key
            # Keys rotate around their back edge when pressed
            obj.location.y = 0
            self.key_objects[note] = obj

        # Position the piano so middle C is roughly at x=0
        middle_c = 60
        if middle_c in note_x_positions:
            offset = note_x_positions[middle_c]
            parent.location.x = -offset

        # Lower the piano so the key tops are at a reasonable height
        parent.location.z = -WHITE_KEY_HEIGHT * self.scale

        return parent

    def _make_key_mesh(self, name: str, width: float, length: float,
                       height: float) -> bpy.types.Object:
        """Create a simple box mesh for a piano key."""
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.collection.objects.link(obj)

        bm = bmesh.new()
        # Box centered on x, starting at y=0 going forward, bottom at z=0
        hw = width / 2
        verts = [
            bm.verts.new((-hw, 0,      0)),
            bm.verts.new(( hw, 0,      0)),
            bm.verts.new(( hw, length, 0)),
            bm.verts.new((-hw, length, 0)),
            bm.verts.new((-hw, 0,      height)),
            bm.verts.new(( hw, 0,      height)),
            bm.verts.new(( hw, length, height)),
            bm.verts.new((-hw, length, height)),
        ]
        faces = [
            (0, 1, 2, 3),  # bottom
            (4, 7, 6, 5),  # top
            (0, 4, 5, 1),  # back
            (2, 6, 7, 3),  # front
            (0, 3, 7, 4),  # left
            (1, 5, 6, 2),  # right
        ]
        for fi in faces:
            try:
                bm.faces.new([verts[i] for i in fi])
            except ValueError:
                pass

        bm.to_mesh(mesh)
        bm.free()

        # Smooth edges slightly
        bevel = obj.modifiers.new("Bevel", "BEVEL")
        bevel.width = 0.0005 * (width / (WHITE_KEY_WIDTH))
        bevel.segments = 2

        return obj

    def _white_key_material(self) -> bpy.types.Material:
        mat = bpy.data.materials.new("WhiteKey")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.95, 0.93, 0.90, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.3
            bsdf.inputs["Specular IOR Level"].default_value = 0.5
        return mat

    def _black_key_material(self) -> bpy.types.Material:
        mat = bpy.data.materials.new("BlackKey")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            bsdf.inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.15
            bsdf.inputs["Specular IOR Level"].default_value = 0.7
        return mat

    def get_key_top_position(self, note: int) -> Vector:
        """Return the world-space position of the top-center of a key's
        playing surface (where a fingertip would touch)."""
        obj = self.key_objects.get(note)
        if obj is None:
            return Vector((0, 0, 0))

        is_black = _note_is_black(note)
        height = (WHITE_KEY_HEIGHT + BLACK_KEY_HEIGHT if is_black
                  else WHITE_KEY_HEIGHT) * self.scale

        # Top center, 60% along the key length
        local_pos = Vector((0, obj.dimensions.y * 0.6, height))
        world_pos = obj.matrix_world @ local_pos
        return world_pos
