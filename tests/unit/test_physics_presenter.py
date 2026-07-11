"""PhysicsPresenterのMaya非依存ロジックを検証するテスト。"""

import json
import unittest
from pathlib import Path

from tests.common.maya_stub import install_headless_ui_stubs

install_headless_ui_stubs()

from mmd_tools.core.physics_scene_query import (  # noqa: E402
    JointSceneRef,
    PhysicsSceneRefs,
    RigidBodySceneRef,
)
from mmd_tools.core.physics_form_validation import JointFormValues, RigidBodyFormValues  # noqa: E402
from mmd_tools.core.physics_scene_writer import PhysicsSceneWriteError  # noqa: E402
from mmd_tools.ui.presenters.physics_presenter import (  # noqa: E402
    PhysicsPresenter,
    _format_non_collision_groups,
)
from mmd_tools.ui.qt_compat import Qt  # noqa: E402

TEST_MODEL = "|test_mmd_model"


class _FakeSignal:
    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in self._callbacks:
            callback(*args)


class _FakeList:
    def __init__(self):
        self.clear_calls = 0
        self.items = []
        self.itemSelectionChanged = _FakeSignal()
        self._selected_items = []
        self._signals_blocked = False
        self.block_signals_calls = []

    def clear(self):
        self.clear_calls += 1
        self.items.clear()
        self._selected_items.clear()

    def addItem(self, item):
        # Ensure setHidden/isHidden track state on stub QListWidgetItem.
        if not hasattr(item, "_filter_hidden"):
            item._filter_hidden = False

            def setHidden(hidden, _item=item):
                _item._filter_hidden = bool(hidden)

            def isHidden(_item=item):
                return bool(_item._filter_hidden)

            item.setHidden = setHidden
            item.isHidden = isHidden
        self.items.append(item)

    def count(self):
        return len(self.items)

    def item(self, index):
        return self.items[index]

    def selectedItems(self):
        return list(self._selected_items)

    def clearSelection(self):
        self._selected_items.clear()
        if not self._signals_blocked:
            self.itemSelectionChanged.emit()

    def blockSignals(self, block):
        previous = self._signals_blocked
        self._signals_blocked = bool(block)
        self.block_signals_calls.append(bool(block))
        return previous

    def select_items(self, *items):
        self._selected_items = list(items)
        if not self._signals_blocked:
            self.itemSelectionChanged.emit()


class _FakeButton:
    def __init__(self):
        self.clicked = _FakeSignal()
        self.enabled = False

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)

    def isEnabled(self):
        return self.enabled


class _FakeCheck:
    def __init__(self, checked=True):
        self._checked = checked
        self.toggled = _FakeSignal()

    def isChecked(self):
        return self._checked

    def set_checked(self, checked):
        self._checked = checked
        self.toggled.emit(checked)

    def setChecked(self, checked):
        self._checked = checked


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class _FakeLineEdit:
    def __init__(self):
        self.textChanged = _FakeSignal()
        self._text = ""

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text


class _FakeTabWidget:
    def __init__(self):
        self.currentChanged = _FakeSignal()
        self._index = 0

    def currentIndex(self):
        return self._index

    def setCurrentIndex(self, index):
        self._index = index
        self.currentChanged.emit(index)


class _FakeSplitter:
    def __init__(self):
        self._sizes = [400, 600]

    def sizes(self):
        return list(self._sizes)

    def setSizes(self, sizes):
        self._sizes = list(sizes)


class _FakeView:
    def __init__(self):
        self.rigid_body_list = _FakeList()
        self.joint_list = _FakeList()
        self.refresh_btn = _FakeButton()
        self.apply_btn = _FakeButton()
        self.reset_btn = _FakeButton()
        self.collider_visible_check = _FakeCheck()
        self.detail_name_value = _FakeLabel()
        self.detail_type_value = _FakeLabel()
        self.detail_shape_value = _FakeLabel()
        self.detail_bodies_value = _FakeLabel()
        self.detail_node_value = _FakeLabel()
        self.rigid_body_search_edit = _FakeLineEdit()
        self.joint_search_edit = _FakeLineEdit()
        self.list_tabs = _FakeTabWidget()
        self.splitter = _FakeSplitter()
        self.physics_form_changed = _FakeSignal()
        self.form_kind = None
        self.form_values = {}
        self.form_dirty = False
        self.validation_error = None
        self.details_enabled = None
        self.details_enabled_calls = []

    def set_physics_details_enabled(self, enabled):
        self.details_enabled = bool(enabled)
        self.details_enabled_calls.append(bool(enabled))
        if not enabled:
            self.set_physics_dirty(False)

    def set_physics_form(self, kind, values):
        self.form_kind = kind
        self.form_values = dict(values)
        self.set_physics_dirty(False)
        self.set_physics_validation_error()

    def get_physics_form_values(self, _kind):
        return dict(self.form_values)

    def set_physics_dirty(self, dirty, valid=False):
        self.form_dirty = bool(dirty)
        enabled = self.form_dirty and bool(self.details_enabled)
        self.apply_btn.setEnabled(enabled and bool(valid))
        self.reset_btn.setEnabled(enabled)

    def set_physics_validation_error(self, field_key=None, message_key=None, params=None):
        if not field_key or not message_key:
            self.validation_error = None
        else:
            self.validation_error = (field_key, message_key, dict(params or {}))


class _FakeAppState:
    def __init__(self, current_model_root=None):
        self.current_model_root = current_model_root
        self.current_model_changed = _FakeSignal()


class _FakeMayaAdapter:
    def __init__(self, exists=True, existing_nodes=None):
        self.exists = exists
        self.existing_nodes = set(existing_nodes or [])
        self.calls = []
        self.attrs = {}
        self.incoming_connections = {}
        self.created_nodes = []
        self.joints = []

    def ls(self, *_args, **_kwargs):
        return list(self.joints)

    def object_exists(self, node):
        self.calls.append(("object_exists", node))
        return self.exists and (not self.existing_nodes or node in self.existing_nodes or node == TEST_MODEL)

    def select(self, nodes, replace=True):
        self.calls.append(("select", tuple(nodes), replace))
        if getattr(self, "select_error", None) is not None:
            raise self.select_error

    def set_attr(self, *args, **kwargs):
        self.calls.append(("set_attr", args, kwargs))
        node, attr = args[0].rsplit(".", 1)
        self.attrs[(node, attr)] = args[1]

    def get_attr(self, attr_path):
        node, attr = attr_path.rsplit(".", 1)
        if attr == "colliderShapeType":
            return 3
        if attr == "radius":
            return 0.5
        if attr == "length":
            return 2.5
        if attr.startswith("colliderShapeSize"):
            return 1.0
        return self.attrs[(node, attr)]

    def attribute_exists(self, attr, node):
        return (node, attr) in self.attrs

    def add_attr(self, node, longName=None, attributeType=None, **kwargs):
        self.calls.append(("add_attr", node, longName, attributeType))
        self.attrs[(node, longName)] = False

    def create_node(self, node_type, name=None, parent=None):
        created = f"{parent}|{name or node_type}"
        self.calls.append(("create_node", node_type, name, parent))
        self.existing_nodes.add(created)
        self.created_nodes.append(created)
        return created

    def all_node_types(self):
        return ["mmdRigidBodyLocator"]

    def list_relatives(self, node, **kwargs):
        if kwargs.get("type") == "mmdRigidBodyLocator":
            return [item for item in self.existing_nodes if "mmdRigidBodyLocator" in item or item.endswith("_colliderLocatorShape")]
        if kwargs.get("type") == "transform":
            return [item for item in self.existing_nodes if item.endswith("_colliderCurve")]
        return []

    def list_connections(self, node, **kwargs):
        if kwargs.get("source") and kwargs.get("plugs"):
            return self.incoming_connections.get(node, [])
        return []

    def connect_attr(self, source, destination, force=False):
        self.calls.append(("connect_attr", source, destination, force))
        self.incoming_connections[destination] = [source]


class _FakePhysicsReader:
    def __init__(self, refs=None):
        self.refs = refs or PhysicsSceneRefs((), ())
        self.calls = []

    def collect(self, root):
        self.calls.append(root)
        return self.refs


class _RootPhysicsReader:
    def __init__(self, refs_by_root):
        self.refs_by_root = refs_by_root
        self.calls = []

    def collect(self, root):
        self.calls.append(root)
        return self.refs_by_root.get(root, PhysicsSceneRefs((), ()))


class _FakePhysicsWriter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def apply_rigid_body(self, ref, values):
        self.calls.append(("rigid", ref, values))
        if self.error is not None:
            raise self.error

    def apply_joint(self, ref, values):
        self.calls.append(("joint", ref, values))
        if self.error is not None:
            raise self.error


def _rigid(
    transform,
    index,
    name,
    shape_type=0,
    physics_mode=0,
    bone=-1,
    locator_shape=None,
    name_english=None,
    **fields,
):
    return RigidBodySceneRef(
        transform=transform,
        bullet_shape=f"{transform}|bulletRigidBodyShape",
        index=index,
        name=name,
        name_english=name_english if name_english is not None else name,
        shape_type=shape_type,
        physics_mode=physics_mode,
        related_bone_index=bone,
        locator_shape=locator_shape,
        **fields,
    )


def _joint(transform, name, joint_type=0, body_a=-1, body_b=-1, name_english=None, index=0, **fields):
    return JointSceneRef(
        transform=transform,
        constraint_shape=f"{transform}|bulletRigidBodyConstraintShape",
        name=name,
        name_english=name_english if name_english is not None else name,
        joint_type=joint_type,
        rigid_body_a_index=body_a,
        rigid_body_b_index=body_b,
        index=index,
        **fields,
    )


def _make_presenter(model=TEST_MODEL, adapter=None, reader=None, writer=None):
    view = _FakeView()
    app_state = _FakeAppState(model)
    adapter = adapter or _FakeMayaAdapter()
    reader = reader or _FakePhysicsReader()
    presenter = PhysicsPresenter(
        view,
        app_state,
        maya_adapter=adapter,
        physics_reader=reader,
        physics_writer=writer,
    )
    return presenter, view, app_state, adapter, reader


def _hidden_flags(list_widget):
    return [item.isHidden() for item in list_widget.items]


class TestPhysicsPresenter(unittest.TestCase):
    def test_non_collision_group_label_is_explicit_in_all_translations(self):
        translation_dir = Path(__file__).parents[2] / "mmd_tools" / "ui" / "translations"
        expected = {
            "ja": "非衝突グループ:",
            "en": "Non-collision Groups:",
            "zh_cn": "非碰撞组:",
            "zh_tw": "非碰撞群組:",
        }
        for language, label in expected.items():
            data = json.loads((translation_dir / f"{language}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["fields"]["non_collision_groups"], label)

    def test_collision_mask_formats_non_collision_groups(self):
        self.assertEqual(
            _format_non_collision_groups(0x0000),
            ", ".join(str(index) for index in range(1, 17)),
        )
        self.assertEqual(_format_non_collision_groups(0xFFFF), "None")
        self.assertEqual(_format_non_collision_groups(0xFF7F), "8")

    def test_load_physics_clears_and_returns_when_no_model(self):
        presenter, view, _, adapter, reader = _make_presenter(model=None)

        presenter.load_physics()

        self.assertEqual(view.rigid_body_list.clear_calls, 1)
        self.assertEqual(view.joint_list.clear_calls, 1)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(reader.calls, [])
        self.assertEqual(view.rigid_body_list.items, [])
        self.assertEqual(view.joint_list.items, [])
        self.assertEqual(view.detail_name_value.text, "None")
        self.assertFalse(view.details_enabled)

    def test_load_physics_returns_when_model_does_not_exist(self):
        adapter = _FakeMayaAdapter(exists=False)
        presenter, view, _, adapter, reader = _make_presenter(adapter=adapter)

        presenter.load_physics()

        self.assertEqual(adapter.calls, [("object_exists", TEST_MODEL)])
        self.assertEqual(reader.calls, [])
        self.assertEqual(view.rigid_body_list.items, [])
        self.assertEqual(view.joint_list.items, [])
        self.assertFalse(view.details_enabled)

    def test_load_physics_adds_root_scoped_bullet_metadata_items(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb2", 2, "skirt", shape_type=1, physics_mode=2, bone=9),
                _rigid("|root|rb5", 5, "hair", shape_type=2, physics_mode=1, bone=3),
            ),
            joints=(
                _joint("|root|jointB", "jointB", joint_type=4, body_a=2, body_b=5, index=12),
            ),
        )
        reader = _FakePhysicsReader(refs)
        presenter, view, _, adapter, reader = _make_presenter(reader=reader)

        presenter.load_physics()

        self.assertEqual(adapter.calls[0], ("object_exists", TEST_MODEL))
        self.assertEqual(reader.calls, [TEST_MODEL])
        self.assertEqual([item.text() for item in view.rigid_body_list.items], [
            "002:G1|skirt - [Unknown]",
            "005:G1|hair - [Unknown]",
        ])
        self.assertEqual(view.rigid_body_list.items[0].data(Qt.UserRole), "|root|rb2")
        self.assertEqual([item.text() for item in view.joint_list.items], [
            "012:jointB - [skirt - hair]",
        ])
        self.assertEqual(view.joint_list.items[0].data(Qt.UserRole), "|root|jointB")
        self.assertEqual(view.rigid_body_list.items[0]._physics_mode_color, "#5f477f")
        self.assertEqual(view.rigid_body_list.items[1]._physics_mode_color, "#8a493f")
        self.assertFalse(view.details_enabled)

    def test_list_labels_resolve_bones_and_search_full_formatted_label(self):
        rigid = _rigid(
            "|root|rb2",
            2,
            "skirt",
            physics_mode=1,
            bone=9,
            collision_group=7,
        )
        adapter = _FakeMayaAdapter(existing_nodes={TEST_MODEL, rigid.transform, "|root|hips"})
        adapter.joints = ["|root|hips"]
        adapter.attrs[("|root|hips", "mmd_bone_index")] = 9
        adapter.attrs[("|root|hips", "mmd_bone_name")] = "センター"
        presenter, view, _, _, _ = _make_presenter(
            adapter=adapter,
            reader=_FakePhysicsReader(PhysicsSceneRefs((rigid,), ())),
        )

        presenter.load_physics()

        self.assertEqual(view.rigid_body_list.items[0].text(), "002:G8|skirt - [センター]")
        presenter.filter_rigid_bodies("G8")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [False])
        presenter.filter_rigid_bodies("センター")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [False])

    def test_valid_load_keeps_details_disabled_until_selection(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(_rigid("|root|rb2", 2, "skirt"),),
            joints=(),
        )
        presenter, view, _, _, _ = _make_presenter(reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        self.assertEqual(len(view.rigid_body_list.items), 1)
        self.assertFalse(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "None")

        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        self.assertTrue(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "skirt")
        self.assertEqual(view.form_kind, "rigid")
        self.assertFalse(view.form_dirty)

    def test_rigid_form_populates_dirty_and_resets_without_scene_write(self):
        rigid = _rigid(
            "|root|rb2",
            2,
            "skirt",
            shape_type=1,
            physics_mode=2,
            bone=9,
            name_english="Skirt",
            collision_group=7,
            collision_mask=0xFF7F,
            mass=2.5,
            linear_damping=0.15,
            angular_damping=0.25,
            restitution=0.35,
            friction=0.45,
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
        presenter, view, _, adapter, _ = _make_presenter(
            adapter=adapter,
            reader=_FakePhysicsReader(PhysicsSceneRefs((rigid,), ())),
        )
        presenter.load_physics()
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])

        self.assertEqual(view.form_kind, "rigid")
        self.assertEqual(
            view.form_values,
            {
                "name": "skirt",
                "name_english": "Skirt",
                "shape": 1,
                "physics_mode": 2,
                "related_bone": "Unknown (9)",
                "collision_group": 7,
                "collision_mask": "8",
                "mass": 2.5,
                "linear_damping": 0.15,
                "angular_damping": 0.25,
                "restitution": 0.35,
                "friction": 0.45,
                "node": "|root|rb2",
            },
        )
        self.assertFalse(view.apply_btn.isEnabled())
        self.assertFalse(view.reset_btn.isEnabled())
        writes_before = sum(1 for call in adapter.calls if call[0] == "set_attr")

        view.form_values["mass"] = 9.0
        view.physics_form_changed.emit()
        self.assertTrue(view.form_dirty)
        self.assertTrue(view.apply_btn.isEnabled())
        self.assertTrue(view.reset_btn.isEnabled())
        self.assertIsInstance(presenter.validated_form_values, RigidBodyFormValues)
        self.assertEqual(presenter.validated_form_values.mass, 9.0)
        self.assertIsNone(view.validation_error)

        view.form_values["mass"] = "nan"
        view.physics_form_changed.emit()
        self.assertFalse(view.apply_btn.isEnabled())
        self.assertTrue(view.reset_btn.isEnabled())
        self.assertIsNone(presenter.validated_form_values)
        self.assertEqual(view.validation_error[:2], ("mass", "physics_validation_finite"))

        view.reset_btn.clicked.emit()
        self.assertEqual(view.form_values["mass"], 2.5)
        self.assertFalse(view.form_dirty)
        self.assertFalse(view.apply_btn.isEnabled())
        self.assertIsNone(view.validation_error)
        self.assertIsNone(presenter.validated_form_values)
        writes_after = sum(1 for call in adapter.calls if call[0] == "set_attr")
        self.assertEqual(writes_after, writes_before)

    def test_joint_form_populates_all_cached_constraint_values(self):
        joint = _joint(
            "|root|jointB",
            "jointB",
            joint_type=4,
            body_a=2,
            body_b=5,
            name_english="Joint B",
            is_pmx=True,
            linear_constraint_states=(0, 1, 2),
            angular_constraint_states=(2, 1, 0),
            translation_limit_min=(-1.0, -2.0, -3.0),
            translation_limit_max=(1.0, 2.0, 3.0),
            rotation_limit_min_degrees=(-10.0, -20.0, -30.0),
            rotation_limit_max_degrees=(10.0, 20.0, 30.0),
            spring_translation=(0.12345678901234567, 0.2, 0.3),
            spring_rotation=(0.4, 0.5, 0.6),
            spring_translation_enabled=(True, False, True),
            spring_rotation_enabled=(False, True, False),
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|jointB"})
        presenter, view, _, _, _ = _make_presenter(
            adapter=adapter,
            reader=_FakePhysicsReader(PhysicsSceneRefs((), (joint,))),
        )
        presenter.load_physics()
        view.joint_list.select_items(view.joint_list.items[0])

        self.assertEqual(view.form_kind, "joint")
        self.assertEqual(view.form_values["name_english"], "Joint B")
        self.assertEqual(view.form_values["joint_type"], "Slider")
        self.assertEqual(view.form_values["rigid_body_a"], "Unknown (2)")
        self.assertEqual(view.form_values["rigid_body_b"], "Unknown (5)")
        self.assertEqual(view.form_values["linear_constraint_states"], "X: 0, Y: 1, Z: 2")
        self.assertEqual(view.form_values["angular_constraint_states"], "X: 2, Y: 1, Z: 0")
        self.assertEqual(view.form_values["translation_limit_min"], "X: -1.0, Y: -2.0, Z: -3.0")
        self.assertEqual(view.form_values["rotation_limit_max_degrees"], "X: 10.0, Y: 20.0, Z: 30.0")
        self.assertEqual(
            view.form_values["spring_translation"],
            "X: 0.12345678901234566, Y: 0.2, Z: 0.3",
        )
        self.assertEqual(view.form_values["spring_rotation_enabled"], "X: 0, Y: 1, Z: 0")
        self.assertFalse(view.form_dirty)

        view.form_values["linear_constraint_states"] = "0, 2, 1"
        view.physics_form_changed.emit()
        self.assertTrue(view.apply_btn.isEnabled())
        self.assertIsInstance(presenter.validated_form_values, JointFormValues)
        self.assertEqual(presenter.validated_form_values.linear_constraint_states, (0, 2, 1))

        view.form_values["spring_rotation_enabled"] = "0, true, 1"
        view.physics_form_changed.emit()
        self.assertFalse(view.apply_btn.isEnabled())
        self.assertTrue(view.reset_btn.isEnabled())
        self.assertEqual(
            view.validation_error[:2],
            ("spring_rotation_enabled", "physics_validation_bool"),
        )

    def test_apply_valid_rigid_form_writes_then_recollects_scene(self):
        rigid = _rigid("|root|rb2", 2, "skirt", mass=2.5)
        reader = _FakePhysicsReader(PhysicsSceneRefs((rigid,), ()))
        writer = _FakePhysicsWriter()
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
        presenter, view, _, _, _ = _make_presenter(
            adapter=adapter,
            reader=reader,
            writer=writer,
        )
        presenter.load_physics()
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        view.form_values["mass"] = "3.25"
        view.physics_form_changed.emit()

        view.apply_btn.clicked.emit()

        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(writer.calls[0][0], "rigid")
        self.assertEqual(writer.calls[0][2].mass, 3.25)
        self.assertEqual(reader.calls, [TEST_MODEL, TEST_MODEL])
        self.assertIsNone(presenter.validated_form_values)
        self.assertIsNone(view.validation_error)
        self.assertFalse(view.form_dirty)
        self.assertTrue(view.details_enabled)
        self.assertEqual(
            view.rigid_body_list.selectedItems()[0].data(Qt.UserRole),
            rigid.transform,
        )

    def test_apply_failure_keeps_dirty_and_shows_error_without_recollect(self):
        rigid = _rigid("|root|rb2", 2, "skirt", mass=2.5)
        reader = _FakePhysicsReader(PhysicsSceneRefs((rigid,), ()))
        writer = _FakePhysicsWriter(
            PhysicsSceneWriteError("mass", "physics_write_failed", error="setAttr failed")
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
        presenter, view, _, _, _ = _make_presenter(
            adapter=adapter,
            reader=reader,
            writer=writer,
        )
        presenter.load_physics()
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        view.form_values["mass"] = "3.25"
        view.physics_form_changed.emit()

        view.apply_btn.clicked.emit()

        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(reader.calls, [TEST_MODEL])
        self.assertTrue(view.form_dirty)
        self.assertTrue(view.apply_btn.isEnabled())
        self.assertTrue(view.reset_btn.isEnabled())
        self.assertEqual(view.validation_error[:2], ("mass", "physics_write_failed"))
        self.assertIsInstance(presenter.validated_form_values, RigidBodyFormValues)

        # A transient write failure must be retryable with the same form value.
        writer.error = None
        view.apply_btn.clicked.emit()
        self.assertEqual(len(writer.calls), 2)
        self.assertEqual(reader.calls, [TEST_MODEL, TEST_MODEL])
        self.assertIsNone(presenter.validated_form_values)
        self.assertFalse(view.form_dirty)

    def test_apply_rejects_stale_validated_ref_identity(self):
        rigid = _rigid("|root|rb2", 2, "skirt", mass=2.5)
        writer = _FakePhysicsWriter()
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
        presenter, view, _, _, _ = _make_presenter(
            adapter=adapter,
            reader=_FakePhysicsReader(PhysicsSceneRefs((rigid,), ())),
            writer=writer,
        )
        presenter.load_physics()
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        view.form_values["mass"] = "3.25"
        view.physics_form_changed.emit()
        presenter._current_physics_ref = _rigid("|root|rb2", 2, "skirt", mass=2.5)

        presenter.apply_physics_form()

        self.assertEqual(writer.calls, [])
        self.assertEqual(view.validation_error[:2], ("node", "physics_write_stale_form"))
        self.assertIsNone(presenter.validated_form_values)
        self.assertFalse(view.apply_btn.isEnabled())
        self.assertTrue(view.reset_btn.isEnabled())

    def test_apply_only_general_write_failure_is_retryable(self):
        error_codes = (
            "physics_write_node_missing",
            "physics_write_attribute_missing",
            "physics_write_attribute_not_settable",
            "physics_write_undo_disabled",
            "physics_write_preflight_failed",
            "physics_write_stale_form",
            "physics_write_rollback_failed",
            "physics_write_failed",
        )
        for error_code in error_codes:
            with self.subTest(error_code=error_code):
                rigid = _rigid("|root|rb2", 2, "skirt", mass=2.5)
                writer = _FakePhysicsWriter(
                    PhysicsSceneWriteError("mass", error_code, error="write failed")
                )
                adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
                presenter, view, _, _, _ = _make_presenter(
                    adapter=adapter,
                    reader=_FakePhysicsReader(PhysicsSceneRefs((rigid,), ())),
                    writer=writer,
                )
                presenter.load_physics()
                view.rigid_body_list.select_items(view.rigid_body_list.items[0])
                view.form_values["mass"] = "3.25"
                view.physics_form_changed.emit()

                view.apply_btn.clicked.emit()

                retryable = error_code == "physics_write_failed"
                self.assertEqual(len(writer.calls), 1)
                self.assertEqual(view.apply_btn.isEnabled(), retryable)
                self.assertEqual(
                    presenter.validated_form_values is not None,
                    retryable,
                )
                self.assertTrue(view.reset_btn.isEnabled())

    def test_rigid_body_selection_selects_user_role_transform(self):
        refs = PhysicsSceneRefs(rigid_bodies=(_rigid("|root|rb2", 2, "skirt"),), joints=())
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.rigid_body_list.select_items(view.rigid_body_list.items[0])

        self.assertEqual(adapter.calls[-2:], [
            ("object_exists", "|root|rb2"),
            ("select", ("|root|rb2",), True),
        ])
        self.assertTrue(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "skirt")
        self.assertEqual(view.detail_type_value.text, "Rigid body")
        self.assertEqual(view.detail_shape_value.text, "sphere, mode=0")
        self.assertEqual(view.detail_bodies_value.text, "bone=-1")
        self.assertEqual(view.detail_node_value.text, "|root|rb2")

    def test_selection_sync_failure_keeps_valid_cached_details(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(_rigid("|root|rb2", 2, "skirt"),),
            joints=(_joint("|root|jointB", "jointB"),),
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2", "|root|jointB"})
        adapter.select_error = RuntimeError("selection temporarily unavailable")
        presenter, view, _, _, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        self.assertTrue(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "skirt")
        self.assertEqual(view.detail_type_value.text, "Rigid body")

        view.joint_list.select_items(view.joint_list.items[0])
        self.assertTrue(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "jointB")
        self.assertEqual(view.detail_type_value.text, "Joint")

    def test_joint_selection_ignores_missing_transform(self):
        refs = PhysicsSceneRefs(rigid_bodies=(), joints=(_joint("|root|jointB", "jointB"),))
        adapter = _FakeMayaAdapter(existing_nodes={"|root|other"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.joint_list.select_items(view.joint_list.items[0])

        self.assertNotIn(("select", ("|root|jointB",), True), adapter.calls)
        self.assertFalse(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "None")
        self.assertEqual(view.detail_type_value.text, "")

    def test_selection_clears_opposite_list_and_missing_selection_disables(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(_rigid("|root|rb2", 2, "skirt"),),
            joints=(_joint("|root|jointB", "jointB"),),
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2", "|root|jointB"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        self.assertTrue(view.details_enabled)
        self.assertEqual(view.detail_type_value.text, "Rigid body")

        view.joint_list.select_items(view.joint_list.items[0])
        self.assertEqual(view.rigid_body_list.selectedItems(), [])
        self.assertTrue(True in view.rigid_body_list.block_signals_calls)
        self.assertEqual(view.detail_type_value.text, "Joint")
        self.assertTrue(view.details_enabled)

        view.joint_list.select_items()  # clear selection
        self.assertFalse(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "None")

    def test_filter_rigid_bodies_matches_text_transform_and_names(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb_skirt", 2, "スカート", name_english="Skirt"),
                _rigid("|root|rb_hair", 5, "髪", name_english="Hair"),
            ),
            joints=(),
        )
        presenter, view, _, adapter, _ = _make_presenter(reader=_FakePhysicsReader(refs))
        presenter.load_physics()
        object_exists_calls_before = sum(1 for c in adapter.calls if c[0] == "object_exists")

        presenter.filter_rigid_bodies("hair")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [True, False])

        presenter.filter_rigid_bodies("|root|rb_skirt")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [False, True])

        presenter.filter_rigid_bodies("スカート")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [False, True])

        presenter.filter_rigid_bodies("Skirt")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [False, True])

        presenter.filter_rigid_bodies("")
        self.assertEqual(_hidden_flags(view.rigid_body_list), [False, False])

        # Filtering must not query Maya per keystroke.
        object_exists_calls_after = sum(1 for c in adapter.calls if c[0] == "object_exists")
        self.assertEqual(object_exists_calls_after, object_exists_calls_before)

    def test_filter_joints_matches_text_transform_and_names(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(),
            joints=(
                _joint("|root|j_a", "ジョイントA", name_english="JointA"),
                _joint("|root|j_b", "ジョイントB", name_english="JointB"),
            ),
        )
        presenter, view, _, adapter, _ = _make_presenter(reader=_FakePhysicsReader(refs))
        presenter.load_physics()
        object_exists_calls_before = sum(1 for c in adapter.calls if c[0] == "object_exists")

        presenter.filter_joints("jointb")
        self.assertEqual(_hidden_flags(view.joint_list), [True, False])

        presenter.filter_joints("|root|j_a")
        self.assertEqual(_hidden_flags(view.joint_list), [False, True])

        presenter.filter_joints("ジョイントA")
        self.assertEqual(_hidden_flags(view.joint_list), [False, True])

        presenter.filter_joints("")
        self.assertEqual(_hidden_flags(view.joint_list), [False, False])

        object_exists_calls_after = sum(1 for c in adapter.calls if c[0] == "object_exists")
        self.assertEqual(object_exists_calls_after, object_exists_calls_before)

    def test_load_reapplies_existing_search_queries(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb_skirt", 2, "Skirt"),
                _rigid("|root|rb_hair", 5, "Hair"),
            ),
            joints=(
                _joint("|root|j_a", "JointA"),
                _joint("|root|j_b", "JointB"),
            ),
        )
        presenter, view, _, _, _ = _make_presenter(reader=_FakePhysicsReader(refs))
        view.rigid_body_search_edit.setText("hair")
        view.joint_search_edit.setText("jointb")

        presenter.load_physics()

        self.assertEqual(_hidden_flags(view.rigid_body_list), [True, False])
        self.assertEqual(_hidden_flags(view.joint_list), [True, False])

    def test_list_tab_change_clears_inactive_selection_and_resets_details(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(_rigid("|root|rb2", 2, "skirt"),),
            joints=(_joint("|root|jointB", "jointB"),),
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2", "|root|jointB"})
        presenter, view, _, _, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.rigid_body_list.select_items(view.rigid_body_list.items[0])
        self.assertTrue(view.details_enabled)

        # Switch to Joints tab (index 1): clear rigid selection, reset details.
        view.list_tabs.setCurrentIndex(1)
        self.assertEqual(view.rigid_body_list.selectedItems(), [])
        self.assertFalse(view.details_enabled)
        self.assertEqual(view.detail_name_value.text, "None")

        view.joint_list.select_items(view.joint_list.items[0])
        self.assertTrue(view.details_enabled)

        # Switch back to Rigid Bodies (index 0): clear joint selection, reset details.
        view.list_tabs.setCurrentIndex(0)
        self.assertEqual(view.joint_list.selectedItems(), [])
        self.assertFalse(view.details_enabled)

    def test_search_and_tab_signals_are_wired(self):
        presenter, view, _, _, _ = _make_presenter()
        self.assertIn(presenter.filter_rigid_bodies, view.rigid_body_search_edit.textChanged._callbacks)
        self.assertIn(presenter.filter_joints, view.joint_search_edit.textChanged._callbacks)
        self.assertIn(presenter.on_list_tab_changed, view.list_tabs.currentChanged._callbacks)

    def test_refresh_button_and_model_change_reload_physics(self):
        presenter, _, app_state, _, reader = _make_presenter()

        app_state.current_model_changed.emit(TEST_MODEL)
        presenter.view.refresh_btn.clicked.emit()

        self.assertEqual(reader.calls, [TEST_MODEL, TEST_MODEL])

    def test_model_switch_restores_root_scoped_ui_state_without_leak(self):
        root_a = "|modelA"
        root_b = "|modelB"
        root_c = "|modelC"
        rigid_a = _rigid("|modelA|rb", 0, "skirt")
        joint_b = _joint("|modelB|joint", "jointB")
        reader = _RootPhysicsReader(
            {
                root_a: PhysicsSceneRefs((rigid_a,), ()),
                root_b: PhysicsSceneRefs((), (joint_b,)),
                root_c: PhysicsSceneRefs((), ()),
            }
        )
        adapter = _FakeMayaAdapter(
            existing_nodes={root_a, root_b, root_c, rigid_a.transform, joint_b.transform}
        )
        presenter, view, app_state, _, _ = _make_presenter(
            model=root_a,
            adapter=adapter,
            reader=reader,
        )
        presenter.load_physics()
        view.rigid_body_search_edit.setText("skirt")
        presenter.filter_rigid_bodies("skirt")
        view.splitter.setSizes([300, 700])
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])

        app_state.current_model_root = root_b
        app_state.current_model_changed.emit(root_b)
        self.assertEqual(view.list_tabs.currentIndex(), 0)
        self.assertEqual(view.rigid_body_search_edit.text(), "")
        self.assertEqual(view.joint_search_edit.text(), "")
        self.assertEqual(view.splitter.sizes(), [400, 600])
        self.assertFalse(view.details_enabled)

        view.list_tabs.setCurrentIndex(1)
        view.joint_search_edit.setText("jointB")
        presenter.filter_joints("jointB")
        view.splitter.setSizes([250, 750])
        view.joint_list.select_items(view.joint_list.items[0])

        app_state.current_model_root = root_a
        app_state.current_model_changed.emit(root_a)
        self.assertEqual(view.list_tabs.currentIndex(), 0)
        self.assertEqual(view.rigid_body_search_edit.text(), "skirt")
        self.assertEqual(view.splitter.sizes(), [300, 700])
        self.assertEqual(
            view.rigid_body_list.selectedItems()[0].data(Qt.UserRole),
            rigid_a.transform,
        )
        self.assertTrue(view.details_enabled)

        app_state.current_model_root = root_b
        app_state.current_model_changed.emit(root_b)
        self.assertEqual(view.list_tabs.currentIndex(), 1)
        self.assertEqual(view.joint_search_edit.text(), "jointB")
        self.assertEqual(view.splitter.sizes(), [250, 750])
        self.assertEqual(
            view.joint_list.selectedItems()[0].data(Qt.UserRole),
            joint_b.transform,
        )

        app_state.current_model_root = root_c
        app_state.current_model_changed.emit(root_c)
        self.assertEqual(view.list_tabs.currentIndex(), 0)
        self.assertEqual(view.rigid_body_search_edit.text(), "")
        self.assertEqual(view.joint_search_edit.text(), "")
        self.assertEqual(view.splitter.sizes(), [400, 600])
        self.assertFalse(view.details_enabled)

    def test_refresh_preserves_active_root_ui_state_and_selection(self):
        rigid = _rigid("|root|rb2", 2, "skirt")
        reader = _FakePhysicsReader(PhysicsSceneRefs((rigid,), ()))
        adapter = _FakeMayaAdapter(existing_nodes={TEST_MODEL, rigid.transform})
        presenter, view, _, _, _ = _make_presenter(adapter=adapter, reader=reader)
        presenter.load_physics()
        view.rigid_body_search_edit.setText("skirt")
        presenter.filter_rigid_bodies("skirt")
        view.splitter.setSizes([320, 680])
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])

        view.refresh_btn.clicked.emit()

        self.assertEqual(reader.calls, [TEST_MODEL, TEST_MODEL])
        self.assertEqual(view.rigid_body_search_edit.text(), "skirt")
        self.assertEqual(view.splitter.sizes(), [320, 680])
        self.assertEqual(
            view.rigid_body_list.selectedItems()[0].data(Qt.UserRole),
            rigid.transform,
        )
        self.assertTrue(view.details_enabled)

        # Re-emitting the same root through ApplicationState preserves it too.
        view.splitter.setSizes([330, 670])
        app_state = presenter.app_state
        app_state.current_model_changed.emit(TEST_MODEL)
        self.assertEqual(view.rigid_body_search_edit.text(), "skirt")
        self.assertEqual(view.splitter.sizes(), [330, 670])
        self.assertEqual(
            view.rigid_body_list.selectedItems()[0].data(Qt.UserRole),
            rigid.transform,
        )

    def test_restore_drops_missing_or_filtered_selection(self):
        root_a = "|modelA"
        root_b = "|modelB"
        rigid_a = _rigid("|modelA|rb", 0, "skirt")
        reader = _RootPhysicsReader(
            {
                root_a: PhysicsSceneRefs((rigid_a,), ()),
                root_b: PhysicsSceneRefs((), ()),
            }
        )
        adapter = _FakeMayaAdapter(existing_nodes={root_a, root_b, rigid_a.transform})
        presenter, view, app_state, _, _ = _make_presenter(
            model=root_a,
            adapter=adapter,
            reader=reader,
        )
        presenter.load_physics()
        view.rigid_body_search_edit.setText("does-not-match")
        presenter.filter_rigid_bodies("does-not-match")
        # Store a selected transform even though the restored filter hides it.
        view.rigid_body_list.select_items(view.rigid_body_list.items[0])

        app_state.current_model_root = root_b
        app_state.current_model_changed.emit(root_b)
        app_state.current_model_root = root_a
        app_state.current_model_changed.emit(root_a)

        self.assertEqual(view.rigid_body_list.selectedItems(), [])
        self.assertFalse(view.details_enabled)

    def test_collider_visibility_toggle_sets_locator_shapes_only(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb2", 2, "skirt", locator_shape="|root|rb2|mmdRigidBodyLocator"),
                _rigid("|root|rb5", 5, "hair", locator_shape=None),
            ),
            joints=(),
        )
        adapter = _FakeMayaAdapter(existing_nodes={"|root|rb2|mmdRigidBodyLocator"})
        presenter, view, _, adapter, _ = _make_presenter(adapter=adapter, reader=_FakePhysicsReader(refs))
        presenter.load_physics()

        view.collider_visible_check.set_checked(False)

        self.assertIn(
            ("set_attr", (f"{TEST_MODEL}.mmd_show_physics_colliders", False), {}),
            adapter.calls,
        )
        self.assertIn(
            (
                "connect_attr",
                f"{TEST_MODEL}.mmd_show_physics_colliders",
                "|root|rb2|mmdRigidBodyLocator.drawEnabled",
                False,
            ),
            adapter.calls,
        )

    def test_load_physics_does_not_create_locator_when_bullet_shape_exists(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb5", 5, "hair", shape_type=2, locator_shape=None),
            ),
            joints=(),
        )
        adapter = _FakeMayaAdapter(
            existing_nodes={
                TEST_MODEL,
                "|root|rb5",
                "|root|rb5|bulletRigidBodyShape",
            }
        )
        presenter, _, _, adapter, _ = _make_presenter(
            adapter=adapter,
            reader=_FakePhysicsReader(refs),
        )

        presenter.load_physics()

        self.assertFalse(any(call[0] == "create_node" for call in adapter.calls))
        self.assertIsNone(presenter._rigid_bodies_by_transform["|root|rb5"].locator_shape)

    def test_load_physics_repairs_missing_collider_locator_when_bullet_shape_absent(self):
        refs = PhysicsSceneRefs(
            rigid_bodies=(
                _rigid("|root|rb5", 5, "hair", shape_type=2, locator_shape=None),
            ),
            joints=(),
        )
        # Bullet shape path is not present in existing_nodes => structurally absent.
        adapter = _FakeMayaAdapter(existing_nodes={TEST_MODEL, "|root|rb5"})
        presenter, _, _, adapter, _ = _make_presenter(
            adapter=adapter,
            reader=_FakePhysicsReader(refs),
        )

        presenter.load_physics()

        created = "|root|rb5|rb5_colliderLocatorShape"
        self.assertIn(("create_node", "mmdRigidBodyLocator", "rb5_colliderLocatorShape", "|root|rb5"), adapter.calls)
        self.assertEqual(presenter._rigid_bodies_by_transform["|root|rb5"].locator_shape, created)
        self.assertIn(("set_attr", (f"{created}.colliderShapeType", 3), {}), adapter.calls)
        self.assertIn(("set_attr", (f"{created}.radius", 0.5), {}), adapter.calls)
        self.assertIn(("set_attr", (f"{created}.length", 2.5), {}), adapter.calls)
        self.assertIn(
            (
                "connect_attr",
                f"{TEST_MODEL}.mmd_show_physics_colliders",
                f"{created}.drawEnabled",
                False,
            ),
            adapter.calls,
        )


if __name__ == "__main__":
    unittest.main()
