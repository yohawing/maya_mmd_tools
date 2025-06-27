# Maya MMD Tools

This repository contains a Python plugin for Autodesk Maya to import MikuMikuDance (MMD) file formats (.pmd, .pmx, .vmd) into Maya scenes.

## Getting Started

### Prerequisites

*   Autodesk Maya (version X.X or later)
*   Python (version X.X or later)

### Installation

1.  Clone this repository or download the source code.
2.  Copy the `maya_mmd_tools.mod` file into your Maya modules directory. This is typically located at:
    *   `C:\Users\<Your Username>\Documents\maya\<Maya Version>\modules`
3.  Copy the `userSetup.py` file into your Maya scripts directory. This is typically located at:
    *   `C:\Users\<Your Username>\Documents\maya\<Maya Version>\scripts`
4.  Open Maya.
5.  Go to `Window > Settings/Preferences > Plug-in Manager`.
6.  Find `mmd_importer.py` (or the final plugin name) in the list and check the `Loaded` box.

## Maya Module File (.mod)

The `maya_mmd_tools.mod` file is used by Maya to recognize this plugin and set up the necessary paths. By placing this file in Maya's `modules` directory, the plugin can be easily loaded.

## userSetup.py

The `userSetup.py` file is automatically executed when Maya starts. It is used to add custom menus and perform initial setup for the plugin. By placing this file in Maya's `scripts` directory, the "MMD Tools" menu will be automatically added upon Maya startup.

## Usage

(Coming soon: Instructions on how to use the plugin within Maya)

## Development

### Directory Structure

```
F:/Develop/maya_mmd_tools/
├── src/
│   ├── __init__.py
│   ├── plugin_main.py
│   ├── ui.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── mmd_parser.py
│   │   ├── pmd_parser.py
│   │   ├── pmx_parser.py
│   │   ├── vmd_parser.py
│   │   ├── maya_utils.py
│   │   ├── pmd_data/
│   │   │   ├── __init__.py
│   │   │   ├── header.py
│   │   │   ├── vertex.py
│   │   │   ├── material.py
│   │   │   ├── bone.py
│   │   │   ├── ik.py
│   │   │   ├── morph.py
│   │   │   ├── display_frame.py
│   │   │   ├── rigid_body.py
│   │   │   └── joint.py
│   │   ├── pmx_data/
│   │   │   ├── __init__.py
│   │   │   ├── header.py
│   │   │   ├── vertex.py
│   │   │   ├── face.py
│   │   │   ├── material.py
│   │   │   ├── bone.py
│   │   │   ├── ik.py
│   │   │   ├── ik_link.py
│   │   │   ├── morph.py
│   │   │   ├── display_frame.py
│   │   │   ├── rigid_body.py
│   │   │   └── joint.py
│   │   └── vmd_data/
│   │       ├── __init__.py
│   │       ├── header.py
│   │       ├── bone_frame.py
│   │       ├── morph_frame.py
│   │       ├── camera_frame.py
│   │       ├── light_frame.py
│   │       ├── shadow_frame.py
│   │       └── ik_show_hide_frame.py
│   ├── converters/
│   │   ├── __init__.py
│   │   ├── mesh_converter.py
│   │   ├── bone_converter.py
│   │   ├── morph_converter.py
│   │   ├── physics_converter.py
│   │   └── animation_converter.py
│   └── io/
│       ├── __init__.py
│       ├── mmd_importer.py
│       ├── pmd_exporter.py
│       ├── pmx_exporter.py
│       └── vmd_exporter.py
├── resources/
│   ├── icons/
│   └── ui/
├── resources/
│   ├── icons/
│   └── ui/
└── tests/
    ├── run_tests.py
    ├── test_mmd_parser.py
    └── common/
        ├── __init__.py
        └── test_base.py
```

### Running Tests

Currently, there are no automated tests set up. Testing needs to be done manually through the Maya GUI or via scripting using `mayapy.exe`.

### Linting and Formatting

To maintain code quality, we use `ruff` and `black`.

To perform static analysis and formatting on the code, run the following commands:

```shell
ruff check .
black .
```

## Contributing

(Coming soon: Guidelines for contributing to the project)

## License

(Coming soon: License information)
