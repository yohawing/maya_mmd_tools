"""Background GUI model Time Editor clip, source restoration and fresh reload."""
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
MARKER = 'TIME_EDITOR_E2E_COMPLETE'


def _write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf8')


def _source_hashes():
    sources = [ROOT/path for path in (
        'mmd_tools/core/mmd_time_editor.py', 'mmd_tools/ui/time_editor_manager.py',
        'mmd_tools/converters/vmd_scene_collector.py',
        'mmd_tools/core/native_layer_routes.py', 'mmd_tools/plugin_main.py',
        'mmd_tools/io/vmd_importer.py', 'mmd_tools/services/settings_service.py',
        'mmd_tools/ui/tabs/import_export_tab.py', 'mmd_tools/core/settings_keys.py',
        'tests/integration/test_mmd_time_editor.py', 'tools/maya_gui/time_editor_e2e.py')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}


def _gate_report(report):
    """Require both hosts to have tested the candidate recorded before launch."""
    phases = [report['phases'].get(phase, {}) for phase in ('baseline', 'save', 'reload')]
    expected = report['expected_source_sha256']
    report['source_hashes_match'] = bool(expected) and all(p.get('source_sha256') == expected for p in phases)
    report['status'] = 'pass' if report['source_hashes_match'] and all(p.get('status') == 'pass' for p in phases) else 'fail'


def probe(config_path, phase):
    from maya import cmds
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from tests.integration.test_mmd_time_editor import curve_payload, poses, pose_error, MODEL, OPTIONS
    from mmd_tools.core.mmd_time_editor import inspect_model_clip
    from mmd_tools.ui.time_editor_manager import TimeEditorManagerWindow
    from tools.maya_gui.pose_layers_e2e import _known_render_warning
    from tools.render_override.common import require_requested_plugin
    config = json.loads(Path(config_path).read_text(encoding='utf8'))
    output = Path(config['out'])
    report = {'status': 'fail', 'phase': phase, 'checks': {}, 'messages': [],
              'scope': 'one bone clip; 30fps direct VMD inputs; render correctness excluded'}
    report['source_sha256'] = _source_hashes()
    callback = om.MCommandMessage.addCommandOutputCallback(
        lambda msg, kind, unused: report['messages'].append({'kind': int(kind), 'text': msg})
        if kind in (om.MCommandMessage.kWarning, om.MCommandMessage.kError) else None)
    manager = None
    import_tab = None
    try:
        require_requested_plugin(cmds, config['plugin'], print)
        load_mmd_tools_plugin(ROOT, cmds_module=cmds)
        cmds.undoInfo(state=True)
        cmds.evaluationManager(mode='parallel')

        def check(name, condition, detail=None):
            report['checks'][name] = {'pass': bool(condition), 'detail': detail}
            if not condition:
                raise AssertionError(name + ': ' + repr(detail))

        def compare(name, joints, expected, offset=0):
            actual = poses(joints, [int(f)+offset for f in expected])
            delta = max(pose_error(actual[int(f)+offset], expected[f]) for f in expected)
            check(name, delta < 1e-4, delta)

        if phase == 'baseline':
            # Maya ASCII itself normalizes unweighted tangent caches and
            # quaternion-group key precision. Establish that baseline in a
            # separate host; never excuse a Time Editor payload difference.
            cmds.file(new=True, force=True)
            from mmd_tools.io.mmd_importer import import_mmd_file
            from mmd_tools.services.settings_service import SettingsService
            from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
            from mmd_tools.core import settings_keys
            cmds.currentUnit(time='ntsc')
            root = import_mmd_file(str(MODEL), options=OPTIONS)
            service = SettingsService()
            import_tab = ImportExportTab(settings_service=service)
            import_tab.show()
            check('layer_checkbox_default_true', import_tab.use_animation_layers_check.isChecked())
            import_tab.use_animation_layers_check.click()
            check('layer_checkbox_off', not import_tab.use_animation_layers_check.isChecked())
            check('layer_setting_false', service.get(settings_keys.IMPORT_ANIMATION_USE_ANIMATION_LAYERS) is False)
            options = service.build_vmd_import_options(target_model=root)
            check('public_options_false', options['use_animation_layers'] is False)
            options['pmx_path'] = str(MODEL)
            check('public_vmd_import', bool(import_mmd_file(str(ROOT/'tests/data/mmt_test_model_test_motion.vmd'), options=options)))
            check('no_animation_layers', not (cmds.ls(type='animLayer') or []))
            joints = cmds.listRelatives(root, allDescendents=True, type='joint', fullPath=True)
            original = curve_payload()
            baseline = poses(joints)
            check('actual_vmd_motion', pose_error(baseline[0], baseline[5]) > 1e-3)
            check('actual_original_keys', len(original) >= 684 and all(v['times'] for v in original.values()), len(original))
            cmds.file(rename=str(output/'baseline.ma'))
            cmds.file(save=True, type='mayaAscii', force=True)
            _write(output/'baseline-identity.json', {'root': root, 'joints': joints, 'baseline': baseline})
            check('baseline_saved', (output/'baseline.ma').is_file())
        elif phase == 'save':
            saved = json.loads((output/'baseline-identity.json').read_text(encoding='utf8'))
            cmds.file(str(output/'baseline.ma'), open=True, force=True)
            root, joints = saved['root'], saved['joints']
            compare('no_clip_fresh_ascii_baseline_pose', joints, saved['baseline'])
            original = curve_payload()
            baseline = poses(joints)
            cmds.file(rename=str(output/'no-te-reference.ma'))
            cmds.file(save=True, type='mayaAscii', force=True)
            check('actual_vmd_motion', pose_error(baseline[0], baseline[5]) > 1e-3)
            check('actual_original_keys', len(original) >= 684 and all(v['times'] for v in original.values()), len(original))
            manager = TimeEditorManagerWindow(cmds_module=cmds)
            manager.show()
            manager.refresh()
            check('manager_selected_model', cmds.ls(manager.selected_model_root(), uuid=True) == cmds.ls(root, uuid=True))
            manager.buttons['create'].click()
            row = inspect_model_clip(root)
            check('manager_create', row['active'], manager.message_label.text())
            check('exact_bone_input_count', row['inputCount'] == 684, row['inputCount'])
            manager.buttons['open'].click()
            compare('clip_original_pose', joints, baseline)
            cmds.timeEditorClip(edit=True, moveClip=20, clipId=row['clipId'])
            compare('clip_moved_pose', joints, baseline, offset=20)
            check('original_curve_payload_active', curve_payload(original) == original)
            manager.buttons['remove'].click()
            check('manager_remove', not inspect_model_clip(root)['active'], manager.message_label.text())
            compare('removed_original_pose', joints, baseline)
            check('original_curve_payload_removed', curve_payload() == original)
            manager.buttons['create'].click()
            row = inspect_model_clip(root)
            check('manager_recreate', row['active'])
            cmds.timeEditorClip(edit=True, moveClip=20, clipId=row['clipId'])
            cmds.file(rename=str(output/'clip.ma'))
            cmds.file(save=True, type='mayaAscii', force=True)
            _write(output/'identity.json', {'root': root, 'joints': joints, 'baseline': baseline, 'curves': original})
            compare('save_moved_pose', joints, baseline, offset=20)
        else:
            saved = json.loads((output/'identity.json').read_text(encoding='utf8'))
            cmds.file(str(output/'no-te-reference.ma'), open=True, force=True)
            reference_curves = curve_payload(saved['curves'])
            check('reference_original_curve_uuids', set(reference_curves) == set(saved['curves']))
            cmds.file(str(output/'clip.ma'), open=True, force=True)
            root, joints = saved['root'], saved['joints']
            check('reload_fps', cmds.currentUnit(query=True, time=True) == 'ntsc')
            check('reload_active', inspect_model_clip(root)['active'])
            compare('fresh_reload_moved_pose', joints, saved['baseline'], offset=20)
            check('reload_original_curve_payload', curve_payload(saved['curves']) == reference_curves)
            manager = TimeEditorManagerWindow(cmds_module=cmds)
            manager.show()
            manager.refresh()
            manager.buttons['remove'].click()
            check('reload_manager_remove', not inspect_model_clip(root)['active'], manager.message_label.text())
            compare('reload_removed_original_pose', joints, saved['baseline'])
            check('reload_removed_curve_payload', curve_payload() == reference_curves)
        report['status'] = 'pass'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if manager:
            manager.close()
        if import_tab:
            import_tab.close()
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
    for name in ('baseline.ma', 'baseline-identity.json', 'clip.ma', 'identity.json', 'no-te-reference.ma'):
        (output/name).unlink(missing_ok=True)
    for phase in ('baseline', 'save', 'reload'):
        d = output/phase
        d.mkdir(exist_ok=True)
        command = ('from maya import cmds\nfrom tools.maya_gui.time_editor_e2e import probe\n'
                   f'cmds.evalDeferred(lambda: probe({str(config)!r}, {phase!r}), lowestPriority=True)')
        try:
            report['phases'][phase] = run_maya_e2e(project_root=ROOT, version=args.maya,
                out_dir=d, port=args.port, timeout=args.timeout, log_path=d/'run.log',
                report_path=d/'report.json', command=command, marker=MARKER,
                send_label='time-editor-'+phase, stale_paths=(d/'run.log', d/'report.json'),
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
