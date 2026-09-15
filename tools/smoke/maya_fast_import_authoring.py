"""Compare editable Fast Load scenes through GUI import, animation and reopen."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_breadcrumb(out, started, event, **fields):
    """Append flushed phase timing evidence while Maya is still running."""
    record = {
        "event": event,
        "elapsedSeconds": round(time.perf_counter() - started, 3),
        **fields,
    }
    with (Path(out) / "probe.log").open("a", encoding="utf-8", buffering=1) as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def _phase_record():
    """Return an explicit artifact phase record before its evidence exists."""
    return {"status": "not_run", "expected": "pass", "evidence": None}


def _phase_pass(report, name, route, evidence):
    """Record route evidence while preserving a fail/not_run distinction."""
    phase = report["phases"].setdefault(name, _phase_record())
    phase.setdefault("routes", {})[route] = {
        "status": "pass",
        "expected": "pass",
        "evidence": evidence,
    }
    if all(item.get("status") == "pass" for item in phase["routes"].values()):
        phase["status"] = "pass"
    else:
        phase["status"] = "fail"


def _phase_gate(report):
    """Turn every required artifact phase into an explicit overall check."""
    required = {
        "ui_binding_python": {"python"},
        "ui_binding_cpp": {"cpp"},
        "multi_import_identity": {"python", "cpp"},
        "failure_cleanup": {"python", "cpp"},
        "vmd_playback": {"python", "cpp"},
        "save_reopen": {"python", "cpp"},
        "scene_contract": set(),
    }
    for name, required_routes in required.items():
        phase = report["phases"].get(name, _phase_record())
        routes = phase.get("routes", {})
        missing_routes = sorted(required_routes - set(routes))
        route_pass = all(routes.get(route, {}).get("status") == "pass" for route in required_routes)
        phase_pass = phase.get("status") == "pass" and route_pass and not missing_routes
        if name == "scene_contract":
            phase_pass = phase.get("status") == "pass"
        report["checks"].append({
            "name": f"phase-{name}",
            "status": phase.get("status", "not_run"),
            "expected": "pass",
            "missingRoutes": missing_routes,
            "pass": phase_pass,
        })


def _json_safe(value):
    """Make request/result evidence serialisable without hiding its shape."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _import_result_witness(result):
    """Return the public result fields needed to classify a real action call."""
    return {
        "outcome": getattr(result, "outcome", None),
        "succeeded": bool(getattr(result, "succeeded", False)),
        "rootNode": getattr(result, "root_node", None),
        "warnings": _json_safe(getattr(result, "warnings", []) or []),
        "error": str(getattr(result, "error", "") or ""),
    }


def _install_import_observer(window, observations):
    """Observe the real ImportModelAction while always delegating to execute."""
    from mmd_tools.actions.import_model_action import ImportModelAction

    action = getattr(window.import_export_presenter, "import_model_action", None)
    if not isinstance(action, ImportModelAction):
        raise RuntimeError("ImportExportPresenter is not using the production ImportModelAction")
    return _observe_action(action, observations)


def _observe_action(action, observations):
    """Capture a production action's request/result while forwarding execution."""
    original_execute = action.execute

    def observe_execute(request):
        entry = {
            "request": {
                "type": type(request).__name__,
                "filePath": str(getattr(request, "file_path", "")),
                "createNewScene": bool(getattr(request, "create_new_scene", False)),
                "optionsBefore": _json_safe(getattr(request, "options", {})),
            }
        }
        observations.append(entry)
        result = original_execute(request)
        entry["request"]["optionsAfter"] = _json_safe(getattr(request, "options", {}))
        entry["result"] = _import_result_witness(result)
        return result

    action.execute = observe_execute

    def restore():
        action.execute = original_execute

    return restore


def _install_vmd_observer(window, observations):
    """Observe the production ImportVmdAction without replacing its execution."""
    from mmd_tools.actions.import_vmd_action import ImportVmdAction

    action = getattr(window.import_export_presenter, "import_vmd_action", None)
    if not isinstance(action, ImportVmdAction):
        raise RuntimeError("ImportExportPresenter is not using the production ImportVmdAction")
    return _observe_action(action, observations)


def _require_import_success(observations, expected_path, expected_root=None):
    """Reject missing, partial, or fatal action evidence for a UI import."""
    if not observations:
        raise RuntimeError("UI Import button produced no observed ImportModelAction call")
    entry = observations[-1]
    request = entry.get("request", {})
    result = entry.get("result", {})
    if request.get("filePath") != str(expected_path):
        raise RuntimeError(f"observed ImportModelAction path mismatch: {request.get('filePath')!r}")
    if result.get("outcome") != "success" or not result.get("succeeded") or result.get("warnings"):
        raise RuntimeError(f"UI model import was not a clean success: {result}")
    if expected_root is not None:
        from maya import cmds

        observed_roots = cmds.ls(result.get("rootNode"), long=True) or []
        expected_roots = cmds.ls(expected_root, long=True) or []
        if not observed_roots or not expected_roots or observed_roots[0] != expected_roots[0]:
            raise RuntimeError(
                f"observed ImportModelAction root mismatch: {result.get('rootNode')!r} != {expected_root!r}"
            )
    return entry


def _require_vmd_success(observations, expected_path, expected_target=None):
    """Reject a silent click, missing target, fatal result, or partial VMD result."""
    if not observations:
        raise RuntimeError("VMD Import button produced no observed ImportVmdAction call")
    entry = observations[-1]
    request = entry.get("request", {})
    result = entry.get("result", {})
    if request.get("filePath") != str(expected_path):
        raise RuntimeError(f"observed ImportVmdAction path mismatch: {request.get('filePath')!r}")
    target_model = (request.get("optionsBefore") or {}).get("target_model")
    if expected_target is not None and target_model != expected_target:
        raise RuntimeError(
            f"observed ImportVmdAction target mismatch: {target_model!r} != {expected_target!r}"
        )
    if result.get("outcome") != "success" or not result.get("succeeded") or result.get("warnings"):
        raise RuntimeError(f"UI VMD import was not a clean success: {result}")
    return entry


def _process_qt_events():
    """Pump the real Maya Qt application after a widget operation."""
    from mmd_tools.ui.qt_compat import QApplication

    instance = QApplication.instance()
    if instance is not None:
        instance.processEvents()


def _configure_route_settings(route, config):
    """Configure the isolated Maya profile used by the visible Settings tab."""
    from mmd_tools.services.settings_service import SettingsService

    service = SettingsService()
    for key, value in {
        "ui.general.development_mode": True,
        "ui.dev.command_port": config.get("command_port", 3939),
        "import.general.scale_factor": config.get("scale", 1.0),
        "import.native.use_cpp_fast_load": route == "cpp",
        "import.native.cpp_fast_load_mesh_only": False,
        "import.native.use_cpp_vp2_ownership": route == "cpp" and config.get("vp2", True),
        "import.native.require_native_pmx_parse": False,
        "import.native.use_cpp_rig_nodes": False,
        "import.physics.import_physics": config.get("physics", False),
        "import.morph.import_morphs": True,
        "import.model.create_mmd_shaders": True,
        "import.model.create_mmd_control_rig": False,
        "import.model.separate_meshes_by_material": config.get("split", False),
    }.items():
        service.set(key, value)


def _configure_import_controls(view, config):
    """Set the ordinary Import tab controls through their production widgets."""
    view.scale_spin.setValue(float(config.get("scale", 1.0)))
    view.create_mmd_control_rig_check.setChecked(False)
    view.separate_meshes_check.setChecked(bool(config.get("split", False)))
    view.import_physics_check.setChecked(bool(config.get("physics", False)))
    view.import_morphs_check.setChecked(True)
    view.new_file_check.setChecked(False)
    _process_qt_events()


def _ui_witness(window, route, out):
    """Capture the visible production window and the bound Import presenter."""
    from mmd_tools.ui.qt_compat import QApplication

    view = window.import_export_tab
    presenter = window.import_export_presenter
    if not window.isVisible():
        raise RuntimeError(f"{route} MainWindow is not visible")
    if getattr(presenter, "view", None) is not view:
        raise RuntimeError(f"{route} ImportExportPresenter is not bound to the visible tab")
    image_path = out / f"{route}-ui.png"
    image = window.grab()
    if image.isNull() or not image.save(str(image_path)):
        raise RuntimeError(f"{route} visible MainWindow screenshot could not be captured")
    app = QApplication.instance()
    return {
        "window": {
            "class": type(window).__name__,
            "objectName": window.objectName(),
            "visible": bool(window.isVisible()),
        },
        "presenter": {
            "class": type(presenter).__name__,
            "viewBound": True,
        },
        "controls": {
            "importPath": view.import_path_edit.objectName() or type(view.import_path_edit).__name__,
            "importButton": view.import_button.objectName() or type(view.import_button).__name__,
            "vmdPath": view.vmd_path_edit.objectName() or type(view.vmd_path_edit).__name__,
            "vmdButton": view.import_vmd_button.objectName() or type(view.import_vmd_button).__name__,
            "settingsDevelopmentMode": bool(window.settings_presenter.view.development_mode_check.isChecked()),
            "scale": float(view.scale_spin.value()),
            "separateMeshes": bool(view.separate_meshes_check.isChecked()),
            "importPhysics": bool(view.import_physics_check.isChecked()),
            "importMorphs": bool(view.import_morphs_check.isChecked()),
            "newFile": bool(view.new_file_check.isChecked()),
        },
        "operations": [
            "MainWindow.show_window(dockable=False)",
            "ImportExportPresenter.view binding verified",
        ],
        "qtApplication": app is not None,
        "screenshot": str(image_path),
    }


def _capture_ui_snapshot(window, route, out, label):
    """Capture the production window after a concrete import operation."""
    from mmd_tools.ui.qt_compat import Qt

    view = window.import_export_tab
    category_stack = getattr(view, "import_category_stack", None)
    current_category = getattr(category_stack, "current_category", None)
    image_path = out / f"{route}-{label}.png"
    image = window.grab()
    if image.isNull() or not image.save(str(image_path)):
        raise RuntimeError(f"{route} post-import UI screenshot could not be captured")
    history = [
        str(view.unified_history_list.item(index).data(Qt.UserRole))
        for index in range(view.unified_history_list.count())
    ]
    return {
        "screenshot": str(image_path),
        "visible": bool(window.isVisible()),
        "importCategory": str(current_category) if current_category is not None else None,
        "currentRoot": window.app_state.current_model_root,
        "importHistory": history,
        "operations": [f"MainWindow.grab() after {label}"],
    }


def _validate_ui_import_options(window, route, config, options):
    """Require the options consumed by the presenter to match visible controls."""
    view = window.import_export_tab
    expected = {
        "scale": float(view.scale_spin.value()),
        "create_mmd_control_rig": bool(view.create_mmd_control_rig_check.isChecked()),
        "separate_meshes_by_material": bool(view.separate_meshes_check.isChecked()),
        "import_physics": bool(view.import_physics_check.isChecked()),
        "import_morphs": bool(view.import_morphs_check.isChecked()),
        "use_cpp_fast_load": route == "cpp",
        "use_cpp_vp2_ownership": route == "cpp" and config.get("vp2", True),
        "cpp_fast_load_mesh_only": False,
    }
    mismatches = {
        key: {"visible": value, "options": options.get(key)}
        for key, value in expected.items()
        if options.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"visible Import controls and presenter options differ: {mismatches}")
    return expected


def _make_invalid_local_axis_fixture(model_path, out):
    """Write one deterministic PMX whose authoring preflight must fail."""
    from mmd_tools.core.pmx_data import PmxData
    from mmd_tools.core.pmx_data.bone import PmxBoneFlag

    parsed = PmxData().parse_file(str(model_path))
    if len(parsed.bones) < 2:
        raise RuntimeError("authoring-failure fixture requires at least two bones")
    bone = parsed.bones[1]
    bone.bone_flag |= PmxBoneFlag.LOCAL_AXIS
    bone.x_axis_direction = (0.0, 0.0, 0.0)
    bone.z_axis_direction = (0.0, 0.0, 1.0)
    path = out / "invalid-local-axis.pmx"
    parsed.write_file(str(path))
    return path


def _modal_watchdog(window):
    """Close real error dialogs through a Qt timer and retain their titles."""
    from mmd_tools.ui.qt_compat import QApplication, QDialog, QTimer

    closed = []
    timer = QTimer(window)

    def inspect():
        app = QApplication.instance()
        for widget in (app.topLevelWidgets() if app is not None else []):
            if not isinstance(widget, QDialog) or not widget.isVisible():
                continue
            title = widget.windowTitle() or widget.objectName() or type(widget).__name__
            closed.append(str(title))
            widget.reject()

    timer.setInterval(25)
    timer.timeout.connect(inspect)
    timer.start()
    return timer, closed


def _click_import_button(window, model_path, dialog_log=None):
    """Set the real PMX path field and activate the real Import button."""
    view = window.import_export_tab
    timer, closed_dialogs = _modal_watchdog(window)
    view.import_path_edit.setText(str(model_path))
    _process_qt_events()
    try:
        view.import_button.click()
        _process_qt_events()
    finally:
        timer.stop()
        timer.deleteLater()
    if dialog_log is not None:
        dialog_log.extend(closed_dialogs)
    root = window.app_state.current_model_root
    if not root:
        raise RuntimeError("real Import button produced no current model root")
    from maya import cmds

    if not cmds.objExists(root):
        raise RuntimeError(f"real Import button returned deleted model root: {root}")
    return root, [
        f"import_export_tab.import_path_edit.setText({str(model_path)!r})",
        "import_export_tab.import_button.click()",
    ]


def _select_animation_import_category(window):
    """Select the production Import/Animation subtab before touching VMD controls."""
    view = window.import_export_tab
    category_stack = getattr(view, "import_category_stack", None)
    select_category = getattr(category_stack, "set_current_category", None)
    if category_stack is None or not callable(select_category):
        raise RuntimeError("ImportExportTab animation category stack is unavailable for VMD")
    before = str(getattr(category_stack, "current_category", ""))
    select_category("animation")
    _process_qt_events()
    after = str(getattr(category_stack, "current_category", ""))
    if after != "animation":
        raise RuntimeError(f"ImportExportTab animation category was not selected: {after!r}")
    return {
        "before": before,
        "after": after,
        "operations": [
            "ImportExportTab.import_category_stack.set_current_category('animation')",
        ],
    }


def _prepare_vmd_target(window, model_root, model_path):
    """Restore the real model selection/state needed by the VMD presenter."""
    from maya import cmds

    view = window.import_export_tab
    animation_category = _select_animation_import_category(window)
    view.import_path_edit.setText(str(model_path))
    cmds.select(model_root, replace=True)
    header = getattr(window, "header_widget", None)
    combo = getattr(header, "model_combo", None)
    refresh_header = getattr(header, "refresh_model_list", None)
    if combo is None or not callable(refresh_header):
        raise RuntimeError("MainWindow Header model combo is unavailable for VMD")
    refresh_header()
    current_root = window.app_state.current_model_root
    expected_root = (cmds.ls(model_root, long=True) or [model_root])[0]
    selected_index = None
    for index in range(combo.count()):
        item_root = combo.itemData(index)
        if not item_root:
            continue
        item_identity = (cmds.ls(item_root, long=True) or [item_root])[0]
        if item_identity == expected_root:
            selected_index = index
            combo.setCurrentIndex(index)
            break
    if selected_index is None:
        raise RuntimeError(f"VMD target model is absent from Header model combo: {expected_root!r}")
    _process_qt_events()
    current_root = window.app_state.current_model_root
    observed_root = (cmds.ls(current_root, long=True) or [current_root])[0] if current_root else None
    if observed_root != expected_root:
        raise RuntimeError(f"VMD target model was not selected: {observed_root!r} != {expected_root!r}")
    return {
        "currentRoot": observed_root,
        "importCategory": animation_category,
        "comboIndex": selected_index,
        "comboItemData": str(combo.itemData(selected_index)),
        "operations": [
            *animation_category["operations"],
            f"import_export_tab.import_path_edit.setText({str(model_path)!r})",
            f"cmds.select({model_root!r}, replace=True)",
            "HeaderWidget.refresh_model_list()",
            f"HeaderWidget.model_combo.setCurrentIndex({selected_index})",
        ],
    }


def _click_vmd_button(window, motion_path, observations, dialog_log=None):
    """Set the real VMD path field and activate the real animation button."""
    view = window.import_export_tab
    view.vmd_path_edit.setText(str(motion_path))
    _process_qt_events()
    if view.vmd_path_edit.text().strip() != str(motion_path):
        raise RuntimeError("real VMD path widget did not retain the requested path")
    visible_before = bool(view.import_vmd_button.isVisible())
    enabled_before = bool(view.import_vmd_button.isEnabled())
    observation_count_before = len(observations)
    if not visible_before or not enabled_before:
        return (
            [],
            {
                "visibleBefore": visible_before,
                "enabledBefore": enabled_before,
                "observationsBefore": observation_count_before,
                "observationsAfter": observation_count_before,
                "closedDialogs": [],
            },
        )
    timer, closed_dialogs = _modal_watchdog(window)
    try:
        view.import_vmd_button.click()
        _process_qt_events()
    finally:
        timer.stop()
        timer.deleteLater()
    if dialog_log is not None:
        dialog_log.extend(closed_dialogs)
    return (
        [
            f"import_export_tab.vmd_path_edit.setText({str(motion_path)!r})",
            f"import_export_tab.import_vmd_button.click(enabled={enabled_before})",
        ],
        {
            "visibleBefore": True,
            "enabledBefore": enabled_before,
            "observationsBefore": observation_count_before,
            "observationsAfter": len(observations),
            "closedDialogs": list(closed_dialogs),
        },
    )


def _close_ui_window(window):
    """Close only the harness-owned production window."""
    if window is None:
        return
    try:
        window.close()
        window.deleteLater()
        _process_qt_events()
    except Exception:
        pass


def _scene_models(window):
    service = getattr(window.app_state, "scene_model_service", None)
    list_models = getattr(service, "list_mmd_models", None)
    if not callable(list_models):
        raise RuntimeError("SceneModelService.list_mmd_models is unavailable for cleanup verification")
    try:
        return tuple(str(item) for item in (list_models() or ()))
    except Exception as exc:
        raise RuntimeError("SceneModelService.list_mmd_models failed during cleanup verification") from exc


def _canonical_node(cmds, node):
    plug_node = str(node).split(".", 1)[0]
    paths = cmds.ls(plug_node, long=True) or []
    return str(paths[0]) if paths else plug_node


def _multi_import_contract(cmds, first_root, second_root):
    """Reject root/skin/morph cross-instance connections after two UI imports."""
    from mmd_tools.core import model_registry

    roots = (str(cmds.ls(first_root, long=True)[0]), str(cmds.ls(second_root, long=True)[0]))
    registry_categories = (
        model_registry.REGISTRY_CATEGORY_MORPH,
        model_registry.REGISTRY_CATEGORY_MATERIAL,
        model_registry.REGISTRY_CATEGORY_TEXTURE,
        model_registry.REGISTRY_CATEGORY_PHYSICS,
        model_registry.REGISTRY_CATEGORY_MATERIAL_MORPH_WORK,
    )
    nodes_by_root = {}
    registries = {}
    registry_members = {}
    critical_nodes = {}
    critical_requirements = {}
    critical_identity_owners = {}
    owner_sets = {}
    connection_pairs = []
    cross_connections = []

    def add_owned(root, node, strict=False, context=""):
        if not node:
            return
        canonical = _canonical_node(cmds, node)
        if any(canonical == other or canonical.startswith(other + "|") for other in roots if other != root):
            if strict:
                raise RuntimeError(f"{context or 'model-owned node'} belongs to the other imported root: {canonical}")
            return
        owner_sets.setdefault(canonical, set()).add(root)

    for root in roots:
        registry = model_registry.get_model_registry(root)
        if not registry:
            raise RuntimeError(f"UI import root has no model registry: {root}")
        registry_roots = cmds.listConnections(
            f"{registry}.{model_registry.ATTR_MMD_REGISTRY_ROOT}",
            source=True,
            destination=False,
        ) or []
        if len(registry_roots) != 1 or str(cmds.ls(registry_roots[0], long=True)[0]) != root:
            raise RuntimeError(f"UI import registry is not owned by its root: {root} -> {registry}")
        registries[root] = str(registry)
        nodes = set(cmds.ls(root, long=True) or ())
        nodes.update(cmds.listRelatives(root, allDescendents=True, fullPath=True) or ())
        for node in nodes:
            add_owned(root, node)
        registry_members[root] = {}
        for category in registry_categories:
            members = model_registry.list_model_registry_members(root, category) or []
            registry_members[root][category] = sorted(_canonical_node(cmds, member) for member in members)
            for member in members:
                add_owned(root, member, strict=True, context=f"registry {category}")
                canonical = _canonical_node(cmds, member)
                critical_identity_owners.setdefault(canonical, set()).add(root)
        meshes = cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []
        joints = cmds.listRelatives(root, allDescendents=True, type="joint", fullPath=True) or []
        clusters = []
        blend_shapes = []
        for mesh in meshes:
            for history_node in cmds.listHistory(mesh, pruneDagObjects=True) or []:
                node_type = cmds.nodeType(history_node)
                if node_type == "skinCluster" and history_node not in clusters:
                    clusters.append(str(history_node))
                elif node_type == "blendShape" and history_node not in blend_shapes:
                    blend_shapes.append(str(history_node))
        critical_nodes[root] = {
            "skinCluster": sorted(clusters),
            "blendShape": sorted(blend_shapes),
            "mmdMorphController": sorted(
                cmds.listConnections(
                    f"{root}.mmd_morph_controller", source=True, destination=False,
                    type="mmdMorphController",
                ) or []
            ) if cmds.attributeQuery("mmd_morph_controller", node=root, exists=True) else [],
        }
        for nodes in critical_nodes[root].values():
            for node in nodes:
                canonical = _canonical_node(cmds, node)
                critical_identity_owners.setdefault(canonical, set()).add(root)
        critical_requirements[root] = {
            "skinCluster": bool(meshes and joints),
            "blendShape": any(
                cmds.attributeQuery("mmd_morph_type", node=node, exists=True)
                and cmds.getAttr(f"{node}.mmd_morph_type") == "vertex"
                for node in registry_members[root][model_registry.REGISTRY_CATEGORY_MORPH]
            ),
            "mmdMorphController": bool(registry_members[root][model_registry.REGISTRY_CATEGORY_MORPH]),
        }
        for node in critical_nodes[root]["skinCluster"]:
            add_owned(root, node, strict=True, context="skinCluster")
            for influence in cmds.skinCluster(node, query=True, influence=True) or []:
                add_owned(root, influence, strict=True, context="skin influence")
            for plug in cmds.listConnections(
                f"{node}.outputGeometry", source=False, destination=True, plugs=True
            ) or ():
                add_owned(root, plug, strict=True, context="skin output geometry")
            for plug in cmds.listConnections(
                f"{node}.input", source=True, destination=False, plugs=True
            ) or ():
                add_owned(root, plug, strict=True, context="skin input geometry")
        for node in critical_nodes[root]["blendShape"] + critical_nodes[root]["mmdMorphController"]:
            add_owned(root, node, strict=True, context="morph DG node")
            for plug in cmds.listConnections(node, source=True, destination=True, plugs=True) or ():
                connection_pairs.append((root, str(node), str(plug)))
        nodes_by_root[root] = sorted(
            node for node, owners in owner_sets.items() if root in owners
        )
        for node in nodes_by_root[root]:
            pairs = cmds.listConnections(
                node,
                source=True,
                destination=True,
                connections=True,
                plugs=True,
            ) or []
            for index in range(0, len(pairs) - 1, 2):
                connection_pairs.append((root, str(pairs[index]), str(pairs[index + 1])))
    for _source_root, left_plug, right_plug in connection_pairs:
        left_owners = owner_sets.get(_canonical_node(cmds, left_plug), set())
        right_owners = owner_sets.get(_canonical_node(cmds, right_plug), set())
        if left_owners and right_owners and left_owners.isdisjoint(right_owners):
            cross_connections.append([left_plug, right_plug])
    critical_presence = {
        root: {node_type: bool(nodes) for node_type, nodes in critical_nodes[root].items()}
        for root in roots
    }
    shared_critical = sorted(
        node for node, owners in critical_identity_owners.items() if len(owners) > 1
    )
    if shared_critical:
        raise RuntimeError("critical DG nodes are shared across imported roots: " + repr(shared_critical[:8]))
    if not any(any(values.values()) for values in critical_presence.values()):
        raise RuntimeError("two UI imports produced no registry-owned skin/morph DG witness")
    for root in roots:
        missing = [
            node_type
            for node_type, required in critical_requirements[root].items()
            if required and not critical_presence[root][node_type]
        ]
        if missing:
            raise RuntimeError(f"UI import critical DG witness missing for {root}: {missing}")
    if cross_connections:
        raise RuntimeError("two UI imports cross-wired model-owned nodes: " + repr(cross_connections[:8]))
    witness = {
        "roots": list(roots),
        "registries": registries,
        "registryMembers": registry_members,
        "nodeCounts": {root: len(nodes) for root, nodes in nodes_by_root.items()},
        "criticalNodes": critical_nodes,
        "criticalIdentityOwners": {
            node: sorted(owners) for node, owners in critical_identity_owners.items()
        },
        "criticalRequirements": critical_requirements,
        "criticalPresence": critical_presence,
        "crossConnections": cross_connections,
        "pass": True,
    }
    _validate_multi_import_witness(witness)
    return witness


def _validate_multi_import_witness(witness):
    """Validate serialized multi-import evidence independently of Maya."""
    roots = tuple(witness.get("roots") or ())
    if len(roots) != 2 or roots[0] == roots[1]:
        raise RuntimeError("multi-import witness does not contain two distinct roots")
    if witness.get("crossConnections"):
        raise RuntimeError("multi-import witness contains cross-instance connections")
    shared_critical = [
        node for node, owners in (witness.get("criticalIdentityOwners") or {}).items()
        if len(set(owners)) > 1
    ]
    if shared_critical:
        raise RuntimeError("multi-import witness shares critical DG nodes: " + repr(shared_critical[:8]))
    requirements = witness.get("criticalRequirements") or {}
    presence = witness.get("criticalPresence") or {}
    if not any(any(values.values()) for values in presence.values()):
        raise RuntimeError("multi-import witness contains no critical DG nodes")
    for root in roots:
        missing = [
            node_type
            for node_type, required in (requirements.get(root) or {}).items()
            if required and not (presence.get(root) or {}).get(node_type, False)
        ]
        if missing:
            raise RuntimeError(f"multi-import witness missing required DG nodes: {missing}")
    return witness


def _failed_import_cleanup(
    window,
    invalid_model_path,
    current_root,
    route,
    observations,
    native_calls,
):
    """Drive a post-native authoring failure through the real button."""
    from maya import cmds

    view = window.import_export_tab
    before_models = _scene_models(window)
    before_nodes = set(str(node) for node in (cmds.ls(long=True, dependencyNodes=True) or ()))
    current_uuids = cmds.ls(current_root, uuid=True) or []
    native_count_before = len(native_calls)
    timer, closed_dialogs = _modal_watchdog(window)
    view.import_path_edit.setText(str(invalid_model_path))
    _process_qt_events()
    try:
        view.import_button.click()
        _process_qt_events()
    finally:
        timer.stop()
        timer.deleteLater()
    after_models = _scene_models(window)
    after_nodes = set(str(node) for node in (cmds.ls(long=True, dependencyNodes=True) or ()))
    from mmd_tools.ui.qt_compat import Qt

    history = [
        str(view.unified_history_list.item(index).data(Qt.UserRole))
        for index in range(view.unified_history_list.count())
    ]
    observed = observations[-1] if observations else {}
    result = observed.get("result", {})
    new_native_calls = native_calls[native_count_before:]
    witness = {
        "beforeModels": list(before_models),
        "afterModels": list(after_models),
        "createdNodes": sorted(after_nodes - before_nodes),
        "currentRoot": window.app_state.current_model_root,
        "expectedCurrentRoot": current_root,
        "currentRootUuid": (cmds.ls(current_root, uuid=True) or [None])[0],
        "expectedCurrentRootUuid": current_uuids[0] if current_uuids else None,
        "observedImport": observed,
        "nativeCalls": new_native_calls,
        "closedDialogs": closed_dialogs,
        "historyContainsInvalidPath": str(invalid_model_path) in history,
        "operations": [
            f"import_export_tab.import_path_edit.setText({str(invalid_model_path)!r})",
            "import_export_tab.import_button.click()",
        ],
    }
    if before_models != after_models:
        raise RuntimeError("failed UI import changed the model list")
    if after_nodes != before_nodes:
        raise RuntimeError("authoring failure left native or authoring nodes behind")
    if window.app_state.current_model_root != current_root:
        raise RuntimeError("failed UI import replaced the current model identity")
    if not current_uuids or (cmds.ls(current_root, uuid=True) or [None])[0] != current_uuids[0]:
        raise RuntimeError("authoring failure changed the existing model UUID")
    if result.get("outcome") != "fatal" or not result.get("error"):
        raise RuntimeError(f"authoring failure did not return a fatal result: {result}")
    if str(invalid_model_path) in history:
        raise RuntimeError("failed UI import was recorded in import history")
    if route == "cpp" and (not new_native_calls or any(not call.get("success") for call in new_native_calls)):
        raise RuntimeError("CPP authoring failure had no successful native completion witness")
    if route == "python" and new_native_calls:
        raise RuntimeError("Python authoring failure unexpectedly used the native route")
    return witness


def _capture_scene_contract(cmds, om, root):
    """Capture the shared scene oracle without turning an unavailable oracle into pass."""
    try:
        from tools.smoke.import_scene_contract import capture_scene
    except ImportError as exc:
        return {"status": "not_run", "expected": "pass", "error": str(exc)}
    try:
        return {"status": "pass", "expected": "pass", "scene": capture_scene(cmds, om, root)}
    except Exception:
        return {"status": "fail", "expected": "pass", "error": traceback.format_exc()}


def _compare_scene_contracts(report):
    """Compare Python/C++ scene captures at import, animated, and reopen phases."""
    try:
        from tools.smoke.import_scene_contract import compare_scenes
    except ImportError as exc:
        report["phases"]["scene_contract"] = {
            "status": "not_run",
            "expected": "pass",
            "error": str(exc),
        }
        report["checks"].append({"name": "scene-contract", "status": "not_run", "pass": False})
        return
    phase_results = {}
    for label, key in (("import", "sceneImport"), ("sample", "sceneSample"), ("reopen", "sceneReopen")):
        left = report["routes"]["python"].get(key, {})
        right = report["routes"]["cpp"].get(key, {})
        if left.get("status") == "fail" or right.get("status") == "fail":
            result = {
                "status": "fail",
                "expected": "pass",
                "captureErrors": {
                    "python": left.get("error"),
                    "cpp": right.get("error"),
                },
            }
        elif left.get("status") != "pass" or right.get("status") != "pass":
            result = {
                "status": "not_run",
                "expected": "pass",
                "captureStatuses": {
                    "python": left.get("status", "not_run"),
                    "cpp": right.get("status", "not_run"),
                },
            }
        else:
            try:
                result = compare_scenes(left["scene"], right["scene"])
            except Exception:
                result = {"status": "fail", "expected": "pass", "error": traceback.format_exc()}
        phase_results[label] = result
        report["checks"].append({
            "name": f"scene-contract-{label}",
            "status": result.get("status", "fail"),
            "checks": result.get("checks", []),
            "exclusions": result.get("exclusions", []),
            "pass": result.get("status") == "pass",
        })
    report["phases"]["scene_contract"] = {
        "status": "pass" if all(item.get("status") == "pass" for item in phase_results.values()) else "fail",
        "expected": "pass",
        "phases": phase_results,
    }


def run_probe(config_path: str) -> None:
    """Run inside an isolated Maya GUI and write a completion marker on failure too."""
    from maya import cmds
    from maya.api import OpenMaya as om

    from tools.smoke.maya_fast_import_parity import _snapshot

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out = Path(config["out"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "probe.log").write_text("", encoding="utf-8")
    started = time.perf_counter()
    _write_breadcrumb(out, started, "probe-start")
    report = {
        "status": "fail",
        "config": config,
        "routes": {},
        "checks": [],
        "phases": {
            "ui_binding_python": _phase_record(),
            "ui_binding_cpp": _phase_record(),
            "multi_import_identity": _phase_record(),
            "failure_cleanup": _phase_record(),
            "vmd_playback": _phase_record(),
            "save_reopen": _phase_record(),
            "scene_contract": _phase_record(),
        },
    }
    active_window = None
    try:
        cmds.loadPlugin(str(ROOT / "plug-ins/mmd_tools_plugin.py"), quiet=True)
        cmds.loadPlugin(config["plugin"], quiet=True)
        _write_breadcrumb(out, started, "plugins-loaded")
        invalid_model_path = _make_invalid_local_axis_fixture(config["model"], out)
        for route in ("python", "cpp"):
            _write_breadcrumb(out, started, "route-start", route=route)
            cmds.file(new=True, force=True)
            _configure_route_settings(route, config)
            from mmd_tools.ui.main_window import MainWindow

            window = MainWindow()
            active_window = window
            window.show_window(dockable=False)
            _process_qt_events()
            _write_breadcrumb(out, started, "ui-ready", route=route)
            view = window.import_export_tab
            _configure_import_controls(view, config)
            route_result = {"ui": _ui_witness(window, route, out)}
            route_result["uiOptions"] = None
            native_calls = []
            import_observations = []
            restore_import_observer = _install_import_observer(window, import_observations)
            original_fast_load = cmds.mmdFastLoad

            def observe_fast_load(*args, **kwargs):
                return _call_native(original_fast_load, native_calls, *args, **kwargs)

            try:
                cmds.mmdFastLoad = observe_fast_load
                root, first_actions = _click_import_button(
                    window, config["model"], route_result["ui"].setdefault("closedDialogs", [])
                )
                route_result["ui"]["operations"].extend(first_actions)
                _write_breadcrumb(
                    out,
                    started,
                    "import-click-complete",
                    route=route,
                    importIndex=1,
                    observed=len(import_observations),
                )
                first_observation = _require_import_success(
                    import_observations,
                    config["model"],
                    root,
                )
                route_result["uiOptions"] = _validate_ui_import_options(
                    window,
                    route,
                    config,
                    first_observation["request"]["optionsBefore"],
                )
                _require_native_route(route, native_calls)

                second_root, second_actions = _click_import_button(
                    window, config["model"], route_result["ui"].setdefault("closedDialogs", [])
                )
                route_result["ui"]["operations"].extend(second_actions)
                _write_breadcrumb(
                    out,
                    started,
                    "import-click-complete",
                    route=route,
                    importIndex=2,
                    observed=len(import_observations),
                )
                second_observation = _require_import_success(
                    import_observations,
                    config["model"],
                    second_root,
                )
                if second_observation["request"]["optionsBefore"] != first_observation["request"]["optionsBefore"]:
                    raise RuntimeError("repeated UI imports consumed different request options")
                if second_root == root:
                    raise RuntimeError(f"{route} repeated UI import reused the same model identity")
                if not cmds.objExists(root) or not cmds.objExists(second_root):
                    raise RuntimeError(f"{route} repeated UI import lost one of its model roots")
                _write_breadcrumb(out, started, "multi-import-contract-start", route=route)
                identity = _multi_import_contract(cmds, root, second_root)
                _write_breadcrumb(out, started, "multi-import-contract-complete", route=route)
                route_result["multiImport"] = identity
                _phase_pass(
                    report,
                    "multi_import_identity",
                    route,
                    identity,
                )
                root = second_root
                _require_native_route(route, native_calls)
            finally:
                cmds.mmdFastLoad = original_fast_load
            if not root:
                raise RuntimeError(f"{route} import returned no model")
            result = {
                "import": _snapshot(cmds, om, root),
                "nativeCalls": native_calls,
                "importObservations": import_observations,
                "successfulImportObservations": [first_observation, second_observation],
                "ui": route_result["ui"],
                "options": first_observation["request"]["optionsBefore"],
                "uiOptions": route_result["uiOptions"],
                "multiImport": route_result["multiImport"],
                "outcome": first_observation["result"]["outcome"],
            }
            report["routes"][route] = result
            _phase_pass(report, f"ui_binding_{route}", route, result["ui"])
            result["ui"]["postImport"] = _capture_ui_snapshot(window, route, out, "post-import")
            scene_import = _capture_scene_contract(cmds, om, root)
            result["sceneImport"] = scene_import
            if route == "cpp" and config.get("vp2", True):
                result["viewport"] = _viewport(cmds, root, out)

            _write_breadcrumb(out, started, "failure-cleanup-start", route=route)
            failure_original_fast_load = cmds.mmdFastLoad
            try:
                cmds.mmdFastLoad = observe_fast_load
                cleanup = _failed_import_cleanup(
                    window,
                    invalid_model_path,
                    root,
                    route,
                    import_observations,
                    native_calls,
                )
            finally:
                cmds.mmdFastLoad = failure_original_fast_load
                restore_import_observer()
            _write_breadcrumb(out, started, "failure-cleanup-complete", route=route)
            result["failureCleanup"] = cleanup
            _phase_pass(report, "failure_cleanup", route, cleanup)

            vmd_observations = []
            restore_vmd_observer = _install_vmd_observer(window, vmd_observations)
            try:
                vmd_target = _prepare_vmd_target(window, root, config["model"])
                result["ui"]["vmdTarget"] = vmd_target
                vmd_actions, vmd_click = _click_vmd_button(
                    window,
                    config["motion"],
                    vmd_observations,
                    result["ui"].setdefault("closedDialogs", []),
                )
                result["vmdImport"] = {
                    "click": vmd_click,
                    "observations": vmd_observations,
                }
                vmd_entry = _require_vmd_success(
                    vmd_observations,
                    config["motion"],
                    expected_target=vmd_target["currentRoot"],
                )
                result["vmdImport"]["successfulObservation"] = vmd_entry
            finally:
                restore_vmd_observer()
            result["ui"]["operations"].extend(vmd_actions)
            result["ui"]["postVmd"] = _capture_ui_snapshot(window, route, out, "post-vmd")
            _write_breadcrumb(out, started, "vmd-click-complete", route=route)
            samples = {}
            physics_witness = _require_inactive_physics(cmds, root)
            for frame in (0, 1, 15, 30, 60):
                cmds.currentTime(frame, update=True)
                _require_inactive_physics(cmds, root)
                samples[str(frame)] = _positions(cmds, om, root)
            result["samples"] = samples
            if _motion_delta(samples) <= 1e-5:
                raise RuntimeError(f"{route} motion did not deform the fixture")
            result["sceneSample"] = _capture_scene_contract(cmds, om, root)
            _phase_pass(
                report,
                "vmd_playback",
                route,
                {"operations": vmd_actions, "motionDelta": _motion_delta(samples),
                 "physicsInactive": physics_witness},
            )
            path = out / f"{route}.ma"
            cmds.file(rename=str(path))
            cmds.file(save=True, type="mayaAscii", force=True)
            cmds.file(new=True, force=True)
            cmds.file(str(path), open=True, force=True)
            _process_qt_events()
            result["reopen"] = _positions(cmds, om, root)
            result["reopenImport"] = _snapshot(cmds, om, root)
            result["sceneReopen"] = _capture_scene_contract(cmds, om, root)
            result["editUndoRedo"] = _edit_roundtrip(cmds, om, root)
            _phase_pass(
                report,
                "save_reopen",
                route,
                {"path": str(path), "editUndoRedo": result["editUndoRedo"]},
            )
            _write_breadcrumb(out, started, "save-reopen-complete", route=route)
            _close_ui_window(window)
            active_window = None
            _write_breadcrumb(out, started, "route-complete", route=route)
        left, right = (report["routes"][name] for name in ("python", "cpp"))
        for phase in ("samples", "reopen"):
            error = _max_error(left[phase], right[phase])
            report["checks"].append({"name": phase, "maxError": error, "pass": error <= 1e-5})
        for route, result in report["routes"].items():
            error = _max_error(result["samples"]["60"], result["reopen"])
            report["checks"].append({"name": f"{route}-save-reopen", "maxError": error, "pass": error <= 1e-5})
        _compare_scene_contracts(report)
        _phase_gate(report)
        report["status"] = "pass" if all(item["pass"] for item in report["checks"]) else "fail"
        _write_breadcrumb(out, started, "probe-checks-complete", status=report["status"])
    except Exception:
        report["error"] = traceback.format_exc()
        _write_breadcrumb(out, started, "probe-error", error=report["error"].splitlines()[-1])
    finally:
        _close_ui_window(active_window)
        (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_breadcrumb(out, started, "probe-complete", status=report["status"])
        with (out / "probe.log").open("a", encoding="utf-8", buffering=1) as stream:
            stream.write("CPP_AUTHORING_COMPLETE\n")


def _edit_roundtrip(cmds, om, root):
    """Exercise a component edit after reopen and verify Undo/Redo restores points."""
    before = _positions(cmds, om, root)
    mesh = next(iter(before))
    cmds.move(0.25, 0, 0, mesh + ".vtx[0]", relative=True, objectSpace=True)
    edited = _positions(cmds, om, root)
    if _max_error(before, edited) <= 1e-5:
        raise RuntimeError("Reopened mesh component edit did not change a vertex")
    cmds.undo()
    if _max_error(before, _positions(cmds, om, root)) > 1e-5:
        raise RuntimeError("Component edit Undo did not restore mesh points")
    cmds.redo()
    if _max_error(edited, _positions(cmds, om, root)) > 1e-5:
        raise RuntimeError("Component edit Redo did not restore mesh points")
    cmds.undo()
    return {
        "status": "pass",
        "scope": "component_edit_only",
        "importAtomicUndo": "not_claimed",
        "operations": [
            f"cmds.move(0.25, 0, 0, {mesh}.vtx[0], relative=True, objectSpace=True)",
            "cmds.undo() and point sample restored",
            "cmds.redo() and point sample restored",
            "cmds.undo() final cleanup",
        ],
        "mesh": mesh,
        "vertexDelta": 0.25,
    }


def _viewport(cmds, root, out):
    """Check MMD Render against the visible, editable source meshes."""
    from tools.render_override.common import capture_view

    shapes = cmds.listRelatives(root, allDescendents=True, type="mmdRenderShape", fullPath=True) or []
    if not shapes:
        raise RuntimeError("Full VP2 import has no render proxies")
    panels = cmds.getPanel(type="modelPanel") or []
    panel = "modelPanel4" if "modelPanel4" in panels else panels[0]
    cmds.modelEditor(panel, edit=True, rendererName="vp2Renderer", rendererOverrideName="mmdOrdered",
                     displayAppearance="smoothShaded", displayTextures=True,
                     wireframeOnShaded=False, grid=False)
    cmds.lookThru(panel, "persp")
    cmds.select(shapes, replace=True)
    cmds.viewFit("persp", all=False, animate=False, fitFactor=0.8)
    cmds.select(clear=True)
    capture = capture_view(cmds, out / "cpp-viewport.png", panel, 800, 600)
    ordered = json.loads(cmds.mmdOrderedRenderWitness())
    if ordered["error"] or ordered["drawCount"] <= 0:
        raise RuntimeError(f"MMD Render did not draw: {ordered}")
    witnesses = {}
    for shape in shapes:
        sources = cmds.listConnections(shape + ".inputMesh", source=True, destination=False, shapes=True) or []
        witness = json.loads(cmds.mmdRenderWitness(node=shape, json=True))
        if len(sources) != 1 or not cmds.getAttr(sources[0] + ".visibility") or witness["geometryUpdates"] <= 0:
            raise RuntimeError(f"Editable source or evaluated geometry unavailable: {shape}, {sources}")
        witnesses[shape] = witness
    return {"witnesses": witnesses, "ordered": ordered, "capture": str(capture)}


def _call_native(command, calls, *args, **kwargs):
    """Record native completion so a later Python fallback cannot count as success."""
    entry = {"arguments": kwargs, "success": False}
    calls.append(entry)
    result = command(*args, **kwargs)
    expected = 1 if kwargs.get("sp") else (3 if kwargs.get("vp2Ownership") else 2)
    entry["success"] = isinstance(result, (list, tuple)) and len(result) == expected
    return result


def _require_native_route(route, calls):
    if (route == "cpp") != bool(calls) or any(not call["success"] for call in calls):
        raise RuntimeError(f"{route} did not complete its requested geometry route")


def _require_inactive_physics(cmds, root):
    """Do not attribute live physics deformation to the imported VMD."""
    from mmd_tools.core import model_registry

    states = []
    for node in model_registry.list_model_registry_members(root, model_registry.REGISTRY_CATEGORY_PHYSICS) or []:
        if cmds.nodeType(node) != "mmdPhysicsSolver":
            continue
        solved = bool(cmds.getAttr(f"{node}.outSolved"))
        worlds = cmds.listConnections(
            f"{node}.inWorldSettings", source=True, destination=False,
            type="mmdPhysicsWorldShape",
        ) or []
        active = bool(cmds.getAttr(f"{node}.enable")) and any(
            cmds.getAttr(f"{world}.enable") for world in worlds
        )
        if solved or active:
            raise RuntimeError(f"VMD-only sampling requires inactive physics: {node}")
        states.append({"solver": node, "worlds": worlds, "solved": solved, "active": active})
    return {"status": "pass", "solvers": states}


def _motion_delta(samples):
    """Recognize motion that returns to its initial pose at the last sample."""
    return max(_max_error(samples["0"], points) for points in samples.values())


def _positions(cmds, om, root):
    """Capture all editable mesh points without relying on generated shape names."""
    values = {}
    for shape in cmds.listRelatives(root, allDescendents=True, type="mesh", fullPath=True) or []:
        if cmds.getAttr(f"{shape}.intermediateObject"):
            continue
        selection = om.MSelectionList()
        selection.add(shape)
        points = om.MFnMesh(selection.getDagPath(0)).getPoints(om.MSpace.kWorld)
        key = shape.rsplit("|", 1)[0]
        values[key] = [[p.x, p.y, p.z] for p in points]
    return values


def _max_error(left, right):
    """Compare nested point samples; different topology is always a failure."""
    if isinstance(left, dict):
        if not left:
            raise ValueError("No mesh samples")
        if left.keys() != right.keys():
            raise ValueError("Sample mesh/frame keys differ")
        return max((_max_error(left[key], right[key]) for key in left), default=0.0)
    if isinstance(left, list):
        if len(left) != len(right):
            raise ValueError("Sample vertex counts differ")
        return max((_max_error(a, b) for a, b in zip(left, right)), default=0.0)
    if not math.isfinite(left) or not math.isfinite(right):
        raise ValueError("Non-finite sampled position")
    return abs(left - right)


def main() -> int:
    from tests.viewport.maya_e2e_harness import run_maya_e2e

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maya", default="2024")
    parser.add_argument("--model", type=Path, default=ROOT / "tests/data/mmt_test_model.pmx")
    parser.add_argument("--motion", type=Path, default=ROOT / "tests/data/mmt_test_model_test_motion.vmd")
    parser.add_argument("--plugin", type=Path)
    parser.add_argument("--port", type=int, default=7791)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--physics", action="store_true")
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--no-vp2", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    out = args.out_dir or (
        ROOT / "build/reports/fast-import-authoring" / f"maya{args.maya}"
        / f"{args.model.stem}-{args.scale}-{'mesh' if args.no_vp2 else 'vp2'}-{'split' if args.split else 'unified'}-{'physics' if args.physics else 'static'}"
    )
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    suffix = ".mll" if sys.platform == "win32" else (".bundle" if sys.platform == "darwin" else ".so")
    plugin = (args.plugin or ROOT / "plug-ins" / args.maya / f"Debug/mmd_tools_cpp{suffix}").resolve()
    config = out / "input.json"
    config.write_text(json.dumps({
        "out": str(out), "plugin": str(plugin), "model": str(args.model.resolve()),
        "motion": str(args.motion.resolve()),
        "physics": args.physics, "split": args.split, "scale": args.scale, "vp2": not args.no_vp2,
        "command_port": args.port,
    }, ensure_ascii=False), encoding="utf-8")
    report = run_maya_e2e(
        project_root=ROOT, version=args.maya, out_dir=out, port=args.port, timeout=300,
        log_path=out / "probe.log", report_path=out / "report.json",
        command=f"from tools.smoke.maya_fast_import_authoring import run_probe\nrun_probe({str(config)!r})",
        marker="CPP_AUTHORING_COMPLETE", send_label="cpp-authoring",
        stale_paths=(out / "probe.log", out / "report.json"),
        env_overrides={"MAYA_VP2_DEVICE_OVERRIDE": "VirtualDeviceDx11" if sys.platform == "win32" else "VirtualDeviceGLCore",
                       "MMD_TOOLS_CPP_PLUGIN": str(plugin),
                       "PATH": str(plugin.parent) + os.pathsep + os.environ.get("PATH", "")},
    )
    failed_checks = [
        str(check.get("name", "<unnamed>"))
        for check in report.get("checks", [])
        if not check.get("pass")
    ]
    error = report.get("error") or ""
    if error:
        error = str(error).splitlines()[-1]
    print(json.dumps({
        "status": report.get("status"),
        "failedChecks": failed_checks,
        "error": error,
        "reportPath": str(out / "report.json"),
    }, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
