"""Focused ownership and priority tests for bone-morph authored routes."""

from __future__ import annotations

from unittest import mock

from tests.common.maya_stub import install_maya_stub

install_maya_stub()

from mmd_tools.converters import bone_morph_runtime  # noqa: E402
from mmd_tools.converters import vmd_legacy_bone_routes  # noqa: E402
from mmd_tools.converters import vmd_scene_collector  # noqa: E402
from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector  # noqa: E402
from mmd_tools.converters.vmd_scene_keying import VmdKeyingError  # noqa: E402
from mmd_tools.converters.vmd_bone_animation import set_bone_keyframes  # noqa: E402
from mmd_tools.converters import vmd_bone_animation  # noqa: E402
from mmd_tools.converters import vmd_rotation_time_curve  # noqa: E402
from mmd_tools.converters import vmd_redirected_authoring_proxy  # noqa: E402


class _AccumulatorCmds:
    def __init__(self) -> None:
        self.nodes = {
            "|model|bone": "joint",
            "accum": "mmdBoneMorphAccum",
        }
        self.marker = True
        self.valid_contract = True
        self.target = "|model|bone"
        self.aliases = {}
        self.outputs = {
            "|model|bone.translate": ["accum.outputTranslate"],
            "|model|bone.rotate": ["accum.outputRotate"],
        }

    def ls(self, value=None, type=None, long=False, **_kwargs):
        if type == "mmdBoneMorphAccum":
            return [node for node, node_type in self.nodes.items() if node_type == type]
        if value in self.aliases:
            return list(self.aliases[value])
        if value in self.nodes:
            return [value]
        return []

    def objExists(self, node):
        return node.split(".", 1)[0] in self.nodes

    def nodeType(self, node):
        return self.nodes.get(node)

    def attributeQuery(self, attr, node, exists=False, **_kwargs):
        return bool(
            exists
            and (
                attr in bone_morph_runtime.REQUIRED_ACCUM_ATTRS
                and self.valid_contract
                or attr in {"mmd_bone_morph_accum", "mmd_target_joint"}
            )
        )

    def getAttr(self, plug, **_kwargs):
        if plug.endswith(".mmd_bone_morph_accum"):
            return self.marker
        if plug.endswith(".mmd_target_joint"):
            return self.target
        return 0.0

    def listConnections(self, plug, s=False, d=False, p=False, **_kwargs):
        return list(self.outputs.get(plug, []))


def _expected_base_routes():
    return {
        f"{kind}{axis}": ("accum", f"base{kind.capitalize()}{axis}")
        for kind in ("translate", "rotate")
        for axis in "XYZ"
    }


def _resolution(routes=None, blocked=None):
    return bone_morph_runtime.BoneMorphBaseRouteResolution(
        routes=routes or {},
        blocked=blocked or {},
    )


def test_resolver_requires_marker_target_and_both_owned_outputs() -> None:
    cmds = _AccumulatorCmds()
    with mock.patch.object(bone_morph_runtime, "cmds", cmds), mock.patch.object(
        bone_morph_runtime,
        "_is_connected",
        side_effect=lambda source, destination: source in cmds.outputs.get(destination, []),
    ):
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone"]
        )
        assert resolution.routes == {"|model|bone": _expected_base_routes()}
        assert not resolution.blocked

        cmds.marker = False
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone"]
        )
        assert not resolution.routes
        assert not resolution.blocked
        cmds.marker = True
        cmds.target = "|other|bone"
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(["|model|bone"])
        assert not resolution.routes
        assert resolution.blocked["|model|bone"][1] == (
            "invalid_or_ambiguous_bone_morph_accumulator"
        )
        cmds.target = "|model|bone"
        cmds.outputs.pop("|model|bone.rotate")
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone"]
        )
        assert not resolution.routes
        assert resolution.blocked["|model|bone"][1] == (
            "bone_morph_accumulator_output_unowned"
        )


def test_resolver_rejects_duplicate_owned_accumulators() -> None:
    cmds = _AccumulatorCmds()
    cmds.nodes["accum2"] = "mmdBoneMorphAccum"
    # Both nodes claim the same marker/target. Output connectivity cannot make
    # that ownership unique, so neither may receive imported keys.
    with mock.patch.object(bone_morph_runtime, "cmds", cmds), mock.patch.object(
        bone_morph_runtime,
        "_is_connected",
        side_effect=lambda source, destination: source in cmds.outputs.get(destination, []),
    ):
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone"]
        )
        assert not resolution.routes
        assert resolution.blocked["|model|bone"][1] == (
            "duplicate_bone_morph_accumulator"
        )


def test_resolver_blocks_invalid_contract_and_ambiguous_target_claims() -> None:
    cmds = _AccumulatorCmds()
    cmds.valid_contract = False
    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone"]
        )
    assert resolution.blocked["|model|bone"][1] == (
        "invalid_or_ambiguous_bone_morph_accumulator"
    )

    cmds.valid_contract = True
    cmds.nodes["|other|bone"] = "joint"
    cmds.target = "bone"
    cmds.aliases["bone"] = ["|model|bone", "|other|bone"]
    with mock.patch.object(bone_morph_runtime, "cmds", cmds):
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone", "|other|bone"]
        )
    assert set(resolution.blocked) == {"|model|bone", "|other|bone"}
    with mock.patch.object(bone_morph_runtime, "cmds", cmds), mock.patch.object(
        bone_morph_runtime,
        "_is_connected",
        side_effect=lambda source, destination: source in cmds.outputs.get(destination, []),
    ):
        resolution = bone_morph_runtime.resolve_owned_bone_morph_base_routes(
            ["|model|bone"]
        )
    assert set(resolution.blocked) == {"|model|bone"}


def test_legacy_accumulator_wins_append_ik_and_physics_but_not_control() -> None:
    converter = mock.MagicMock()
    converter.bone_name_mapping = {"bone": "|model|bone"}
    converter._collect_append_info.return_value = {
        "|model|bone": {
            "node": "append",
            "attr_map": {"translateX": "inputTranslateX"},
        }
    }
    converter._collect_ik_link_joints.return_value = {
        "|model|bone": {"solver": "ik", "slot": 0}
    }
    accum = {"|model|bone": _expected_base_routes()}
    control = {"|model|bone": {"rotateX": ("control", "rotateX")}}
    with (
        mock.patch.object(
            vmd_legacy_bone_routes,
            "resolve_owned_bone_morph_base_routes",
            return_value=_resolution(routes=accum),
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_edit_routes_for_joints",
            return_value=control,
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_edit_authoring_bases_for_joints",
            return_value={},
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_fixed_axis_twist_joints",
            return_value=set(),
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "_physics_pre_input_routes",
            return_value=(
                {"|model|bone": {"translateX": ("physics", "inPreTranslateX")}},
                {},
            ),
        ),
    ):
        route = vmd_legacy_bone_routes.build_legacy_bone_key_routes(converter)[
            "|model|bone"
        ]

    assert route["attr_targets"]["translateX"] == ("accum", "baseTranslateX")
    assert route["attr_targets"]["rotateX"] == ("control", "rotateX")
    assert route["attr_targets"]["rotateY"] == ("accum", "baseRotateY")
    assert route["skip_rotate"] is False
    assert route["ik_solver_rotate"] is None
    assert route["quaternion_interpolation_safe"] is False

    proxy_route = {
        f"translate{axis}": ("proxy", f"translate{axis}")
        for axis in "XYZ"
    }
    with mock.patch.object(
        vmd_redirected_authoring_proxy,
        "ensure_redirected_authoring_proxy",
        return_value=proxy_route,
    ), mock.patch.object(
        vmd_bone_animation,
        "_set_bone_keyframes_impl",
    ) as key_impl:
        set_bone_keyframes(mock.MagicMock(), "|model|bone", [], "bone", route)
    keyed_route = key_impl.call_args.args[4]
    assert keyed_route["attr_targets"]["rotateX"] == ("control", "rotateX")
    assert keyed_route["attr_targets"]["rotateY"] == ("accum", "baseRotateY")
    assert keyed_route["attr_targets"]["translateX"] == ("proxy", "translateX")
    semantic_frames = [
        {
            "frame_number": frame,
            "semantic_interpolation": {"rotation": (0.1, 0.3, 0.7, 0.9)},
        }
        for frame in (0, 10)
    ]
    try:
        vmd_bone_animation._configure_sparse_rotation_track(
            mock.MagicMock(),
            "|model|bone",
            semantic_frames,
            "bone",
            keyed_route,
            skip_rotate=False,
            animation_layer=None,
        )
    except VmdKeyingError as exc:
        assert "mixed or unsafe owners" in str(exc)
    else:
        raise AssertionError("mixed semantic rotation silently used scalar fallback")

    complete_control = {
        "|model|bone": {
            channel: ("control", channel)
            for channel in vmd_legacy_bone_routes._PHYSICS_PRE_INPUT_ATTRS
        }
    }
    with (
        mock.patch.object(
            vmd_legacy_bone_routes,
            "resolve_owned_bone_morph_base_routes",
            return_value=_resolution(routes=accum),
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_edit_routes_for_joints",
            return_value=complete_control,
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_edit_authoring_bases_for_joints",
            return_value={},
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_fixed_axis_twist_joints",
            return_value=set(),
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "_physics_pre_input_routes",
            return_value=({}, {}),
        ),
    ):
        control_route = vmd_legacy_bone_routes.build_legacy_bone_key_routes(converter)[
            "|model|bone"
        ]
    assert control_route["attr_targets"] == complete_control["|model|bone"]
    assert control_route["quaternion_interpolation_safe"] is True


def test_collector_accumulator_wins_append_and_ik() -> None:
    collector = VmdSceneCollector()
    accum = {"|model|bone": _expected_base_routes()}
    with (
        mock.patch.object(vmd_scene_collector.cmds, "ls", return_value=["|model|bone"]),
        mock.patch.object(
            vmd_scene_collector,
            "collect_append_info",
            return_value={
                "|model|bone": {
                    "node": "append",
                    "attr_map": {"translateX": "inputTranslateX"},
                }
            },
        ),
        mock.patch.object(
            vmd_scene_collector,
            "collect_mmd_ik_passthrough_info",
            return_value={"|model|bone": {"node": "ik", "input_slot": 0}},
        ),
        mock.patch.object(
            vmd_scene_collector,
            "resolve_owned_bone_morph_base_routes",
            return_value=_resolution(routes=accum),
        ),
    ):
        routes = collector._scene_authored_input_routes(["|model|bone"])

    assert routes["|model|bone"] == _expected_base_routes()


def test_unresolved_accumulator_blocks_import_and_collection() -> None:
    blocked = {
        "|model|bone": (
            tuple(_expected_base_routes()),
            "bone_morph_accumulator_output_unowned",
        )
    }
    resolution = _resolution(blocked=blocked)
    converter = mock.MagicMock()
    converter.bone_name_mapping = {"bone": "|model|bone"}
    converter._collect_append_info.return_value = {}
    converter._collect_ik_link_joints.return_value = {}
    with (
        mock.patch.object(
            vmd_legacy_bone_routes,
            "resolve_owned_bone_morph_base_routes",
            return_value=resolution,
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_edit_routes_for_joints",
            return_value={},
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_edit_authoring_bases_for_joints",
            return_value={},
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "control_rig_fixed_axis_twist_joints",
            return_value=set(),
        ),
        mock.patch.object(
            vmd_legacy_bone_routes,
            "_physics_pre_input_routes",
            return_value=({}, {}),
        ),
    ):
        route = vmd_legacy_bone_routes.build_legacy_bone_key_routes(converter)[
            "|model|bone"
        ]
    try:
        set_bone_keyframes(converter, "|model|bone", [], "bone", route)
    except VmdKeyingError as exc:
        assert "bone_morph_accumulator_output_unowned" in str(exc)
    else:
        raise AssertionError("unresolved accumulator allowed direct joint keying")

    collector = VmdSceneCollector()
    with (
        mock.patch.object(vmd_scene_collector.cmds, "ls", return_value=["|model|bone"]),
        mock.patch.object(vmd_scene_collector, "collect_append_info", return_value={}),
        mock.patch.object(
            vmd_scene_collector,
            "collect_mmd_ik_passthrough_info",
            return_value={},
        ),
        mock.patch.object(
            vmd_scene_collector,
            "resolve_owned_bone_morph_base_routes",
            return_value=resolution,
        ),
    ):
        try:
            collector._scene_authored_input_routes(["|model|bone"])
        except ValueError as exc:
            assert "bone_morph_accumulator_output_unowned" in str(exc)
        else:
            raise AssertionError("unresolved accumulator allowed collector fallback")


def test_complete_accumulator_rotation_keeps_sparse_quaternion_time_curve() -> None:
    context = mock.MagicMock()
    context.use_quaternion_interpolation = False
    context.use_vmd_rotation_time_curve = False
    context.rotation_time_curve_records = []
    context.vmd_frame_to_maya_time = float
    frames = [
        {
            "frame_number": 0,
            "rotation": (0.0, 0.0, 0.0, 1.0),
            "semantic_interpolation": {"rotation": (0.0, 0.2, 0.8, 1.0)},
        },
        {
            "frame_number": 10,
            "rotation": (0.0, 0.70710678, 0.0, 0.70710678),
            "semantic_interpolation": {"rotation": (0.1, 0.3, 0.7, 0.9)},
        },
    ]
    route = {
        "attr_targets": {
            f"rotate{axis}": ("accum", f"baseRotate{axis}")
            for axis in "XYZ"
        },
        "quaternion_interpolation_safe": True,
    }
    record = {"boneName": "bone", "keyCount": 2}
    with mock.patch.object(
        vmd_bone_animation,
        "_apply_quaternion_interpolation",
    ) as quaternion, mock.patch.object(
        vmd_rotation_time_curve,
        "apply_vmd_rotation_time_curve",
        return_value=record,
    ) as time_curve:
        plugs = vmd_bone_animation._configure_sparse_rotation_track(
            context,
            "|model|bone",
            frames,
            "bone",
            route,
            skip_rotate=False,
            animation_layer=None,
        )

    expected_plugs = [f"accum.baseRotate{axis}" for axis in "XYZ"]
    assert plugs == expected_plugs
    quaternion.assert_called_once_with(
        context,
        expected_plugs,
        animation_layer=None,
    )
    time_curve.assert_called_once()
    assert context.rotation_time_curve_records == [record]

    with mock.patch.object(
        vmd_bone_animation,
        "_apply_quaternion_interpolation",
        return_value=False,
    ), mock.patch.object(
        vmd_rotation_time_curve,
        "apply_vmd_rotation_time_curve",
    ) as blocked_time_curve:
        try:
            vmd_bone_animation._configure_sparse_rotation_track(
                context,
                "|model|bone",
                frames,
                "bone",
                route,
                skip_rotate=False,
                animation_layer=None,
            )
        except VmdKeyingError as exc:
            assert "could not be established" in str(exc)
        else:
            raise AssertionError("failed quaternion conversion was ignored")
    blocked_time_curve.assert_not_called()


def test_complete_redirected_owner_is_promoted_to_transform_proxy() -> None:
    route = {
        "attr_targets": {
            f"{kind}{axis}": ("physics", f"inPre{kind.capitalize()}{axis}")
            for kind in ("translate", "rotate")
            for axis in "XYZ"
        },
        "quaternion_interpolation_safe": False,
    }
    proxy_route = {
        f"{kind}{axis}": ("proxy", f"{kind}{axis}")
        for kind in ("translate", "rotate")
        for axis in "XYZ"
    }
    with mock.patch.object(
        vmd_redirected_authoring_proxy,
        "ensure_redirected_authoring_proxy",
        return_value=proxy_route,
    ), mock.patch.object(
        vmd_bone_animation,
        "_set_bone_keyframes_impl",
    ) as key_impl:
        set_bone_keyframes(mock.MagicMock(), "|model|bone", [], "bone", route)
    promoted = key_impl.call_args.args[4]
    assert promoted["attr_targets"] == proxy_route
    assert promoted["quaternion_interpolation_safe"] is True


def test_redirected_proxy_rejects_stale_owner_or_plug_authority() -> None:
    current = {
        f"rotate{axis}": ("driver_new", f"inPreRotate{axis}")
        for axis in "XYZ"
    }
    stale_owner = {
        f"rotate{axis}": ("driver_old", f"inPreRotate{axis}")
        for axis in "XYZ"
    }
    stale_plug = dict(current)
    stale_plug["rotateZ"] = ("driver_new", "otherRotateZ")
    uuids = {
        "driver_new": "uuid-new",
        "driver_old": "uuid-old",
    }
    with mock.patch.object(
        vmd_redirected_authoring_proxy,
        "_single_uuid",
        side_effect=lambda node: uuids.get(node, ""),
    ):
        assert not vmd_redirected_authoring_proxy.redirected_authority_matches(
            current, stale_owner
        )
        assert not vmd_redirected_authoring_proxy.redirected_authority_matches(
            current, stale_plug
        )

    existing = {
        f"rotate{axis}": ("proxy", f"rotate{axis}")
        for axis in "XYZ"
    }
    with mock.patch.object(
        vmd_redirected_authoring_proxy,
        "_eligible_destinations",
        return_value=current,
    ), mock.patch.object(
        vmd_redirected_authoring_proxy,
        "resolve_redirected_authoring_proxy_authority",
        return_value=(existing, stale_owner, True),
    ), mock.patch.object(
        vmd_redirected_authoring_proxy,
        "redirected_authority_matches",
        return_value=False,
    ):
        try:
            vmd_redirected_authoring_proxy.ensure_redirected_authoring_proxy(
                "|model|bone", current
            )
        except RuntimeError as exc:
            assert "authority is stale" in str(exc)
        else:
            raise AssertionError("stale redirected proxy was silently reused")


def test_redirected_proxy_accepts_only_its_automatic_unit_conversion() -> None:
    maya_cmds = mock.MagicMock()
    maya_cmds.isConnected.side_effect = lambda source, destination: (
        (source, destination)
        in {
            ("unitConversion1.output", "append.baseRotateX"),
            ("proxy.rotateX", "unitConversion1.input"),
        }
    )
    maya_cmds.nodeType.return_value = "unitConversion"
    maya_cmds.getAttr.return_value = 57.29577951308232

    def connections(plug, **_kwargs):
        return {
            "append.baseRotateX": ["unitConversion1.output"],
            "unitConversion1.input": ["proxy.rotateX"],
        }.get(plug, [])

    maya_cmds.listConnections.side_effect = connections
    with mock.patch.object(
        vmd_redirected_authoring_proxy, "cmds", maya_cmds
    ), mock.patch.object(
        vmd_redirected_authoring_proxy,
        "_single_uuid",
        return_value="conversion-uuid",
    ):
        assert vmd_redirected_authoring_proxy._proxy_drives_destination(
            "proxy.rotateX",
            "append.baseRotateX",
            {"uuid": "conversion-uuid", "factor": 57.29577951308232},
            {"conversion-uuid"},
        )
        maya_cmds.getAttr.return_value = 2.0
        assert not vmd_redirected_authoring_proxy._proxy_drives_destination(
            "proxy.rotateX",
            "append.baseRotateX",
            {"uuid": "conversion-uuid", "factor": 57.29577951308232},
            {"conversion-uuid"},
        )
        maya_cmds.getAttr.return_value = 57.29577951308232
        maya_cmds.listConnections.side_effect = lambda plug, **_kwargs: (
            ["foreign.rotateX"] if plug == "unitConversion1.input" else connections(plug)
        )
        assert not vmd_redirected_authoring_proxy._proxy_drives_destination(
            "proxy.rotateX",
            "append.baseRotateX",
            {"uuid": "conversion-uuid", "factor": 57.29577951308232},
            {"conversion-uuid"},
        )
