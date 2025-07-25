"""
テストフィクスチャプロバイダーを提供するモジュール
"""

import os
import tempfile
from typing import List, Dict, Union, Optional

from mmd_tools.core.pmx_parser import PmxParser
from mmd_tools.core.vmd_parser import VmdParser
from mmd_tools.core.pmd_parser import PmdParser


class TestFixtureProvider:
    """テストフィクスチャを提供するクラス"""

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
            FileNotFoundError: ファイルが見つからない場合
        """
        pmd_files = self._file_cache.get("pmd", {})
        if not pmd_files:
            raise FileNotFoundError("No PMD files found")

        if name is None:
            return next(iter(pmd_files.values()))

        if name in pmd_files:
            return pmd_files[name]

        raise FileNotFoundError(f"PMD file '{name}' not found")

    def get_pmx_file(self, name: str = None) -> str:
        """PMXファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        pmx_files = self._file_cache.get("pmx", {})
        if not pmx_files:
            raise FileNotFoundError("No PMX files found")

        if name is None:
            return next(iter(pmx_files.values()))

        if name in pmx_files:
            return pmx_files[name]

        raise FileNotFoundError(f"PMX file '{name}' not found")

    def get_vmd_file(self, name: str = None) -> str:
        """VMDファイルのパスを取得

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            str: ファイルパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
        """
        vmd_files = self._file_cache.get("vmd", {})
        if not vmd_files:
            raise FileNotFoundError("No VMD files found")

        if name is None:
            return next(iter(vmd_files.values()))

        if name in vmd_files:
            return vmd_files[name]

        raise FileNotFoundError(f"VMD file '{name}' not found")

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
        return {
            "pmd": self.get_all_pmd_files(),
            "pmx": self.get_all_pmx_files()
        }

    def load_pmd_data(self, name: str = None) -> dict:
        """PMDファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            dict: パース済みデータ
        """
        file_path = self.get_pmd_file(name)
        cache_key = f"pmd_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード
            pmd_parser = PmdParser()
            pmd_data = pmd_parser.parse_file(file_path)
            self._data_cache[cache_key] = {"file_path": file_path, "data": pmd_data}

        return self._data_cache[cache_key]

    def load_pmx_data(self, name: str = None) -> dict:
        """PMXファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            dict: パース済みデータ
        """
        file_path = self.get_pmx_file(name)
        cache_key = f"pmx_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード

            pmx_parser = PmxParser()
            pmx_data = pmx_parser.parse_file(file_path)
            self._data_cache[cache_key] = {"file_path": file_path, "data": pmx_data}

        return self._data_cache[cache_key]

    def load_vmd_data(self, name: str = None) -> dict:
        """VMDファイルをロードしてパース済みデータを返す（キャッシュあり）

        Args:
            name: 特定のファイル名（拡張子なし）。Noneの場合は最初に見つかったファイル

        Returns:
            dict: パース済みデータ
        """
        file_path = self.get_vmd_file(name)
        cache_key = f"vmd_{file_path}"

        if cache_key not in self._data_cache:
            # 実際のパーサーを使用してデータをロード

            vmd_parser = VmdParser()
            vmd_data = vmd_parser.parse_file(file_path)
            self._data_cache[cache_key] = {"file_path": file_path, "data": vmd_data}

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
