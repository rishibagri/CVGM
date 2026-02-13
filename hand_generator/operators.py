"""
Blender operators for the hand generator add-on.
"""

import os

import bpy

from .mesh_builder import HandMeshBuilder
from .rig_builder import HandRigBuilder
from .piano_builder import PianoBuilder
from .midi_parser import parse_midi
from .animator import PianoAnimator


class HANDGEN_OT_generate_hands(bpy.types.Operator):
    """Generate realistic hand models based on the configured age"""

    bl_idname = "handgen.generate_hands"
    bl_label = "Generate Hands"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.hand_gen
        generated = []

        sides = []
        if props.hand_side in ("RIGHT", "BOTH"):
            sides.append("RIGHT")
        if props.hand_side in ("LEFT", "BOTH"):
            sides.append("LEFT")

        for side in sides:
            # Build mesh
            builder = HandMeshBuilder(
                age=props.age,
                side=side,
                scale=props.hand_scale,
                add_nails=props.add_nails,
                subdivisions=props.skin_subdivisions,
            )
            mesh_obj = builder.build()

            # Build rig and bind
            rig_builder = HandRigBuilder(
                age=props.age,
                side=side,
                scale=props.hand_scale,
            )
            rig_obj = rig_builder.build(mesh_obj)

            # Add IK targets for piano playing
            rig_builder.add_piano_ik_targets(rig_obj)

            # Offset left hand
            if side == "LEFT":
                rig_obj.location.x = -0.15 * props.hand_scale
                mesh_obj.location.x = -0.15 * props.hand_scale
            else:
                rig_obj.location.x = 0.15 * props.hand_scale
                mesh_obj.location.x = 0.15 * props.hand_scale

            generated.append((side, mesh_obj, rig_obj))

        self.report({"INFO"},
                     f"Generated {len(generated)} hand(s) for age {props.age}")
        return {"FINISHED"}


class HANDGEN_OT_generate_piano(bpy.types.Operator):
    """Generate a piano keyboard model"""

    bl_idname = "handgen.generate_piano"
    bl_label = "Generate Piano"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.hand_gen

        # Determine note range from MIDI if available, else use 3 octaves
        low = props.piano_start_octave * 12 + 21  # adjust to MIDI range
        high = min(low + 36, 108)  # 3 octaves

        if props.midi_filepath and os.path.isfile(bpy.path.abspath(props.midi_filepath)):
            try:
                midi = parse_midi(
                    bpy.path.abspath(props.midi_filepath),
                    bpm_override=props.bpm_override,
                )
                lo, hi = midi.note_range
                # Add some padding
                low = max(21, lo - 5)
                high = min(108, hi + 5)
            except Exception as e:
                self.report({"WARNING"}, f"Could not read MIDI for range: {e}")

        builder = PianoBuilder(low_note=low, high_note=high)
        piano_parent = builder.build()

        # Store reference for animation
        context.scene["_piano_builder_low"] = low
        context.scene["_piano_builder_high"] = high

        self.report({"INFO"},
                     f"Generated piano: notes {low}-{high}")
        return {"FINISHED"}


class HANDGEN_OT_animate_midi(bpy.types.Operator):
    """Parse MIDI file and animate hands playing the piano"""

    bl_idname = "handgen.animate_midi"
    bl_label = "Animate from MIDI"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = context.scene.hand_gen
        return bool(props.midi_filepath)

    def execute(self, context):
        props = context.scene.hand_gen

        midi_path = bpy.path.abspath(props.midi_filepath)
        if not os.path.isfile(midi_path):
            self.report({"ERROR"}, f"MIDI file not found: {midi_path}")
            return {"CANCELLED"}

        # Parse MIDI
        try:
            midi_data = parse_midi(midi_path, bpm_override=props.bpm_override)
        except Exception as e:
            self.report({"ERROR"}, f"Failed to parse MIDI: {e}")
            return {"CANCELLED"}

        self.report({"INFO"},
                     f"Parsed {len(midi_data.notes)} notes, "
                     f"{midi_data.duration_seconds:.1f}s, "
                     f"{midi_data.tempo_bpm:.0f} BPM")

        # Find rig objects in the scene
        rig_left = None
        rig_right = None
        for obj in context.scene.objects:
            if obj.type == "ARMATURE":
                if "LEFT" in obj.name:
                    rig_left = obj
                elif "RIGHT" in obj.name:
                    rig_right = obj

        if rig_left is None and rig_right is None:
            self.report({"ERROR"},
                        "No hand rigs found. Generate hands first.")
            return {"CANCELLED"}

        # Find piano
        piano_builder = None
        piano_parent = context.scene.objects.get("Piano")
        if piano_parent:
            # Reconstruct PianoBuilder with key references
            low = context.scene.get("_piano_builder_low", 21)
            high = context.scene.get("_piano_builder_high", 108)
            piano_builder = PianoBuilder(low_note=low, high_note=high)
            # Map existing key objects
            for obj in piano_parent.children:
                if obj.name.startswith("Key_"):
                    try:
                        note_num = int(obj.name.split("_")[1])
                        piano_builder.key_objects[note_num] = obj
                    except (ValueError, IndexError):
                        pass

        # Set up animation FPS
        context.scene.render.fps = props.animation_fps

        # Animate
        animator = PianoAnimator(
            fps=props.animation_fps,
            key_press_depth=props.key_press_depth,
            finger_lift_height=props.finger_lift_height,
        )
        animator.animate(midi_data, rig_left, rig_right, piano_builder)

        total_frames = int(midi_data.duration_seconds * props.animation_fps) + 30
        context.scene.frame_end = max(context.scene.frame_end, total_frames)
        context.scene.frame_set(1)

        self.report({"INFO"},
                     f"Animation complete: {total_frames} frames "
                     f"at {props.animation_fps} FPS")
        return {"FINISHED"}


class HANDGEN_OT_full_pipeline(bpy.types.Operator):
    """Run the full pipeline: generate hands, piano, and animate from MIDI"""

    bl_idname = "handgen.full_pipeline"
    bl_label = "Full Pipeline (Hands + Piano + Animate)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        props = context.scene.hand_gen
        return bool(props.midi_filepath)

    def execute(self, context):
        bpy.ops.handgen.generate_hands()

        props = context.scene.hand_gen
        if props.generate_piano:
            bpy.ops.handgen.generate_piano()

        bpy.ops.handgen.animate_midi()

        self.report({"INFO"}, "Full pipeline complete")
        return {"FINISHED"}


classes = (
    HANDGEN_OT_generate_hands,
    HANDGEN_OT_generate_piano,
    HANDGEN_OT_animate_midi,
    HANDGEN_OT_full_pipeline,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
