"""Test fixture discovery and integrity verification helpers."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import List, Dict

from mmd_tools.core.mmd_parser import parse_pmx_file
from mmd_tools.core.vmd_data import VmdData
from mmd_tools.core.pmd_data import PmdData


class TestFixtureProvider:
    """テストフィクスチャを提供するクラス"""

    # Manifest names are intentionally explicit.  A fixture is not considered
    # registered merely because a similarly named PMX happens to be present.
    _FIXTURE_MANIFESTS = {
        "yw_test_model": "yw_test_model.fixture.json",
        "yw_test_model_control_rig_vmd": "yw_test_model_control_rig_vmd.fixture.json",
        "yw_test_model_control_rig_bone_morph": "yw_test_model_control_rig_bone_morph.fixture.json",
    }

    def __init__(self, data_dir: str = None):
        """TestFixtureProviderを初期化

        Args:
            data_dir: テストデータディレクトリ（Noneの場合はデフォルト）
        """
        self._data_dir = data_dir or self._get_default_data_dir()
        self._file_cache = {}
        self._data_cache = {}
        self._temp_files = []

        # 初期化時に一度だけファイル探索を実行
        self._scan_files()

    def get_registered_fixture_names(self) -> List[str]:
        """Return stable names for manifest-backed regression fixtures."""
        return sorted(self._FIXTURE_MANIFESTS)

    def get_fixture_manifest(self, name: str) -> Dict:
        """Load a registered fixture manifest from ``tests/data``.

        Missing manifests are errors rather than optional skips: a registered
        fixture must fail closed when its declaration is not available.

        Args:
            name: Stable fixture name.

        Returns:
            Parsed manifest dictionary.

        Raises:
            KeyError: If ``name`` is not registered.
            FileNotFoundError: If the registered manifest is missing.
            ValueError: If the manifest is malformed or names another fixture.
        """
        try:
            filename = self._FIXTURE_MANIFESTS[name]
        except KeyError as exc:
            raise KeyError(f"Unknown fixture '{name}'") from exc
        manifest_path = Path(self._data_dir) / filename
        if not manifest_path.is_file():
            raise FileNotFoundError(f"Fixture manifest not found: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Invalid fixture manifest: {manifest_path}") from exc
        if not isinstance(manifest, dict) or manifest.get("name") != name:
            raise ValueError(f"Fixture manifest name mismatch: {manifest_path}")
        if not isinstance(manifest.get("files"), list) or not manifest["files"]:
            raise ValueError(f"Fixture manifest has no files: {manifest_path}")
        return manifest

    def get_verified_fixture(self, name: str) -> Dict:
        """Verify every manifest file and return its resolved paths.

        Paths are resolved relative to the provider data directory and are
        rejected if they escape it.  Both size and SHA-256 are checked so a
        local replacement cannot silently satisfy the regression gate.

        Args:
            name: Stable fixture name.

        Returns:
            A dictionary containing the parsed ``manifest`` and ``files`` map.

        Raises:
            FileNotFoundError: If a declared file is absent.
            ValueError: If a path, size, or digest does not match.
        """
        manifest = self.get_fixture_manifest(name)
        root = Path(self._data_dir).resolve()
        resolved_files = {}
        for entry in manifest["files"]:
            if not isinstance(entry, dict):
                raise ValueError(f"Malformed file entry in fixture '{name}'")
            relative = entry.get("path")
            expected_size = entry.get("size")
            expected_sha256 = entry.get("sha256")
            if not isinstance(relative, str) or not isinstance(expected_size, int) or not isinstance(expected_sha256, str):
                raise ValueError(f"Malformed file entry in fixture '{name}': {entry!r}")
            candidate = (root / relative).resolve()
            if candidate != root and root not in candidate.parents:
                raise ValueError(f"Fixture path escapes data directory: {relative}")
            if not candidate.is_file():
                raise FileNotFoundError(f"Fixture file not found: {candidate}")
            if candidate.stat().st_size != expected_size:
                raise ValueError(
                    f"Fixture size mismatch for {relative}: "
                    f"expected {expected_size}, got {candidate.stat().st_size}"
                )
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest.lower() != expected_sha256.lower():
                raise ValueError(
                    f"Fixture SHA-256 mismatch for {relative}: "
                    f"expected {expected_sha256}, got {digest}"
                )
            resolved_files[relative] = str(candidate)
        return {"manifest": manifest, "files": resolved_files}

    def get_verified_pmx_file(self, name: str = "yw_test_model") -> str:
        """Return the PMX path after manifest and hash verification."""
        verified = self.get_verified_fixture(name)
        for entry in verified["manifest"]["files"]:
            if entry.get("kind") == "pmx":
                return verified["files"][entry["path"]]
        raise ValueError(f"Fixture '{name}' has no PMX file entry")

    def get_verified_vmd_file(self, name: str = "yw_test_model_control_rig_vmd") -> str:
        """Return a manifest-backed VMD path after size and SHA-256 checks."""
        verified = self.get_verified_fixture(name)
        for entry in verified["manifest"]["files"]:
            if entry.get("kind") == "vmd":
                return verified["files"][entry["path"]]
        raise ValueError(f"Fixture '{name}' has no VMD file entry")

    def get_verified_source_file(self, name: str = "yw_test_model_control_rig_vmd") -> str:
        """Return deterministic fixture source JSON after manifest verification."""
        verified = self.get_verified_fixture(name)
        for entry in verified["manifest"]["files"]:
            if entry.get("kind") == "source":
                return verified["files"][entry["path"]]
        raise ValueError(f"Fixture '{name}' has no source file entry")

    def _get_default_data_dir(self) -> str:
        """デフォルトのデータディレクトリを取得

        Returns:
            str: デフォルトのデータディレクトリパス
        """
        # tests/data ディレクトリを取得
        current_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(current_dir), "data")

    def _scan_files(self):
        """ディレクトリを再帰的に探索してファイルキャッシュを作成"""
        if not os.path.exists(self._data_dir):
            return

        for root, dirs, files in os.walk(self._data_dir):
            for file in files:
                file_path = os.path.join(root, file)
                file_name = os.path.splitext(file)[0]
                ext = os.path.splitext(file)[1].lower()

                if ext == ".pmd":
                    self._file_cache.setdefault("pmd", {})[file_name] = file_path
                elif ext == ".pmx":
                    self._file_cache.setdefault("pmx", {})[file_name] = file_path
                elif ext == ".vmd":
                    self._file_cache.setdefault("vmd", {})[file_name] = file_path
                elif ext in [".png", ".jpg", ".jpeg", ".bmp", ".tga"]:
                    self._file_cache.setdefault("texture", {})[file_name] = file_path

    def get_pmd_file(self, name: str = None) -> str:
        """PMDファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            unittest.SkipTest: テスト用ファイルが見つからない場合
        """
        pmd_files = self._file_cache.get("pmd", {})
        if not pmd_files:
            raise unittest.SkipTest("No PMD files found")

        if name is None:
            return next(iter(pmd_files.values()))

        if name in pmd_files:
            return pmd_files[name]

        raise unittest.SkipTest(f"PMD file '{name}' not found")

    def get_pmx_file(self, name: str = None) -> str:
        """PMXファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            unittest.SkipTest: テスト用ファイルが見つからない場合
        """
        pmx_files = self._file_cache.get("pmx", {})
        if not pmx_files:
            raise unittest.SkipTest("No PMX files found")

        if name is None:
            return next(iter(pmx_files.values()))

        if name in pmx_files:
            return pmx_files[name]

        raise unittest.SkipTest(f"PMX file '{name}' not found")

    def get_vmd_file(self, name: str = None) -> str:
        """VMDファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            unittest.SkipTest: テスト用ファイルが見つからない場合
        """
        vmd_files = self._file_cache.get("vmd", {})
        if not vmd_files:
            raise unittest.SkipTest("No VMD files found")

        if name is None:
            return next(iter(vmd_files.values()))

        if name in vmd_files:
            return vmd_files[name]

        raise unittest.SkipTest(f"VMD file '{name}' not found")

    def get_texture_file(self, model_name: str, texture_name: str) -> str:
        """テクスチャファイルのパスを取得

        Args:
            model_name: モデル名
            texture_name: テクスチャ名

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        texture_files = self._file_cache.get("texture", {})
        if texture_name in texture_files:
            return texture_files[texture_name]

        raise FileNotFoundError(f"Texture file '{texture_name}' not found")

    def get_available_pmd_files(self) -> List[str]:
        """利用可能なPMDファイルの一覧を取得

        Returns:
            List[str]: 利用可能なPMDファイル名のリスト
        """
        return list(self._file_cache.get("pmd", {}).keys())

    def get_available_pmx_files(self) -> List[str]:
        """利用可能なPMXファイルの一覧を取得

        Returns:
            List[str]: 利用可能なPMXファイル名のリスト
        """
        return list(self._file_cache.get("pmx", {}).keys())

    def get_available_vmd_files(self) -> List[str]:
        """利用可能なVMDファイルの一覧を取得

        Returns:
            List[str]: 利用可能なVMDファイル名のリスト
        """
        return list(self._file_cache.get("vmd", {}).keys())

    def get_all_pmd_files(self) -> Dict[str, str]:
        """全てのPMDファイルを取得

        Returns:
            Dict[str, str]: ファイル名をキー、ファイルパスを値とする辞書
        """
        return self._file_cache.get("pmd", {}).copy()

    def get_all_pmx_files(self) -> Dict[str, str]:
        """全てのPMXファイルを取得

        Returns:
            Dict[str, str]: ファイル名をキー、ファイルパスを値とする辞書
        """
        return self._file_cache.get("pmx", {}).copy()

    def get_all_vmd_files(self) -> Dict[str, str]:
        """全てのVMDファイルを取得

        Returns:
            Dict[str, str]: ファイル名をキー、ファイルパスを値とする辞書
        """
        return self._file_cache.get("vmd", {}).copy()

    def get_all_model_files(self) -> Dict[str, Dict[str, str]]:
        """全てのモデルファイル（PMDとPMX）を取得

        Returns:
            Dict[str, Dict[str, str]]: フォーマットをキー、ファイル辞書を値とする辞書
                例: {
                    "pmd": {"miku_v2": "/path/to/miku_v2.pmd"},
                    "pmx": {"model1": "/path/to/model1.pmx"}
                }
        """
        return {"pmd": self.get_all_pmd_files(), "pmx": self.get_all_pmx_files()}

    def load_pmd_data(self, name: str = None) -> tuple:
        """PMDファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            tuple: パース済みデータ
        """
        file_path = self.get_pmd_file(name)
        cache_key = f"pmd_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード
            pmd_parser = PmdData()
            pmd_data = pmd_parser.parse_file(file_path)
            self._data_cache[cache_key] = (pmd_data, file_path)

        return self._data_cache[cache_key]

    def load_pmx_data(self, name: str = None) -> tuple:
        """PMXファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            tuple: パース済みデータ
        """
        file_path = self.get_pmx_file(name)
        cache_key = f"pmx_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード

            pmx_data = parse_pmx_file(file_path)
            self._data_cache[cache_key] = (pmx_data, file_path)

        return self._data_cache[cache_key]

    def load_vmd_data(self, name: str = None) -> tuple:
        """VMDファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル
        Returns:
            tuple: パース済みデータ
        """
        file_path = self.get_vmd_file(name)
        cache_key = f"vmd_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード

            vmd_parser = VmdData()
            vmd_data = vmd_parser.parse_file(file_path)
            self._data_cache[cache_key] = (vmd_data, file_path)

        return self._data_cache[cache_key]

    def create_temp_file(self, content: bytes, extension: str) -> str:
        """一時ファイルを作成してパスを返す

        Args:
            content: ファイル内容
            extension: ファイル拡張子

        Returns:
            str: 一時ファイルのパス
        """
        fd, temp_path = tempfile.mkstemp(suffix=extension)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content)
            self._temp_files.append(temp_path)
            return temp_path
        except:
            os.close(fd)
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def cleanup_temp_files(self):
        """作成した一時ファイルを削除"""
        for temp_path in self._temp_files:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        self._temp_files.clear()
