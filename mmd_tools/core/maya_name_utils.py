"""Maya-safe name normalization helpers."""

from . import utils


def sanitize_text(name):
    """
    Maya用に名前をサニタイズする。
    日本語などのマルチバイト文字をASCII文字に変換し、Maya互換の名前にする。
    """
    if not name:
        return "unnamed"

    converted_name = utils.convert_utf8_to_ascii(name)
    return converted_name or "default_name"


def sanitize_bone_name(name):
    """Maya用にMMD/PMXボーン名をサニタイズする。"""
    if not name:
        return "unnamed"

    from .mmd_bone_names import convert_mmd_bone_name_to_ascii

    converted_name = convert_mmd_bone_name_to_ascii(name)
    if converted_name and converted_name[0].isdigit():
        return f"bone_{converted_name}"
    return converted_name or "default_name"
