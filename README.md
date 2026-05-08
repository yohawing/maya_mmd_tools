# Maya MMD Tools

[![Version](https://img.shields.io/badge/version-0.1.0-orange.svg)](https://github.com/yohawing/maya_mmd_tools/releases)
[![Maya](https://img.shields.io/badge/Maya-2024-blue.svg)](https://www.autodesk.com/products/maya)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Maya MMD Tools is a Python plugin for Autodesk Maya that imports MikuMikuDance (MMD) model, motion, and pose data into Maya scenes.

This is a `0.x` early release. Features may be unstable, and production use is not recommended. Please report bugs and feedback through [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues).

## Status

The first release target is `0.1.0`.

### Supported

- PMD/PMX model import
- VMD animation import for bones, morphs, cameras, and lights
- VPD pose parsing/import support
- Basic MMD Tools UI
- Japanese/English UI text support
- Namespace support for loading multiple models
- Log viewer for diagnostics

### Not Yet Supported

- PMD/PMX/VMD export
- Complete physics support
- C++ node implementation in the release target

## Requirements

- Autodesk Maya 2024 or later
- Windows 11 or macOS 15.6
- Python 3.7 or later through Maya's bundled Python environment

## Installation

1. Clone this repository or download the release archive.
2. Copy `maya_mmd_tools.mod` into your Maya modules directory.
3. Copy `userSetup.py` into your Maya scripts directory.
4. Restart Maya.
5. Open `Window > Settings/Preferences > Plug-in Manager`.
6. Enable `plugin_main.py`.

For detailed setup and usage instructions, see [docs/README.md](docs/README.md).

## Quick Usage

Open the UI from Maya:

```text
MMD > MMD Tools
```

Or import files from Maya Script Editor:

```python
from mmd_tools.io.mmd_importer import import_mmd_file

import_mmd_file("C:/Models/character.pmx")
import_mmd_file("C:/Motions/motion.vmd")
```

Check the installed version:

```python
import mmd_tools
print(mmd_tools.__version__)
```

Expected version for the current release:

```text
0.1.0
```

## Documentation

### User Documentation

All user-facing documentation is consolidated into:

- [docs/README.md](docs/README.md)

It includes installation, quick start, model import, log viewer usage, troubleshooting, best practices, and support information.

### Developer Documentation

Developer documentation is under [docs-dev/](docs-dev/). The current structure is intentionally compact:

- [Architecture](docs-dev/architecture.md)
- [ASCII translation](docs-dev/ascii-translation.md)
- [Settings](docs-dev/setting.md)
- [Testing overview](docs-dev/testing-overview.md)
- [Testing mock design](docs-dev/testing-mock.md)
- [Release process](docs-dev/release-process.md)
- [Release versioning](docs-dev/release-versioning.md)
- File format specs:
  - [PMD](docs-dev/spec-pmd.md)
  - [PMX](docs-dev/spec-pmx.md)
  - [VMD](docs-dev/spec-vmd.md)

## Running Tests

Unit tests:

```shell
python tests/run_tests.py --type unit
```

Integration tests require a Maya environment:

```shell
python tests/run_tests.py --type integration
```

GUI tests:

```shell
python tests/run_gui_tests.py
```

See [docs-dev/testing-overview.md](docs-dev/testing-overview.md) for detailed test guidance.

## Release

The release process is documented in [docs-dev/release-process.md](docs-dev/release-process.md).

Current versioning policy:

- Use simple `0.x` versions such as `0.1.0`, `0.1.1`, and `0.2.0`.
- Do not use `alpha`, `beta`, or `rc` suffixes for now.
- If the release should be marked unstable, use GitHub Release's Pre-release flag.

## Contributing

1. Fork the repository.
2. Create a branch for your feature or fix.
3. Make your changes and commit them with clear messages.
4. Run the relevant tests.
5. Push your branch and create a pull request.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
