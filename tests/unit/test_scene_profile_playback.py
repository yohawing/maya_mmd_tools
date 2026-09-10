"""Native playback measurement units, partial-frame rejection, and cleanup."""

import json
import sys
from types import SimpleNamespace

import pytest

from tools.render_override import scene_profile


class Playback:
    def __init__(self, missing=False, stop_error=False):
        self.frame = 0
        self.missing = missing
        self.stop_error = stop_error
        self.callback = None
        self.removed = []
        self.settings = {}

    def currentUnit(self, **kwargs):
        return 'ntsc'

    def profiler(self, **kwargs):
        pass

    def modelEditor(self, *args, **kwargs):
        pass

    def setFocus(self, *args):
        pass

    def playbackOptions(self, **kwargs):
        if kwargs.pop('query', False):
            return self.settings[next(iter(kwargs))]
        self.settings.update(kwargs)

    def currentTime(self, frame=None, **kwargs):
        if kwargs.get('query'):
            return self.frame
        self.frame = frame

    def play(self, **kwargs):
        if kwargs.get('forward'):
            for frame in range(self.settings['minTime'], self.settings['maxTime'] + 1):
                if self.missing and frame == self.settings['maxTime'] - 1:
                    continue
                self.frame = frame
                self.callback()
        if kwargs.get('query'):
            return False
        if kwargs.get('state') is False and self.stop_error:
            raise RuntimeError('stop failed')

    def mmdOrderedRenderWitness(self):
        return json.dumps({'error': '', 'drawCount': 1})

    def evaluator(self, **kwargs):
        return False

    def register(self, panel, callback):
        self.callback = callback
        return 42


def run(monkeypatch, playback, report):
    maya = SimpleNamespace(
        OpenMaya=SimpleNamespace(MMessage=SimpleNamespace(removeCallback=playback.removed.append)),
        OpenMayaUI=SimpleNamespace(MUiMessage=SimpleNamespace(add3dViewPostRenderMsgCallback=playback.register)),
    )
    monkeypatch.setitem(sys.modules, 'maya', maya)
    stamps = iter(i * 0.005 for i in range(1000))
    monkeypatch.setattr(scene_profile.time, 'perf_counter', lambda: next(stamps))
    list(scene_profile.playback_steps(playback, 'panel', {'start': 650, 'playbackFrames': 2}, report))


def test_playback_records_milliseconds_after_warmup(monkeypatch):
    playback, report = Playback(), {}
    run(monkeypatch, playback, report)
    assert playback.removed == [42, 42, 42]
    for case in report['nativePlayback']:
        assert case['status'] == 'pass'
        assert case['intervalMs'] == pytest.approx([5, 5])
        assert case['meanMs'] == pytest.approx(5)
        assert len(case['samples']) == 13


def test_missing_frame_preserves_samples_without_passing(monkeypatch):
    playback, report = Playback(missing=True), {}
    with pytest.raises(AssertionError):
        run(monkeypatch, playback, report)
    assert playback.removed == [42]
    case = report['nativePlayback'][0]
    assert len(case['samples']) == 12
    assert case['status'] != 'pass'
    assert 'meanMs' not in case


def test_callback_is_removed_even_if_playback_stop_fails(monkeypatch):
    playback, report = Playback(stop_error=True), {}
    with pytest.raises(RuntimeError, match='stop failed'):
        run(monkeypatch, playback, report)
    assert playback.removed == [42]
    assert report['nativePlayback'][0]['samples']
