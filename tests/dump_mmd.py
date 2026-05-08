#!/usr/bin/env python
"""
MMDファイル（PMX/VMD）ダンパーのCLIスクリプト

使用例:
    # PMXファイルをダンプ
    mayapy tests/dump_mmd.py model.pmx

    # VMDファイルをダンプ
    mayapy tests/dump_mmd.py motion.vmd

    # 出力ファイルを指定
    mayapy tests/dump_mmd.py model.pmx -o dump.txt
    mayapy tests/dump_mmd.py motion.vmd -o motion_dump.txt

    # PMXのセクションを指定
    mayapy tests/dump_mmd.py model.pmx -s header statistics bones

    # VMDのセクションを指定
    mayapy tests/dump_mmd.py motion.vmd -s header statistics bones morphs

"""

import argparse
import os
import sys

# プロジェクトのルートディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mmd_tools.tools.pmx_dumper import PmxDumper
from mmd_tools.tools.vmd_dumper import VmdDumper
from mmd_tools.core.pmx_data import PmxData
from mmd_tools.core.vmd_data import VmdData


def detect_file_type(file_path):
    """
    ファイルの拡張子からファイルタイプを判定

    Args:
        file_path: ファイルパス

    Returns:
        'pmx' または 'vmd'、判定できない場合は None
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in [".pmx", ".pmd"]:
        return "pmx"
    elif ext == ".vmd":
        return "vmd"
    return None


def main():
    """CLIエントリーポイント"""
    parser = argparse.ArgumentParser(description="MMDファイル（PMX/VMD）の構造を人間が読みやすい形式でダンプします")
    parser.add_argument("mmd_file", help="ダンプするMMDファイル（PMX/VMD）のパス")
    parser.add_argument("-o", "--output", help="出力ファイルパス（指定しない場合は標準出力）")
    parser.add_argument(
        "-t",
        "--type",
        choices=["pmx", "vmd"],
        help="ファイルタイプを明示的に指定（自動検出が失敗する場合）",
    )

    # PMX用セクション
    pmx_sections = ["header", "statistics", "bones", "morphs", "materials", "physics", "vertices"]
    # VMD用セクション
    vmd_sections = ["header", "statistics", "bones", "morphs", "camera", "light", "shadow", "ikdisplay"]

    parser.add_argument(
        "-s",
        "--sections",
        nargs="+",
        help=f"出力するセクションを指定\nPMX: {', '.join(pmx_sections)}\nVMD: {', '.join(vmd_sections)}",
    )

    args = parser.parse_args()

    # ファイルタイプを判定
    file_type = args.type
    if not file_type:
        file_type = detect_file_type(args.mmd_file)
        if not file_type:
            print(f"Error: Unable to detect file type for '{args.mmd_file}'")
            print("Please specify file type with -t/--type option (pmx or vmd)")
            sys.exit(1)

    try:
        if file_type == "pmx":
            # PMXファイルを処理
            pmx_parser = PmxData()
            pmx_parser.parse_file(args.mmd_file)
            dumper = PmxDumper(pmx_parser)

            # セクションの検証
            if args.sections:
                invalid_sections = [s for s in args.sections if s not in pmx_sections]
                if invalid_sections:
                    print(f"Warning: Invalid sections for PMX: {', '.join(invalid_sections)}")
                    print(f"Valid sections: {', '.join(pmx_sections)}")
                    valid_sections = [s for s in args.sections if s in pmx_sections]
                    if not valid_sections:
                        print("Error: No valid sections specified")
                        sys.exit(1)
                    args.sections = valid_sections

        elif file_type == "vmd":
            # VMDファイルを処理
            vmd_parser = VmdData()
            vmd_parser.parse_file(args.mmd_file)
            dumper = VmdDumper(vmd_parser)

            # セクションの検証
            if args.sections:
                invalid_sections = [s for s in args.sections if s not in vmd_sections]
                if invalid_sections:
                    print(f"Warning: Invalid sections for VMD: {', '.join(invalid_sections)}")
                    print(f"Valid sections: {', '.join(vmd_sections)}")
                    valid_sections = [s for s in args.sections if s in vmd_sections]
                    if not valid_sections:
                        print("Error: No valid sections specified")
                        sys.exit(1)
                    args.sections = valid_sections

        # 出力先を決定
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                dumper.dump(f, sections=args.sections)
            print(f"Dump completed: {args.output}")
        else:
            print(dumper.dump(sections=args.sections))

    except Exception as e:
        print(f"Failed to dump {file_type.upper()} file: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
