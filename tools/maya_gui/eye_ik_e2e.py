"""Background GUI Eye IK Manager dispatch, VMD export and fresh-process reload."""
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
MARKER = 'EYE_IK_E2E_COMPLETE'


def _write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')


def _source_hashes():
    sources = list((ROOT/'mmd_tools/core').glob('mmd_control_rig*.py')) + [ROOT/'mmd_tools/ui/control_rig_manager.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}


def _gate_report(report):
    """Require both hosts to have tested the candidate recorded before launch."""
    phases = [report['phases'].get(phase, {}) for phase in ('save', 'reload')]
    expected = report['expected_source_sha256']
    report['source_hashes_match'] = bool(expected) and all(p.get('source_sha256') == expected for p in phases)
    report['status'] = 'pass' if report['source_hashes_match'] and all(p.get('status') == 'pass' for p in phases) else 'fail'


def probe(config_path, phase):
    from maya import cmds
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from tests.integration.test_control_rig_eye import create_eye_ik_case, eye_joints, pose, append_edges, MODEL, OPTIONS
    from tests.integration.test_control_rig_arm_ik import pose_delta
    from mmd_tools.core.mmd_control_rig_eye_ik import inspect_eye_ik
    from mmd_tools.core.mmd_control_rig_builder import read_mmd_control_rig_metadata
    from mmd_tools.ui.control_rig_manager import ControlRigManagerWindow
    from tools.maya_gui.pose_layers_e2e import _known_render_warning
    from tools.render_override.common import require_requested_plugin
    config = json.loads(Path(config_path).read_text(encoding='utf8'))
    output = Path(config['out'])
    report = {'status': 'fail', 'phase': phase, 'checks': {}, 'messages': [],
              'scope': 'Eye IK manager, actual target keys, eye append, 30fps authored integer-frame VMD and reload'}
    report['source_sha256'] = _source_hashes()
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

        def sample(joints):
            result = {}
            for frame in range(1, 11):
                cmds.currentTime(frame, update=True)
                result[str(frame)] = pose(joints)
            return result

        def compare(name, joints, expected):
            actual = sample(joints)
            delta = max(pose_delta(actual[f], expected[f]) for f in expected)
            check(name, delta < 1e-4, delta)

        if phase == 'save':
            cmds.file(new=True, force=True)
            root, control, joints = create_eye_ik_case()
            manager = ControlRigManagerWindow(cmds_module=cmds)
            manager.show()
            manager.refresh()
            check('manager_model', cmds.ls(manager.selected_model_root(), uuid=True) == cmds.ls(root, uuid=True))
            original = pose(joints)
            edges = append_edges(joints[1:])
            manager.eye_buttons['ik'].click()
            row = inspect_eye_ik(root)
            check('button_enable', row['active'])
            check('button_select_target', cmds.ls(selection=True, long=True) == [row['target']])
            check('enable_nonzero_fk_roll_pose', pose_delta(original, pose(joints)) < 1e-4)
            target = row['target']
            check('target_keyable', cmds.getAttr(target+'.translateX', keyable=True)
                  and not cmds.getAttr(target+'.translateX', lock=True))
            start = cmds.getAttr(target+'.translateX')
            for frame in range(1, 11):
                check('target_key_'+str(frame), cmds.setKeyframe(target, attribute='translateX', time=frame,
                      value=start+.05*(frame-1)) > 0)
            writer = cmds.connectionInfo(target+'.translateX', sourceFromDestination=True)
            check('actual_target_curve', bool(writer) and cmds.nodeType(writer.split('.')[0]).startswith('animCurve'))
            check('actual_ten_keys', cmds.keyframe(target, attribute='translateX', query=True, timeChange=True) == list(range(1, 11)))
            expected = sample(joints)
            for index, name in enumerate(('both', 'left', 'right')):
                delta = pose_delta([expected['1'][index]], [expected['10'][index]])
                check('actual_eye_motion_'+name, delta > 1e-3, delta)
            check('append_edges_live', append_edges(joints[1:]) == edges)
            before = pose(joints)
            manager.eye_buttons['fk'].click()
            check('button_match_fk', not inspect_eye_ik(root)['active'])
            check('match_pose', pose_delta(before, pose(joints)) < 1e-4)
            manager.eye_buttons['ik'].click()
            check('reenable', inspect_eye_ik(root)['active'])
            check('reenable_pose', pose_delta(before, pose(joints)) < 1e-4)
            expected = sample(joints)
            cmds.file(rename=str(output/'eye.ma'))
            cmds.file(save=True, type='mayaAscii', force=True)
            _write(output/'identity.json', {'root': root, 'joints': joints, 'expected': expected, 'edges': edges})
            compare('save_pose', joints, expected)
            manager.action_buttons['bake_mmd'].click()
            check('manager_bake', read_mmd_control_rig_metadata(root)['state'] == 'BAKED')
            compare('bake_pose', joints, expected)
            check('append_edges_baked', append_edges(joints[1:]) == edges)
            from mmd_tools.converters.vmd_scene_collector import VmdSceneCollector
            from mmd_tools.io.vmd_exporter import VmdExporter
            from mmd_tools.io.mmd_importer import import_mmd_file
            vmd = str(output/'eye.vmd')
            VmdExporter().export_vmd_animation(vmd, VmdSceneCollector().collect({'target_model': root}))
            fresh = import_mmd_file(str(MODEL), options=OPTIONS)
            check('vmd_import', bool(import_mmd_file(vmd, options={'target_model': fresh, 'pmx_path': str(MODEL)})))
            compare('vmd_fresh_model', eye_joints(fresh), expected)
        else:
            saved = json.loads((output/'identity.json').read_text(encoding='utf8'))
            cmds.file(str(output/'eye.ma'), open=True, force=True)
            root, joints = saved['root'], saved['joints']
            check('reload_fps', cmds.currentUnit(query=True, time=True) == 'ntsc')
            check('reload_active', inspect_eye_ik(root)['active'])
            compare('fresh_reload_pose', joints, saved['expected'])
            check('reload_append_edges', append_edges(joints[1:]) == saved['edges'])
            manager = ControlRigManagerWindow(cmds_module=cmds)
            manager.show()
            manager.refresh()
            before = pose(joints)
            manager.eye_buttons['fk'].click()
            check('reload_match_fk', not inspect_eye_ik(root)['active'])
            check('reload_match_pose', pose_delta(before, pose(joints)) < 1e-4)
            manager.eye_buttons['ik'].click()
            check('reload_reenable', inspect_eye_ik(root)['active'])
            manager.eye_buttons['restore'].click()
            check('reload_original_fk_restore', not inspect_eye_ik(root)['active'])
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
    report['expected_source_sha256'] = _source_hashes()
    for name in ('eye.ma', 'identity.json'):
        (output/name).unlink(missing_ok=True)
    for phase in ('save', 'reload'):
        d = output/phase
        d.mkdir(exist_ok=True)
        command = ('from maya import cmds\nfrom tools.maya_gui.eye_ik_e2e import probe\n'
                   f'cmds.evalDeferred(lambda: probe({str(config)!r}, {phase!r}), lowestPriority=True)')
        try:
            report['phases'][phase] = run_maya_e2e(project_root=ROOT, version=args.maya,
                out_dir=d, port=args.port, timeout=args.timeout, log_path=d/'run.log',
                report_path=d/'report.json', command=command, marker=MARKER,
                send_label='eye-ik-'+phase, stale_paths=(d/'run.log', d/'report.json'),
                env_overrides={'MAYA_SKIP_USERSETUP_PY': '1', 'MMD_TOOLS_CPP_PLUGIN': str(plugin),
                               'PATH': str(plugin.parent)+os.pathsep+os.environ.get('PATH', '')})
        except Exception:
            report['phases'][phase] = {'status': 'fail', 'error': traceback.format_exc()}
        _write(output/'report.json', report)
    _gate_report(report)
    _write(output/'report.json', report)
    print(report['status'])
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
