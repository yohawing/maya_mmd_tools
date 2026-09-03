from __future__ import annotations

import json

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.ui import morph_controller_ae  # noqa: E402


class _Cmds:
    def __init__(self) -> None:
        self.sliders: list[dict[str, object]] = []

    def editorTemplate(self, **_kwargs):  # noqa: N802
        return None

    def getAttr(self, plug, **kwargs):  # noqa: N802
        if kwargs.get("multiIndices"):
            return [3, 8]
        if plug == "boneMorph.mmd_morph_index":
            return 3
        if plug == "boneMorph.mmd_morph_name":
            return "ボーン表示名"
        if plug == "faceBS.mmd_blendshape_morph_names_json":
            return json.dumps({"2": {"name": "頂点表示名", "index": 8}})
        raise AssertionError(plug)

    def listConnections(self, plug, **_kwargs):  # noqa: N802
        if plug.endswith("outputWeight[3]"):
            return ["boneMorph.weight"]
        if plug.endswith("outputWeight[8]"):
            return ["faceBS.weight[2]"]
        return []

    def attributeQuery(self, attribute, node, exists=False):  # noqa: N802
        assert exists
        return (node, attribute) in {
            ("boneMorph", "mmd_morph_index"),
            ("faceBS", "mmd_blendshape_morph_names_json"),
        }

    def nodeType(self, node):  # noqa: N802
        return "blendShape" if node == "faceBS" else "network"

    def aliasAttr(self, plug, query=False):  # noqa: N802
        assert query
        return f"alias_{plug.rsplit('[', 1)[-1].rstrip(']')}"

    def attrFieldSliderGrp(self, **kwargs):  # noqa: N802
        self.sliders.append(kwargs)


def test_build_shows_semantic_names_and_binds_full_input_plugs(monkeypatch) -> None:
    from mmd_tools.ui.translations import UITranslator

    translator = UITranslator.instance()
    previous = translator.get_language()
    translator.set_language("en")
    cmds = _Cmds()
    monkeypatch.setattr(morph_controller_ae, "cmds", cmds)

    try:
        controls = morph_controller_ae._build_weight_controls("controller")
    finally:
        translator.set_language(previous)

    assert controls == [None, None]
    assert cmds.sliders == [
        {
            "attribute": "controller.inputWeight[3]",
            "label": "boneMorph",
            "minValue": 0.0,
            "maxValue": 1.0,
            "precision": 3,
        },
        {
            "attribute": "controller.inputWeight[8]",
            "label": "faceBS[2]",
            "minValue": 0.0,
            "maxValue": 1.0,
            "precision": 3,
        },
    ]


def test_english_build_rejects_non_ascii_alias_fallback(monkeypatch) -> None:
    from mmd_tools.ui.translations import UITranslator

    class NonAsciiAliasCmds(_Cmds):
        def listConnections(self, _plug, **_kwargs):  # noqa: N802
            return []

        def aliasAttr(self, _plug, query=False):  # noqa: N802
            assert query
            return "笑顔"

    translator = UITranslator.instance()
    previous = translator.get_language()
    translator.set_language("en")
    cmds = NonAsciiAliasCmds()
    monkeypatch.setattr(morph_controller_ae, "cmds", cmds)

    try:
        morph_controller_ae._build_weight_controls("controller")
    finally:
        translator.set_language(previous)

    assert [slider["label"] for slider in cmds.sliders] == ["Morph 3", "Morph 8"]
