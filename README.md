# Maya MMD Tools

[日本語ドキュメント](docs/README_ja.md)

![feature](docs/assets/feature.png)
> Credits — Model: [Sour](https://bowlroll.net/file/146103) / Motion: [mobiusP](https://www.nicovideo.jp/watch/sm42576784)

Maya MMD Tools is a tool for importing MikuMikuDance (MMD) PMD/PMX models and VMD motions into Autodesk Maya.

Its long-term goal is to provide a complete workflow for editing and exporting models and animations.

> Maya MMD Tools is currently in alpha, and its UI and workflows may change. Comprehensive guides for individual features are not yet available. See the feature support matrix below for details.

## Feature Support Matrix

Legend: ✅ Supported · 🔶 Partial / with caveats · 🧪 Experimental (opt-in) · ⛔ Not supported yet

### Model (PMX, PMD)

| Feature | Status | Notes |
|---|---|---|
| Mesh | ✅ | |
| Materials & textures | 🔶 | MMD toon shading through DX11 and OpenGL shaders. MMD shader fidelity is limited by Viewport 2.0 constraints. |
| Maya name resolution | ✅ | Names are converted to ASCII-safe Maya names. Japanese and Chinese texture paths are also resolved automatically to safe paths. |
| Edge / outline flags | 🔶 | Can be enabled as an option, subject to Viewport 2.0 constraints. |
| Bones, skeleton & rig (IK / append / local axis) | 🔶 | Partially supported. Some complex models still have known issues. |
| Display frames (表示枠) | 🔶 | Imported frame metadata can be edited in a dedicated tab and preserved through the development PMX round-trip path. |
| Morphs (vertex / bone / material / group / UV) | 🔶 | Vertex, bone, material, and UV morphs are supported. Flip and Impulse morphs are not supported. |
| Physics (rigid bodies & joints) | 🔶 | PMX/PMD physics import is enabled by default, with editable authoring and PMX round-trip support; native bake is experimental. Live simulation is unsupported. |
| Soft body (PMX 2.1) | ⛔ | Not supported |
| Export | ⛔ | Not supported |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | 🔶 | MMD rigs are supported through the Maya DG. Bake mode uses [mmd-anim](https://github.com/yohawing/mmd-anim) final-pose evaluation. |
| VPD | ✅ | Available through drag and drop only |
| Morph animation | 🔶 | Vertex, bone, material, and UV morphs are supported. Flip and Impulse morphs are not supported. |
| Camera animation | ✅ | Creates and keys `mmd_camera`. Lighting drives the `mmd_light` controller. Self-shadow is not supported. |
| IK on/off frames | 🔶 | Supported for import/bake. Runtime bake applies the state to the baked pose; rig mode keys `mmdCcdIk.enabled`. |
| Physics | 🧪 | Native mmd-anim physics bake is supported experimentally. Real-time/live physics evaluation is unsupported, and physics is off by default. |
| HumanIK / retargeting | ⛔ | Not supported |
| Export | ⛔ | Not supported. Partial public support is planned after import and editing features mature. |

## Known Limitations

- **Various features are still incomplete.** This is an experimental alpha release; feedback is welcome.
- **Parity is not guaranteed for complex rigs or motions.** Editable `mmdCcdIk` / `mmdAppend` nodes are preserved, but cases involving joint orientation, IK, append transforms, or local axes may not exactly match Bake mode or MMD mesh deformation.

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

The installer copies all Maya MMD Tools files into Maya's user `modules` folder, then writes a `maya_mmd_tools.mod` file next to that copy.

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
6. For more MMD-like shading, also enable `dx11Shader`; `Create MMD Shader` is enabled by default.

## Verify Installation

### Check the Menu

Confirm that `MMD > MMD Tools` appears in Maya's menu bar.

## Quick Start

### Open the MMD Tools UI

1. Select `MMD > MMD Tools`.
2. The MMD Tools UI opens.
3. You can inspect and adjust settings in each tab.

The UI fields follow PMX Editor conventions. Many fields are not yet implemented, so the tool should currently be considered primarily a preview tool.

### Import a Model

1. In the Import/Export tab, choose a PMX or PMD file.
2. Click `Import Model`.

If textures fail to load due to multi-byte characters in the path, enable the automatic texture repair option. The textures will be copied automatically and renamed to loadable names.

### Import Animation

1. In the Import/Export tab, choose a VMD file.
2. Click `Import Animation`.
3. The animation is applied to the matching model in the scene.

## Viewport Setup

To view the shader that reproduces the MMD toon look, enable the MMD shader creation option and the shader plug-in for your rendering environment. Use the `dx11Shader` plug-in on Windows and the `glslShader` (GLSLShader) plug-in on macOS. The following settings are also applied automatically on import:

- **Rendering space** → `ACEScg` → `scene-linear Rec.709-sRGB`.
- **View Transform** → `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`.

Both are applied to reproduce the MMD-style color response (sRGB gamma-space input/output).

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the following information:
