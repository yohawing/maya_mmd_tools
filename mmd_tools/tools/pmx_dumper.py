"""
PMXファイルの構造を人間とLLMが読みやすい独自テキスト形式でダンプするツール
"""

import argparse
import sys
from typing import List, Optional, TextIO

from mmd_tools.core import utils
from mmd_tools.core.pmx_data import PmxData


class PmxDumper:
    """PMXファイルの内容を人間が読みやすい形式でダンプするクラス"""

    def __init__(self, pmx_data: PmxData):
        """
        Args:
            pmx_data: 解析済みのPMXデータ
        """
        self.pmx = pmx_data

    def dump(
        self,
        output: Optional[TextIO] = None,
        sections: Optional[List[str]] = None,
    ) -> str:
        """
        PMXデータをダンプする

        Args:
            output: 出力先（None の場合は文字列として返す）
            sections: 出力するセクションのリスト（None の場合は全セクション）

        Returns:
            ダンプ結果の文字列（output が指定されている場合は空文字列）
        """
        buffer = []

        # 利用可能なセクション
        available_sections = {
            "header": self._dump_header,
            "statistics": self._dump_statistics,
            "bones": self._dump_bones,
            "morphs": self._dump_morphs,
            "materials": self._dump_materials,
            "physics": self._dump_physics,
            "vertices": self._dump_vertices,
        }

        # セクションが指定されていない場合はデフォルトセット
        if sections is None:
            sections = [
                "header",
                "statistics",
                "bones",
                "morphs",
                "materials",
                "physics",
                "vertices",
            ]

        # 各セクションを出力
        for section in sections:
            if section in available_sections:
                try:
                    content = available_sections[section]()
                    buffer.append(content)
                except Exception as e:
                    buffer.append(f"\n=== {section.upper()} [ERROR] ===")
                    buffer.append(f"Failed to dump section: {e}")

        result = "\n".join(buffer)

        if output:
            output.write(result)
            return ""
        else:
            return result

    def _dump_header(self) -> str:
        """ヘッダー情報をダンプ"""
        header = self.pmx.header
        encoding = utils.get_pmx_encoding_string(header.encoding)

        lines = [
            "=== PMX MODEL DEBUG DUMP ===",
            f"Version: {header.version} | Encoding: {encoding}",
            f"Model: {header.model_name} ({header.model_name_english})",
            f"Comment: {header.comment[:50]}... " if len(header.comment) > 50 else f"Comment: {header.comment}",
        ]

        lines.extend(
            [
                "\nIndex Sizes:",
                f"  Vertex: {header.vertex_index_size} bytes",
                f"  Texture: {header.texture_index_size} bytes",
                f"  Material: {header.material_index_size} bytes",
                f"  Bone: {header.bone_index_size} bytes",
                f"  Morph: {header.morph_index_size} bytes",
                f"  Rigid Body: {header.rigid_body_index_size} bytes",
                f"Additional UV: {header.additional_uv}",
            ]
        )

        return "\n".join(lines)

    def _dump_statistics(self) -> str:
        """統計情報をダンプ"""
        lines = [
            "\n=== STATISTICS ===",
            f"Vertices: {len(self.pmx.vertices):,} | Faces: {len(self.pmx.faces):,} | Materials: {len(self.pmx.materials)}",
            f"Bones: {len(self.pmx.bones)} | Morphs: {len(self.pmx.morphs)} | Display Frames: {len(self.pmx.display_frames)}",
            f"Rigid Bodies: {len(self.pmx.rigid_bodies)} | Joints: {len(self.pmx.joints)}",
        ]

        if self.pmx.vertices:
            # 頂点のウェイトタイプ別集計
            weight_types = {}
            for v in self.pmx.vertices:
                weight_type = v.weight_transform_type
                weight_types[weight_type] = weight_types.get(weight_type, 0) + 1

            lines.append("\nVertex Weight Types:")
            for wtype, count in sorted(weight_types.items()):
                wtype_name = {
                    0: "BDEF1",
                    1: "BDEF2",
                    2: "BDEF4",
                    3: "SDEF",
                    4: "QDEF",
                }.get(wtype, f"Unknown({wtype})")
                lines.append(f"  {wtype_name}: {count:,}")

        return "\n".join(lines)

    def _dump_bones(self) -> str:
        """ボーン情報をダンプ"""
        if not self.pmx.bones:
            return "\n=== BONES (0) ===\nNo bones found."

        lines = [f"\n=== BONE HIERARCHY ({len(self.pmx.bones)}) ==="]

        # ボーン階層を構築
        root_bones = []
        bone_children = {}

        for i, bone in enumerate(self.pmx.bones):
            if bone.parent_bone_index == -1:
                root_bones.append(i)
            else:
                if bone.parent_bone_index not in bone_children:
                    bone_children[bone.parent_bone_index] = []
                bone_children[bone.parent_bone_index].append(i)

        def dump_bone_tree(index: int, indent: str = "", is_last: bool = True, depth: int = 0) -> List[str]:
            bone = self.pmx.bones[index]
            tree_lines = []

            # フラグを解析
            flags = []
            if bone.bone_flag & 0x0020:  # IK
                flags.append("IK")
            if bone.bone_flag & 0x0002:  # 回転可能
                flags.append("ROT")
            if bone.bone_flag & 0x0004:  # 移動可能
                flags.append("MOVE")
            if bone.bone_flag & 0x0008:  # 表示
                flags.append("VIS")
            if bone.bone_flag & 0x0010:  # 操作可能
                flags.append("EN")

            flag_str = f" [{'+'.join(flags)}]" if flags else ""

            # 接続先情報を取得
            if bone.bone_flag & 0x0001:  # 接続先がボーン
                tail_str = f" → [{bone.connect_bone_index}]" if bone.connect_bone_index >= 0 else ""
            else:  # 接続先が相対座標
                tail_str = f" → offset({bone.connect_position_offset[0]:.1f}, {bone.connect_position_offset[1]:.1f}, {bone.connect_position_offset[2]:.1f})"

            prefix = "└─" if is_last else "├─"
            tree_lines.append(f"{indent}{prefix}[{index}] {bone.name} ({bone.name_english}){flag_str}{tail_str}")

            # 子ボーンを表示
            if index in bone_children:
                children = bone_children[index]
                for i, child_idx in enumerate(children):
                    is_last_child = i == len(children) - 1
                    child_indent = indent + ("    " if is_last else "│   ")
                    tree_lines.extend(dump_bone_tree(child_idx, child_indent, is_last_child, depth + 1))

            return tree_lines

        # ルートボーンから表示
        for i, root_idx in enumerate(root_bones):
            is_last = i == len(root_bones) - 1 or i == root_bones[-1]
            lines.extend(dump_bone_tree(root_idx, "", is_last))

        # IKボーンの詳細
        lines.append("\n=== BONE DETAILS ===")
        ik_bones = [b for b in self.pmx.bones if b.bone_flag & 0x0020]  # IKフラグが立っているボーン

        for bone in ik_bones:
            lines.append(f"\n[{self.pmx.bones.index(bone)}] {bone.name} ({bone.name_english})")
            parent_name = self.pmx.bones[bone.parent_bone_index].name if bone.parent_bone_index >= 0 else "None"
            lines.append(f"    Parent: [{bone.parent_bone_index}] {parent_name} | Layer: {bone.transform_layer}")
            lines.append(f"    Position: ({bone.position[0]:.3f}, {bone.position[1]:.3f}, {bone.position[2]:.3f})")
            lines.append(f"    Flags: 0x{bone.bone_flag:04X}")

            if bone.bone_flag & 0x0020:  # IKフラグが立っている場合
                lines.append(
                    f"    IK Target: [{bone.ik_target_bone_index}] | Loop: {bone.ik_loop_count} | Limit: {bone.ik_limit_angle:.2f} rad"
                )
                if bone.ik_links:
                    lines.append(f"    IK Links: {len(bone.ik_links)} links")
                    for link in bone.ik_links:
                        limit_str = ""
                        if hasattr(link, "angle_limit") and link.angle_limit:
                            limit_str = f" [角度制限] ({link.limit_min[0]:.2f}~{link.limit_max[0]:.2f}, {link.limit_min[1]:.2f}~{link.limit_max[1]:.2f}, {link.limit_min[2]:.2f}~{link.limit_max[2]:.2f})"
                        lines.append(f"        [{link.ik_bone_index}]{limit_str}")

        return "\n".join(lines)

    def _dump_morphs(self) -> str:
        """モーフ情報をダンプ"""
        if not self.pmx.morphs:
            return "\n=== MORPHS (0) ===\nNo morphs found."

        lines = [f"\n=== MORPHS ({len(self.pmx.morphs)}) ==="]

        # モーフタイプ別に集計
        morph_types = {}
        for morph in self.pmx.morphs:
            morph_type = morph.morph_type
            morph_types[morph_type] = morph_types.get(morph_type, 0) + 1

        lines.append("Morph Types:")
        type_names = {
            0: "Group",
            1: "Vertex",
            2: "Bone",
            3: "UV",
            4: "UV2",
            5: "UV3",
            6: "UV4",
            7: "UV5",
            8: "Material",
            9: "Flip",
            10: "Impulse",
        }
        for mtype, count in sorted(morph_types.items()):
            type_name = type_names.get(mtype, f"Unknown({mtype})")
            lines.append(f"  {type_name}: {count}")

        lines.append("\nMorph List:")
        for i, morph in enumerate(self.pmx.morphs):
            panel = {1: "眉", 2: "目", 3: "口", 4: "他"}.get(morph.panel, "?")
            type_name = type_names.get(morph.morph_type, "?")
            lines.append(f"  [{i}] {morph.name} ({morph.name_english}) - {type_name} [Panel: {panel}]")

        return "\n".join(lines)

    def _dump_materials(self) -> str:
        """材質情報をダンプ"""
        if not self.pmx.materials:
            return "\n=== MATERIALS (0) ===\nNo materials found."

        lines = [f"\n=== MATERIALS ({len(self.pmx.materials)}) ==="]

        for i, mat in enumerate(self.pmx.materials):
            lines.append(f"\n[{i}] {mat.name} ({mat.name_english})")
            lines.append(
                f"    Diffuse: ({mat.diffuse[0]:.2f}, {mat.diffuse[1]:.2f}, {mat.diffuse[2]:.2f}, {mat.diffuse[3]:.2f})"
            )
            lines.append(
                f"    Specular: ({mat.specular[0]:.2f}, {mat.specular[1]:.2f}, {mat.specular[2]:.2f}) Power: {mat.specular_coefficient}"
            )
            lines.append(f"    Ambient: ({mat.ambient[0]:.2f}, {mat.ambient[1]:.2f}, {mat.ambient[2]:.2f})")

            # フラグ
            flags = []
            if mat.draw_flag & 0x01:  # 両面描画
                flags.append("DoubleSide")
            if mat.draw_flag & 0x02:  # 地面影
                flags.append("Shadow")
            if mat.draw_flag & 0x08:  # セルフシャドウ
                flags.append("SelfShadow")
            if mat.draw_flag & 0x10:  # エッジ描画
                flags.append("Edge")

            lines.append(f"    Flags: {' | '.join(flags) if flags else 'None'}")

            # テクスチャ
            if mat.texture_index >= 0:
                tex_path = self.pmx.textures[mat.texture_index] if mat.texture_index < len(self.pmx.textures) else "[Invalid]"
                lines.append(f"    Texture: [{mat.texture_index}] {tex_path}")

            lines.append(f"    Face Count: {mat.face_count // 3:,}")

        return "\n".join(lines)

    def _dump_physics(self) -> str:
        """物理演算情報をダンプ"""
        lines = ["\n=== PHYSICS ==="]

        if self.pmx.rigid_bodies:
            lines.append(f"Rigid Bodies: {len(self.pmx.rigid_bodies)}")

            # 形状タイプ別集計
            shape_types = {}
            for rb in self.pmx.rigid_bodies:
                shape = rb.shape_type
                shape_types[shape] = shape_types.get(shape, 0) + 1

            lines.append("  Shape Types:")
            shape_names = {0: "Sphere", 1: "Box", 2: "Capsule"}
            for stype, count in sorted(shape_types.items()):
                shape_name = shape_names.get(stype, f"Unknown({stype})")
                lines.append(f"    {shape_name}: {count}")

            # 物理タイプ別集計
            physics_modes = {}
            for rb in self.pmx.rigid_bodies:
                mode = rb.physics_mode
                physics_modes[mode] = physics_modes.get(mode, 0) + 1

            lines.append("  Physics Modes:")
            mode_names = {0: "Static", 1: "Dynamic", 2: "Dynamic + Bone"}
            for mode, count in sorted(physics_modes.items()):
                mode_name = mode_names.get(mode, f"Unknown({mode})")
                lines.append(f"    {mode_name}: {count}")
        else:
            lines.append("Rigid Bodies: 0")

        if self.pmx.joints:
            lines.append(f"\nJoints: {len(self.pmx.joints)}")

            if self.pmx.joints:
                lines.append("  Joint Samples:")
                for i, joint in enumerate(self.pmx.joints):
                    lines.append(f"    [{i}] {joint.name} ({joint.name_english})")
                    lines.append(f"        Rigid Bodies: [{joint.rigid_body_a_index}] <-> [{joint.rigid_body_b_index}]")
        else:
            lines.append("Joints: 0")

        return "\n".join(lines)

    def _dump_vertices(self) -> str:
        """頂点情報をダンプ"""
        if not self.pmx.vertices:
            return "\n=== VERTICES (0) ===\nNo vertices found."

        lines = [f"\n=== VERTICES ({len(self.pmx.vertices):,}) ==="]

        # 座標範囲を計算
        min_pos = [float("inf")] * 3
        max_pos = [float("-inf")] * 3

        for v in self.pmx.vertices:
            for i in range(3):
                min_pos[i] = min(min_pos[i], v.position[i])
                max_pos[i] = max(max_pos[i], v.position[i])

        lines.append("Position Range:")
        lines.append(f"  X: {min_pos[0]:.3f} ~ {max_pos[0]:.3f} (width: {max_pos[0] - min_pos[0]:.3f})")
        lines.append(f"  Y: {min_pos[1]:.3f} ~ {max_pos[1]:.3f} (height: {max_pos[1] - min_pos[1]:.3f})")
        lines.append(f"  Z: {min_pos[2]:.3f} ~ {max_pos[2]:.3f} (depth: {max_pos[2] - min_pos[2]:.3f})")

        # サンプル頂点（最大5個）
        lines.append("\nVertex Samples:")
        for i, v in enumerate(self.pmx.vertices[:5]):
            deform_type = {
                0: "BDEF1",
                1: "BDEF2",
                2: "BDEF4",
                3: "SDEF",
                4: "QDEF",
            }.get(v.weight_transform_type, "?")
            lines.append(
                f"  [{i}] Pos: ({v.position[0]:.3f}, {v.position[1]:.3f}, {v.position[2]:.3f}) | Normal: ({v.normal[0]:.2f}, {v.normal[1]:.2f}, {v.normal[2]:.2f}) | UV: ({v.uv[0]:.3f}, {v.uv[1]:.3f}) | Deform: {deform_type}"
            )

        if len(self.pmx.vertices) > 5:
            lines.append(f"  ... and {len(self.pmx.vertices) - 5:,} more vertices")

        return "\n".join(lines)


def main():
    """CLIエントリーポイント"""
    parser = argparse.ArgumentParser(description="PMXファイルの構造を人間が読みやすい形式でダンプします")
    parser.add_argument("pmx_file", help="ダンプするPMXファイルのパス")
    parser.add_argument("-o", "--output", help="出力ファイルパス（指定しない場合は標準出力）")
    parser.add_argument(
        "-s",
        "--sections",
        nargs="+",
        choices=[
            "header",
            "statistics",
            "bones",
            "morphs",
            "materials",
            "physics",
            "vertices",
        ],
        help="出力するセクションを指定（デフォルト: header statistics bones morphs materials physics）",
    )

    args = parser.parse_args()

    try:
        # PMXファイルを解析
        pmx_parser = PmxData()
        pmx_parser.parse_file(args.pmx_file)

        # ダンパーを作成
        dumper = PmxDumper(pmx_parser)

        # 出力先を決定
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                dumper.dump(f, sections=args.sections)
            print(f"Dump completed: {args.output}")
        else:
            print(dumper.dump(sections=args.sections))

    except Exception as e:
        print(f"Failed to dump PMX file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
