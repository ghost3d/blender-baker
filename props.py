import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


def _poll_mesh(self, obj):
    return obj.type == 'MESH'


RESOLUTIONS = [
    ('512', "512", ""),
    ('1024', "1024", ""),
    ('2048', "2048", ""),
    ('4096', "4096", ""),
    ('8192', "8192", ""),
    ('CUSTOM', "Custom", "Set width and height yourself"),
]

FILE_FORMATS = [
    ('PNG', "PNG", ".png, 8 or 16 bit"),
    ('TIFF', "TIFF", ".tif, 8 or 16 bit"),
    ('OPEN_EXR', "OpenEXR", ".exr, 16 or 32 bit float"),
    ('TARGA', "Targa", ".tga, 8 bit"),
    ('JPEG', "JPEG", ".jpg, 8 bit (lossy, not recommended for height)"),
]

BIT_DEPTHS = [
    ('8', "8 bit", ""),
    ('16', "16 bit", ""),
    ('32', "32 bit", "Float, OpenEXR only"),
]


class BAKER_PG_pair(bpy.types.PropertyGroup):
    name: StringProperty(
        name="Name",
        description="Used in output file names. Leave blank to use the low poly object's name",
    )
    enabled: BoolProperty(name="Enabled", default=True, description="Include this pair in Bake All")
    low: PointerProperty(name="Low Poly", type=bpy.types.Object, poll=_poll_mesh,
                         description="Mesh that receives the maps (needs UVs)")
    high_mode: EnumProperty(
        name="High Poly Source",
        items=[
            ('OBJECT', "Object", "A single high poly mesh"),
            ('COLLECTION', "Collection", "Every mesh in a collection (for high poly split into parts)"),
        ],
        default='OBJECT',
    )
    high: PointerProperty(name="High Poly", type=bpy.types.Object, poll=_poll_mesh)
    high_collection: PointerProperty(name="High Poly Collection", type=bpy.types.Collection)
    cage: PointerProperty(name="Cage", type=bpy.types.Object, poll=_poll_mesh,
                          description="Optional cage mesh. Must have the same topology as the low poly")
    override_rays: BoolProperty(name="Override Ray Settings", default=False,
                                description="Use ray distances for this pair instead of the global ones")
    cage_extrusion: FloatProperty(name="Extrusion", default=0.02, min=0.0, soft_max=1.0,
                                  subtype='DISTANCE', unit='LENGTH')
    max_ray_distance: FloatProperty(name="Max Ray Distance", default=0.0, min=0.0, soft_max=1.0,
                                    subtype='DISTANCE', unit='LENGTH')

    def label(self):
        if self.name.strip():
            return self.name.strip()
        return self.low.name if self.low else "Unassigned"


class BAKER_PG_settings(bpy.types.PropertyGroup):
    pairs: CollectionProperty(type=BAKER_PG_pair)
    active_index: IntProperty(default=0)

    # Maps
    bake_normal: BoolProperty(name="Normal", default=True)
    bake_displacement: BoolProperty(name="Displacement", default=True)
    bake_cavity: BoolProperty(name="Cavity", default=True)

    normal_convention: EnumProperty(
        name="Normal Convention",
        items=[
            ('OPENGL', "OpenGL (Y+)", "Blender, Maya, Unity, Substance default"),
            ('DIRECTX', "DirectX (Y-)", "Unreal, 3ds Max"),
        ],
        default='OPENGL',
    )
    normal_use_high_materials: BoolProperty(
        name="Use High Poly Materials",
        default=False,
        description="Include bump/normal nodes from the high poly materials in the normal bake. "
                    "Off bakes pure geometry",
    )
    displacement_max: FloatProperty(
        name="Height Range",
        default=0.0, min=0.0, soft_max=1.0, subtype='DISTANCE', unit='LENGTH',
        description="Distance mapped to black/white. 0 = automatic (largest difference found). "
                    "The used range is written to a .json next to the maps",
    )
    displacement_raw: BoolProperty(
        name="Raw Distance (EXR)",
        default=False,
        description="Write signed distance in scene units instead of 0-1 grey (0.5 = surface). "
                    "Only useful with OpenEXR",
    )
    cavity_contrast: FloatProperty(name="Contrast", default=1.0, min=0.05, soft_max=5.0,
                                   description="Strength of the cavity effect. Curvature is "
                                               "auto-normalised, so 1.0 works for most meshes")
    cavity_smoothing: IntProperty(name="Smoothing", default=2, min=0, max=50,
                                  description="Blur the curvature across neighbouring vertices "
                                              "to reduce noise on very dense meshes")
    cavity_mode: EnumProperty(
        name="Cavity Style",
        items=[
            ('CAVITY', "Cavities Only", "White surface, dark crevices"),
            ('BOTH', "Cavities + Edges", "Mid grey surface, dark crevices, bright edges"),
        ],
        default='CAVITY',
    )

    # Output
    output_dir: StringProperty(name="Output Folder", subtype='DIR_PATH', default="//baked/",
                               description="Folder the maps are saved to (// = next to the .blend)")
    subfolder_per_pair: BoolProperty(name="Subfolder per Pair", default=False)
    filename_template: StringProperty(
        name="File Name", default="{name}_{map}",
        description="{name} = pair name, {map} = map suffix",
    )
    suffix_normal: StringProperty(name="Normal Suffix", default="normal")
    suffix_displacement: StringProperty(name="Displacement Suffix", default="height")
    suffix_cavity: StringProperty(name="Cavity Suffix", default="cavity")

    resolution: EnumProperty(name="Resolution", items=RESOLUTIONS, default='2048')
    width: IntProperty(name="Width", default=2048, min=16, max=16384)
    height: IntProperty(name="Height", default=2048, min=16, max=16384)
    file_format: EnumProperty(name="File Type", items=FILE_FORMATS, default='PNG')
    bit_depth: EnumProperty(name="Bit Depth", items=BIT_DEPTHS, default='16')
    keep_images: BoolProperty(name="Keep Images in .blend", default=False,
                              description="Leave the baked images in the file so you can view them "
                                          "in the Image Editor")

    # Bake / rays
    cage_extrusion: FloatProperty(
        name="Extrusion", default=0.02, min=0.0, soft_max=1.0, subtype='DISTANCE', unit='LENGTH',
        description="How far rays start outside the low poly. Increase if details are missing",
    )
    max_ray_distance: FloatProperty(
        name="Max Ray Distance", default=0.0, min=0.0, soft_max=1.0, subtype='DISTANCE', unit='LENGTH',
        description="Maximum ray travel distance (0 = unlimited)",
    )
    margin: IntProperty(name="Margin", default=16, min=0, max=64, subtype='PIXEL',
                        description="Pixels to extend the bake past UV island borders")
    samples: IntProperty(name="Samples", default=8, min=1, max=1024,
                         description="Anti-aliasing samples per pixel")
    use_gpu: BoolProperty(name="Use GPU", default=False,
                          description="Bake with the GPU set in Preferences > System")

    # Batch import / auto pairing
    low_suffix: StringProperty(name="Low Suffix", default="_low")
    high_suffix: StringProperty(name="High Suffix", default="_high")

    def resolution_xy(self):
        if self.resolution == 'CUSTOM':
            return self.width, self.height
        r = int(self.resolution)
        return r, r


classes = (BAKER_PG_pair, BAKER_PG_settings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.baker = PointerProperty(type=BAKER_PG_settings)


def unregister():
    del bpy.types.Scene.baker
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
