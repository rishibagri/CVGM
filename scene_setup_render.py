"""
Script 8: scene_setup_render.py

Set up camera, lighting, materials, and render the final video.

Adds:
    - A 50 mm camera aimed at the keyboard centre
    - Three-point lighting (key, fill, rim)
    - Skin material on both hand meshes
    - Enhanced key materials
    - EEVEE (or Cycles) render settings outputting MP4 via FFMPEG
"""

import os
import sys
import math

import bpy
from mathutils import Vector, Euler

# ---------------------------------------------------------------------------
# Import shared config
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from config import (
    OUTPUT_DIR, FPS,
    ensure_output_dir,
)


# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

def _setup_camera():
    """Create and position the camera for a player-perspective view."""
    # Remove existing camera if present
    for obj in bpy.data.objects:
        if obj.type == 'CAMERA' and obj.name.startswith("PianoCamera"):
            bpy.data.objects.remove(obj, do_unlink=True)

    cam_data = bpy.data.cameras.new("PianoCamera")
    cam_data.lens = 50  # 50mm focal length
    cam_data.clip_start = 0.01
    cam_data.clip_end = 100.0
    cam_data.dof.use_dof = False

    cam_obj = bpy.data.objects.new("PianoCamera", cam_data)
    bpy.context.collection.objects.link(cam_obj)

    # Position: above and behind the player, angled down at the keyboard
    # Centre of keyboard is roughly x=0 (middle C area)
    cam_obj.location = Vector((0.55, -0.35, 0.25))
    cam_obj.rotation_euler = Euler((math.radians(72), 0, math.radians(12)), 'XYZ')

    # Make this the active scene camera
    bpy.context.scene.camera = cam_obj

    print("    Camera 'PianoCamera' created at (0.55, -0.35, 0.25).")
    return cam_obj


# ---------------------------------------------------------------------------
# Lighting
# ---------------------------------------------------------------------------

def _setup_lighting():
    """Create a three-point lighting setup."""
    # Remove old lights with our prefix
    for obj in list(bpy.data.objects):
        if obj.type == 'LIGHT' and obj.name.startswith("Piano_"):
            bpy.data.objects.remove(obj, do_unlink=True)

    # --- Key light: warm area light above and in front ---
    key_data = bpy.data.lights.new("Piano_KeyLight", 'AREA')
    key_data.energy = 200
    key_data.color = _kelvin_to_rgb(4500)
    key_data.size = 0.5
    key_data.shape = 'RECTANGLE'
    key_data.size_y = 0.3

    key_obj = bpy.data.objects.new("Piano_KeyLight", key_data)
    key_obj.location = Vector((0.3, 0.1, 0.5))
    key_obj.rotation_euler = Euler((math.radians(50), math.radians(10), 0), 'XYZ')
    bpy.context.collection.objects.link(key_obj)

    # --- Fill light: softer, from the left side ---
    fill_data = bpy.data.lights.new("Piano_FillLight", 'AREA')
    fill_data.energy = 50
    fill_data.color = (0.9, 0.92, 1.0)  # slightly cool
    fill_data.size = 0.8

    fill_obj = bpy.data.objects.new("Piano_FillLight", fill_data)
    fill_obj.location = Vector((-0.5, -0.2, 0.35))
    fill_obj.rotation_euler = Euler((math.radians(60), 0, math.radians(-30)), 'XYZ')
    bpy.context.collection.objects.link(fill_obj)

    # --- Rim light: subtle backlight behind the hands ---
    rim_data = bpy.data.lights.new("Piano_RimLight", 'AREA')
    rim_data.energy = 30
    rim_data.color = (1.0, 0.95, 0.9)
    rim_data.size = 0.3

    rim_obj = bpy.data.objects.new("Piano_RimLight", rim_data)
    rim_obj.location = Vector((0.0, 0.3, 0.3))
    rim_obj.rotation_euler = Euler((math.radians(120), 0, 0), 'XYZ')
    bpy.context.collection.objects.link(rim_obj)

    # --- World background ---
    world = bpy.context.scene.world
    if world is None:
        world = bpy.data.worlds.new("PianoWorld")
        bpy.context.scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1.0)
        bg.inputs["Strength"].default_value = 1.0

    print("    Three-point lighting created (key 200W, fill 50W, rim 30W).")


def _kelvin_to_rgb(kelvin):
    """Approximate colour temperature to RGB (normalised).

    Uses a simplified Planckian locus approximation good enough for
    CG lighting in the 1000-10000 K range.
    """
    temp = kelvin / 100.0
    if temp <= 66:
        r = 1.0
        g = max(0, min(1, (99.4708025861 * math.log(temp) - 161.1195681661) / 255))
        if temp <= 19:
            b = 0.0
        else:
            b = max(0, min(1, (138.5177312231 * math.log(temp - 10) - 305.0447927307) / 255))
    else:
        r = max(0, min(1, (329.698727446 * (temp - 60) ** -0.1332047592) / 255))
        g = max(0, min(1, (288.1221695283 * (temp - 60) ** -0.0755148492) / 255))
        b = 1.0
    return (r, g, b)


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _setup_materials():
    """Enhance existing key materials and apply skin material to hand meshes."""

    # --- White key material ---
    white_mat = bpy.data.materials.get("WhiteKey")
    if white_mat is None:
        white_mat = bpy.data.materials.new("WhiteKey_Render")
        white_mat.use_nodes = True
    bsdf = white_mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.95, 0.93, 0.90, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.3
        bsdf.inputs["Specular IOR Level"].default_value = 0.5

    # --- Black key material ---
    black_mat = bpy.data.materials.get("BlackKey")
    if black_mat is None:
        black_mat = bpy.data.materials.new("BlackKey_Render")
        black_mat.use_nodes = True
    bsdf = black_mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.7

    # --- Skin material ---
    skin_mat = bpy.data.materials.get("Skin_Material")
    if skin_mat is None:
        skin_mat = bpy.data.materials.new("Skin_Material")
    skin_mat.use_nodes = True

    # Clear existing nodes
    for node in list(skin_mat.node_tree.nodes):
        skin_mat.node_tree.nodes.remove(node)

    nodes = skin_mat.node_tree.nodes
    links = skin_mat.node_tree.links

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (200, 0)
    bsdf.inputs["Base Color"].default_value = (0.8, 0.6, 0.5, 1.0)
    bsdf.inputs["Subsurface Weight"].default_value = 0.3
    bsdf.inputs["Subsurface Radius"].default_value = (1.0, 0.2, 0.1)
    bsdf.inputs["Roughness"].default_value = 0.5
    bsdf.inputs["Specular IOR Level"].default_value = 0.3

    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    # Apply skin material to both hand meshes
    for hand_name in ("MANO_RightHand", "MANO_LeftHand",
                       "Hand_RIGHT", "Hand_LEFT"):
        hand_obj = bpy.data.objects.get(hand_name)
        if hand_obj and hand_obj.type == 'MESH':
            # Replace all materials with skin material
            hand_obj.data.materials.clear()
            hand_obj.data.materials.append(skin_mat)

    print("    Materials set up (white keys, black keys, skin).")


# ---------------------------------------------------------------------------
# Render settings
# ---------------------------------------------------------------------------

def _setup_render():
    """Configure render settings for MP4 output."""
    ensure_output_dir()

    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE_NEXT'

    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    scene.render.fps = FPS

    scene.render.filepath = os.path.join(OUTPUT_DIR, "piano_animation")
    scene.render.image_settings.file_format = 'FFMPEG'
    scene.render.ffmpeg.format = 'MPEG4'
    scene.render.ffmpeg.codec = 'H264'
    scene.render.ffmpeg.constant_rate_factor = 'MEDIUM'
    scene.render.ffmpeg.audio_codec = 'AAC'

    scene.frame_start = 1
    # frame_end already set by ik_animator.py

    # EEVEE quality settings
    scene.eevee.taa_render_samples = 64
    scene.eevee.use_gtao = True

    print(f"    Render: EEVEE {scene.render.resolution_x}x"
          f"{scene.render.resolution_y} @ {FPS} FPS")
    print(f"    Output: {scene.render.filepath}")


# ---------------------------------------------------------------------------
# Optional: add MIDI audio to sequencer
# ---------------------------------------------------------------------------

def _add_audio_if_available():
    """If a .wav file exists alongside the MIDI, add it to the sequencer."""
    from config import MIDI_PATH

    # Look for audio file with same stem
    midi_stem = os.path.splitext(MIDI_PATH)[0]
    for ext in (".wav", ".mp3", ".ogg", ".flac"):
        audio_path = midi_stem + ext
        if os.path.isfile(audio_path):
            scene = bpy.context.scene
            if not scene.sequence_editor:
                scene.sequence_editor_create()
            try:
                scene.sequence_editor.sequences.new_sound(
                    "PianoAudio", audio_path, channel=1, frame_start=1
                )
                print(f"    Audio added: {audio_path}")
            except Exception as e:
                print(f"    Warning: could not add audio: {e}")
            return

    print("    No audio file found alongside MIDI (optional).")


# ---------------------------------------------------------------------------
# Render trigger
# ---------------------------------------------------------------------------

def _do_render():
    """Trigger the animation render."""
    print("\n    Starting render ...")
    bpy.ops.render.render(animation=True)
    scene = bpy.context.scene
    print(f"    Render complete: {scene.render.filepath}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_scene_setup_render():
    """Set up the scene and render."""
    print("  [scene_setup_render] Setting up scene ...")

    _setup_camera()
    _setup_lighting()
    _setup_materials()
    _setup_render()
    _add_audio_if_available()

    print("  [scene_setup_render] Scene setup complete.")
    print("  [scene_setup_render] Starting render ...")

    _do_render()

    print("  [scene_setup_render] Done.\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__" or True:
    run_scene_setup_render()
