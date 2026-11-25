bl_info = {
    "name": "Baker",
    "author": "Michael Kirylo",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport > Sidebar > Baker",
    "description": "Batch bake normal, displacement and cavity maps from high to low poly meshes",
    "category": "Render",
}

from . import props, operators, ui  # noqa: E402

_modules = (props, operators, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        m.unregister()
