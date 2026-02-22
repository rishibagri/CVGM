"""
Script 9: master_runner.py

Orchestrate the full MIDI-to-Piano-Animation pipeline by executing
scripts 1-8 in sequence.

Run via:
    blender --background --python master_runner.py

Or paste into Blender's script editor and click "Run Script".
"""

import os
import sys
import time

import bpy

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Ensure the script directory is on the Python path so that
# `from config import *` works inside each sub-script.
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Ordered list of pipeline scripts.
# Scripts 1-4 build the piano and right hand (assumed to already exist
# or to be present in the same directory).
# Scripts 5-8 are the new scripts built by this project.
SCRIPTS = [
    "build_piano.py",           # 1 — Procedural 88-key piano
    "animate_keys.py",          # 2 — MIDI → key press animation
    "import_hand.py",           # 3 — Import MANO right-hand mesh
    "rig_hand.py",              # 4 — Armature + IK for right hand
    "import_left_hand.py",      # 5 — Import + rig left hand (mirrored)
    "finger_assigner.py",       # 6 — MIDI → finger assignments
    "ik_animator.py",           # 7 — Animate IK targets
    "scene_setup_render.py",    # 8 — Camera, lights, render to MP4
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    total_start = time.time()

    print("\n" + "=" * 64)
    print("  MIDI → Piano Animation Pipeline")
    print("=" * 64)

    succeeded = []
    failed = []

    for script_name in SCRIPTS:
        path = os.path.join(SCRIPT_DIR, script_name)

        if not os.path.isfile(path):
            print(f"\n  SKIP: {script_name} (file not found)")
            continue

        print(f"\n{'=' * 60}")
        print(f"  RUNNING: {script_name}")
        print(f"{'=' * 60}")

        start = time.time()
        try:
            # Execute the script in the current namespace so it can
            # access bpy and any objects created by earlier scripts.
            exec(
                compile(open(path).read(), path, "exec"),
                {"__name__": "__main__", "__file__": path}
            )
            elapsed = time.time() - start
            print(f"  DONE: {script_name} ({elapsed:.1f}s)")
            succeeded.append(script_name)
        except Exception as exc:
            elapsed = time.time() - start
            print(f"  FAILED: {script_name} ({elapsed:.1f}s)")
            print(f"  ERROR: {exc}")
            import traceback
            traceback.print_exc()
            failed.append((script_name, str(exc)))

    total_elapsed = time.time() - total_start

    # Summary
    print(f"\n\n{'=' * 64}")
    print("  PIPELINE COMPLETE")
    print(f"{'=' * 64}")
    print(f"  Total time : {total_elapsed:.1f}s")
    print(f"  Succeeded  : {len(succeeded)}/{len(SCRIPTS)}")
    if failed:
        print(f"  Failed     : {len(failed)}")
        for name, err in failed:
            print(f"    - {name}: {err}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__" or True:
    main()
