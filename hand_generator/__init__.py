bl_info = {
    "name": "Realistic Hand Generator & MIDI Piano Animator",
    "author": "CVGM",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Hand Gen",
    "description": "Generate age-based realistic 3D hand models rigged for MIDI-driven piano animation",
    "category": "Animation",
}

import bpy

from . import properties, operators, ui


def register():
    properties.register()
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    properties.unregister()


if __name__ == "__main__":
    register()
