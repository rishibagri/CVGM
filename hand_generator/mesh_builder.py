"""
Procedural hand mesh builder.

Generates a realistic hand mesh by constructing the palm, fingers, and thumb
from contoured cross-sections that are lofted together. Age affects proportions,
joint prominence, finger taper, and wrinkle intensity.
"""

import math
from dataclasses import dataclass, field
from typing import List, Tuple

import bpy
import bmesh
from mathutils import Vector, Matrix


# ---------------------------------------------------------------------------
# Age-driven parameter tables
# ---------------------------------------------------------------------------

@dataclass
class AgeProfile:
    """Derived proportions for a given age."""

    # Overall scale factor relative to adult (age 25)
    size_factor: float = 1.0
    # How much wider the joints are relative to the phalanx midpoints
    joint_bulge: float = 1.0
    # Finger taper from base to tip (1 = no taper)
    taper: float = 1.0
    # Wrinkle displacement intensity 0..1
    wrinkle_intensity: float = 0.0
    # Skin thickness (affects nail bed offset, knuckle rounding)
    skin_thickness: float = 1.0
    # Nail length factor
    nail_length: float = 1.0
    # Palm width / length ratio tweak
    palm_aspect: float = 1.0
    # Finger chubbiness (radius multiplier)
    chubbiness: float = 1.0


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def age_profile(age: int) -> AgeProfile:
    """Return an AgeProfile interpolated for the given age."""

    p = AgeProfile()

    # --- size (hand length relative to adult) ---
    if age < 6:
        p.size_factor = _lerp(0.40, 0.50, (age - 3) / 3)
    elif age < 12:
        p.size_factor = _lerp(0.50, 0.75, (age - 6) / 6)
    elif age < 18:
        p.size_factor = _lerp(0.75, 0.97, (age - 12) / 6)
    elif age <= 25:
        p.size_factor = _lerp(0.97, 1.00, (age - 18) / 7)
    else:
        # Slight shrinkage in old age
        p.size_factor = _lerp(1.00, 0.95, (age - 25) / 65)

    # --- joint prominence ---
    if age < 18:
        p.joint_bulge = _lerp(1.00, 1.05, age / 18)
    elif age < 50:
        p.joint_bulge = _lerp(1.05, 1.12, (age - 18) / 32)
    else:
        p.joint_bulge = _lerp(1.12, 1.30, (age - 50) / 40)

    # --- taper ---
    if age < 10:
        p.taper = 0.85  # chubbier child fingers, less taper
    elif age < 25:
        p.taper = _lerp(0.85, 0.72, (age - 10) / 15)
    else:
        p.taper = _lerp(0.72, 0.68, (age - 25) / 65)

    # --- wrinkles ---
    if age < 20:
        p.wrinkle_intensity = 0.0
    elif age < 40:
        p.wrinkle_intensity = _lerp(0.0, 0.3, (age - 20) / 20)
    elif age < 60:
        p.wrinkle_intensity = _lerp(0.3, 0.7, (age - 40) / 20)
    else:
        p.wrinkle_intensity = _lerp(0.7, 1.0, (age - 60) / 30)

    # --- chubbiness ---
    if age < 8:
        p.chubbiness = _lerp(1.35, 1.15, (age - 3) / 5)
    elif age < 18:
        p.chubbiness = _lerp(1.15, 1.00, (age - 8) / 10)
    elif age < 50:
        p.chubbiness = 1.00
    else:
        p.chubbiness = _lerp(1.00, 0.92, (age - 50) / 40)

    # --- palm aspect (wider for children) ---
    if age < 12:
        p.palm_aspect = _lerp(1.15, 1.05, (age - 3) / 9)
    else:
        p.palm_aspect = _lerp(1.05, 1.00, (age - 12) / 13)

    # --- nail length ---
    p.nail_length = _lerp(0.6, 1.0, min(age / 18, 1.0))

    # --- skin thickness ---
    if age < 25:
        p.skin_thickness = _lerp(0.7, 1.0, age / 25)
    else:
        p.skin_thickness = _lerp(1.0, 0.85, (age - 25) / 65)

    return p


# ---------------------------------------------------------------------------
# Cross-section helpers
# ---------------------------------------------------------------------------

def _ellipse_ring(center: Vector, radius_x: float, radius_y: float,
                  segments: int, up: Vector, forward: Vector) -> List[Vector]:
    """Return vertices forming an elliptical ring at *center*."""
    right = up.cross(forward).normalized()
    actual_up = forward.cross(right).normalized()
    verts = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        offset = right * (math.cos(angle) * radius_x) + actual_up * (math.sin(angle) * radius_y)
        verts.append(center + offset)
    return verts


def _rounded_rect_ring(center: Vector, width: float, height: float,
                       corner_r: float, segments: int,
                       up: Vector, forward: Vector) -> List[Vector]:
    """Rounded-rectangle cross-section ring (good for palm slices)."""
    right = up.cross(forward).normalized()
    actual_up = forward.cross(right).normalized()
    hw = width / 2 - corner_r
    hh = height / 2 - corner_r
    corners = [
        (hw, hh, 0, math.pi / 2),
        (-hw, hh, math.pi / 2, math.pi),
        (-hw, -hh, math.pi, 3 * math.pi / 2),
        (hw, -hh, 3 * math.pi / 2, 2 * math.pi),
    ]
    segs_per_corner = max(2, segments // 4)
    verts = []
    for cx, cy, a_start, a_end in corners:
        for j in range(segs_per_corner):
            t = j / segs_per_corner
            angle = a_start + (a_end - a_start) * t
            px = cx + corner_r * math.cos(angle)
            py = cy + corner_r * math.sin(angle)
            verts.append(center + right * px + actual_up * py)
    return verts


# ---------------------------------------------------------------------------
# Finger / thumb data
# ---------------------------------------------------------------------------

# Proportions relative to total hand length (wrist to middle-tip).
# Format per finger: (base_x_offset, base_y_offset,
#                      phalanx_lengths: (proximal, middle, distal),
#                      base_radius)
# X = lateral (thumb side negative for right hand), Y = forward from palm base.

FINGER_DATA = {
    "thumb":  (-0.42, 0.10, (0.17, 0.13, 0.10), 0.055),
    "index":  (-0.17, 0.52, (0.18, 0.13, 0.10), 0.042),
    "middle": ( 0.00, 0.55, (0.20, 0.14, 0.11), 0.044),
    "ring":   ( 0.16, 0.52, (0.19, 0.13, 0.10), 0.042),
    "pinky":  ( 0.31, 0.46, (0.15, 0.10, 0.09), 0.036),
}


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class HandMeshBuilder:
    """Build a single hand mesh + armature-ready vertex groups."""

    RING_SEGMENTS = 12  # verts per cross-section ring

    def __init__(self, age: int, side: str = "RIGHT", scale: float = 1.0,
                 add_nails: bool = True, subdivisions: int = 2):
        self.profile = age_profile(age)
        self.age = age
        self.side = side
        self.scale = scale * self.profile.size_factor * 0.18  # 0.18m ~ adult hand
        self.add_nails = add_nails
        self.subdivisions = subdivisions
        self.mirror = -1.0 if side == "LEFT" else 1.0

    # ---- public API -------------------------------------------------------

    def build(self) -> bpy.types.Object:
        """Create and return the hand mesh object."""
        mesh = bpy.data.meshes.new(f"Hand_{self.side}")
        obj = bpy.data.objects.new(f"Hand_{self.side}", mesh)
        bpy.context.collection.objects.link(obj)

        bm = bmesh.new()
        try:
            self._build_palm(bm)
            for name, data in FINGER_DATA.items():
                self._build_finger(bm, name, data)
            bm.to_mesh(mesh)
        finally:
            bm.free()

        # Smooth shading
        for poly in mesh.polygons:
            poly.use_smooth = True

        # Subdivision surface modifier
        mod = obj.modifiers.new("Subsurf", "SUBSURF")
        mod.levels = self.subdivisions
        mod.render_levels = self.subdivisions + 1

        # Corrective smooth to reduce pinching
        cs = obj.modifiers.new("CorrectiveSmooth", "CORRECTIVE_SMOOTH")
        cs.iterations = 5
        cs.scale = 0.8

        # Skin material
        self._apply_skin_material(obj)

        # Vertex groups (will be used by the rig)
        self._create_vertex_groups(obj, mesh)

        return obj

    # ---- palm construction ------------------------------------------------

    def _build_palm(self, bm: bmesh.types.BMesh):
        s = self.scale
        ap = self.profile.palm_aspect
        chub = self.profile.chubbiness

        # Palm dimensions
        palm_width = 0.48 * s * ap * self.mirror
        palm_height = 0.14 * s * chub
        palm_length = 0.55 * s

        up = Vector((0, 0, 1))
        fwd = Vector((0, 1, 0))

        slices = 6
        rings = []
        for i in range(slices):
            t = i / (slices - 1)
            y = t * palm_length
            # Width narrows slightly toward the wrist
            w_factor = _lerp(0.85, 1.0, t)
            # Height (thickness) is max at center
            h_factor = _lerp(0.9, 1.0, 0.5 - abs(t - 0.5))
            w = abs(palm_width) * w_factor
            h = palm_height * h_factor
            center = Vector((0, y, 0))
            ring = _rounded_rect_ring(center, w, h, min(w, h) * 0.25,
                                      self.RING_SEGMENTS, up, fwd)
            bm_verts = [bm.verts.new(v) for v in ring]
            rings.append(bm_verts)

        # Loft faces between rings
        for i in range(len(rings) - 1):
            r0 = rings[i]
            r1 = rings[i + 1]
            n = len(r0)
            for j in range(n):
                j1 = (j + 1) % n
                try:
                    bm.faces.new([r0[j], r0[j1], r1[j1], r1[j]])
                except ValueError:
                    pass

        # Cap the wrist end
        try:
            bm.faces.new(rings[0])
        except ValueError:
            pass

    # ---- finger construction ----------------------------------------------

    def _build_finger(self, bm: bmesh.types.BMesh, name: str,
                      data: Tuple):
        base_x, base_y, phalanx_lens, base_radius = data
        s = self.scale
        p = self.profile

        # Position
        bx = base_x * s * self.mirror
        by = base_y * s
        base = Vector((bx, by, 0))

        radius = base_radius * s * p.chubbiness

        up = Vector((0, 0, 1))

        # Thumb has different orientation
        if name == "thumb":
            fwd = Vector((self.mirror * -0.5, 0.75, 0.15)).normalized()
        else:
            fwd = Vector((0, 1, 0))

        cursor = base.copy()
        prev_ring = None
        joint_idx = 0

        phalanx_names = ["proximal", "middle", "distal"]
        for pi, plen in enumerate(phalanx_lens):
            seg_len = plen * s
            # Number of rings per phalanx
            n_rings = 4

            for ri in range(n_rings):
                t = ri / (n_rings - 1)
                pos = cursor + fwd * (seg_len * t)

                # Taper along the finger
                overall_t = (pi + t) / len(phalanx_lens)
                r = radius * _lerp(1.0, p.taper, overall_t)

                # Joint bulge at phalanx boundaries
                if ri == 0 and pi > 0:
                    r *= p.joint_bulge

                # Slightly oval cross-section (wider than tall)
                rx = r * 1.05
                ry = r * 0.95

                ring_verts = _ellipse_ring(pos, rx, ry, self.RING_SEGMENTS, up, fwd)
                bm_ring = [bm.verts.new(v) for v in ring_verts]

                if prev_ring is not None:
                    n = len(prev_ring)
                    for j in range(n):
                        j1 = (j + 1) % n
                        try:
                            bm.faces.new([prev_ring[j], prev_ring[j1],
                                          bm_ring[j1], bm_ring[j]])
                        except ValueError:
                            pass

                prev_ring = bm_ring

            cursor = cursor + fwd * seg_len
            joint_idx += 1

        # Cap the fingertip (rounded)
        if prev_ring:
            tip = cursor + fwd * (radius * p.taper * 0.3)
            tip_vert = bm.verts.new(tip)
            n = len(prev_ring)
            for j in range(n):
                j1 = (j + 1) % n
                try:
                    bm.faces.new([prev_ring[j], prev_ring[j1], tip_vert])
                except ValueError:
                    pass

        # Fingernail
        if self.add_nails and prev_ring:
            self._build_nail(bm, cursor, fwd, up, radius * p.taper, p.nail_length)

    # ---- fingernail -------------------------------------------------------

    def _build_nail(self, bm: bmesh.types.BMesh, tip_base: Vector,
                    fwd: Vector, up: Vector, radius: float,
                    length_factor: float):
        """Add a simple nail plate on top of the fingertip."""
        right = up.cross(fwd).normalized()
        nail_up = fwd.cross(right).normalized()

        nail_len = radius * 1.6 * length_factor
        nail_w = radius * 0.85
        nail_thick = radius * 0.08

        # Offset to dorsal (top) surface
        base_center = tip_base - fwd * (nail_len * 0.7) + nail_up * (radius * 0.85)

        # Simple quad strip
        rows = 4
        cols = 5
        verts_grid = []
        for r in range(rows):
            row = []
            u = r / (rows - 1)
            for c in range(cols):
                v = c / (cols - 1)
                x = (v - 0.5) * nail_w * 2
                y = u * nail_len
                # Slight curvature
                z = nail_thick * math.cos(math.pi * (v - 0.5)) * 0.5
                pos = base_center + right * x + fwd * y + nail_up * z
                row.append(bm.verts.new(pos))
            verts_grid.append(row)

        for r in range(rows - 1):
            for c in range(cols - 1):
                try:
                    bm.faces.new([
                        verts_grid[r][c],
                        verts_grid[r][c + 1],
                        verts_grid[r + 1][c + 1],
                        verts_grid[r + 1][c],
                    ])
                except ValueError:
                    pass

    # ---- materials --------------------------------------------------------

    def _apply_skin_material(self, obj: bpy.types.Object):
        mat = bpy.data.materials.new(f"Skin_{self.side}")
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        links = mat.node_tree.links

        # Clear defaults
        for n in nodes:
            nodes.remove(n)

        output = nodes.new("ShaderNodeOutputMaterial")
        output.location = (600, 0)

        principled = nodes.new("ShaderNodeBsdfPrincipled")
        principled.location = (200, 0)

        # Skin-like settings
        principled.inputs["Base Color"].default_value = (0.76, 0.57, 0.45, 1.0)
        principled.inputs["Subsurface Weight"].default_value = 0.3
        principled.inputs["Subsurface Radius"].default_value = (0.8, 0.4, 0.2)
        principled.inputs["Roughness"].default_value = 0.55
        principled.inputs["Specular IOR Level"].default_value = 0.3

        links.new(principled.outputs["BSDF"], output.inputs["Surface"])

        # Age-dependent wrinkle bump via noise
        if self.profile.wrinkle_intensity > 0.01:
            noise = nodes.new("ShaderNodeTexNoise")
            noise.location = (-200, -200)
            noise.inputs["Scale"].default_value = 120.0
            noise.inputs["Detail"].default_value = 8.0
            noise.inputs["Roughness"].default_value = 0.7

            bump = nodes.new("ShaderNodeBump")
            bump.location = (0, -200)
            bump.inputs["Strength"].default_value = self.profile.wrinkle_intensity * 0.15

            links.new(noise.outputs["Fac"], bump.inputs["Height"])
            links.new(bump.outputs["Normal"], principled.inputs["Normal"])

        # Nail material (separate)
        nail_mat = bpy.data.materials.new(f"Nail_{self.side}")
        nail_mat.use_nodes = True
        nail_nodes = nail_mat.node_tree.nodes
        nail_links = nail_mat.node_tree.links
        for n in nail_nodes:
            nail_nodes.remove(n)
        n_out = nail_nodes.new("ShaderNodeOutputMaterial")
        n_out.location = (400, 0)
        n_bsdf = nail_nodes.new("ShaderNodeBsdfPrincipled")
        n_bsdf.location = (0, 0)
        n_bsdf.inputs["Base Color"].default_value = (0.85, 0.75, 0.72, 1.0)
        n_bsdf.inputs["Roughness"].default_value = 0.2
        n_bsdf.inputs["Specular IOR Level"].default_value = 0.6
        n_bsdf.inputs["Coat Weight"].default_value = 0.4
        nail_links.new(n_bsdf.outputs["BSDF"], n_out.inputs["Surface"])

        obj.data.materials.append(mat)
        obj.data.materials.append(nail_mat)

    # ---- vertex groups (for rigging) --------------------------------------

    def _create_vertex_groups(self, obj: bpy.types.Object,
                              mesh: bpy.types.Mesh):
        """Create vertex groups matching the bone names the rig will use."""
        s = self.scale

        bone_names = ["palm"]
        for fname in ["thumb", "index", "middle", "ring", "pinky"]:
            for seg in ["proximal", "middle", "distal"]:
                bone_names.append(f"{fname}_{seg}")

        for bname in bone_names:
            obj.vertex_groups.new(name=bname)

        # Assign verts to groups by proximity to finger regions
        for v in mesh.vertices:
            co = Vector(v.co)
            # Simple heuristic: assign by Y-position and X-position
            assigned = False
            for fname, (bx, by, plens, _) in FINGER_DATA.items():
                fx = bx * s * self.mirror
                fy = by * s
                dx = abs(co.x - fx)
                dy = co.y - fy

                if dx < 0.06 * s and dy > -0.03 * s:
                    # Determine which phalanx
                    accum = 0.0
                    for pi, pl in enumerate(plens):
                        seg_len = pl * s
                        if dy < accum + seg_len or pi == len(plens) - 1:
                            seg_name = ["proximal", "middle", "distal"][pi]
                            group_name = f"{fname}_{seg_name}"
                            grp = obj.vertex_groups.get(group_name)
                            if grp:
                                weight = max(0.0, 1.0 - dx / (0.06 * s))
                                grp.add([v.index], weight, "REPLACE")
                            assigned = True
                            break
                        accum += seg_len
                    if assigned:
                        break

            if not assigned:
                grp = obj.vertex_groups.get("palm")
                if grp:
                    grp.add([v.index], 1.0, "REPLACE")
