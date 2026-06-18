# Maya MMD Tools

[日本語ドキュメント](docs/README_ja.md)

Maya MMD Tools is a tool for importing MikuMikuDance (MMD) PMD/PMX models and VMD motions into Autodesk Maya.

This is an alpha early release. Some features may be undeveloped or unstable.

## Feature Support Matrix

Legend: ✅ Supported · 🔶 Partial / with caveats · 🧪 Experimental (opt-in) · ⛔ Not supported yet

> This is an alpha release. See [Known Limitations](#known-limitations) below for details.

### Import — Model (PMX, PMD)

| Feature | Status | Notes |
|---|---|---|
| Mesh / vertices / normals | ✅ | |
| Materials & textures | ✅ | Auto-applied (texture search path supported) |
| Maya name resolution | 🔶 | Partial; texture resolution can fail in some cases |
| Primary UV | ✅ | |
| Additional UV (UV1–4) | ⛔ | Not supported |
| Edge / outline flags | ✅ | Shader outline rendering is opt-in from the Material tab |
| Bones & skeleton | ✅ | |
| IK | ⛔ | Not supported |
| Append (grant / 付与) bones | ⛔ | Not supported |
| Bone local axis | ⛔ | Not supported |
| Display frames (表示枠) | ⛔ | Not supported |
| Vertex morph | ✅ | blendShape targets |
| Bone morph | 🔶 | Driven by motion bake; limited interactive control |
| Material morph | 🔶 | Driven by motion bake; limited interactive control |
| Group morph | ⛔ | |
| UV morph (incl. additional UV) | ⛔ | |
| Flip morph | ⛔ | |
| Impulse morph | ⛔ | |
| Rigid bodies & joints | ⛔ | Not supported |
| Soft body (PMX 2.1) | ⛔ | Not supported |
| Export | ⛔ | Not supported |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | 🔶 | High-precision bake via [mmd-anim](https://github.com/yohawing/mmd-anim) only (Bézier interpolation, IK, and grant resolved) |
| Morph animation | 🔶 | Vertex morphs only |
| Camera animation | ✅ | Creates/keys `mmd_camera` |
| Light animation | ✅ | Drives the `mmd_light` controller |
| IK on/off frames | ⛔ | Not supported |
| Export | ⛔ | Not supported |

### Viewport & Shading

| Feature | Status | Notes |
|---|---|---|
| DX11 MMD toon shader (Windows) | ✅ | Toon shading and transparency; outline rendering is off by default and opt-in from the Material tab |
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

### Import Animation

1. In the Import/Export tab, choose a VMD file.
2. (Optional) In animation import settings, set VMD FPS (30 or 60; default 30). This changes the Maya scene time unit before import.
3. Click `Import Animation`.
4. The animation is applied to the matching model in the scene.

## Viewport Setup

The shader that reproduces the MMD toon look can be confirmed by enabling the MMD shader creation option together with the `dx11Shader.dll` plugin. The following settings are also applied automatically on import:

- **Rendering space** → `ACEScg` → `scene-linear Rec.709-sRGB`.
- **View Transform** → `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`.

Both are applied to reproduce the MMD-style color response (sRGB gamma-space input/output).

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the following information:
