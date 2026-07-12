# Maya MMD Tools

[日本語ドキュメント](docs/README_ja.md)

Maya MMD Tools is a tool for importing MikuMikuDance (MMD) PMD/PMX models and VMD motions into Autodesk Maya.

It aims to reproduce MMD rigs and provide a complete workflow for importing, editing, and exporting animations.

This is an alpha early release. Some features may be undeveloped or unstable.

## Feature Support Matrix

Legend: ✅ Supported · 🔶 Partial / with caveats · 🧪 Experimental (opt-in) · ⛔ Not supported yet

> This is an alpha release. See [Known Limitations](#known-limitations) below for details.

### Import — Model (PMX, PMD)

| Feature | Status | Notes |
|---|---|---|
| Mesh / vertices / normals | ✅ | |
| Materials & textures | 🔶 | MMD toon shading through the DX11 shader. Semi-transparent material fidelity is limited by draw-order constraints. Japanese/Chinese texture paths are copied/renamed to safe fallback paths when needed. |
| Maya name resolution | ✅ | Names are converted to ASCII-safe Maya names |
| Primary UV | ✅ | |
| Additional UV (UV1–4) | ⛔ | Not supported |
| Edge / outline flags | 🔶 | Can be enabled as an option, but draw-order limitations remain |
| Bones & skeleton | 🔶 | Some complex models still have known issues |
| Rig (IK / grant / local axis) | ✅ | Supported |
| Display frames (表示枠) | 🔶 | Preserved as model metadata for development-mode PMX round-trip; no dedicated editing UI yet |
| Morphs (vertex / bone / material / group / UV) | 🔶 | Vertex, bone, material, and group controls are supported. Material morphs drive the complete MMD hardware-shader parameter set and fail closed when a backend is incomplete. UV, Flip, and Impulse morphs are not supported. |
| Rigid bodies & joints | 🧪 | Imported as Maya Bullet nodes with preview simulation. The normal Physics tab provides read-only inspection of model-scoped rigid bodies and joints. |
| Soft body (PMX 2.1) | ⛔ | Not supported |
| HumanIK | 🧪 | Experimental Bone tab action creates a HumanIK definition/control rig from the imported MMD skeleton |
| Export | ⛔ | Not supported |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | 🔶 | Bake mode uses high-precision [mmd-anim](https://github.com/yohawing/mmd-anim) final-pose evaluation. Rig mode keeps editable sparse keys and live MMD rig nodes, but remains experimental for complex motions. |
| Morph animation | 🔶 | Vertex, bone, and complete hardware-shader material morph animation are supported. |
| Camera animation | ✅ | Creates/keys `mmd_camera` |
| Light animation | ✅ | Drives the `mmd_light` controller |
| IK on/off frames | 🔶 | Supported for import/bake. Runtime bake applies the state to the baked pose; rig mode keys `mmdCcdIk.enabled`. |
| Export | ⛔ | Not supported |

### Viewport & Shading

| Feature | Status | Notes |
|---|---|---|
| DX11 MMD toon shader (Windows) | 🔶 | Toon shading and transparency. Outline rendering is off by default due to draw-order constraints; it can be enabled per-material from the Material tab, but fidelity is limited. |
| MMD light controller | ✅ | Single directional-light null |
| Transparency (opaque / cutout / blend) | ✅ | Manual, plus opt-in auto-classification |
| GLSL shader (macOS) | ⛔ | Not supported |

## Known Limitations

- **Export is not available.** This is an import-only tool for now — PMX/PMD/VMD export is not implemented (the UI states this explicitly).
- **VPD pose import is available through drag-and-drop.** It applies the pose at the current frame to the selected MMD model, or to a PMX/PMD model dropped together with the VPD file.
- **Additional UV / multi-UV is not applied** (read but ignored).
- **Material morph shader runtime is complete-or-none.** Diffuse/alpha, specular, ambient, edge color/size, and texture/sphere/toon factors are connected together; an incomplete backend is left untouched and reported.
- **UV, Flip, and Impulse morphs are not supported.** Vertex, bone, material, and group controls are available.
- **Soft body (PMX 2.1) data is silently ignored.** The rest of the file still imports correctly.
- **Display frames (表示枠) are preserved for PMX round-trip but do not have a dedicated editing UI.**
- **Physics is experimental.** PMX/PMD rigid bodies and joints are imported by default when Maya Bullet is available. The Physics tab is always available for read-only inspection; native physics motion bake remains an opt-in path.
- **Bake mode is the fidelity path for VMD motion.** It bakes final poses from the `mmd-anim` runtime and is the recommended path when matching MMD output matters.
- **Rig mode is experimental for complex motion parity.** It keeps editable sparse keys plus live `mmdCcdIk` / `mmdAppend` nodes, but complex joint-orient, IK, append, and local-axis cases may not match Bake mode or MMD mesh deformation exactly.
- **HumanIK setup requires a valid full-body skeleton.** The Bone tab action reports an error if Maya cannot create the HumanIK control rig from the current model.
- **File > Import integration is not part of this release.** Use the MMD Tools UI or drag-and-drop import instead. The Maya file translator path is deferred because it needs a separate safe integration path.
- Large models may have performance issues, and some PMX files may fail to import.
- The opt-in C++ fast-import path supports mesh, basic materials, basic skeleton/skin, and vertex-morph blendShape targets only (UV / material / bone / group morphs are not handled on that path).

## System Requirements

### Required

- **Maya**: 2024 or later
- **OS**: Windows 11 / macOS 15.6
- **Python**: 3.10 or later (bundled with Maya 2024+)

## Installation

### Download

1. Download the latest release from the [GitHub Releases page](https://github.com/yohawing/maya_mmd_tools/releases).
2. Extract the ZIP file to a temporary folder.

### Drag and Drop Install

1. Start Maya.
2. Drag `drag_drop_install.py` from the extracted folder into the Maya viewport.
3. Confirm the install dialog.
4. Restart Maya.

The installer copies all Maya MMD Tools files into Maya's user modules folder, then writes a `maya_mmd_tools.mod` file next to that copy. The module file contains entries for the Maya versions bundled in the ZIP, so installing from one Maya version also prepares the same copy for the other bundled versions.
After confirming that Maya starts with the plugin, you can delete the temporary extracted folder.

On Windows, the installed copy is placed under:

```text
C:\Users\<User Name>\Documents\maya\modules\maya_mmd_tools
```

On macOS, the installed copy is placed under:

```text
~/Documents/maya/modules/maya_mmd_tools
```

The generated module file is written next to that folder:

```text
C:\Users\<User Name>\Documents\maya\modules\maya_mmd_tools.mod
```

### Enable the Plugin

1. Start Maya. If Maya is already running, restart it.
2. Open `Window > Settings/Preferences > Plug-in Manager`.
3. Find `mmd_tools_plugin.py`.
4. Check `Loaded`.
5. If you want it to load automatically, also check `Auto load`.

## Verify Installation

### Check the Menu

Confirm that `MMD > MMD Tools` appears in Maya's menu bar.

## Quick Start

### Open the MMD Tools UI

1. Select `MMD > MMD Tools`.
2. The MMD Tools UI opens.
3. You can inspect and adjust settings in each tab.

Main tabs:

- **Info**: Model information
- **Material**: Material settings
- **Morph**: Facial expression/morph controls
- **Bone**: Bone information

### Import a Model

1. In the Import/Export tab, choose a PMX or PMD file.
2. Click `Import Model`.

After a successful import, a `model_root` group is created in the Outliner, and the model appears in the viewport. Materials and textures are applied automatically.
If textures fail to load due to multi-byte characters in the path, enable the automatic texture repair option. Textures will be automatically copied and renamed to loadable names.

### Import Animation

1. In the Import/Export tab, choose a VMD file.
2. (Optional) In animation import settings, set VMD FPS (30 or 60; default 30). This changes the Maya scene time unit before import.
3. Click `Import Animation`.
4. The animation is applied to the matching model in the scene.

### Drag and Drop Import

You can also import MMD files by dragging them into the Maya viewport.

- Drop a PMX or PMD file to import a model.
- Drop a PMX/PMD file together with a VMD file to import the model first and then apply the motion.
- Drop a VMD file after a model is already loaded to apply the motion to the selected or existing MMD model.
- Dropping a VMD file before loading a model shows a warning and does not import the motion.

Maya `File > Import` is not a supported MMD import path in this release.

## Viewport Setup

The shader that reproduces the MMD toon look can be confirmed by enabling the MMD shader creation option together with the `dx11Shader.dll` plugin. The following settings are also applied automatically on import:

- **Rendering space** → `ACEScg` → `scene-linear Rec.709-sRGB`.
- **View Transform** → `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`.

Both are applied to reproduce the MMD-style color response (sRGB gamma-space input/output).

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the following information:
