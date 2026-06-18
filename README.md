# Maya MMD Tools

[日本語ドキュメント](docs/README_ja.md)

Maya MMD Tools is a tool for importing MikuMikuDance (MMD) PMD/PMX models and VMD motions into Autodesk Maya.

This is an alpha early release. Some features may be undeveloped or unstable.

## Feature Support Matrix

Legend: ✅ Supported · 🔶 Partial / with caveats · 🧪 Experimental (opt-in) · ⛔ Not supported yet

> This is an alpha release. See [Known Limitations](#known-limitations) below for details.

### Import — File Formats

| Format | Status | Notes |
|---|---|---|
| PMX (`.pmx`) 2.0 / 2.1 | ✅ | Recommended format |
| PMD (`.pmd`) | ✅ | Legacy format |
| VMD (`.vmd`) motion | ✅ | See [Animation](#animation-vmd) below |
| VPD (`.vpd`) pose | ⛔ | Parser exists, but the import UI is disabled and not yet wired up |

### Import — Model

| Feature | Status | Notes |
|---|---|---|
| Mesh / vertices / normals | ✅ | |
| Materials & textures | ✅ | Auto-applied when found; texture search path supported |
| Primary UV | ✅ | |
| Additional UV (UV1–4) | ⛔ | Read but not applied |
| Edge / outline flags | ✅ | |
| Bones & skeleton | ✅ | |
| IK | ✅ | Resolved on import / during motion bake |
| Append (grant / 付与) bones | ✅ | |
| Bone local axis | 🔶 | Approximate; fidelity not fully verified |
| Display frames (表示枠) | ⛔ | Read but not reflected in Maya yet |
| Name translation (JP → EN) | ✅ | Toggle |

### Import — Morphs

| Morph type | Status | Notes |
|---|---|---|
| Vertex | ✅ | blendShape targets |
| Bone | 🔶 | Driven by motion bake; limited interactive control |
| Material | 🔶 | Driven by motion bake; limited interactive control |
| Group | ⛔ | |
| UV (incl. additional UV) | ⛔ | |
| Flip | ⛔ | |
| Impulse | ⛔ | |

### Import — Physics

| Feature | Status | Notes |
|---|---|---|
| Rigid bodies & joints | 🧪 | Opt-in (`import.physics.import_physics`) |
| Soft body (PMX 2.1) | ⛔ | Read but silently ignored |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | ✅ | High-precision bake via the MMD runtime (Bézier interpolation, IK, and grant resolved); falls back to linear interpolation when the runtime is unavailable |
| Morph animation | ✅ | |
| Camera animation | ✅ | |
| Light animation | ✅ | Drives the `mmd_light` controller |
| IK on/off frames | 🔶 | Simplified handling |

### Export

| Format | Status | Notes |
|---|---|---|
| PMX / PMD / VMD | ⛔ | Import-only for now; the UI shows an explicit "not implemented" message |

### Viewport & Shading

| Feature | Status | Notes |
|---|---|---|
| DX11 MMD toon shader (Windows) | ✅ | Toon shading, outline, and transparency; automatic color-management setup on import |
| MMD light controller | ✅ | Single directional-light null |
| Transparency (opaque / cutout / blend) | ✅ | Manual, plus opt-in auto-classification |
| GLSL shader (macOS) | 🧪 | Not fully verified on macOS |

### UI

| Feature | Status |
|---|---|
| Info / Material / Morph / Bone tabs | ✅ |
| Japanese / English UI | ✅ |
| Namespace (multiple models) | ✅ |

## Known Limitations

- **Export is not available.** This is an import-only tool for now — PMX/PMD/VMD export is not implemented (the UI states this explicitly).
- **VPD pose import is not yet available.** The parser exists, but the UI is disabled until it is wired up.
- **Additional UV / multi-UV is not applied** (read but ignored).
- **Group, UV, Flip, and Impulse morphs are not supported.** Vertex morphs are fully supported; bone and material morphs are applied through motion bake.
- **Soft body (PMX 2.1) data is silently ignored.** The rest of the file still imports correctly.
- **Display frames (表示枠) are read but not reflected in Maya.**
- **Physics is experimental** and off by default.
- **Bone local-axis fidelity is approximate** and not fully verified.
- Large models may have performance issues, and some PMX files may fail to import.
- The opt-in C++ fast-import path supports mesh, basic materials, basic skeleton/skin, and vertex-morph blendShape targets only (UV / material / bone / group morphs are not handled on that path).

## System Requirements

### Required

- **Maya**: 2024 or later
- **OS**: Windows 11 / macOS 15.6
- **Python**: 3.7 or later, bundled with Maya

## Installation

### Download

1. Download the latest release from the [GitHub Releases page](https://github.com/yohawing/maya_mmd_tools/releases).
2. Extract the ZIP file anywhere you like.

### Place the `.mod` File

You can place the Maya MMD Tools folder anywhere. Only `maya_mmd_tools.mod` needs to be placed in Maya's `modules` folder.

On Windows, open:

```text
C:\Users\<User Name>\Documents\maya\modules
```

On macOS, open:

```text
~/Documents/maya/modules
```

Create the folder if it does not exist.

Next, open `maya_mmd_tools.mod` from the extracted folder in a text editor, and change the end of the first line to the folder path of Maya MMD Tools. Replace the `2026` part with the Maya version you use.

```text
+ MAYAVERSION:2026 maya_mmd_tools 0.1.0 <extracted folder path>
scripts: .
plug-ins: plug-ins
icons: resources/icons
MMD_TOOLS_ROOT:= .
PYTHONPATH +:= .
```

Example:

```text
+ MAYAVERSION:2026 maya_mmd_tools 0.1.0 C:/Tools/maya_mmd_tools
scripts: .
plug-ins: plug-ins
icons: resources/icons
MMD_TOOLS_ROOT:= .
PYTHONPATH +:= .
```

If the path contains spaces, wrap it in quotes.

```text
+ MAYAVERSION:2026 maya_mmd_tools 0.1.0 "C:/Program Files/maya_mmd_tools"
scripts: .
plug-ins: plug-ins
icons: resources/icons
MMD_TOOLS_ROOT:= .
PYTHONPATH +:= .
```

Copy the edited `maya_mmd_tools.mod` into Maya's `modules` folder.

You do not need to copy `userSetup.py` separately into Maya's scripts folder. The `.mod` file's `scripts: .` setting points Maya to the `userSetup.py` inside the Maya MMD Tools folder.

### Enable the Plugin

1. Start Maya. If Maya is already running, restart it.
2. Open `Window > Settings/Preferences > Plug-in Manager`.
3. Find `mmd_tools_plugin.py`.
4. Check `Loaded`.
5. If you want it to load automatically, also check `Auto load`.

## Verify Installation

### Check the Menu

Confirm that `MMD > MMD Tools` appears in Maya's menu bar.

### Check from Script Editor

Run this in Maya Script Editor:

```python
import mmd_tools
print(mmd_tools.__version__)
```

Expected output:

```text
0.1.0
```

## Quick Start

### Import a Model

1. Select `MMD > MMD Tools`.
2. In the Import/Export tab, choose a PMX or PMD file.
3. Click `Import Model`.

To import from a script:

```python
from mmd_tools.io.mmd_importer import import_mmd_file

import_mmd_file("path/to/your/model.pmx")
```

After a successful import, a `model_root` group is created in the Outliner, and the model appears in the viewport. Materials and textures are applied automatically when possible.

### Open the MMD Tools UI

1. Select `MMD > MMD Tools`.
2. The MMD Tools UI opens.
3. You can inspect and adjust settings in each tab.

Main tabs:

- **Info**: Model information
- **Material**: Material settings
- **Morph**: Facial expression/morph controls
- **Bone**: Bone information

### Import Animation

If you have a VMD file:

1. Select `MMD > MMD Tools`.
2. In the Import/Export tab, choose a VMD file.
3. (Optional) In animation import settings, set VMD FPS (choices: 30 or 60; default 30). This changes the Maya scene time unit before import.
4. Click `Import Animation`.
5. The animation is applied to the matching model in the scene.

Note: VMD stores integer frame numbers but does not store FPS metadata. Frame numbers are used as-is (not rescaled); only the scene time unit is set according to the VMD FPS choice.

## Model Import

### Supported Formats

- **PMX** (`.pmx`) - Recommended format
  - PMX 2.0
  - PMX 2.1
- **PMD** (`.pmd`) - Legacy format

### Basic Import

```python
from mmd_tools.io.mmd_importer import import_mmd_file

import_mmd_file("C:/Models/character.pmx")
```

To enable namespace support:

```python
from mmd_tools.io.mmd_importer import import_mmd_file

options = {"use_namespace": True}
import_mmd_file("C:/Models/character.pmx", options=options)
```

### Import Settings

```python
from mmd_tools.core import settings

# Scale factor. MMD models usually use centimeters.
settings.set("import.general.scale_factor", 1.0)

# Namespace support for multiple models.
settings.set("import.general.use_namespace", True)

# Material creation.
settings.set("import.model.create_mmd_shaders", True)

# Split meshes per material. Off by default.
# Enable only if you want to edit or toggle visibility per material.
settings.set("import.model.separate_meshes_by_material", False)

# Physics import. Experimental in this early release.
settings.set("import.physics.import_physics", False)
```

`import.model.separate_meshes_by_material` is an opt-in setting that splits the
mesh per PMX/PMD material. With the default `False`, materials are assigned per
face on a single mesh, which is recommended for normal model imports. Setting it
to `True` makes per-material editing and visibility toggling easier, but it
creates more mesh / skinCluster / blendShape nodes, increasing import time and
scene node count for heavy models.

### Imported Scene Structure

```text
model_root
├── mesh_root
│   └── model_mesh
├── bone_root
│   ├── センター
│   ├── 上半身
│   └── ...
└── morph_root
    └── blendShapes
```

The model receives these custom attributes:

- `mmd_model`: Model identifier
- `mmd_model_name`: Japanese model name
- `mmd_model_name_en`: English model name
- `mmd_comment`: Comment

### Import Multiple Models

Namespace support avoids name conflicts between bones and meshes. If you import the same model multiple times, suffixes are assigned automatically.

```python
from mmd_tools.io.mmd_importer import import_mmd_file
from mmd_tools.core import settings

settings.set("import.general.use_namespace", True)

models = ["character1.pmx", "character2.pmx", "stage.pmx"]

for model_path in models:
    root_node = import_mmd_file(model_path)
    print(f"Imported: {root_node}")
```

### Adjust After Import

```python
import maya.cmds as cmds

cmds.select("model_root")
cmds.scale(0.1, 0.1, 0.1)
cmds.move(0, 0, 100)
```

## Viewport Look (DX11 Shader & Color Management)

The DX11 MMD toon shader (`MMDShader.fx`) reproduces the MikuMikuDance look, which
is authored and lit in **gamma (sRGB) space**. Maya's default Color Management
renders through a linear / ACES pipeline, so without matching settings an imported
model looks washed out and desaturated.

To get the correct look, model import automatically configures Color Management.
Color Management stays **enabled** — only the following are changed:

- **Rendering space** → `scene-linear Rec.709-sRGB`. Maya 2026 defaults to
  `ACEScg`, whose wider (AP1) primaries are converted to Rec.709 by the view
  transform; the shader cannot cancel that color matrix, so colors shift and
  over-saturate.
- **View Transform** → `Un-tone-mapped (sRGB)`. The default
  `ACES 1.0 SDR-video (sRGB)` applies a film tone-map that washes out and
  desaturates the toon shading.

The shader de-gammas its final output so the `Un-tone-mapped (sRGB)` view
transform's sRGB encode cancels it exactly, restoring the gamma-space MMD result.
ACES users can switch the View Transform back afterwards; non-MMD assets are
unaffected by this opt-out:

```python
from mmd_tools.core import settings

# Skip the automatic MMD color-management setup on import.
settings.set("import.view.setup_color_management", False)
```

### MMD Light

Each import creates (or reuses) a single **`mmd_light`** controller null: a
directional light with an arrow draw handle and an `mmd_light_color` attribute.
The DX11 shader is driven **only** by this controller (Maya's automatic
scene-light binding is intentionally not used), so rotating the `mmd_light` null
changes the MMD light direction live in Viewport 2.0 — independent of the
viewport lighting mode ("Use Default/All Lights"). VMD light animation keys this
same controller.

## Uninstall

1. Quit Maya.
2. Delete `modules/maya_mmd_tools.mod`.
3. Delete the installed Maya MMD Tools folder.

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the following information:

- Full error message
- Maya version
- OS
- Maya MMD Tools version
- PMD/PMX/VMD file type used
- Steps to reproduce

Developer documentation is available at [docs-dev](docs-dev/README.md).
