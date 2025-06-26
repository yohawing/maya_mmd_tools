# Maya MMD Tools

This repository contains a Python plugin for Autodesk Maya to import MikuMikuDance (MMD) file formats (.pmd, .pmx, .vmd) into Maya scenes.

## Getting Started

### Prerequisites

*   Autodesk Maya (version X.X or later)
*   Python (version X.X or later)

### Installation

1.  Clone this repository or download the source code.
2.  Copy the `src` and `resources` directories into your Maya plugins directory. This is typically located at:
    *   `C:\Users\<Your Username>\Documents\maya\<Maya Version>\plug-ins`
3.  Open Maya.
4.  Go to `Window > Settings/Preferences > Plug-in Manager`.
5.  Find `mmd_importer.py` (or the final plugin name) in the list and check the `Loaded` box.

## Usage

(Coming soon: Instructions on how to use the plugin within Maya)

## Development

### Directory Structure

```
F:/Develop/maya_mmd_tools/
├── src/
│   ├── __init__.py
│   ├── plugin_main.py
│   └── mmd_importer.py
├── resources/
│   ├── icons/
│   └── ui/
└── tests/
    └── (test files will go here)
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
