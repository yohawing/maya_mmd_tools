#!/usr/bin/env python
"""
PMXファイルダンパーのCLIスクリプト

使用例:
    # 基本的な使用
    mayapy tests/dump_pmx.py model.pmx
    
    # 出力ファイルを指定
    mayapy tests/dump_pmx.py model.pmx -o dump.txt
    
    # セクションを指定
    mayapy tests/dump_pmx.py model.pmx -s header statistics bones
    
    # 詳細モード
    mayapy tests/dump_pmx.py model.pmx -v
"""

import os
import sys

# プロジェクトのルートディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mmd_tools.tools.pmx_dumper import main

if __name__ == "__main__":
    main()