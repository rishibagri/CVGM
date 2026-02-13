import bpy
from bpy.props import (
    IntProperty,
    FloatProperty,
    EnumProperty,
    StringProperty,
    BoolProperty,
)


class HandGenProperties(bpy.types.PropertyGroup):
    """Properties for the hand generator and MIDI animator."""

    age: IntProperty(
        name="Age",
        description="Age of the hand model (affects proportions, skin detail, joint size)",
        default=25,
        min=3,
        max=90,
        subtype="UNSIGNED",
    )

    hand_side: EnumProperty(
        name="Hand",
        description="Which hand to generate",
        items=[
            ("LEFT", "Left", "Generate left hand"),
            ("RIGHT", "Right", "Generate right hand"),
            ("BOTH", "Both", "Generate both hands"),
        ],
        default="BOTH",
    )

    hand_scale: FloatProperty(
        name="Scale",
        description="Overall hand scale multiplier",
        default=1.0,
        min=0.5,
        max=2.0,
    )

    skin_subdivisions: IntProperty(
        name="Subdivisions",
        description="Subdivision level for mesh smoothness (higher = more detail)",
        default=2,
        min=1,
        max=4,
    )

    add_nails: BoolProperty(
        name="Add Fingernails",
        description="Generate fingernail geometry",
        default=True,
    )

    add_wrinkles: BoolProperty(
        name="Add Wrinkle Detail",
        description="Add displacement-based wrinkle detail (age-dependent intensity)",
        default=True,
    )

    # MIDI properties
    midi_filepath: StringProperty(
        name="MIDI File",
        description="Path to the MIDI file for piano animation",
        subtype="FILE_PATH",
        default="",
    )

    piano_start_octave: IntProperty(
        name="Start Octave",
        description="Lowest octave of the piano range to animate",
        default=3,
        min=0,
        max=8,
    )

    bpm_override: FloatProperty(
        name="BPM Override",
        description="Override MIDI tempo (0 = use MIDI tempo)",
        default=0.0,
        min=0.0,
        max=300.0,
    )

    key_press_depth: FloatProperty(
        name="Key Press Depth",
        description="How far piano keys depress (in Blender units)",
        default=0.015,
        min=0.005,
        max=0.05,
    )

    finger_lift_height: FloatProperty(
        name="Finger Lift Height",
        description="How high fingers lift between notes",
        default=0.03,
        min=0.01,
        max=0.1,
    )

    animation_fps: IntProperty(
        name="Animation FPS",
        description="Frame rate for the animation",
        default=30,
        min=24,
        max=120,
    )

    generate_piano: BoolProperty(
        name="Generate Piano Keys",
        description="Also generate a piano keyboard model",
        default=True,
    )


classes = (HandGenProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hand_gen = bpy.props.PointerProperty(type=HandGenProperties)


def unregister():
    del bpy.types.Scene.hand_gen
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
