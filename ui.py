import bpy

from . import bake


class BAKER_UL_pairs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        err = bake.validate_pair(item)
        row.label(text=item.label(), icon='ERROR' if err else 'MESH_DATA')
        if item.high_mode == 'COLLECTION' and item.high_collection:
            n = len(bake.high_objects(item))
            row.label(text=f"{n} high", icon='OUTLINER_COLLECTION')


class _Panel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Baker"


class BAKER_PT_main(_Panel, bpy.types.Panel):
    bl_label = "Baker"
    bl_idname = "BAKER_PT_main"

    def draw(self, context):
        s = context.scene.baker
        layout = self.layout

        row = layout.row()
        row.template_list("BAKER_UL_pairs", "", s, "pairs", s, "active_index", rows=4)
        col = row.column(align=True)
        col.operator("baker.pair_add", text="", icon='ADD')
        col.operator("baker.pair_remove", text="", icon='REMOVE')
        col.separator()
        col.operator("baker.pair_move", text="", icon='TRIA_UP').direction = 'UP'
        col.operator("baker.pair_move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        col.separator()
        col.operator("baker.pair_clear", text="", icon='X')

        col = layout.column(align=True)
        col.operator("baker.pair_from_selection", icon='RESTRICT_SELECT_OFF')
        row = col.row(align=True)
        row.operator("baker.import_obj_pair", icon='IMPORT')
        row.operator("baker.import_obj_folder", text="Import Folder", icon='FILE_FOLDER')
        col.operator("baker.auto_pair", icon='LINKED')

        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.6
        n = sum(1 for p in s.pairs if p.enabled)
        col.operator("baker.bake", text=f"Bake All Enabled ({n})", icon='RENDER_STILL').scope = 'ALL'
        sub = col.row(align=True)
        sub.scale_y = 0.7
        sub.operator("baker.bake", text="Bake Selected Pair").scope = 'ACTIVE'
        sub.operator("baker.open_output", text="", icon='FOLDER_REDIRECT')


class BAKER_PT_pair(_Panel, bpy.types.Panel):
    bl_label = "Selected Pair"
    bl_parent_id = "BAKER_PT_main"

    @classmethod
    def poll(cls, context):
        s = context.scene.baker
        return 0 <= s.active_index < len(s.pairs)

    def draw(self, context):
        s = context.scene.baker
        pair = s.pairs[s.active_index]
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(pair, "name", placeholder=pair.low.name if pair.low else "")
        layout.prop(pair, "low", icon='MESH_ICOSPHERE')
        layout.prop(pair, "high_mode", expand=True)
        if pair.high_mode == 'OBJECT':
            layout.prop(pair, "high", icon='MESH_MONKEY')
        else:
            layout.prop(pair, "high_collection")
        layout.prop(pair, "cage")

        layout.prop(pair, "override_rays")
        if pair.override_rays:
            col = layout.column(align=True)
            col.prop(pair, "cage_extrusion")
            col.prop(pair, "max_ray_distance")

        err = bake.validate_pair(pair)
        if err:
            box = layout.box()
            box.label(text=err.capitalize(), icon='ERROR')


class BAKER_PT_maps(_Panel, bpy.types.Panel):
    bl_label = "Maps"
    bl_parent_id = "BAKER_PT_main"

    def draw(self, context):
        s = context.scene.baker
        layout = self.layout

        box = layout.box()
        box.prop(s, "bake_normal")
        if s.bake_normal:
            col = box.column()
            col.use_property_split = True
            col.prop(s, "normal_convention", text="Convention")
            col.prop(s, "normal_use_high_materials")
            col.prop(s, "suffix_normal", text="Suffix")

        box = layout.box()
        box.prop(s, "bake_displacement")
        if s.bake_displacement:
            col = box.column()
            col.use_property_split = True
            col.prop(s, "displacement_max")
            col.prop(s, "displacement_raw")
            col.prop(s, "suffix_displacement", text="Suffix")

        box = layout.box()
        box.prop(s, "bake_cavity")
        if s.bake_cavity:
            col = box.column()
            col.use_property_split = True
            col.prop(s, "cavity_mode", text="Style")
            col.prop(s, "cavity_contrast")
            col.prop(s, "cavity_smoothing")
            col.prop(s, "suffix_cavity", text="Suffix")


class BAKER_PT_output(_Panel, bpy.types.Panel):
    bl_label = "Output"
    bl_parent_id = "BAKER_PT_main"

    def draw(self, context):
        s = context.scene.baker
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(s, "output_dir", text="Folder")
        layout.prop(s, "subfolder_per_pair")
        layout.prop(s, "filename_template")
        layout.separator()
        layout.prop(s, "resolution")
        if s.resolution == 'CUSTOM':
            col = layout.column(align=True)
            col.prop(s, "width")
            col.prop(s, "height")
        layout.prop(s, "file_format")
        row = layout.row()
        row.enabled = s.file_format in {'PNG', 'TIFF', 'OPEN_EXR'}
        row.prop(s, "bit_depth")
        layout.prop(s, "keep_images")


class BAKER_PT_settings(_Panel, bpy.types.Panel):
    bl_label = "Bake Settings"
    bl_parent_id = "BAKER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.baker
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(s, "cage_extrusion")
        col.prop(s, "max_ray_distance")
        layout.prop(s, "margin")
        layout.prop(s, "samples")
        layout.prop(s, "use_gpu")
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Name Matching (Auto Pair / Import Folder)")
        col.prop(s, "low_suffix")
        col.prop(s, "high_suffix")


classes = (
    BAKER_UL_pairs,
    BAKER_PT_main,
    BAKER_PT_pair,
    BAKER_PT_maps,
    BAKER_PT_output,
    BAKER_PT_settings,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
