"""Focused regression tests for VMD clear inventory and ownership guards."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mmd_tools.converters.vmd_context import VmdImportStateContext
from mmd_tools.converters.vmd_import_state import (
    _read_vmd_clear_scope,
    build_motion_clear_inventory,
    clear_existing_motion,
    refresh_motion_clear_inventory,
)
from mmd_tools.core.exceptions import MMDImportException
from mmd_tools.core.mmd_control_rig_builder import CONTROL_RIG_CONTROL_OWNED


def _context(*, bone_name_mapping=None):
    return VmdImportStateContext(
        logger=MagicMock(),
        bone_name_mapping=bone_name_mapping or {},
        bone_bind_poses={},
        morph_name_mapping={},
        collect_append_info=lambda: {},
        iter_morph_mappings=lambda _mapping: [],
        set_refresh_suspended=lambda _value: None,
    )


class TestVmdMotionClearReliability(unittest.TestCase):
    def test_clear_scope_requires_current_schema_and_target_uuid(self):
        with patch("mmd_tools.converters.vmd_import_state.cmds") as cmds:
            cmds.objExists.return_value = True
            cmds.ls.return_value = ["current-root-uuid"]

            for scope in (
                {"schema": 0, "target_model_uuid": "current-root-uuid"},
                {"schema": 1, "target_model_uuid": "stale-root-uuid"},
            ):
                cmds.getAttr.return_value = json.dumps({"clear_scope": scope})
                self.assertEqual(_read_vmd_clear_scope("|model"), {})

            valid_scope = {
                "schema": 1,
                "target_model_uuid": "current-root-uuid",
                "curve_uuids": ["curve-1"],
            }
            cmds.getAttr.return_value = json.dumps({"clear_scope": valid_scope})
            self.assertEqual(_read_vmd_clear_scope("|model"), valid_scope)

    def test_inventory_includes_control_rig_routes_and_blocks_unknown_curves(self):
        context = _context(bone_name_mapping={"center": "|model|center"})
        control_routes = {
            "|model|center": {
                "translateX": ("|model|center_CTRL", "translateX"),
            }
        }
        curve_records = {
            ("|model|center_CTRL", "translateX"): [
                {"name": "foreignCurve", "uuid": "foreign-curve", "key_count": 2}
            ],
            ("|model|center_CTRL", "ikEnabled"): [
                {"name": "foreignIkCurve", "uuid": "foreign-ik", "key_count": 1}
            ],
        }
        with patch(
            "mmd_tools.converters.vmd_import_state.root_owned_joints",
            return_value={"|model|center"},
        ), patch(
            "mmd_tools.converters.vmd_import_state.read_mmd_control_rig_metadata",
            return_value={"owner": CONTROL_RIG_CONTROL_OWNED},
        ), patch(
            "mmd_tools.converters.vmd_import_state.control_rig_edit_routes_for_joints",
            return_value=control_routes,
        ), patch(
            "mmd_tools.converters.vmd_import_state.control_rig_edit_ik_enabled_plugs_for_model",
            return_value=["|model|center_CTRL.ikEnabled"],
        ), patch(
            "mmd_tools.converters.vmd_import_state._curve_records_for_plug",
            side_effect=lambda node, attribute: curve_records.get((node, attribute), []),
        ), patch(
            "mmd_tools.converters.vmd_import_state._canonical_node_path",
            side_effect=lambda node: str(node or ""),
        ), patch(
            "mmd_tools.converters.vmd_import_state._read_vmd_clear_scope",
            return_value={},
        ), patch(
            "mmd_tools.converters.vmd_import_state.resolve_owned_bone_morph_base_routes",
            return_value=SimpleNamespace(routes={}, blocked={}),
        ), patch(
            "mmd_tools.converters.vmd_import_state._ls_mmd_ccd_ik_nodes",
            return_value=[],
        ), patch(
            "mmd_tools.converters.vmd_import_state.resolve_redirected_authoring_proxy_authority",
            return_value=(None, None, False),
        ), patch("mmd_tools.converters.vmd_import_state.cmds") as cmds:
            cmds.animLayer.return_value = []
            inventory = build_motion_clear_inventory(
                context,
                "VMD_Motion",
                target_model="|model",
            )

        route_plugs = {route["plug"] for route in inventory["routes"]}
        self.assertIn("|model|center_CTRL.translateX", route_plugs)
        self.assertIn("|model|center_CTRL.ikEnabled", route_plugs)
        self.assertEqual(
            {item["code"] for item in inventory["blockers"]},
            {"unknown_curve_ownership"},
        )
        self.assertEqual(inventory["key_count"], 3)

    def test_unprovenanced_curve_blocks_strict_clear_before_mutation(self):
        before = {
            "target_model": "|model",
            "target_namespace": "",
            "route_count": 1,
            "known_curve_uuids": [],
            "key_count": 2,
            "blockers": [
                {
                    "code": "unknown_curve_ownership",
                    "reason": "curve_has_keys_without_vmd_provenance",
                }
            ],
        }
        profile = {}
        with patch(
            "mmd_tools.converters.vmd_import_state.build_motion_clear_inventory",
            return_value=before,
        ), patch("mmd_tools.converters.vmd_import_state.cut_keyable_attrs") as cut:
            with self.assertRaises(MMDImportException) as raised:
                clear_existing_motion(
                    _context(),
                    "VMD_Motion",
                    target_model="|model",
                    profile=profile,
                    strict=True,
                )

        self.assertEqual(raised.exception.reason_code, "vmd_clear_ownership_blocked")
        self.assertEqual(profile["motion_clear"]["status"], "blocked")
        cut.assert_not_called()

    def test_clear_reuses_inventory_plan_without_rerunning_route_resolvers(self):
        before = {
            "target_model": "|model",
            "target_namespace": "",
            "route_count": 1,
            "known_curve_uuids": [],
            "key_count": 7,
            "blockers": [],
            "routes": [
                {
                    "source": "control_rig",
                    "node": "|model|center_CTRL",
                    "attribute": "translateX",
                    "plug": "|model|center_CTRL.translateX",
                    "curves": [{"uuid": "curve-1", "key_count": 7}],
                }
            ],
        }
        after = {
            **before,
            "key_count": 0,
            "routes": [
                {
                    **before["routes"][0],
                    "curves": [],
                }
            ],
        }
        profile = {}
        with patch(
            "mmd_tools.converters.vmd_import_state.build_motion_clear_inventory",
            return_value=before,
        ) as build, patch(
            "mmd_tools.converters.vmd_import_state.refresh_motion_clear_inventory",
            return_value=after,
        ) as refresh, patch(
            "mmd_tools.converters.vmd_import_state.cut_keyable_attrs",
            return_value=5,
        ) as cut, patch(
            "mmd_tools.converters.vmd_import_state.root_owned_joints",
            return_value=set(),
        ), patch(
            "mmd_tools.converters.vmd_import_state._capture_fallback_rest_translates",
            return_value={},
        ), patch(
            "mmd_tools.converters.vmd_import_state._restore_joints_to_rest"
        ), patch(
            "mmd_tools.converters.vmd_import_state.delete_vmd_rotation_time_curves_for_controls",
            return_value=[],
        ), patch(
            "mmd_tools.converters.vmd_import_state._anim_layer_is_exclusively_owned_by",
            return_value=True,
        ), patch(
            "mmd_tools.converters.vmd_import_state.resolve_owned_bone_morph_base_routes",
            side_effect=AssertionError("route resolver reran during mutation"),
        ), patch(
            "mmd_tools.converters.vmd_import_state.control_rig_edit_routes_for_joints",
            side_effect=AssertionError("Control Rig route resolver reran during mutation"),
        ), patch(
            "mmd_tools.converters.vmd_import_state.cmds"
        ) as cmds:
            cmds.objExists.return_value = False
            result = clear_existing_motion(
                _context(),
                "VMD_Motion",
                target_model="|model",
                profile=profile,
                strict=True,
            )

        build.assert_called_once()
        refresh.assert_called_once_with(before)
        cut.assert_called_once_with(
            "|model|center_CTRL",
            ("translateX",),
            preserve_curve_nodes=True,
            detached_curve_nodes=None,
        )
        self.assertEqual(result["effective"]["cleared_key_count"], 7)
        self.assertEqual(profile["motion_clear"]["status"], "success")

    def test_refresh_reports_current_curve_and_key_counts(self):
        inventory = {
            "route_count": 1,
            "key_count": 99,
            "curve_uuids": ["old-curve"],
            "blockers": [{"code": "stale"}],
            "routes": [
                {
                    "source": "control_rig",
                    "node": "|model|center_CTRL",
                    "attribute": "translateX",
                    "plug": "|model|center_CTRL.translateX",
                    "curves": [{"uuid": "old-curve", "key_count": 99}],
                }
            ],
        }
        with patch(
            "mmd_tools.converters.vmd_import_state._curve_records_for_plug",
            return_value=[
                {"name": "curve-a", "uuid": "curve-a", "key_count": 2},
                {"name": "curve-b", "uuid": "curve-b", "key_count": 3},
            ],
        ) as records:
            refreshed = refresh_motion_clear_inventory(inventory)

        records.assert_called_once_with("|model|center_CTRL", "translateX")
        self.assertEqual(refreshed["route_count"], 1)
        self.assertEqual(refreshed["key_count"], 5)
        self.assertEqual(refreshed["curve_uuids"], ["curve-a", "curve-b"])
        self.assertEqual(refreshed["blockers"], [])
        self.assertEqual(inventory["key_count"], 99)
