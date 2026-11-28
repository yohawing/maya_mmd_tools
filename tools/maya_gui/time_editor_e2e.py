"""Background GUI E2E: Import tab Time Editor clips, clip edits, undo and fresh reload."""
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


def _known_render_warning(message):
    """Keep unrelated renderer limitations visible but out of the gate."""
    return message.strip() in {
        "不明なオブジェクト タイプ: dx11Shader",
        "Unknown object type: dx11Shader",
        "不明なオブジェクト タイプ: GLSLShader",
        "Unknown object type: GLSLShader",
        "MMD Render requires an active DirectX 11 renderer; override skipped.",
    }

def _source_hashes():
    sources = [ROOT/path for path in (
        'mmd_tools/core/mmd_time_editor.py', 'mmd_tools/io/mmd_importer.py',
        'mmd_tools/io/vmd_importer.py', 'mmd_tools/converters/vmd_scene_collector.py',
        'mmd_tools/ui/presenters/import_export_presenter.py', 'mmd_tools/ui/tabs/import_export_tab.py',
        'mmd_tools/services/settings_service.py', 'mmd_tools/plugin_main.py',
        'tests/integration/test_mmd_time_editor.py', 'tools/maya_gui/time_editor_e2e.py')]
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}


def _gate_report(report):
    """Require both hosts to have tested the candidate recorded before launch."""
    phases = [report['phases'].get(phase, {}) for phase in ('baseline', 'save', 'reload')]
    expected = report['expected_source_sha256']
    report['source_hashes_match'] = bool(expected) and all(p.get('source_sha256') == expected for p in phases)
    report['status'] = 'pass' if report['source_hashes_match'] and all(p.get('status') == 'pass' for p in phases) else 'fail'


class _MemoryStore:
    """QSettings stand-in so the probe does not touch the user's Import tab view state."""

    def __init__(self):
        self._values = {}

    def value(self, key, default=None):
        return self._values.get(key, default)

    def setValue(self, key, value):
        self._values[key] = value


def _import_tab(root):
    """Build the real Import tab and presenter targeting ``root`` without persisting settings."""
    from types import SimpleNamespace
    from mmd_tools.actions.import_vmd_action import ImportVmdAction
    from mmd_tools.services.settings_service import SettingsService
    from mmd_tools.ui.import_export_view_state import ImportExportViewState
    from mmd_tools.ui.presenters.import_export_presenter import ImportExportPresenter
    from mmd_tools.ui.tabs.import_export_tab import ImportExportTab
    from tests.integration.test_mmd_time_editor import VMD

    class RecordingAction(ImportVmdAction):
        last = None

        def execute(self, request):
            RecordingAction.last = super().execute(request)
            return RecordingAction.last

    service = SettingsService()
    build = service.build_vmd_import_options
    # Pin the import settings in memory; the user's optionVars stay untouched.
    service.build_vmd_import_options = lambda target_model=None: dict(
        build(target_model), vmd_fps=30, motion_scale=1.0, bake_mode=False, create_mmd_control_rig=False,
        use_animation_layers=False, clear_existing_motion=False)
    statuses = []
    app_state = SimpleNamespace(current_model_root=root, scene_model_service=None, available_models=None,
                                emit_status=statuses.append, emit_progress=lambda _value: None)
    tab = ImportExportTab(view_state=ImportExportViewState(settings_store=_MemoryStore()), settings_service=service)
    presenter = ImportExportPresenter(tab, app_state, import_vmd_action=RecordingAction(), settings_service=service)
    tab.vmd_path_edit.setText(str(VMD))
    tab.show()
    return tab, presenter, statuses


def probe(config_path, phase):
    from maya import cmds, mel
    from maya.api import OpenMaya as om
    from tests.common.maya_plugin_setup import load_mmd_tools_plugin
    from tests.integration.test_mmd_time_editor import add_clip, import_model, poses, pose_error
    from mmd_tools.core.mmd_time_editor import is_time_editor_driven
    from tools.render_override.common import require_requested_plugin
    config = json.loads(Path(config_path).read_text(encoding='utf8'))
    output = Path(config['out'])
    report = {'status': 'fail', 'phase': phase, 'checks': {}, 'messages': [],
              'scope': 'baked VMD bone clips via the Import tab; 30fps; render correctness excluded'}
    report['source_sha256'] = _source_hashes()
    callback = om.MCommandMessage.addCommandOutputCallback(
        lambda msg, kind, unused: report['messages'].append({'kind': int(kind), 'text': msg})
        if kind in (om.MCommandMessage.kWarning, om.MCommandMessage.kError) else None)
    tab = None
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
            # poses() moves currentTime; getAttr(time=) does not evaluate Time Editor clips.
            actual = poses(joints, [int(f)+offset for f in expected])
            delta = max(pose_error(actual[int(f)+offset], expected[f]) for f in expected)
            check(name, delta < 1e-4, delta)

        if phase == 'baseline':
            # Ordinary VMD import through the same Import tab settings, in its own host.
            cmds.file(new=True, force=True)
            root, joints = import_model()
            tab, presenter, statuses = _import_tab(root)
            tab.import_vmd_button.click()
            check('ordinary_vmd_import', bool(presenter.import_vmd_action.last.root_node), statuses[-1:])
            check('ordinary_no_clip', not (cmds.ls(type='timeEditorClip') or []))
            baseline = poses(joints)
            check('actual_vmd_motion', pose_error(baseline[0], baseline[5]) > 1e-3)
            _write(output/'baseline.json', {'baseline': baseline})
        elif phase == 'save':
            baseline = json.loads((output/'baseline.json').read_text(encoding='utf8'))['baseline']
            cmds.file(new=True, force=True)
            root, joints = import_model()
            tab, presenter, statuses = _import_tab(root)
            cmds.select(joints[len(joints)//2], replace=True)
            tab.import_vmd_time_editor_button.click()
            first = presenter.import_vmd_action.last.root_node
            check('ui_clip_added', isinstance(first, dict) and bool(first.get('clipId')), statuses[-1:])
            check('ui_status_names_clip', first['clip'] in statuses[-1], statuses[-1:])
            check('joint_selection_survives', bool(cmds.ls(selection=True)))
            check('time_editor_driven', is_time_editor_driven(root))
            compare('first_clip_pose', joints, baseline)

            clips = sorted(cmds.ls(type='timeEditorClip') or [])
            cmds.undo()
            check('undo_keeps_clip', sorted(cmds.ls(type='timeEditorClip') or []) == clips, clips)
            compare('undo_first_clip_pose', joints, baseline)

            mel.eval('TimeEditorWindow')
            second = add_clip(root, name='second', start=20)
            check('second_clip_same_track', second['track'] == first['track'], [first['track'], second['track']])
            compare('two_clips_first_pose', joints, baseline)
            compare('two_clips_second_pose', joints, baseline, offset=20)

            # clipId must be a keyword; a positional id is silently ignored by timeEditorClip.
            cmds.timeEditorClip(edit=True, moveClip=10, clipId=second['clipId'])
            compare('moved_second_pose', joints, baseline, offset=30)
            cmds.timeEditorClip(edit=True, removeClip=True, clipId=first['clipId'])
            check('first_clip_removed', len(cmds.ls(type='timeEditorClip') or []) == 1)
            compare('after_remove_second_pose', joints, baseline, offset=30)

            cmds.file(rename=str(output/'clip.ma'))
            cmds.file(save=True, type='mayaAscii', force=True)
            _write(output/'identity.json', {'root': root, 'joints': joints, 'baseline': baseline})
            check('clip_saved', (output/'clip.ma').is_file())
        else:
            saved = json.loads((output/'identity.json').read_text(encoding='utf8'))
            cmds.file(str(output/'clip.ma'), open=True, force=True)
            root, joints = saved['root'], saved['joints']
            check('reload_fps', cmds.currentUnit(query=True, time=True) == 'ntsc')
            check('reload_one_clip', len(cmds.ls(type='timeEditorClip') or []) == 1)
            check('reload_time_editor_driven', is_time_editor_driven(root))
            compare('reload_second_pose', joints, saved['baseline'], offset=30)
        report['status'] = 'pass'
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if tab:
            tab.close()
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
    for name in ('baseline.json', 'clip.ma', 'identity.json'):
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
