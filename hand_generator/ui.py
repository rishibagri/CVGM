"""
Blender UI panels for the hand generator add-on.
"""

import bpy


class HANDGEN_PT_main(bpy.types.Panel):
    """Main panel for hand generation settings"""

    bl_label = "Hand Generator"
    bl_idname = "HANDGEN_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hand Gen"

    def draw(self, context):
        layout = self.layout
        props = context.scene.hand_gen

        # --- Hand settings ---
        box = layout.box()
        box.label(text="Hand Settings", icon="BONE_DATA")

        row = box.row()
        row.prop(props, "age")

        row = box.row()
        row.prop(props, "hand_side")

        row = box.row()
        row.prop(props, "hand_scale")

        row = box.row()
        row.prop(props, "skin_subdivisions")

        row = box.row()
        row.prop(props, "add_nails")
        row.prop(props, "add_wrinkles")

        layout.separator()

        # Generate button
        layout.operator("handgen.generate_hands", icon="MESH_DATA")


class HANDGEN_PT_piano(bpy.types.Panel):
    """Piano generation settings"""

    bl_label = "Piano"
    bl_idname = "HANDGEN_PT_piano"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hand Gen"

    def draw(self, context):
        layout = self.layout
        props = context.scene.hand_gen

        box = layout.box()
        box.label(text="Piano Settings", icon="PLAY_SOUND")

        row = box.row()
        row.prop(props, "generate_piano")

        row = box.row()
        row.prop(props, "piano_start_octave")

        layout.separator()
        layout.operator("handgen.generate_piano", icon="MESH_CUBE")


class HANDGEN_PT_midi(bpy.types.Panel):
    """MIDI animation settings"""

    bl_label = "MIDI Animation"
    bl_idname = "HANDGEN_PT_midi"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Hand Gen"

    def draw(self, context):
        layout = self.layout
        props = context.scene.hand_gen

        box = layout.box()
        box.label(text="MIDI Settings", icon="FILE_SOUND")

        row = box.row()
        row.prop(props, "midi_filepath")

        row = box.row()
        row.prop(props, "bpm_override")

        layout.separator()

        box = layout.box()
        box.label(text="Animation Tuning", icon="ANIM")

        row = box.row()
        row.prop(props, "animation_fps")

        row = box.row()
        row.prop(props, "key_press_depth")

        row = box.row()
        row.prop(props, "finger_lift_height")

        layout.separator()

        # Individual animate button
        layout.operator("handgen.animate_midi", icon="ANIM_DATA")

        layout.separator()

        # Full pipeline button (prominent)
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("handgen.full_pipeline", icon="PLAY")


classes = (
    HANDGEN_PT_main,
    HANDGEN_PT_piano,
    HANDGEN_PT_midi,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
