"""Maya GUI commandPort probe for live mmdCcdIk controller movement.

This module is imported from a short commandPort call.  Keeping the heavy logic
in a file avoids commandPort truncation/hangs with large inline Python blocks.
"""

from __future__ import annotations

import importlib
import math
import sys
import traceback
from pathlib import Path


def run_probe(log_path: str, model_path: str, repo_path: str, force_enable: bool = False) -> None:
    """Reload the plug-in in Maya GUI, import a rig, and move one IK controller.

    By default this checks the actual interactive state a user gets immediately
    after import.  Passing ``force_enable=True`` only verifies the solver
    implementation and must not be used as proof that the imported rig is
    interactively usable.
    """
    from maya import cmds

    repo = Path(repo_path)
    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(message: object) -> None:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(str(message) + "\n")

    def plugin_names():
        out = []
        for plugin in cmds.pluginInfo(q=True, listPlugins=True) or []:
            if "mmd" in plugin.lower() or plugin == "plugin_main.py":
                try:
                    out.append(
                        (
                            plugin,
                            cmds.pluginInfo(plugin, q=True, path=True),
                            cmds.pluginInfo(plugin, q=True, loaded=True),
                        )
                    )
                except Exception as exc:  # pragma: no cover - Maya diagnostic
                    out.append((plugin, f"ERR {exc}", None))
        return out

    def source(attr: str):
        return cmds.listConnections(attr, s=True, d=False, plugs=True) or []

    def dest(attr: str):
        return cmds.listConnections(attr, s=False, d=True, plugs=True) or []

    log_file.write_text("=== GUI IK fresh import probe ===\n", encoding="utf-8")
    try:
        log(f"repo={repo}")
        log(f"model={model_path}")
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))

        log(f"before_plugins={plugin_names()!r}")
        cmds.file(new=True, force=True)

        plugin_path = repo / "mmd_tools" / "plugin_main.py"
        for plugin, path, loaded in list(plugin_names()):
            if loaded:
                try:
                    cmds.unloadPlugin(plugin, force=True)
                    log(f"unloaded={(plugin, path)!r}")
                except Exception as exc:
                    log(f"unload_failed={(plugin, path, str(exc))!r}")
        try:
            if cmds.pluginInfo(str(plugin_path), q=True, loaded=True):
                cmds.unloadPlugin(str(plugin_path), force=True)
                log(f"unloaded_by_path={plugin_path}")
        except Exception as exc:
            log(f"unload_by_path_skipped={exc}")

        for name in sorted(
            [n for n in sys.modules if n == "mmd_tools" or n.startswith("mmd_tools.")],
            key=lambda n: n.count("."),
            reverse=True,
        ):
            sys.modules.pop(name, None)
        importlib.invalidate_caches()

        cmds.loadPlugin(str(plugin_path), quiet=True)
        log(f"after_plugins={plugin_names()!r}")
        try:
            log(
                "plugin_by_path="
                + repr(
                    (
                        cmds.pluginInfo(str(plugin_path), q=True, loaded=True),
                        cmds.pluginInfo(str(plugin_path), q=True, path=True),
                    )
                )
            )
        except Exception as exc:
            log(f"plugin_by_path_error={exc}")

        import mmd_tools
        import mmd_tools.nodes.mmd_ccd_ik_node as ikmod
        from mmd_tools.io.mmd_importer import import_mmd_file

        log(f"mmd_tools_file={getattr(mmd_tools, '__file__', None)}")
        log(f"ik_module_file={getattr(ikmod, '__file__', None)}")
        log(f"ik_is_output_func={getattr(ikmod.MmdCcdIkNode, '_is_output_plug', None)!r}")

        root = import_mmd_file(
            model_path,
            options={
                "setup_rig": True,
                "setup_bone_orientation": True,
                "import_physics": False,
                "create_mmd_shaders": False,
            },
        )
        log(f"import_root={root!r}")
        log(f"joint_count={len(cmds.ls(type='joint') or [])}")

        nodes = cmds.ls(type="mmdCcdIk") or []
        log(f"ik_nodes={nodes!r}")
        candidates = []
        for node in nodes:
            sx = source(f"{node}.goalX")
            sy = source(f"{node}.goalY")
            sz = source(f"{node}.goalZ")
            blob = " ".join([node] + sx + sy + sz).lower()
            score = (10 if "left_leg" in blob else 0) + (3 if "leg" in blob else 0)
            candidates.append((score, node, sx, sy, sz))
        candidates.sort(reverse=True)
        log(f"candidates={candidates!r}")
        if not candidates:
            log("RESULT=NO_IK_NODES")
            return

        _score, node, sx, sy, sz = candidates[0]
        if sx and sy and sz:
            controller = sx[0].split(".")[0]
        elif node.endswith("_mmdCcdIk"):
            controller = node[: -len("_mmdCcdIk")]
            log(f"using_controller_from_node_name={controller}")
        else:
            log(f"RESULT=GOAL_NOT_CONNECTED chosen={node!r} goalX={sx!r} goalY={sy!r} goalZ={sz!r}")
            return
        if not cmds.objExists(controller):
            log(f"RESULT=CONTROLLER_NOT_FOUND controller={controller!r}")
            return
        log(f"chosen={node}")
        log(f"controller={controller}")
        native_handles = []
        for handle in cmds.ls(type="ikHandle") or []:
            if not cmds.attributeQuery("mmd_ik_native_handle", node=handle, exists=True):
                continue
            handle_controller = ""
            if cmds.attributeQuery("mmd_ik_controller", node=handle, exists=True):
                try:
                    handle_controller = cmds.getAttr(f"{handle}.mmd_ik_controller") or ""
                except Exception:
                    handle_controller = ""
            if handle_controller == controller:
                native_handles.append(handle)
        log(f"native_handles={native_handles!r}")
        enabled_before = cmds.getAttr(f"{node}.enabled")
        log(f"enabled_before={enabled_before}")
        if not enabled_before and not force_enable:
            log("RESULT=IK_DISABLED")
            return
        if force_enable:
            cmds.setAttr(f"{node}.enabled", True)
        log(f"enabled_after={cmds.getAttr(f'{node}.enabled')}")
        log(f"chain_len={len(cmds.getAttr(f'{node}.chainJson') or '')}")
        try:
            import json

            chain_data = json.loads(cmds.getAttr(f"{node}.chainJson") or "{}")
            controller_slot = chain_data.get("controllerBoneSlot", -1)
            if controller_slot >= 0:
                rest = chain_data["bones"][controller_slot]["rest_position"]
                current = cmds.getAttr(f"{node}.inputTranslate[{controller_slot}]")[0]
                offset = (current[0] - rest[0], current[1] - rest[1], -current[2] - rest[2])
                log(
                    "controller_slot_offset_before="
                    + repr(tuple(round(float(x), 9) for x in offset))
                )
        except Exception as exc:
            log(f"controller_slot_offset_probe_error={exc}")

        out_dests = []
        out_values_before = []
        for i in range(32):
            plug = f"{node}.outputRotate[{i}]"
            try:
                destinations = dest(plug)
                if destinations:
                    out_dests.append((i, destinations))
                out_values_before.append((i, tuple(float(x) for x in cmds.getAttr(plug)[0])))
            except Exception:
                pass
        log(f"outputRotate_element_dests={out_dests!r}")
        log(
            "outputRotate_values_before="
            + repr([(i, tuple(round(x, 6) for x in v)) for i, v in out_values_before[:12]])
        )

        driven = sorted(set(dp.split(".")[0] for _i, destinations in out_dests for dp in destinations))
        log(f"driven_joints={driven!r}")
        before_local = cmds.getAttr(f"{controller}.translate")[0]
        before_world = cmds.xform(controller, q=True, ws=True, t=True)
        before_rots = {j: cmds.getAttr(f"{j}.rotate")[0] for j in driven if cmds.objExists(j)}
        log(f"controller_before_local={tuple(round(x, 6) for x in before_local)!r}")
        log(f"controller_before_world={tuple(round(x, 6) for x in before_world)!r}")
        before_rot_log = {k: tuple(round(x, 6) for x in v) for k, v in before_rots.items()}
        log(f"driven_rotate_before={before_rot_log!r}")

        rest_knee_world = cmds.xform("left_knee", q=True, ws=True, t=True) if cmds.objExists("left_knee") else None
        new_local = (before_local[0], before_local[1] + 1.0, before_local[2])
        cmds.setAttr(f"{controller}.translate", *new_local, type="double3")
        cmds.dgdirty(node)
        cmds.refresh(force=True)

        out_values_after = []
        for i in range(32):
            plug = f"{node}.outputRotate[{i}]"
            try:
                out_values_after.append((i, tuple(float(x) for x in cmds.getAttr(plug)[0])))
            except Exception:
                pass
        after_rots = {j: cmds.getAttr(f"{j}.rotate")[0] for j in driven if cmds.objExists(j)}
        after_world = cmds.xform(controller, q=True, ws=True, t=True)
        native_blends_after = {
            handle: cmds.getAttr(f"{handle}.ikBlend") for handle in native_handles if cmds.objExists(handle)
        }
        ankle_distance = None
        knee_delta_z = None
        knee_rotate_x = None
        if cmds.objExists("left_ankle"):
            ankle_world = cmds.xform("left_ankle", q=True, ws=True, t=True)
            ankle_distance = math.dist(ankle_world, after_world)
        if rest_knee_world is not None and cmds.objExists("left_knee"):
            moved_knee_world = cmds.xform("left_knee", q=True, ws=True, t=True)
            knee_delta_z = moved_knee_world[2] - rest_knee_world[2]
            knee_rotate_x = float(cmds.getAttr("left_knee.rotateX"))
        log(f"controller_after_local={tuple(round(x, 6) for x in new_local)!r}")
        log(f"controller_after_world={tuple(round(x, 6) for x in after_world)!r}")
        log(f"native_blends_after={native_blends_after!r}")
        log(f"ankle_controller_distance={ankle_distance}")
        log(f"knee_delta_z={knee_delta_z}")
        log(f"knee_rotate_x={knee_rotate_x}")
        log(
            "outputRotate_values_after="
            + repr([(i, tuple(round(x, 6) for x in v)) for i, v in out_values_after[:12]])
        )
        after_rot_log = {k: tuple(round(x, 6) for x in v) for k, v in after_rots.items()}
        log(f"driven_rotate_after={after_rot_log!r}")

        before_by_index = dict(out_values_before)
        max_output_delta = 0.0
        for i, after in out_values_after:
            before = before_by_index.get(i)
            if before:
                max_output_delta = max(max_output_delta, max(abs(after[k] - before[k]) for k in range(3)))
        max_rotate_delta = 0.0
        for joint, after in after_rots.items():
            before = before_rots.get(joint)
            if before:
                max_rotate_delta = max(max_rotate_delta, max(abs(after[k] - before[k]) for k in range(3)))

        log(f"max_output_delta={max_output_delta}")
        log(f"max_driven_rotate_delta={max_rotate_delta}")
        cmds.setAttr(f"{controller}.translate", *before_local, type="double3")
        cmds.refresh(force=True)
        native_ok = (
            bool(native_handles)
            and any(float(v) > 0.5 for v in native_blends_after.values())
            and ankle_distance is not None
            and ankle_distance < 0.05
            and knee_rotate_x is not None
            and knee_rotate_x > 0.0
        )
        legacy_ok = (
            (max_output_delta > 1e-4 or max_rotate_delta > 1e-4)
            and ankle_distance is not None
            and ankle_distance < 0.05
            and knee_rotate_x is not None
            and knee_rotate_x > 0.0
        )
        log("RESULT=IK_MOVED" if native_ok or legacy_ok else "RESULT=IK_STILL_STATIC")
    except Exception:
        log("EXCEPTION=" + traceback.format_exc())
