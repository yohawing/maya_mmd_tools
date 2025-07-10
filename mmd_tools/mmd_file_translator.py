"""
Maya File Translator for MMD formats
Mayaの標準Import/Exportダイアログに MMD形式を追加する
"""

import maya.api.OpenMaya as om
import maya.api.OpenMayaMPx as ommpx
import maya.cmds as cmds
import sys
import os


class MmdFileTranslator(ommpx.MPxFileTranslator):
    """MMDファイル用のMayaファイルトランスレーター"""
    
    # プラグインの識別情報
    kPluginTranslatorTypeName = "MMD Model"
    kPluginTranslatorTypeId = om.MTypeId(0x00001234)  # 一意のID
    
    def __init__(self):
        ommpx.MPxFileTranslator.__init__(self)
    
    @staticmethod
    def creator():
        """ファイルトランスレーターのインスタンスを作成"""
        return MmdFileTranslator()
    
    def haveReadMethod(self):
        """読み込み（インポート）をサポートするか"""
        return True
    
    def haveWriteMethod(self):
        """書き込み（エクスポート）をサポートするか"""
        return True
    
    def haveNamespaceSupport(self):
        """ネームスペースをサポートするか"""
        return True
    
    def haveReferenceMethod(self):
        """リファレンスをサポートするか"""
        return False
    
    def filter(self):
        """ファイルフィルター（拡張子）を返す"""
        return "*.pmx;*.pmd"
    
    def defaultExtension(self):
        """デフォルトの拡張子を返す"""
        return "pmx"
    
    def canBeOpened(self):
        """ファイルを開くことができるか"""
        return True
    
    def doRead(self, file_object, options_string, access_mode):
        """
        ファイル読み込み（インポート）処理
        
        Args:
            file_object: ファイルオブジェクト
            options_string: オプション文字列
            access_mode: アクセスモード
            
        Returns:
            bool: 成功/失敗
        """
        try:
            file_path = file_object.fullName()
            
            # オプションを解析
            options = self._parse_options(options_string)
            
            # ファイル拡張子で処理を分岐
            if file_path.lower().endswith('.pmx'):
                return self._import_pmx(file_path, options)
            elif file_path.lower().endswith('.pmd'):
                return self._import_pmd(file_path, options)
            else:
                om.MGlobal.displayError(f"サポートされていないファイル形式: {file_path}")
                return False
                
        except Exception as e:
            om.MGlobal.displayError(f"インポート中にエラーが発生しました: {str(e)}")
            return False
    
    def doWrite(self, file_object, options_string, access_mode):
        """
        ファイル書き込み（エクスポート）処理
        
        Args:
            file_object: ファイルオブジェクト
            options_string: オプション文字列
            access_mode: アクセスモード
            
        Returns:
            bool: 成功/失敗
        """
        try:
            file_path = file_object.fullName()
            
            # オプションを解析
            options = self._parse_options(options_string)
            
            # ファイル拡張子で処理を分岐
            if file_path.lower().endswith('.pmx'):
                return self._export_pmx(file_path, options)
            elif file_path.lower().endswith('.pmd'):
                return self._export_pmd(file_path, options)
            else:
                om.MGlobal.displayError(f"サポートされていないファイル形式: {file_path}")
                return False
                
        except Exception as e:
            om.MGlobal.displayError(f"エクスポート中にエラーが発生しました: {str(e)}")
            return False
    
    def _parse_options(self, options_string):
        """
        オプション文字列を解析してディクショナリに変換
        
        Args:
            options_string: セミコロン区切りのオプション文字列
            
        Returns:
            dict: オプション辞書
        """
        options = {}
        if not options_string:
            return options
        
        for option in options_string.split(';'):
            if '=' in option:
                key, value = option.split('=', 1)
                options[key.strip()] = value.strip()
            else:
                options[option.strip()] = True
        
        return options
    
    def _import_pmx(self, file_path, options):
        """PMXファイルをインポート"""
        try:
            from mmd_tools.io.mmd_importer import import_mmd_file
            
            # 統一されたMMDインポーターを使用
            success = import_mmd_file(file_path)
            
            if success:
                om.MGlobal.displayInfo(f"PMXファイルのインポートが完了しました: {file_path}")
                return True
            else:
                om.MGlobal.displayError(f"PMXファイルのインポートに失敗しました: {file_path}")
                return False
                
        except ImportError as e:
            om.MGlobal.displayError(f"MMD Importer モジュールが見つかりません: {str(e)}")
            return False
        except Exception as e:
            om.MGlobal.displayError(f"PMXインポート中にエラーが発生しました: {str(e)}")
            return False
    
    def _import_pmd(self, file_path, options):
        """PMDファイルをインポート"""
        try:
            from mmd_tools.io.mmd_importer import import_mmd_file
            
            # 統一されたMMDインポーターを使用
            success = import_mmd_file(file_path)
            
            if success:
                om.MGlobal.displayInfo(f"PMDファイルのインポートが完了しました: {file_path}")
                return True
            else:
                om.MGlobal.displayError(f"PMDファイルのインポートに失敗しました: {file_path}")
                return False
                
        except ImportError as e:
            om.MGlobal.displayError(f"MMD Importer モジュールが見つかりません: {str(e)}")
            return False
        except Exception as e:
            om.MGlobal.displayError(f"PMDインポート中にエラーが発生しました: {str(e)}")
            return False
    
    def _export_pmx(self, file_path, options):
        """PMXファイルをエクスポート"""
        try:
            from mmd_tools.io.pmx_exporter import PmxExporter
            
            # exporter = PmxExporter()
            # success = exporter.export_model(file_path, options)
            
            # if success:
            #     om.MGlobal.displayInfo(f"PMXファイルのエクスポートが完了しました: {file_path}")
            #     return True
            # else:
            #     om.MGlobal.displayError(f"PMXファイルのエクスポートに失敗しました: {file_path}")
            #     return False
                
        except ImportError:
            om.MGlobal.displayError("PMX Exporter モジュールが見つかりません")
            return False
    
    def _export_pmd(self, file_path, options):
        """PMDファイルをエクスポート"""
        try:
            from mmd_tools.io.pmd_exporter import PmdExporter
            
            # exporter = PmdExporter()
            # success = exporter.export_model(file_path, options)
            
            # if success:
            #     om.MGlobal.displayInfo(f"PMDファイルのエクスポートが完了しました: {file_path}")
            #     return True
            # else:
            #     om.MGlobal.displayError(f"PMDファイルのエクスポートに失敗しました: {file_path}")
            #     return False
                
        except ImportError:
            om.MGlobal.displayError("PMD Exporter モジュールが見つかりません")
            return False


class VmdFileTranslator(ommpx.MPxFileTranslator):
    """VMDファイル用のMayaファイルトランスレーター"""
    
    kPluginTranslatorTypeName = "MMD Motion"
    kPluginTranslatorTypeId = om.MTypeId(0x00001235)
    
    def __init__(self):
        ommpx.MPxFileTranslator.__init__(self)
    
    @staticmethod
    def creator():
        return VmdFileTranslator()
    
    def haveReadMethod(self):
        return True
    
    def haveWriteMethod(self):
        return True
    
    def haveNamespaceSupport(self):
        return False
    
    def haveReferenceMethod(self):
        return False
    
    def filter(self):
        return "*.vmd"
    
    def defaultExtension(self):
        return "vmd"
    
    def canBeOpened(self):
        return False  # VMDは独立したシーンファイルではない
    
    def doRead(self, file_object, options_string, access_mode):
        """VMDファイル読み込み処理"""
        try:
            file_path = file_object.fullName()
            options = self._parse_options(options_string)
            
            from mmd_tools.io.mmd_importer import import_mmd_file
            
            # 統一されたMMDインポーターを使用
            success = import_mmd_file(file_path)
            
            if success:
                om.MGlobal.displayInfo(f"VMDファイルのインポートが完了しました: {file_path}")
                return True
            else:
                om.MGlobal.displayError(f"VMDファイルのインポートに失敗しました: {file_path}")
                return False
                
        except Exception as e:
            om.MGlobal.displayError(f"VMDインポート中にエラーが発生しました: {str(e)}")
            return False
    
    def doWrite(self, file_object, options_string, access_mode):
        """VMDファイル書き込み処理"""
        try:
            file_path = file_object.fullName()
            options = self._parse_options(options_string)
            
            from mmd_tools.io.vmd_exporter import VmdExporter
            
            exporter = VmdExporter()
            # success = exporter.export_motion(file_path, options)
            
            # if success:
            #     om.MGlobal.displayInfo(f"VMDファイルのエクスポートが完了しました: {file_path}")
            #     return True
            # else:
            #     om.MGlobal.displayError(f"VMDファイルのエクスポートに失敗しました: {file_path}")
            #     return False
                
        except Exception as e:
            om.MGlobal.displayError(f"VMDエクスポート中にエラーが発生しました: {str(e)}")
            return False
    
    def _parse_options(self, options_string):
        """オプション文字列を解析"""
        options = {}
        if not options_string:
            return options
        
        for option in options_string.split(';'):
            if '=' in option:
                key, value = option.split('=', 1)
                options[key.strip()] = value.strip()
            else:
                options[option.strip()] = True
        
        return options


def register_file_translators(plugin):
    """ファイルトランスレーターを登録"""
    try:
        # MMDモデル用トランスレーターを登録
        plugin.registerFileTranslator(
            MmdFileTranslator.kPluginTranslatorTypeName,
            None,  # 作成関数は None でも可
            MmdFileTranslator.creator,
            None,  # オプションスクリプト（なし）
            None,  # デフォルトオプション（なし）
            True   # canBeOpened
        )
        
        # VMDモーション用トランスレーターを登録
        plugin.registerFileTranslator(
            VmdFileTranslator.kPluginTranslatorTypeName,
            None,
            VmdFileTranslator.creator,
            None,
            None,
            False  # canBeOpened = False (VMDはシーンファイルではない)
        )
        
        om.MGlobal.displayInfo("MMD File Translators が正常に登録されました")
        
    except Exception as e:
        om.MGlobal.displayError(f"ファイルトランスレーター登録中にエラーが発生しました: {str(e)}")


def unregister_file_translators(plugin):
    """ファイルトランスレーターの登録を解除"""
    try:
        plugin.deregisterFileTranslator(MmdFileTranslator.kPluginTranslatorTypeName)
        plugin.deregisterFileTranslator(VmdFileTranslator.kPluginTranslatorTypeName)
        
        om.MGlobal.displayInfo("MMD File Translators が正常に登録解除されました")
        
    except Exception as e:
        om.MGlobal.displayError(f"ファイルトランスレーター登録解除中にエラーが発生しました: {str(e)}")