# Maya MMD Tools

[日本語ドキュメント](docs/README_ja.md)

Maya MMD Tools imports, edits, and exports MikuMikuDance (MMD) PMD/PMX/VMD data in Maya.

![feature](docs/assets/feature.png)

> Credits — Model: [Genoge miku](https://bowlroll.net/file/320915)

> [!WARNING]
> Maya MMD Tools is currently in alpha, and its UI and workflows may change. Comprehensive guides for individual features are not yet available. See the feature support matrix below for details.

## Feature Support Matrix

Legend: ✅ Supported · ℹ️ Partial / with caveats · 🧪 Experimental

### Model (PMX, PMD)

| Feature | Status | Notes |
|---|---|---|
| Mesh | ℹ️ | SDEF/QDEF import is not supported; they are imported as BDEF4-equivalent weights. Additional UVs are retained as metadata. |
| Materials & textures | ℹ️ | MMD toon shaders are implemented for DX11 and OpenGL. Due to Viewport 2.0 limitations, outlines, edge flags, transparency, and self-shadow are not supported. |
| Maya name resolution | ✅ | Names are converted to safe English or hashed names. Non-English image paths are also resolved automatically to safe paths. |
| Bones, skeleton & rig (IK / append / local axis) | ℹ️ | Basic PMX 2.0 IK, append, axis, and after-physics deformation are supported. Complex rigs have known issues. |
| Display frames (表示枠) | ✅ | Display-frame names, special-frame flags, and ordered bone/morph items can be edited. |
| Morphs (vertex / bone / material / group / UV) | ℹ️ | Vertex, bone, material, and group morphs are supported. PMX 2.0 UV/additional-UV morph metadata (types 3–7) is preserved through export and re-import, but is not applied to Maya UV sets. |
| Physics (rigid bodies & joints) | ℹ️ | Performance and quality have known issues. Some editing operations remain unsupported. |
| Export | 🧪 | Broadly supported, but there are likely still many bugs. Invalid data is rejected during validation with an explanation. |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | ℹ️ | Basic MMD rigs are supported, but complex mechanisms are not. Bake Export uses [mmd-anim](https://github.com/yohawing/mmd-anim). |
| VPD | ✅ | Drag-and-drop import only |
| Morph animation | ℹ️ | UV morphs are unverified. Material morphs work only with the DX11 or OpenGL shader. |
| Camera animation | ✅ | Creates and keys `mmd_camera`. Lighting drives the `mmd_light` controller. |
| IK on/off frames | ℹ️ | Supported for import and bake. Runtime bake applies the state to the final pose; rig mode keys `mmdCcdIk.enabled`. |
| Physics | ℹ️ | Supports Bullet-based real-time physics and physics bake. Live evaluation is off by default and can be enabled from the Physics tab. Accuracy is still limited. |
| HumanIK / retargeting | 🧪 | Experimental support for retargeting between imported MMD models. Try it from `MMD > HumanIK (Experimental)`. |
| Control Rig | 🧪 | An optional Control Rig can be generated from the semi-standard bone layout. Restore and bake have known issues. |
| Export | 🧪 | Exports the current character over the selected timeline. Only baked export is currently available. VMD camera, light, and self-shadow export are unsupported. |

## Known Limitations

- **Detailed documentation is not written yet.** This is an alpha release, and development speed is prioritized over documentation maintenance.
- **Various features are still incomplete.** This is an experimental alpha release; feedback is welcome.
- **QDEF and SDEF are downgraded to BDEF4.** Their specialized deformation is not preserved, so meshes may appear thinner with some model and motion combinations.
- **Export supports a bounded, validated scope.** Export depends on many features, and we have not yet tested it across a wide range of cases, so it likely still contains many bugs.
- **Leg rotations and bones that conflict with bone morphs work only under the Control Rig.** Bones may become immovable when their connections conflict with bone morphs.

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

### Enable the Plugin

1. Start Maya. If Maya is already running, restart it.
2. Open `Window > Settings/Preferences > Plug-in Manager`.
3. Find `mmd_tools_plugin.py`.
4. Check `Loaded`. If you want it to load automatically, also check `Auto load`.
5. For more MMD-like shading, also check `Create MMD Shader`; it is enabled by default.

## Verify Installation

### Check the Menu

Confirm that `MMD > MMD Editor` appears in Maya's menu bar.

## Quick Start

### Open MMD Editor

1. Select `MMD > MMD Editor`.
2. The MMD Editor window opens.
3. You can inspect and adjust settings in each tab.

The UI follows PMX Editor conventions.

### Import a Model

1. In the Import tab, choose a PMX or PMD file to import.
2. Click `Import Model`.

### Import Animation

1. In the Import tab, choose a VMD file.
2. Click `Import Animation`.
3. The animation is applied to the matching model in the scene.

### Create and Edit a Model

1. In the Import tab, choose a packaged template under `Create MMD Model`.
2. Edit the current model from the Material, Bone, Morph, and related authoring tabs.

### Export a Model or Animation

1. Select the model to make it the current model.
2. In the Export tab, choose `Model` or `Animation`.
3. Review validation, then export PMX or VMD.

### Use HumanIK (Experimental)

![HumanIK window](docs/assets/humanik.png)

1. Select `MMD > HumanIK (Experimental)` from Maya's menu bar to open the standalone window.
2. Select the MMD character's ModelRoot and click `Set Up Selected Model` to create its character definition.
3. Import two characters and set up both. Choose the character that contains the motion from the SOURCE list to retarget its motion.
4. The retargeted motion can be baked to a Control Rig.
5. Use `Restore MMD Rig` to return from the Control Rig to the MMD rig state.

Using Maya's HumanIK features directly can break the MMD rig in some workflows.

## Viewport Setup

To view the shader that reproduces the MMD toon look, enable the MMD shader creation option and the shader plug-in for your rendering environment. Use the `dx11Shader` plug-in on Windows and the `glslShader` (GLSLShader) plug-in on macOS. The following settings are also applied automatically on import:

- **Rendering space** → `ACEScg` → `scene-linear Rec.709-sRGB`.
- **View Transform** → `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`.

Both are applied to reproduce the MMD-style color response (sRGB gamma-space input/output).

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the Maya and OS versions, reproduction steps, the error, and a screenshot when possible.
