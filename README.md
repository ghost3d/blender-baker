# Baker

Blender add-on that bakes detail from high poly meshes onto low poly meshes and writes
**Normal**, **Displacement (height)** and **Cavity** maps to disk, for one pair or a whole batch at a time.
It uses Cycles under the hood and handles all the setup (image nodes, selected-to-active,
cage, render engine) for you.

Requires Blender 4.2+ (tested on 5.2).

## Install

Zip the `baker` folder, then in Blender go to **Edit > Preferences > Get Extensions > ⌄ > Install from Disk**
and pick the zip. The panel lives in **3D Viewport > Sidebar (N) > Baker**.

```powershell
Compress-Archive -Path baker -DestinationPath baker.zip -Force
```

## Workflow

1. **Add pairs** (one low poly + its high poly) in any of these ways:
   - **Import OBJ Pair**: pick a low `.obj` and a high `.obj` in a dialog.
   - **Import Folder**: imports every `name_low.obj` / `name_high.obj` pair in a folder.
   - **Pair from Selection**: select the high poly mesh(es), then the low poly last so it's active.
   - **Auto Pair by Name**: pairs scene meshes named `name_low` / `name_high`.
   - **+**: add an empty pair and pick the objects by hand.
2. The high poly can be a single **Object** or a **Collection** (for a high poly split into parts).
   An optional **Cage** mesh is supported.
3. Choose the maps, then set **resolution**, **file type**, **bit depth** and **output folder**.
4. Click **Bake All Enabled** (every ticked pair) or **Bake Selected Pair**.

Files are named `{name}_{map}` by default, e.g. `rock_normal.png`, `rock_height.png`, `rock_cavity.png`.

## Maps

| Map | How it's made | Notes |
|---|---|---|
| Normal | Cycles tangent-space normal bake | OpenGL (Y+) or DirectX (Y−). Off by default: *Use High Poly Materials* (bakes bump nodes too). |
| Displacement | Signed distance from the low poly surface to the high poly, baked through an emission shader | 0.5 grey = low poly surface. The range used is saved to `{name}_bake.json`. Use 16-bit PNG/TIFF or EXR; 8-bit will band. *Raw Distance* + EXR writes real scene-unit values. |
| Cavity | Mean curvature of the high poly, normalised automatically | Gives the same look at any polycount. *Cavities Only* = white surface with dark crevices; *Cavities + Edges* = grey base with dark crevices and bright edges. |

## Tips

- **Missing details or black spots**: raise **Extrusion** under Bake Settings (or per pair with *Override Ray Settings*),
  or use a cage.
- **Detail from the wrong part of the mesh**: lower **Extrusion** or set **Max Ray Distance**.
- Modifiers on either mesh are applied for the bake. Your objects and materials are never changed:
  everything is baked from temporary copies, which are deleted afterwards.
- Turn on **Use GPU** if you have a GPU set up in Preferences > System.
