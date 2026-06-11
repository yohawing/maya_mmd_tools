# Maya MMD Tools

[日本語ドキュメント](docs/README_ja.md)

Maya MMD Tools is a tool for importing MikuMikuDance (MMD) PMD/PMX models and VMD motions into Autodesk Maya.

This is an alpha early release. Some features may be undeveloped or unstable.

## Supported Features

- PMD/PMX model import
- VMD animation import for bones, morphs, cameras, and lights
- Basic UI with Info, Material, Morph, and Bone tabs
- Japanese/English UI
- Namespace support
- Log viewer

## Known Limitations

- PMD/PMX/VMD export is not implemented.
- Physics support is incomplete.
- VMD motion import has an unresolved issue where newly imported motion may not play back correctly. In `0.1.0`, VMD loading/parsing is available, but motion playback should be treated as incomplete.
- Large models may have performance issues.
- Some PMX files may fail to import.
- The opt-in C++ PMX fast path currently supports mesh, basic materials, basic skeleton/skin, and vertex morph blendShape targets.
- C++ fast path morphs not yet implemented: UV/additional UV morphs, material morphs, bone morphs, and group morphs.

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

# Physics import. Experimental in this early release.
settings.set("import.physics.import_physics", False)
```

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
