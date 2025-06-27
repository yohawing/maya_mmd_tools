# Maya MMD Tools

This repository contains a Python plugin for Autodesk Maya to import MikuMikuDance (MMD) file formats (.pmd, .pmx, .vmd) into Maya scenes.

## Getting Started

### Prerequisites

*   Autodesk Maya 2024 or later
*   Python (version 3.7 or later)

### Installation

1.  Clone this repository or download the source code.
2.  Copy the `maya_mmd_tools.mod` file into your Maya modules directory. This is typically located at:
    *   `C:\Users\<Your Username>\Documents\maya\<Maya Version>\modules`
3.  Copy the `userSetup.py` file into your Maya scripts directory. This is typically located at:
    *   `C:\Users\<Your Username>\Documents\maya\<Maya Version>\scripts`
4.  Open Maya.
5.  Go to `Window > Settings/Preferences > Plug-in Manager`.
6.  Find `mmd_importer.py` (or the final plugin name) in the list and check the `Loaded` box.


## Usage

(Coming soon: Instructions on how to use the plugin within Maya)


### Running Tests

Unit tests can be run from the project root using the following command: 
```shell
python tests/run_tests.py
```
Integration tests (tests that require a running Maya environment) currently need to be executed manually within Maya or using `mayapy.exe` for script execution.

```shell
"c:\Program Files\Autodesk\Maya<Version>\bin\mayapy.exe" tests/run_tests.py
```

## Contributing

We welcome contributions to this project! Please follow these steps:
1.  Fork the repository.
2.  Create a new branch for your feature or bug fix.
3.  Make your changes and commit them with clear messages.
4.  Push your changes to your forked repository.
5.  Create a pull request to the main repository.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.