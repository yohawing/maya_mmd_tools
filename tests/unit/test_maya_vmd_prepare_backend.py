"""Unit coverage for the Maya-owned Bake Timeline preparation adapter."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mmd_tools.actions.prepare_vmd_export_action import (
    PrepareVmdExportAction,
    PrepareVmdExportError,
)
from mmd_tools.adapters.maya_vmd_prepare_backend import (
    MayaVmdPrepareBackend,
    create_maya_vmd_prepare_action,
)
class _FakeCmds:
    def __init__(self):
        self.uuid_by_node = {
            "|model": "uuid-model",
            "|model|bone": "uuid-bone",
            "|model|mesh": "uuid-mesh",
            "|curve": "uuid-curve",
            "|camera": "uuid-camera",
            "|camera|shape": "uuid-camera-shape",
            "|consumer": "uuid-consumer",
            "bakeTimelineBlendShape": "uuid-blend",
            "|model|sourceShape": "uuid-source-shape",
            "|rotationTime": "uuid-rotation-time",
            "|time1": "uuid-time",
        }
        self.type_by_node = {
            "|model": "transform",
            "|model|bone": "joint",
            "|model|mesh": "mesh",
            "|curve": "animCurveTL",
            "|camera": "transform",
            "|camera|shape": "camera",
            "|consumer": "network",
            "bakeTimelineBlendShape": "blendShape",
            "|model|sourceShape": "mesh",
            "|rotationTime": "network",
            "|time1": "time",
        }
        self.connections = {
            "|model": [],
            "|model|bone": ["|model|bone.rotateX", "|curve.output"],
            "|model|mesh": [],
            "|curve": [],
            "|camera": [],
            "|camera|shape": [],
            "|consumer": [],
            "bakeTimelineBlendShape": [],
            "|model|sourceShape": [],
            "|rotationTime": [],
            "|time1": [],
        }
        self.incoming = {
            "|model": [],
            "|model|bone": ["|curve.output"],
            "|model|mesh": [],
            "|curve": [],
            "|camera": [],
            "|camera|shape": [],
            "|consumer": [],
            "bakeTimelineBlendShape": [],
            "|model|sourceShape": [],
            "|rotationTime": [],
            "|time1": [],
        }
        self.long_name_aliases = {"sourceShape": "|model|sourceShape"}

    def ls(self, value=None, **kwargs):
        if kwargs.get("uuid"):
            node = str(value)
            return [self.uuid_by_node[node]] if node in self.uuid_by_node else []
        if value == "*.mmd_camera":
            return ["|camera"]
        if value is None:
            return []
        node = self.long_name_aliases.get(str(value), str(value))
        if node in self.uuid_by_node:
            return [node]
        return []

    def nodeType(self, node):
        return self.type_by_node[str(node)]

    def listRelatives(self, node, **kwargs):
        if kwargs.get("shapes"):
            return ["|camera|shape"] if str(node) == "|camera" else []
        if kwargs.get("type") == "mesh":
            return ["|model|mesh"] if str(node) == "|model" else []
        if kwargs.get("type") == "joint":
            return ["|model|bone"] if str(node) == "|model" else []
        return ["|model|bone", "|model|mesh"] if str(node) == "|model" else []

    def listHistory(self, node, **kwargs):
        return []

    def listConnections(self, node, **kwargs):
        if kwargs.get("connections"):
            return list(self.connections.get(str(node), []))
        return list(self.incoming.get(str(node), []))


class _FakeWatch:
    def __init__(self):
        self.closed = False
        self.stale = False

    @property
    def usable(self):
        return not self.closed and not self.stale

    @property
    def current(self):
        return self.usable

    def close(self):
        self.closed = True


class _FakeRevisionService:
    session_id = "scene-1"

    def __init__(self):
        self.watches = []
        self.revision = 3

    def arm(self, dependencies):
        watch = _FakeWatch()
        watch.dependencies = tuple(dependencies)
        self.watches.append(watch)
        return watch

    def current_revision(self):
        return self.revision


class _FakeCollector:
    def __init__(self):
        self.calls = []

    def collect(self, options):
        self.calls.append(dict(options))
        return {"bone_frames": [], "morph_frames": []}


class _StreamingCollector:
    def __init__(self):
        self.stream_calls = []
        self.diagnostics = {
            "track_selection": {"counts": {"direct": 1}},
            "streaming": {"enabled": True},
        }

    def collect_to_sink(self, options, sink):
        self.stream_calls.append((dict(options), sink))
        sections = ("bones", "morphs", "cameras", "lights", "shadows", "ik")
        for index, section in enumerate(sections):
            sink.begin_section(section)
            if index < len(sections) - 1:
                sink.end_section()
        return {
            "model_name": "fixture",
            "validation_frame_range": (0, 10),
            "raw_provenance": False,
            "section_counts": {
                "bones": 0,
                "morphs": 0,
                "cameras": 0,
                "lights": 0,
                "shadows": 0,
                "ik": 0,
            },
        }


class _FakeSink:
    def __init__(self):
        self.sections = []

    def begin_section(self, section):
        self.sections.append(section)

    def end_section(self):
        pass


def _request(**options):
    values = {
        "current_model_root": "|model",
        "target_model": "|model",
        "export_strategy": "bake_timeline",
        "frame_range": (0, 10),
    }
    values.update(options)
    return {"options": values}


class MayaVmdPrepareBackendTests(unittest.TestCase):
    def setUp(self):
        self.cmds = _FakeCmds()
        self.service = _FakeRevisionService()
        self.collector = _StreamingCollector()
        self.mobjects = {}
        self.backend = MayaVmdPrepareBackend(
            self.cmds,
            collector=self.collector,
            revision_service=self.service,
            mobject_resolver=lambda node: self.mobjects.setdefault(node, object()),
        )

    def test_edit_control_owned_rig_is_left_untouched_for_direct_collection(self):
        self.cmds.attributeQuery = lambda *_args, **_kwargs: True
        edit_metadata = {"state": "EDIT", "owner": "CONTROL_OWNED"}
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=edit_metadata,
        ) as read_metadata, patch(
            "mmd_tools.core.mmd_control_rig_motion.bake_mmd_control_rig",
        ) as bake:
            self.assertTrue(self.backend.can_prepare_for_collection(_request()))
            self.assertIsNone(self.backend.prepare_for_collection(_request()))

        bake.assert_not_called()
        self.assertEqual(read_metadata.call_count, 2)

    def test_non_edit_control_rig_is_not_mutated_for_collection(self):
        self.cmds.attributeQuery = lambda *_args, **_kwargs: True
        attached_metadata = {"state": "ATTACHED", "owner": "MMD_OWNED"}
        with patch(
            "mmd_tools.core.mmd_control_rig_builder.read_mmd_control_rig_metadata",
            return_value=attached_metadata,
        ), patch(
            "mmd_tools.core.mmd_control_rig_motion.bake_mmd_control_rig",
        ) as bake:
            self.assertIsNone(self.backend.prepare_for_collection(_request()))
        bake.assert_not_called()

    def test_requires_explicit_current_model_projection(self):
        with self.assertRaises(PrepareVmdExportError):
            self.backend.discover({"options": {"export_strategy": "bake_timeline", "target_model": "|model"}})
        with self.assertRaises(PrepareVmdExportError):
            self.backend.discover(_request(target_model="|camera"))

    def test_canonical_identity_and_dependency_fingerprint_are_deterministic(self):
        first = self.backend.discover(_request())
        second = self.backend.discover(_request())
        self.assertEqual(first.target_uuid, "uuid-model")
        self.assertEqual(first.target_identity, "|model")
        self.assertEqual(first.dependency_closure_fingerprint, second.dependency_closure_fingerprint)
        self.assertEqual(first.cache_id, second.cache_id)

        self.cmds.connections["|model|bone"] = ["|other.output", "|model|bone.rotateX"]
        self.cmds.uuid_by_node["|other"] = "uuid-other"
        self.cmds.type_by_node["|other"] = "network"
        changed = self.backend.discover(_request())
        self.assertNotEqual(first.dependency_closure_fingerprint, changed.dependency_closure_fingerprint)

    def test_self_connection_with_short_plugs_preserves_direction_and_fingerprint(self):
        # Maya can return short plug names for a full-path DAG node.  A
        # parentConstraint also exposes valid source/destination pairs on the
        # same node (for example targetWeight <- parentW0), so node identity
        # alone cannot determine the direction.
        self.cmds.long_name_aliases["bone"] = "|model|bone"
        self.cmds.connections["|model|bone"] = ["bone.targetWeight", "bone.parentW0"]
        self.cmds.incoming["|model|bone"] = ["bone.parentW0"]

        first = self.backend.discover(_request())
        _, topology = self.backend._dependency_closure("|model", _request()["options"])
        self.assertIn(
            ("uuid-bone", "parentW0", "uuid-bone", "targetWeight"),
            topology,
        )

        self.cmds.connections["|model|bone"][0] = "bone.targetWeight2"
        changed = self.backend.discover(_request())
        self.assertNotEqual(
            first.dependency_closure_fingerprint,
            changed.dependency_closure_fingerprint,
        )

    def test_self_connection_with_both_endpoints_marked_upstream_fails_closed(self):
        self.cmds.long_name_aliases["bone"] = "|model|bone"
        self.cmds.connections["|model|bone"] = ["bone.targetWeight", "bone.parentW0"]
        self.cmds.incoming["|model|bone"] = ["bone.targetWeight", "bone.parentW0"]

        with self.assertRaisesRegex(PrepareVmdExportError, "ambiguous connection topology"):
            self.backend.discover(_request())

    def test_model_name_prefers_request_then_mmd_attribute_then_identity(self):
        self.cmds.getAttr = lambda plug: "Imported Name" if plug == "|model.mmd_model_name" else None
        self.assertEqual(self.backend.discover(_request()).model_name, "Imported Name")
        self.assertEqual(
            self.backend.discover(_request(model_name="Requested Name")).model_name,
            "Requested Name",
        )
        self.cmds.getAttr = lambda plug: None
        self.assertEqual(self.backend.discover(_request()).model_name, "|model")

    def test_upstream_closure_does_not_expand_downstream_scene(self):
        self.cmds.uuid_by_node["|consumer"] = "uuid-consumer"
        self.cmds.connections["|model|bone"] = ["|model|bone.rotateX", "|curve.output"]
        self.cmds.connections["|consumer"] = ["|consumer.input", "|model|bone.rotateX"]
        discovery = self.backend.discover(_request())
        self.assertNotIn("uuid-consumer", discovery.route.dependency_uuids)

    def test_nested_blendshape_topology_preserves_full_attribute_path(self):
        destination = (
            "bakeTimelineBlendShape.inputTarget[0].inputTargetGroup[0]."
            "inputTargetItem[6000].inputGeomTarget"
        )
        self.cmds.connections["bakeTimelineBlendShape"] = [
            destination,
            "sourceShape.worldMesh[0]",
        ]
        self.cmds.incoming["bakeTimelineBlendShape"] = ["sourceShape.worldMesh[0]"]

        first = self.backend.discover(_request(blend_shapes=["bakeTimelineBlendShape"]))

        self.assertIn("uuid-source-shape", first.route.dependency_uuids)
        self.assertEqual(
            self.backend._split_plug(destination),
            (
                "bakeTimelineBlendShape",
                "inputTarget[0].inputTargetGroup[0]."
                "inputTargetItem[6000].inputGeomTarget",
            ),
        )

        self.cmds.connections["bakeTimelineBlendShape"][0] = destination.replace(
            "inputTargetItem[6000]",
            "inputTargetItem[6001]",
        )
        changed = self.backend.discover(
            _request(blend_shapes=["bakeTimelineBlendShape"])
        )

        self.assertNotEqual(
            first.dependency_closure_fingerprint,
            changed.dependency_closure_fingerprint,
        )

    def test_time_driver_is_fingerprinted_but_not_watched(self):
        self.cmds.connections["|model|bone"] = [
            "|model|bone.rotateX",
            "|rotationTime.output",
        ]
        self.cmds.incoming["|model|bone"] = ["|rotationTime.output"]
        self.cmds.connections["|rotationTime"] = [
            "|rotationTime.inputTime",
            "|time1.outTime",
        ]
        self.cmds.incoming["|rotationTime"] = ["|time1.outTime"]
        backend = MayaVmdPrepareBackend(
            self.cmds,
            collector=self.collector,
            revision_service=self.service,
            mobject_resolver=lambda node: node,
        )

        discovery = backend.discover(_request())
        backend.arm(_request(), discovery)

        self.assertIn("uuid-time", discovery.route.dependency_uuids)
        self.assertIn("uuid-rotation-time", discovery.route.dependency_uuids)
        watched = self.service.watches[-1].dependencies
        self.assertNotIn("|time1", watched)
        self.assertIn("|rotationTime", watched)
        self.assertIn("|model|bone", watched)

        self.cmds.connections["|rotationTime"][1] = "|time1.unwarpedTime"
        self.cmds.incoming["|rotationTime"] = ["|time1.unwarpedTime"]
        changed = backend.discover(_request())

        self.assertNotEqual(
            discovery.dependency_closure_fingerprint,
            changed.dependency_closure_fingerprint,
        )

    def test_arm_streams_once_with_current_model_bake_timeline_options(self):
        discovery = self.backend.discover(_request())
        self.backend.arm(_request(), discovery)
        self.assertTrue(self.service.watches[0].dependencies)
        self.assertTrue(all(value in self.mobjects.values() for value in self.service.watches[0].dependencies))
        self.assertEqual(self.backend.current_revision(_request(), discovery), "3:0")
        sink = _FakeSink()
        prepared = self.backend.collect_to_sink(_request(), sink)
        self.assertEqual(len(self.collector.stream_calls), 1)
        options = self.collector.stream_calls[0][0]
        self.assertEqual(options["target_model"], "|model")
        self.assertEqual(options["export_strategy"], "bake_timeline")
        diagnostics = self.backend.diagnostics
        self.assertIn("dependency_discovery", diagnostics)
        self.assertIn("raw_collector", diagnostics)
        self.assertTrue(diagnostics["raw_collector"]["streaming"])
        self.assertGreaterEqual(diagnostics["collect_total"], 0.0)
        self.assertEqual(prepared["section_counts"]["bones"], 0)

    def test_stream_capability_is_explicit(self):
        collector = _StreamingCollector()
        backend = MayaVmdPrepareBackend(
            self.cmds,
            collector=collector,
            revision_service=self.service,
            mobject_resolver=lambda node: self.mobjects.setdefault(node, object()),
        )
        self.assertTrue(backend.supports_streaming())
        discovery = backend.discover(_request(model_name="header"))
        backend.arm(_request(model_name="header"), discovery)
        sink = _FakeSink()
        result = backend.collect_to_sink(_request(model_name="header"), sink)

        self.assertEqual(len(collector.stream_calls), 1)
        self.assertEqual(sink.sections, ["bones", "morphs", "cameras", "lights", "shadows", "ik"])
        self.assertEqual(result["validation_frame_range"], [0, 10])
        self.assertIn("track_selection", backend.diagnostics["collector"])

    def test_legacy_injected_collector_does_not_claim_stream_capability(self):
        backend = MayaVmdPrepareBackend(self.cmds, collector=_FakeCollector())
        self.assertFalse(backend.supports_streaming())

    def test_optional_diagnostics_sink_is_wired_through_factory_and_backend(self):
        events = []
        sink = events.append
        action = create_maya_vmd_prepare_action(diagnostics_sink=sink)
        self.assertIs(action._boundary._diagnostics_sink, sink)

        backend = MayaVmdPrepareBackend(
            self.cmds,
            collector=self.collector,
            revision_service=self.service,
            mobject_resolver=lambda node: self.mobjects.setdefault(node, object()),
            diagnostics_sink=sink,
        )
        discovery = backend.discover(_request())
        backend.arm(_request(), discovery)
        backend.collect_to_sink(_request(), _FakeSink())
        self.assertTrue(events)
        self.assertIn("dependency_discovery", events[0])

    def test_disabled_or_stale_watch_fails_closed(self):
        discovery = self.backend.discover(_request())
        watch = self.backend.arm(_request(), discovery)
        watch.stale = True
        with self.assertRaises(PrepareVmdExportError):
            self.backend.current_revision(_request(), discovery)
        with self.assertRaises(PrepareVmdExportError):
            self.backend.collect_to_sink(_request(), _FakeSink())

    def test_reprepare_replaces_watch_and_invalidates_older_token(self):
        action = PrepareVmdExportAction(self.backend)
        with tempfile.TemporaryDirectory() as directory:
            request = _request(output_path=str(Path(directory) / "motion.vmd"))
            token = action.prepare(request)
            first_stage = Path(token.staged_artifact.file_path)
            self.assertEqual(len(self.service.watches), 1)
            replacement = action.prepare(request)
            replacement_stage = Path(replacement.staged_artifact.file_path)
            self.assertTrue(self.service.watches[0].closed)
            with self.assertRaisesRegex(ValueError, "token is not active"):
                action.validate_token(request, token)
            self.assertFalse(first_stage.exists())
            action.invalidate(replacement)
            self.assertFalse(replacement_stage.exists())

    def test_prepare_action_rejects_caller_uuid_assertion_mismatch(self):
        action = PrepareVmdExportAction(self.backend)
        result = action.execute(_request(target_uuid="caller-uuid"))
        self.assertEqual(result.status, "failed")
        self.assertIsNone(result.token)
        self.assertIn("target_uuid", str(result.error))
        self.assertEqual(self.collector.stream_calls, [])


if __name__ == "__main__":
    unittest.main()
