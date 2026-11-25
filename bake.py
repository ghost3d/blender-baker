"""Core baking logic.

Everything works on temporary copies of the low and high poly meshes (with modifiers
applied), so the user's objects and materials are never modified.
"""

import json
import os

import bmesh
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

TMP_PREFIX = "__baker_"
HEIGHT_ATTR = "baker_height"
CAVITY_ATTR = "baker_cavity"

EXTENSIONS = {
    'PNG': ".png",
    'TIFF': ".tif",
    'OPEN_EXR': ".exr",
    'TARGA': ".tga",
    'JPEG': ".jpg",
}


class BakeError(Exception):
    pass


# --------------------------------------------------------------------------- helpers

def high_objects(pair):
    if pair.high_mode == 'OBJECT':
        objs = [pair.high] if pair.high else []
    else:
        objs = list(pair.high_collection.all_objects) if pair.high_collection else []
    return [o for o in objs if o.type == 'MESH' and o != pair.low]


def validate_pair(pair):
    """Return an error string, or None if the pair can be baked."""
    if not pair.low:
        return "no low poly object"
    if not pair.low.data.uv_layers:
        return f"low poly '{pair.low.name}' has no UV map"
    if not high_objects(pair):
        return "no high poly mesh"
    if pair.cage and len(pair.cage.data.vertices) != len(pair.low.data.vertices):
        return "cage does not have the same vertex count as the low poly"
    return None


def output_folder(settings, pair):
    raw = settings.output_dir.strip() or "//baked/"
    if raw.startswith("//") and not bpy.data.filepath:
        raise BakeError("Save the .blend first, or pick an absolute output folder")
    folder = bpy.path.abspath(raw)
    if settings.subfolder_per_pair:
        folder = os.path.join(folder, bpy.path.clean_name(pair.label()))
    os.makedirs(folder, exist_ok=True)
    return folder


def output_path(settings, pair, folder, suffix):
    try:
        stem = settings.filename_template.format(name=pair.label(), map=suffix)
    except (KeyError, IndexError, ValueError):
        stem = f"{pair.label()}_{suffix}"
    return os.path.join(folder, bpy.path.clean_name(stem) + EXTENSIONS[settings.file_format])


def _temp_copy(obj, depsgraph, collection):
    """Copy of obj with modifiers applied, in world space."""
    mesh = bpy.data.meshes.new_from_object(
        obj.evaluated_get(depsgraph), preserve_all_data_layers=True, depsgraph=depsgraph)
    mesh.name = TMP_PREFIX + obj.name
    tmp = bpy.data.objects.new(TMP_PREFIX + obj.name, mesh)
    tmp.matrix_world = obj.matrix_world.copy()
    collection.objects.link(tmp)
    return tmp


def _set_single_material(obj, mat):
    me = obj.data
    me.materials.clear()
    me.materials.append(mat)
    if me.polygons:
        me.polygons.foreach_set("material_index", np.zeros(len(me.polygons), dtype=np.int32))


def _new_material(name):
    mat = bpy.data.materials.new(TMP_PREFIX + name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


def _emission_material(name, build_value):
    """Material that emits a grey value. build_value(nodes, links) -> output socket."""
    mat = _new_material(name)
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    out = nodes.new("ShaderNodeOutputMaterial")
    emit = nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    links.new(build_value(nodes, links), emit.inputs["Color"])
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    return mat


def _attribute_material(attr_name):
    def build(nodes, links):
        attr = nodes.new("ShaderNodeAttribute")
        attr.attribute_type = 'GEOMETRY'
        attr.attribute_name = attr_name
        return attr.outputs["Fac"]
    return _emission_material(attr_name, build)


def _world_points_normals(obj):
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    nor = np.empty(n * 3, dtype=np.float64)
    if hasattr(me, "vertex_normals"):
        me.vertex_normals.foreach_get("vector", nor)
    else:
        me.vertices.foreach_get("normal", nor)
    m = np.array(obj.matrix_world)
    rot = m[:3, :3]
    co = co.reshape(-1, 3) @ rot.T + m[:3, 3]
    nor = nor.reshape(-1, 3) @ np.linalg.inv(rot)  # inverse-transpose for normals
    nor /= np.maximum(np.linalg.norm(nor, axis=1, keepdims=True), 1e-12)
    return co, nor


def _compute_cavity(highs_tmp, contrast, mode, smooth_steps):
    """Per-vertex mean curvature on the high poly, auto-normalised so the result does not
    depend on mesh density or scale. Written as a 0-1 point attribute."""
    per_obj = []
    samples = []
    for h in highs_tmp:
        me = h.data
        nv, ne = len(me.vertices), len(me.edges)
        if not nv or not ne:
            per_obj.append((me, np.zeros(nv), None))
            continue
        co, nor = _world_points_normals(h)
        ev = np.empty(ne * 2, dtype=np.int64)
        me.edges.foreach_get("vertices", ev)
        i, j = ev[0::2], ev[1::2]
        e = co[j] - co[i]
        l2 = np.maximum((e * e).sum(1), 1e-20)
        k_edge = ((nor[j] - nor[i]) * e).sum(1) / l2  # >0 convex, <0 concave (1/length)
        k = np.zeros(nv)
        deg = np.zeros(nv)
        np.add.at(k, i, k_edge)
        np.add.at(k, j, k_edge)
        np.add.at(deg, i, 1.0)
        np.add.at(deg, j, 1.0)
        deg = np.maximum(deg, 1.0)
        k /= deg
        for _ in range(smooth_steps):
            acc = k.copy()
            np.add.at(acc, i, k[j])
            np.add.at(acc, j, k[i])
            k = acc / (deg + 1.0)
        per_obj.append((me, k, deg))
        samples.append(np.abs(k))

    scale = 1.0
    if samples:
        scale = float(np.percentile(np.concatenate(samples), 98.0)) or 1.0

    for me, k, _deg in per_obj:
        c = k / scale * contrast
        if mode == 'CAVITY':
            values = np.clip(1.0 + c, 0.0, 1.0)  # convex/flat white, concave dark
        else:
            values = np.clip(0.5 + 0.5 * c, 0.0, 1.0)
        attr = me.attributes.get(CAVITY_ATTR) or me.attributes.new(CAVITY_ATTR, 'FLOAT', 'POINT')
        attr.data.foreach_set("value", values.astype(np.float32))
        me.update()


def _plain_material():
    mat = _new_material("plain")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfDiffuse")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _target_material():
    """Material for the low poly copy: holds the active image node the bake writes into."""
    mat = _new_material("target")
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfDiffuse")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    tex = nodes.new("ShaderNodeTexImage")
    nodes.active = tex
    tex.select = True
    return mat, tex


def _compute_height(low_tmp, highs_tmp, fixed_range, raw):
    """Write signed distance from the low poly surface to each high poly vertex as a
    point attribute. Returns the range used for normalisation."""
    bm = bmesh.new()
    bm.from_mesh(low_tmp.data)
    bm.transform(low_tmp.matrix_world)
    bm.normal_update()  # face normals are stale after from_mesh/transform; they give the sign
    bvh = BVHTree.FromBMesh(bm)
    bm.free()

    per_obj = []
    biggest = 0.0
    for h in highs_tmp:
        me = h.data
        n = len(me.vertices)
        co = np.empty(n * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        m = np.array(h.matrix_world)
        wco = co @ m[:3, :3].T + m[:3, 3]

        dist = np.zeros(n, dtype=np.float64)
        find = bvh.find_nearest
        for i in range(n):
            p = Vector(wco[i])
            loc, nor, _idx, d = find(p)
            if loc is None:
                continue
            dist[i] = d if (p - loc).dot(nor) >= 0.0 else -d
        if n:
            biggest = max(biggest, float(np.abs(dist).max()))
        per_obj.append((me, dist))

    rng = fixed_range if fixed_range > 0.0 else (biggest or 1.0)
    for me, dist in per_obj:
        if raw:
            values = dist
        else:
            values = np.clip(0.5 + dist / (2.0 * rng), 0.0, 1.0)
        attr = me.attributes.get(HEIGHT_ATTR) or me.attributes.new(HEIGHT_ATTR, 'FLOAT', 'POINT')
        attr.data.foreach_set("value", values.astype(np.float32))
        me.update()
    return rng


def _configure_save_scene(scene, settings, grey):
    ims = scene.render.image_settings
    if hasattr(ims, "media_type"):  # Blender 5.0+
        try:
            ims.media_type = 'IMAGE'
        except TypeError:
            pass
    ims.file_format = settings.file_format

    def try_set(attr, *values):
        for v in values:
            try:
                setattr(ims, attr, v)
                return
            except TypeError:
                continue

    if grey:
        try_set("color_mode", 'BW', 'RGB')
    else:
        try_set("color_mode", 'RGB')

    depth = settings.bit_depth
    if settings.file_format == 'OPEN_EXR':
        try_set("color_depth", '32' if depth == '32' else '16')
        try_set("exr_codec", 'ZIP')
    elif settings.file_format in {'PNG', 'TIFF'}:
        try_set("color_depth", '16' if depth in {'16', '32'} else '8', '8')
    if settings.file_format == 'JPEG':
        ims.quality = 95
    elif settings.file_format == 'PNG':
        ims.compression = 15

    # Baked maps are data, never apply a view transform.
    if hasattr(ims, "color_management"):
        try:
            ims.color_management = 'OVERRIDE'
            ims.view_settings.view_transform = 'Standard'
            ims.view_settings.look = 'None'
            ims.view_settings.exposure = 0.0
            ims.view_settings.gamma = 1.0
        except (TypeError, AttributeError):
            pass
    scene.view_settings.view_transform = 'Standard'
    scene.view_settings.look = 'None'


# --------------------------------------------------------------------------- state

class _SceneState:
    """Remembers render / selection state and restores it afterwards."""

    def __init__(self, context):
        scene = context.scene
        self.scene = scene
        self.engine = scene.render.engine
        self.samples = scene.cycles.samples
        self.device = scene.cycles.device
        self.denoise = scene.cycles.use_denoising
        self.view_layer = context.view_layer
        self.selected = [o.name for o in context.selected_objects]
        active = context.view_layer.objects.active
        self.active = active.name if active else None

    def restore(self):
        scene = self.scene
        try:
            scene.render.engine = self.engine
        except TypeError:
            pass
        scene.cycles.samples = self.samples
        scene.cycles.device = self.device
        scene.cycles.use_denoising = self.denoise
        self.view_layer.update()  # drop references to the deleted temp objects
        for o in self.view_layer.objects:
            if o is not None:
                o.select_set(o.name in self.selected)
        if self.active and self.active in self.view_layer.objects:
            self.view_layer.objects.active = self.view_layer.objects[self.active]


# --------------------------------------------------------------------------- bake

def enabled_maps(settings):
    maps = []
    if settings.bake_normal:
        maps.append(('NORMAL', settings.suffix_normal or "normal"))
    if settings.bake_displacement:
        maps.append(('DISPLACEMENT', settings.suffix_displacement or "height"))
    if settings.bake_cavity:
        maps.append(('CAVITY', settings.suffix_cavity or "cavity"))
    return maps


def bake_pair(context, settings, pair, log=print):
    """Bake every enabled map for one pair. Returns list of written file paths."""
    err = validate_pair(pair)
    if err:
        raise BakeError(f"{pair.label()}: {err}")
    maps = enabled_maps(settings)
    if not maps:
        raise BakeError("No maps enabled")

    scene = context.scene
    view_layer = context.view_layer
    folder = output_folder(settings, pair)
    width, height = settings.resolution_xy()

    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    state = _SceneState(context)
    created_mats, created_imgs = [], []
    tmp_coll = bpy.data.collections.new(TMP_PREFIX + "tmp")
    scene.collection.children.link(tmp_coll)
    save_scene = bpy.data.scenes.new(TMP_PREFIX + "save")
    written = []

    try:
        scene.render.engine = 'CYCLES'
        scene.cycles.samples = settings.samples
        scene.cycles.use_denoising = False
        scene.cycles.device = 'GPU' if settings.use_gpu else 'CPU'

        depsgraph = context.evaluated_depsgraph_get()
        low_tmp = _temp_copy(pair.low, depsgraph, tmp_coll)
        highs_tmp = [_temp_copy(h, depsgraph, tmp_coll) for h in high_objects(pair)]
        # The high copies keep the user's materials (shared datablocks) until we swap them;
        # remember them for the optional "use high poly materials" normal bake.
        high_orig_mats = {h.name: list(h.data.materials) for h in highs_tmp}

        target_mat, target_node = _target_material()
        created_mats.append(target_mat)
        _set_single_material(low_tmp, target_mat)

        extrusion = pair.cage_extrusion if pair.override_rays else settings.cage_extrusion
        max_ray = pair.max_ray_distance if pair.override_rays else settings.max_ray_distance

        meta = {"pair": pair.label(), "low": pair.low.name,
                "high": [h.name for h in high_objects(pair)],
                "resolution": [width, height]}

        for map_type, suffix in maps:
            log(f"[Baker] {pair.label()}: baking {map_type.lower()} ({width}x{height})")

            if map_type == 'NORMAL':
                if settings.normal_use_high_materials:
                    for h in highs_tmp:
                        h.data.materials.clear()
                        for m in high_orig_mats[h.name]:
                            h.data.materials.append(m)
                else:
                    plain = _plain_material()
                    created_mats.append(plain)
                    for h in highs_tmp:
                        _set_single_material(h, plain)
                bake_type = 'NORMAL'
            elif map_type == 'DISPLACEMENT':
                rng = _compute_height(low_tmp, highs_tmp, settings.displacement_max,
                                      settings.displacement_raw)
                meta["height_range"] = rng
                meta["height_encoding"] = ("raw signed distance" if settings.displacement_raw
                                           else "0.5 = low poly surface, 0/1 = -/+ height_range")
                mat = _attribute_material(HEIGHT_ATTR)
                created_mats.append(mat)
                for h in highs_tmp:
                    _set_single_material(h, mat)
                bake_type = 'EMIT'
            else:  # CAVITY
                _compute_cavity(highs_tmp, settings.cavity_contrast, settings.cavity_mode,
                                settings.cavity_smoothing)
                mat = _attribute_material(CAVITY_ATTR)
                created_mats.append(mat)
                for h in highs_tmp:
                    _set_single_material(h, mat)
                bake_type = 'EMIT'

            img = bpy.data.images.new(f"{pair.label()}_{suffix}", width, height,
                                      alpha=False, float_buffer=True)
            img.colorspace_settings.name = 'Non-Color'
            created_imgs.append(img)
            target_node.image = img

            for o in view_layer.objects:
                o.select_set(False)
            for h in highs_tmp:
                h.select_set(True)
            low_tmp.select_set(True)
            view_layer.objects.active = low_tmp

            kwargs = dict(
                type=bake_type,
                use_selected_to_active=True,
                cage_extrusion=extrusion,
                max_ray_distance=max_ray,
                margin=settings.margin,
                target='IMAGE_TEXTURES',
                use_clear=True,
            )
            if pair.cage:
                kwargs.update(use_cage=True, cage_object=pair.cage.name)
            if bake_type == 'NORMAL':
                kwargs.update(normal_space='TANGENT', normal_r='POS_X', normal_b='POS_Z',
                              normal_g='NEG_Y' if settings.normal_convention == 'DIRECTX' else 'POS_Y')

            result = bpy.ops.object.bake(**kwargs)
            if 'FINISHED' not in result:
                raise BakeError(f"{pair.label()}: {map_type.lower()} bake failed")

            path = output_path(settings, pair, folder, suffix)
            _configure_save_scene(save_scene, settings, grey=(map_type != 'NORMAL'))
            img.save_render(filepath=path, scene=save_scene)
            written.append(path)
            log(f"[Baker]   -> {path}")

        if settings.bake_displacement:
            meta_path = os.path.join(folder, bpy.path.clean_name(f"{pair.label()}_bake") + ".json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

    finally:
        for o in list(tmp_coll.objects):
            me = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me and me.users == 0:
                bpy.data.meshes.remove(me)
        bpy.data.collections.remove(tmp_coll)
        bpy.data.scenes.remove(save_scene)
        for m in created_mats:
            bpy.data.materials.remove(m)
        for img in created_imgs:
            bpy.data.images.remove(img)
        if settings.keep_images:
            for path in written:
                img = bpy.data.images.load(path, check_existing=True)
                img.reload()
                img.colorspace_settings.name = 'Non-Color'
        state.restore()

    return written
