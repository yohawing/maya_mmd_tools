"""Background Maya GUI arm IK manager actions and fresh-process scene reload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
MARKER = 'ARM_IK_E2E_COMPLETE'


def _write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')


def probe(config_path, phase):
    from maya import cmds
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from tests.integration.test_control_rig_arm_ik import create_arm_case, arm_pose, pose_delta, move_target
    from mmd_tools.core.mmd_control_rig_arm_ik import inspect_arm_ik
    from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata
    from mmd_tools.ui.control_rig_manager import ControlRigManagerWindow
    from tools.maya_gui.pose_layers_e2e import _known_render_warning
    from tools.render_override.common import require_requested_plugin
    config = json.loads(Path(config_path).read_text(encoding='utf8'))
    output = Path(config['out'])
    report = {'status': 'fail', 'phase': phase, 'checks': {}, 'messages': [],
              'scope': 'Manager dispatch, arm IK pose and save/reload; not rendering or external PMX arm IK compatibility'}
    sources = ['core/mmd_control_rig_arm_ik.py', 'core/mmd_control_rig_motion.py',
               'core/mmd_control_rig_builder.py', 'ui/control_rig_manager.py']
    report['source_sha256'] = {p: hashlib.sha256((ROOT/'mmd_tools'/p).read_bytes()).hexdigest() for p in sources}
    callback = om.MCommandMessage.addCommandOutputCallback(
        lambda msg, kind, unused: report['messages'].append({'kind': int(kind), 'text': msg})
        if kind in (om.MCommandMessage.kWarning, om.MCommandMessage.kError) else None)
    manager = None
    try:
        require_requested_plugin(cmds, config['plugin'], print)
        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        cmds.undoInfo(state=True)
        cmds.evaluationManager(mode='parallel')

        def check(name, condition, detail=None):
            report['checks'][name] = {'pass': bool(condition), 'detail': detail}
            if not condition:
                raise AssertionError(name + ': ' + repr(detail))

        if phase == 'save':
            cmds.file(new=True, force=True)
            root, controls, joints = create_arm_case()
            manager = ControlRigManagerWindow(cmds_module=cmds)
            manager.show()
            manager.refresh()
            check('manager_model', cmds.ls(manager.selected_model_root(), uuid=True) == cmds.ls(root, uuid=True))
            original = arm_pose(joints)
            manager.arm_buttons[('right', 'ik')].click()
            row = inspect_arm_ik(root, 'right')
            check('button_enable', row['active'])
            check('button_select_wrist', cmds.ls(selection=True, long=True) == [row['target']])
            check('enable_pose', pose_delta(original, arm_pose(joints)) < 1e-4)
            move_target(row, joints)
            moved = arm_pose(joints)
            manager.arm_buttons[('right', 'pole')].click()
            check('button_select_pole', cmds.ls(selection=True, long=True) == [row['pole']])
            manager.arm_buttons[('right', 'fk')].click()
            check('button_match_fk', not inspect_arm_ik(root, 'right')['active'])
            check('matched_pose', pose_delta(moved, arm_pose(joints)) < 1e-4)
            manager.arm_buttons[('right', 'ik')].click()
            check('reenable', inspect_arm_ik(root, 'right')['active'])
            manager.arm_buttons[('right', 'restore')].click()
            check('button_restore_fk', not inspect_arm_ik(root, 'right')['active'])
            check('restore_fk_pose', pose_delta(moved, arm_pose(joints)) < 1e-4)
            manager.arm_buttons[('right', 'ik')].click()
            row = inspect_arm_ik(root, 'right')
            move_target(row, joints)
            expected = arm_pose(joints)
            cmds.file(rename=str(output/'arm.ma'))
            cmds.file(save=True, type='mayaAscii', force=True)
            _write(output/'identity.json', {'root': root, 'joints': joints, 'pose': expected})
            check('save_pose', pose_delta(expected, arm_pose(joints)) < 1e-4)
            manager.action_buttons['bake_mmd'].click()
            check('manager_bake', read_mmd_control_rig_metadata(root)['state'] == 'BAKED')
            check('baked_pose', pose_delta(expected, arm_pose(joints)) < 1e-4)
        else:
            saved = json.loads((output/'identity.json').read_text(encoding='utf8'))
            cmds.file(str(output/'arm.ma'), open=True, force=True)
            root, joints = saved['root'], saved['joints']
            check('reload_active_graph', inspect_arm_ik(root, 'right')['active'])
            check('reload_pose', pose_delta(saved['pose'], arm_pose(joints)) < 1e-4)
            manager = ControlRigManagerWindow(cmds_module=cmds)
            manager.show()
            manager.refresh()
            manager.arm_buttons[('right', 'fk')].click()
            check('reload_match_fk', not inspect_arm_ik(root, 'right')['active'])
            check('reload_match_pose', pose_delta(saved['pose'], arm_pose(joints)) < 1e-4)
            manager.action_buttons['restore'].click()
            check('manager_restore_attached', read_mmd_control_rig_metadata(root)['state'] == 'ATTACHED')
        report['status'] = 'pass'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if manager:
            manager.close()
        om.MMessage.removeCallback(callback)
        report['known_render_warnings'] = [m for m in report['messages']
            if m['kind'] == int(om.MCommandMessage.kWarning) and _known_render_warning(m['text'])]
        report['unexpected_messages'] = [m for m in report['messages'] if m not in report['known_render_warnings']]
        if report['unexpected_messages']:
            report['status'] = 'fail'
        _write(output/phase/'report.json', report)
        (output/phase/'run.log').write_text(MARKER+'\n', encoding='utf8')


def main():
    from tests.viewport.maya_e2e_harness import run_maya_e2e
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--maya', default='2024')
    parser.add_argument('--config', choices=('Debug', 'Release'), default='Debug')
    parser.add_argument('--port', type=int, default=7741)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=600)
    args = parser.parse_args()
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    plugin = ROOT/f'plug-ins/{args.maya}/{args.config}/mmd_tools_cpp.mll'
    config = output/'config.json'
    _write(config, {'out': str(output), 'plugin': str(plugin)})
    report = {'status': 'fail', 'phases': {}, 'plugin_sha256': hashlib.sha256(plugin.read_bytes()).hexdigest()}
    for name in ('arm.ma', 'identity.json'):
        (output/name).unlink(missing_ok=True)
    for phase in ('save', 'reload'):
        d = output/phase
        d.mkdir(exist_ok=True)
        command = ('from maya import cmds\nfrom tools.maya_gui.arm_ik_e2e import probe\n'
                   f'cmds.evalDeferred(lambda: probe({str(config)!r}, {phase!r}), lowestPriority=True)')
        try:
            report['phases'][phase] = run_maya_e2e(project_root=ROOT, version=args.maya,
                out_dir=d, port=args.port, timeout=args.timeout, log_path=d/'run.log',
                report_path=d/'report.json', command=command, marker=MARKER,
                send_label='arm-ik-'+phase, stale_paths=(d/'run.log', d/'report.json'),
                env_overrides={'MAYA_SKIP_USERSETUP_PY': '1', 'MMD_TOOLS_CPP_PLUGIN': str(plugin),
                               'PATH': str(plugin.parent)+os.pathsep+os.environ.get('PATH', '')})
        except Exception:
            report['phases'][phase] = {'status': 'fail', 'error': traceback.format_exc()}
        _write(output/'report.json', report)
    report['status'] = 'pass' if all(p['status'] == 'pass' for p in report['phases'].values()) else 'fail'
    _write(output/'report.json', report)
    print(report['status'])
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
