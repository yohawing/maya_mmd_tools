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
| Materials & textures | ℹ️ | MMD shading is implemented through RenderOverride (DirectX only). Select `Renderer > MMD Render` in the viewport to enable it. |
| Maya name resolution | ✅ | Names are converted to safe English or hashed names. Non-English image paths are also resolved automatically to safe paths. |
| Bones, skeleton & rig (IK / append / local axis) | ℹ️ | Basic PMX 2.0 IK, append, axis, and after-physics deformation are supported. Complex rigs have known issues. |
| Display frames (表示枠) | ✅ | Display-frame names, special-frame flags, and ordered bone/morph items can be edited. |
| Morphs (vertex / bone / material / group / UV) | ℹ️ | Vertex, bone, material, and group morphs are supported. PMX 2.0 UV/additional-UV morph metadata (types 3–7) is preserved through export and re-import, but is not applied to Maya UV sets. |
| Physics (rigid bodies & joints) | ℹ️ | Performance and quality have known issues. Some editing operations remain unsupported. |
| Export | 🧪 | Export is experimental. Testing across a wide range of models is not yet complete. |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | ℹ️ | Basic MMD rigs are supported, but complex mechanisms are not. Bake Export uses [mmd-anim](https://github.com/yohawing/mmd-anim). |
| VPD | ✅ | VPD files can be imported by drag-and-drop. |
| Morph animation | ℹ️ | UV morphs are unverified. Material morphs drive the standard material preview and MMD Render; the standard preview approximates color and opacity. |
| Camera animation | ✅ | Creates and keys `mmd_camera`. Lighting drives the `mmd_light` controller. |
| IK on/off frames | ℹ️ | Supported for import and bake. Runtime bake applies the state to the final pose; rig mode keys `mmdCcdIk.enabled`. |
| Physics | ℹ️ | Supports Bullet-based real-time physics and physics bake. Live evaluation is off by default and can be enabled from the Physics tab. Accuracy is still limited. |
| HumanIK / retargeting | 🧪 | Experimental support for retargeting between imported MMD models. Try it from `MMD > HumanIK (Experimental)`. |
| Control Rig | 🧪 | An optional Control Rig can be generated from the semi-standard bone layout. Restore and bake have known issues. |
| Export | 🧪 | Supports VMD export for characters, cameras, and lights, as well as VPD export. Export is currently bake-only. Self-shadow is unsupported. |

## Known Limitations

- **Detailed documentation is not written yet.** This is an alpha release, and development speed is prioritized over documentation maintenance.
- **QDEF and SDEF are downgraded to BDEF4.** Their specialized deformation is not preserved, so meshes may appear thinner with some model and motion combinations.
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
5. Confirm that `MMD > MMD Editor` appears in Maya's menu bar.

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

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the Maya and OS versions, reproduction steps, and error details. If possible, also include a link to the model's download page and a screenshot.
