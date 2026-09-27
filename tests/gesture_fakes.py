"""Extend the existing test-owned wx controls, never install a native substitute.

Capture ownership, missing capture and duplicate release are observable here.
These event constants model the binding API, not Windows notification ordering.
The fixture augments FakeWx only; no real wx or pygame import is intercepted.
"""
from tests.wx_fakes import FakeSlider, FakeWxModule


class GestureEvent:
    def __init__(self, *, x=80, position=800, key=None, down=True, owner=None):
        self.x, self.position, self.key = x, position, key
        self.down, self.owner, self.skipped = down, owner, False

    def GetX(self):
        return self.x

    def GetPosition(self):
        return self.position

    def GetKeyCode(self):
        return self.key

    def LeftIsDown(self):
        return self.down

    def GetEventObject(self):
        return self.owner

    def Skip(self):
        self.skipped = True


def install_progress_fakes(monkeypatch):
    events = ('LEFT_UP', 'SCROLL_THUMBTRACK', 'SCROLL_THUMBRELEASE', 'SCROLL_CHANGED',
              'MOUSE_CAPTURE_LOST', 'KILL_FOCUS', 'KEY_DOWN', 'KEY_UP', 'WINDOW_DESTROY')
    for name in events:
        monkeypatch.setattr(FakeWxModule, 'EVT_' + name, 'EVT_' + name, raising=False)
    for number, name in enumerate(('LEFT', 'RIGHT', 'UP', 'DOWN', 'HOME', 'END', 'PAGEUP', 'PAGEDOWN'), 300):
        monkeypatch.setattr(FakeWxModule, 'WXK_' + name, number, raising=False)
    monkeypatch.setattr(FakeWxModule, 'WXK_ESCAPE', 27, raising=False)

    def capture(slider):
        if getattr(slider, '_test_capture', False):
            raise RuntimeError('Test control already owns capture')
        slider._test_capture = True
        slider.capture_calls = getattr(slider, 'capture_calls', 0) + 1

    def release(slider):
        if not getattr(slider, '_test_capture', False):
            raise RuntimeError('Release without test-owned capture')
        slider._test_capture = False
        slider.release_calls = getattr(slider, 'release_calls', 0) + 1

    monkeypatch.setattr(FakeSlider, 'CaptureMouse', capture, raising=False)
    monkeypatch.setattr(FakeSlider, 'ReleaseMouse', release, raising=False)
    monkeypatch.setattr(FakeSlider, 'HasCapture', lambda slider: getattr(slider, '_test_capture', False), raising=False)
