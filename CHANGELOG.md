# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-08

### Added
- PMD/PMX model import.
- VMD animation import for bones, morphs, cameras, and lights.
- Basic MMD Tools UI with Info, Material, Morph, and Bone tabs.
- Japanese/English UI text support.
- Namespace support for loading multiple models:
  - PMX/PMD importer namespace generation
  - Japanese model name conversion for namespace-safe names
  - Sequential namespace management for duplicate model names
  - VMD importer namespace detection
- NamespaceUtils class:
  - Namespace generation and management
  - Context manager support
  - Cleanup support after import errors
- Maya Python API 2.0 based helper paths for performance-sensitive operations.

### Changed
- Import setting `use_namespace` is now wired to importer behavior.
- Bone list display order follows MMD bone indices.
- Custom attribute names were standardized.
- Release docs and README now use simple `0.x` versioning without alpha/beta suffixes.

### Fixed
- Improved unit and integration test stability.
- Fixed multiple UI issues.
- Improved VMD importer support for morph, camera, and light animation.

### Removed
- Removed unused mocks.
- Removed parent-bone and destination selection buttons from the basic info panel.

### Known Issues
- Large models may have performance issues.
- Some PMX files may fail to import.
- Physics support is incomplete.
- PMD/PMX/VMD export is not implemented.

### Notes
- This is an early `0.x` release. Production use is not recommended.
- Please report bugs and feedback through [GitHub Issues](https://github.com/yohawing/maya_mmd_tools/issues).
