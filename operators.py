import os
import time

import bpy
from bpy.props import EnumProperty, StringProperty

from . import bake


def _settings(context):
    return context.scene.baker


def _active_pair(context):
    s = _settings(context)
    if 0 <= s.active_index < len(s.pairs):
        return s.pairs[s.active_index]
    return None


def _strip_suffix(name, suffix):
    if suffix and name.lower().endswith(suffix.lower()):
        return name[: -len(suffix)]
    return name


# --------------------------------------------------------------------------- pair list

class BAKER_OT_pair_add(bpy.types.Operator):
    bl_idname = "baker.pair_add"
    bl_label = "Add Pair"
    bl_description = "Add an empty bake pair"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        s.pairs.add()
        s.active_index = len(s.pairs) - 1
        return {'FINISHED'}


class BAKER_OT_pair_remove(bpy.types.Operator):
    bl_idname = "baker.pair_remove"
    bl_label = "Remove Pair"
    bl_description = "Remove the selected bake pair (objects are not deleted)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_pair(context) is not None

    def execute(self, context):
        s = _settings(context)
        s.pairs.remove(s.active_index)
        s.active_index = min(s.active_index, len(s.pairs) - 1)
        return {'FINISHED'}


class BAKER_OT_pair_clear(bpy.types.Operator):
    bl_idname = "baker.pair_clear"
    bl_label = "Clear All Pairs"
    bl_description = "Remove every bake pair from the list (objects are not deleted)"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        s = _settings(context)
        s.pairs.clear()
        s.active_index = 0
        return {'FINISHED'}


class BAKER_OT_pair_move(bpy.types.Operator):
    bl_idname = "baker.pair_move"
    bl_label = "Move Pair"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context):
        s = _settings(context)
        i = s.active_index
        j = i - 1 if self.direction == 'UP' else i + 1
        if 0 <= j < len(s.pairs):
            s.pairs.move(i, j)
            s.active_index = j
        return {'FINISHED'}


class BAKER_OT_pair_from_selection(bpy.types.Operator):
    bl_idname = "baker.pair_from_selection"
    bl_label = "Pair from Selection"
    bl_description = ("Create a pair from the selected meshes: the ACTIVE object is the low poly, "
                      "the other selected meshes are the high poly")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        a = context.active_object
        return a is not None and a.type == 'MESH' and len(context.selected_objects) >= 2

    def execute(self, context):
        low = context.active_object
        highs = [o for o in context.selected_objects if o.type == 'MESH' and o != low]
        if not highs:
            self.report({'ERROR'}, "Select at least one high poly mesh plus the low poly (active)")
            return {'CANCELLED'}
        s = _settings(context)
        pair = s.pairs.add()
        pair.low = low
        pair.name = _strip_suffix(low.name, s.low_suffix)
        if len(highs) == 1:
            pair.high_mode = 'OBJECT'
            pair.high = highs[0]
        else:
            coll = bpy.data.collections.new(f"{pair.name}_high")
            context.scene.collection.children.link(coll)
            for h in highs:
                coll.objects.link(h)
            pair.high_mode = 'COLLECTION'
            pair.high_collection = coll
        s.active_index = len(s.pairs) - 1
        return {'FINISHED'}


class BAKER_OT_auto_pair(bpy.types.Operator):
    bl_idname = "baker.auto_pair"
    bl_label = "Auto Pair by Name"
    bl_description = ("Pair every mesh in the scene named <name><low suffix> with "
                      "<name><high suffix>, e.g. rock_low + rock_high")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = _settings(context)
        lo, hi = s.low_suffix.lower(), s.high_suffix.lower()
        meshes = [o for o in context.scene.objects if o.type == 'MESH']
        highs = {}
        for o in meshes:
            if hi and o.name.lower().endswith(hi):
                highs[o.name[: -len(hi)].lower()] = o
        existing = {p.low for p in s.pairs if p.low}
        added = 0
        for o in meshes:
            if not (lo and o.name.lower().endswith(lo)) or o in existing:
                continue
            base = o.name[: -len(lo)]
            h = highs.get(base.lower())
            if not h:
                continue
            pair = s.pairs.add()
            pair.name, pair.low, pair.high_mode, pair.high = base, o, 'OBJECT', h
            added += 1
        if added:
            s.active_index = len(s.pairs) - 1
        self.report({'INFO'}, f"Added {added} pair(s)")
        return {'FINISHED'}


# --------------------------------------------------------------------------- OBJ import

def _import_obj(context, filepath):
    """Import an OBJ and return the new mesh objects."""
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "obj_import"):
        bpy.ops.wm.obj_import(filepath=filepath)
    else:  # pre 4.0 fallback
        bpy.ops.import_scene.obj(filepath=filepath)
    return [o for o in bpy.data.objects if o not in before and o.type == 'MESH']


def _join(context, objs, name):
    if len(objs) == 1:
        objs[0].name = name
        return objs[0]
    for o in context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    obj = context.view_layer.objects.active
    obj.name = name
    return obj


def _make_pair_from_files(context, low_path, high_path, name):
    s = _settings(context)
    if context.object and context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    lows = _import_obj(context, low_path)
    if not lows:
        raise bake.BakeError(f"No mesh found in {low_path}")
    # The low poly must be one object so all its UVs bake into a single map.
    low = _join(context, lows, f"{name}{s.low_suffix}")

    highs = _import_obj(context, high_path)
    if not highs:
        raise bake.BakeError(f"No mesh found in {high_path}")

    pair = s.pairs.add()
    pair.name = name
    pair.low = low
    if len(highs) == 1:
        highs[0].name = f"{name}{s.high_suffix}"
        pair.high_mode, pair.high = 'OBJECT', highs[0]
    else:
        coll = bpy.data.collections.new(f"{name}{s.high_suffix}")
        context.scene.collection.children.link(coll)
        for h in highs:
            for c in list(h.users_collection):
                c.objects.unlink(h)
            coll.objects.link(h)
        pair.high_mode, pair.high_collection = 'COLLECTION', coll
    s.active_index = len(s.pairs) - 1
    return pair


class BAKER_OT_import_obj_pair(bpy.types.Operator):
    bl_idname = "baker.import_obj_pair"
    bl_label = "Import OBJ Pair"
    bl_description = "Pick a low poly and a high poly .obj file, import both and add them as a pair"
    bl_options = {'REGISTER', 'UNDO'}

    low_path: StringProperty(name="Low Poly OBJ", subtype='FILE_PATH')
    high_path: StringProperty(name="High Poly OBJ", subtype='FILE_PATH')
    name: StringProperty(name="Pair Name", description="Blank = low poly file name")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "low_path")
        col.prop(self, "high_path")
        col.prop(self, "name")

    def execute(self, context):
        low = bpy.path.abspath(self.low_path)
        high = bpy.path.abspath(self.high_path)
        for p in (low, high):
            if not os.path.isfile(p):
                self.report({'ERROR'}, f"File not found: {p or '(empty)'}")
                return {'CANCELLED'}
        s = _settings(context)
        name = self.name.strip() or _strip_suffix(os.path.splitext(os.path.basename(low))[0],
                                                  s.low_suffix)
        try:
            _make_pair_from_files(context, low, high, name)
        except bake.BakeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Imported pair '{name}'")
        return {'FINISHED'}


class BAKER_OT_import_obj_folder(bpy.types.Operator):
    bl_idname = "baker.import_obj_folder"
    bl_label = "Import OBJ Folder"
    bl_description = ("Import every matching pair of .obj files from a folder "
                      "(e.g. rock_low.obj + rock_high.obj) and add them as pairs")
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(subtype='DIR_PATH')
    filter_glob: StringProperty(default="*.obj", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        s = _settings(context)
        folder = self.directory
        if not os.path.isdir(folder):
            self.report({'ERROR'}, "Pick a folder")
            return {'CANCELLED'}
        lo, hi = s.low_suffix.lower(), s.high_suffix.lower()
        files = {os.path.splitext(f)[0].lower(): f for f in os.listdir(folder)
                 if f.lower().endswith(".obj")}
        added, missing = 0, []
        for stem, fname in sorted(files.items()):
            if not stem.endswith(lo):
                continue
            base = stem[: -len(lo)]
            high_name = files.get(base + hi)
            display = os.path.splitext(fname)[0][: -len(lo)]
            if not high_name:
                missing.append(display)
                continue
            try:
                _make_pair_from_files(context, os.path.join(folder, fname),
                                      os.path.join(folder, high_name), display)
                added += 1
            except bake.BakeError as e:
                self.report({'WARNING'}, str(e))
        msg = f"Imported {added} pair(s)"
        if missing:
            msg += f"; no high poly for: {', '.join(missing)}"
        self.report({'INFO'} if added else {'WARNING'}, msg)
        return {'FINISHED'}


# --------------------------------------------------------------------------- baking

class BAKER_OT_bake(bpy.types.Operator):
    bl_idname = "baker.bake"
    bl_label = "Bake"
    bl_description = "Bake the enabled maps and save them to the output folder"

    scope: EnumProperty(items=[
        ('ALL', "All Enabled", "Bake every enabled pair"),
        ('ACTIVE', "Selected Pair", "Bake only the pair selected in the list"),
    ], default='ALL')

    @classmethod
    def poll(cls, context):
        return len(context.scene.baker.pairs) > 0

    def execute(self, context):
        s = _settings(context)
        if self.scope == 'ACTIVE':
            p = _active_pair(context)
            pairs = [p] if p else []
        else:
            pairs = [p for p in s.pairs if p.enabled]
        if not pairs:
            self.report({'ERROR'}, "Nothing to bake")
            return {'CANCELLED'}
        if not bake.enabled_maps(s):
            self.report({'ERROR'}, "Enable at least one map")
            return {'CANCELLED'}

        problems = [(p.label(), bake.validate_pair(p)) for p in pairs]
        problems = [f"{n}: {e}" for n, e in problems if e]
        if problems:
            for msg in problems:
                self.report({'ERROR'}, msg)
            return {'CANCELLED'}

        wm = context.window_manager
        wm.progress_begin(0, len(pairs))
        start = time.time()
        files, failed = [], []
        try:
            for i, pair in enumerate(pairs):
                wm.progress_update(i)
                try:
                    files += bake.bake_pair(context, s, pair)
                except (bake.BakeError, RuntimeError) as e:
                    failed.append(pair.label())
                    self.report({'ERROR'}, f"{pair.label()}: {e}")
        finally:
            wm.progress_end()

        secs = time.time() - start
        if failed:
            self.report({'WARNING'}, f"Wrote {len(files)} map(s), {len(failed)} pair(s) failed "
                                     f"({secs:.1f}s)")
        else:
            self.report({'INFO'}, f"Wrote {len(files)} map(s) for {len(pairs)} pair(s) "
                                  f"in {secs:.1f}s")
        return {'FINISHED'} if files else {'CANCELLED'}


class BAKER_OT_open_output(bpy.types.Operator):
    bl_idname = "baker.open_output"
    bl_label = "Open Output Folder"
    bl_description = "Open the output folder in your file browser"

    def execute(self, context):
        raw = _settings(context).output_dir or "//baked/"
        path = bpy.path.abspath(raw)
        if not os.path.isdir(path):
            self.report({'WARNING'}, f"Folder does not exist yet: {path}")
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


classes = (
    BAKER_OT_pair_add,
    BAKER_OT_pair_remove,
    BAKER_OT_pair_clear,
    BAKER_OT_pair_move,
    BAKER_OT_pair_from_selection,
    BAKER_OT_auto_pair,
    BAKER_OT_import_obj_pair,
    BAKER_OT_import_obj_folder,
    BAKER_OT_bake,
    BAKER_OT_open_output,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
