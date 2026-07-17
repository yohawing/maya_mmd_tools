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
| Materials & textures | 🔶 | MMD toon shading through DX11 and OpenGL shaders. VP2.0 fidelity is limited. |
| Maya name resolution | ✅ | Names are converted to ASCII-safe Maya names. Japanese and Chinese texture paths are also resolved automatically to safe paths. |
| UV | 🔶 | Primary UVs are supported. Additional UVs (UV1–4) are not supported. |
| Edge / outline flags | 🔶 | Can be enabled as an option, but draw-order limitations remain |
| Bones & skeleton | 🔶 | Some complex models still have known issues |
| Rig (IK / grant / local axis) | 🔶 | Partially supported. Some complex models still have known issues. |
| Display frames (表示枠) | 🔶 | Preserved as model metadata for development-mode PMX round-trip; no dedicated editing UI yet |
| Morphs (vertex / bone / material / group / UV) | 🔶 | Vertex and bone morphs are supported. Material, UV, Flip, and Impulse morphs are not supported. |
| Rigid bodies & joints | 🧪 | Development-mode authoring: edit imported PMX fields and named bone/body bindings (including `None`), validate and undo edits, inspect bone-following rest-pose Colliders, and PMX export/re-import. Live simulation is not supported. |
| Soft body (PMX 2.1) | ⛔ | Not supported |
| HumanIK | ⛔ | Not supported |
| Export | ⛔ | Not a public supported feature. Development Mode exposes PMX/VMD export UI, but this matrix claims only the validated Physics-authoring PMX export/re-import scope. |

### Animation (VMD)

| Feature | Status | Notes |
|---|---|---|
| Bone animation | 🔶 | MMD rigs are supported through the Maya DG. Bake mode uses [mmd-anim](https://github.com/yohawing/mmd-anim) final-pose evaluation. |
| VPD | ✅ | Available through drag and drop only |
| Morph animation | 🔶 | Vertex and bone morphs are supported. Material, UV, Flip, and Impulse morphs are not supported. |
| Camera animation | ✅ | Creates/keys `mmd_camera` |
| Light animation | ✅ | Drives the `mmd_light` controller |
| IK on/off frames | 🔶 | Supported for import/bake. Runtime bake applies the state to the baked pose; rig mode keys `mmdCcdIk.enabled`. |
| Physics | 🔶 | VMD Bake mode only. Live simulation, Controller/IK-driven physics, animated Colliders, and physics cache are not supported. |
| Export | ⛔ | Not supported |

## Known Limitations

- **Export is not a public supported feature.** Development Mode exposes PMX/VMD export UI, but the support claim here is limited to PMX export/re-import of the Physics-authoring fields described above.
- **Rig mode is experimental for complex motion parity.** It keeps editable sparse keys plus live `mmdCcdIk` / `mmdAppend` nodes, but complex joint-orient, IK, append, and local-axis cases may not match Bake mode or MMD mesh deformation exactly.
- **Physics authoring is not live physics.** It is off by default and limited to editing imported rigid bodies/joints, authoring/rest-pose Collider display and visibility, and development-mode PMX export/re-import. The scene-wide world enable control does not make live simulation supported.
- **Physics object creation is not available.** Create, duplicate, and delete controls remain hidden. Controller/IK/arbitrary-key pre-physics poses, animated Collider collision, hair/skirt live collision, random scrubbing, and physics caches are unsupported.
- **Native physics bake is a separate feature.** VMD Bake mode does not make the authoring view a live simulator.

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

If textures fail to load due to multi-byte characters in the path, enable the automatic texture repair option. The textures will be copied automatically and renamed to loadable names.

### Import Animation

1. In the Import/Export tab, choose a VMD file.
2. (Optional) In animation import settings, set VMD FPS (30 or 60; default 30). This changes the Maya scene time unit before import.
3. Click `Import Animation`.
4. The animation is applied to the matching model in the scene.

You can also import MMD files by dragging them into the Maya viewport. This is an experimental feature.

Maya `File > Import` is not a supported MMD import path in this release.

## Viewport Setup

To view the shader that reproduces the MMD toon look, enable the MMD shader creation option and the shader plug-in for your rendering environment. Use the `dx11Shader` plug-in on Windows and the `glslShader` (GLSLShader) plug-in on macOS. The following settings are also applied automatically on import:

- **Rendering space** → `ACEScg` → `scene-linear Rec.709-sRGB`.
- **View Transform** → `ACES 1.0 SDR-video (sRGB)` → `Un-tone-mapped (sRGB)`.

Both are applied to reproduce the MMD-style color response (sRGB gamma-space input/output).

## Support

If the problem is not resolved, report it on [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues) with the following information:
