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
| Display frames (表示枠) | ⛔ | Not supported yet; planned as control rig or AnimPicker data |
| Morphs (vertex / bone / material / group / UV) | 🔶 | Partially supported |
| Rigid bodies & joints | ⛔ | Not supported |
| Soft body (PMX 2.1) | ⛔ | Not supported |
| HumanIK | ⛔ | Not supported |
| Export | ⛔ | Not supported |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | 🔶 | Normally plays through the MMD rig. Bake mode uses high-precision [mmd-anim](https://github.com/yohawing/mmd-anim) evaluation. |
| Morph animation | ✅ | Vertex and bone morphs are supported. Material morphs are partially supported. |
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
- **Python**: 3.7 or later (bundled with Maya)

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

## Viewport Setup

The shader that reproduces the MMD toon look can be confirmed by enabling the MMD shader creation option together with the `dx11Shader.dll` plugin. The following settings are also applied automatically on import:

- **Rendering space** → `ACEScg` → `scene-linear Rec.709-sRGB`.
- **View Transform** → `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`.

Both are applied to reproduce the MMD-style color response (sRGB gamma-space input/output).

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the following information:
