"""Real Windows Media Engine timed-text control probe, without loading media.

Run in a disposable child process. Uses the production STA manager, core setup,
MFGetService route, metadata decoder and selection API. No mocked COM objects.
A hidden STATIC window hosts the real engine; no user file/URL is opened.
The probe verifies control/metadata, not cue rendering, codecs or leak freedom.
"""
from __future__ import annotations

import argparse
import ctypes as ct
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import traceback
from collections import deque
from typing import Callable, TypeVar, TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from src.video.media_engine_core import MediaEngineCore
    from src.video.media_engine_timed_text_backend import TimedTextPointer, TrackPointer
    from src.video.component_adapter.com_thread_manager import ComThreadManager

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_Result = TypeVar('_Result')
SOURCE_PATHS = (
    'tools/probe_timed_text_native.py', 'src/video/media_engine_timed_text_backend.py',
    'src/video/media_engine_core.py', 'src/video/media_engine_core_timed_text.py',
    'src/video/media_engine_core_playback.py', 'src/video/media_engine_core_shared.py',
    'src/video/media_engine_core_setup.py', 'src/video/media_engine_core_shutdown.py',
    'src/video/media_engine_core_vtable.py', 'src/video/media_engine_events.py',
    'src/video/component_adapter/com_thread_manager.py',
    'src/video/component_adapter/com_thread_manager_runtime.py',
    'src/video/component_adapter/media_engine_events.py',
    'src/video/component_base/definitions.py', 'src/video/component_base/definitions_abi.py',
    'src/video/component_base/definitions_runtime.py', 'src/video/component_base/iid_registry.py',
    'src/video/component_base/mf_helpers.py', 'src/video/component_base/com_helpers.py',
    'src/video/component_base/utils.py', 'src/video/component_base/definitions_events.py',
    'src/video/component_adapter/com_thread_manager_api.py',
    'src/video/media_engine_core_stream_api.py', 'src/video/media_engine_seek_events.py',
    'src/video/media_engine_seek.py', 'src/video/seek_receipt.py',
    'src/video/media_engine_clock.py', 'src/playback_observation.py',
    'src/video/media_engine_core_audio_streams.py',
)


def source_seals() -> dict[str, dict[str, int | str]]:
    result = {}
    for relative in SOURCE_PATHS:
        path = ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Expected regular source file: {relative}')
        data = path.read_bytes()
        if len(data) > 2 * 1024 * 1024:
            raise ValueError(f'Source file exceeds probe budget: {relative}')
        result[relative] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return result


def _write(path: Path, record: object) -> None:
    payload = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(payload + '\n', encoding='utf-8')
    temporary.replace(path)


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


class DiagnosticRecord(TypedDict):
    logger: str
    level: str
    message: str
    exception_type: str


class ProbeDiagnostics(logging.Handler):
    """Bounded probe-only error gate; retain facts without silencing existing logs.

    Callback exceptions may be swallowed at the native ABI boundary. They must fail
    this probe. Benign warnings are not errors; excess diagnostics remain counted.
    No exception/traceback object or per-success callback log is retained here.
    """
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.failure_count = 0
        self.records: list[DiagnosticRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith('src.video.'):
            return
        if record.levelno < logging.ERROR and not record.exc_info:
            return
        self.failure_count += 1
        if len(self.records) >= 8:
            return
        error_type = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else ''
        try:
            message = record.getMessage()[:1024]
        except (TypeError, ValueError) as exc:
            message = f'Diagnostic formatting failed: {type(exc).__name__}'
        self.records.append({'logger': record.name[:160], 'level': record.levelname[:32],
                             'message': message, 'exception_type': error_type[:128]})

    def snapshot(self) -> dict[str, object]:
        # Logging calls emit while holding this Handler's RLock.
        with self.lock:
            return {'failure_count': self.failure_count,
                    'records': [dict(value) for value in self.records],
                    'omitted_count': self.failure_count - len(self.records)}


class ProbeAdapter:
    """Real production STA dispatch, with bounded observation-only event storage."""
    def __init__(self) -> None:
        from src.video.component_adapter.com_thread_manager import ComThreadManager
        from src.video.media_engine_core import MediaEngineCore
        self._closed = False
        self._shutdown_requested = False
        self._core = MediaEngineCore(self)
        self.manager = ComThreadManager('WaveHelmTimedTextProbe', com_task_timeout=25.0)
        self.events: deque[tuple[int, int, int]] = deque(maxlen=128)

    def _get_com_thread_manager(self, *, ensure_started: bool = True) -> ComThreadManager:
        if ensure_started:
            self.manager.start()
        return self.manager

    def call_on_com_thread(self, name: str, callback: Callable[[], _Result]) -> _Result:
        return self.manager.call_on_com_thread(name, callback)

    def post_to_com_thread(self, name: str, callback: Callable[[], None]) -> None:
        self.manager.post_to_com_thread(name, callback)

    def submit_frame_to_com_thread(self, name: str, callback: Callable[[], object],
                                   response: object) -> bool:
        """Expose the production WIC admission surface without requesting frames here."""
        return self.manager.submit_to_com_thread(name, callback, response) is True

    def frame_ready(self) -> bool:
        """The timed-text probe never loads media, so it never publishes WIC pixels."""
        return False

    def frame_pump_ready(self) -> bool:
        """No source or seek is exercised by this control-only native probe."""
        return False

    def on_media_engine_event(self, event: int, param1: int, param2: int) -> None:
        from src.video.component_base.definitions_events import MF_MEDIA_ENGINE_EVENT_ERROR
        self.events.append((event, param1, param2))
        if event == MF_MEDIA_ENGINE_EVENT_ERROR:
            logging.getLogger('src.video.native_probe').error(
                'Media Engine ERROR notification: error=%s hresult=%s', param1, param2)



def _create_hidden_window() -> int:
    from ctypes import wintypes
    user32 = ct.WinDLL('user32.dll', use_last_error=True)
    create = user32.CreateWindowExW
    create.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                       ct.c_int, ct.c_int, ct.c_int, ct.c_int, wintypes.HWND, wintypes.HMENU,
                       wintypes.HINSTANCE, ct.c_void_p]
    create.restype = wintypes.HWND
    hwnd = create(0, 'STATIC', 'WaveHelm timed-text qualification', 0, 0, 0, 64, 64, None, None, None, None)
    if not hwnd:
        raise OSError(ct.get_last_error(), 'CreateWindowExW failed')
    return int(hwnd)


def _destroy_window(hwnd: int) -> None:
    from ctypes import wintypes
    destroy = ct.WinDLL('user32.dll', use_last_error=True).DestroyWindow
    destroy.argtypes, destroy.restype = [wintypes.HWND], wintypes.BOOL
    if not destroy(hwnd):
        raise OSError(ct.get_last_error(), 'DestroyWindow failed')


def _add_track(service: TimedTextPointer, label: str, language: str) -> TrackPointer:
    from src.video.media_engine_timed_text_backend import TrackPointer, _check_output
    owner = TrackPointer()
    hr = int(service.contents.lpVtbl.contents.AddTrack(service, label, language, 1, ct.byref(owner)))
    _check_output(hr, bool(owner), 'IMFTimedText.AddTrack(native probe)')
    return owner


def _remove_tracks(service: TimedTextPointer, owners: list[TrackPointer]) -> None:
    from src.video.media_engine_timed_text_backend import release_owned
    failures: list[str] = []
    for owner in owners:
        try:
            hr = int(service.contents.lpVtbl.contents.RemoveTrack(service, owner))
            if hr & 0x80000000:
                failures.append(f'RemoveTrack failed: 0x{hr & 0xFFFFFFFF:08X}')
        except (OSError, RuntimeError, ValueError, TypeError, ct.ArgumentError) as exc:
            failures.append(f'RemoveTrack call error: {exc}')
        finally:
            try:
                release_owned(owner, 'native probe added track')
            except (OSError, RuntimeError, ValueError, TypeError, ct.ArgumentError) as exc:
                failures.append(f'Added track release error: {exc}')
    if failures:
        raise RuntimeError('; '.join(failures))


def _exercise_tracks(core: MediaEngineCore, observe: Callable[[str], None]) -> None:
    from src.video.media_engine_timed_text_backend import timed_text_service
    from src.video.media_engine_core_timed_text import _get_text_track_descriptors_on_com_thread
    labels = (('WaveHelm italiano', 'it-IT'), ('WaveHelm \u03b1\u03b2', 'el-GR'))
    owners = []
    with timed_text_service(core) as service:
        observe('real_timed_text_service_acquired')
        try:
            for label, language in labels:
                owners.append(_add_track(service, label, language))
            observe('two_native_tracks_added')
            descriptors = _get_text_track_descriptors_on_com_thread(core)
            actual = {(d['raw_label'], d['language']) for d in descriptors}
            _check(set(labels).issubset(actual), 'Native GetLabel/GetLanguage roundtrip mismatch')
            observe('native_metadata_copied_and_task_memory_released')
            ids = [int(owner.contents.lpVtbl.contents.GetId(owner)) for owner in owners]
            _check(len(set(ids)) == 2, 'Native added tracks do not have distinct IDs')
            for target in ids:
                _check(core.select_text_track(target) is True, f'Native selection failed for {target}')
                _check(set(core.get_active_text_track_ids()).intersection(ids) == {target}, 'Active native IDs mismatch')
            observe('both_native_tracks_selected_and_read_back')
            _check(core.disable_text_tracks() is True, 'Native disable failed')
            _check(not set(core.get_active_text_track_ids()).intersection(ids), 'Native tracks remain active')
            observe('native_disable_confirmed')
        finally:
            _remove_tracks(service, owners)
        _check(not _get_text_track_descriptors_on_com_thread(core), 'Native removal did not leave an empty list')
        observe('native_tracks_removed')


def run_probe(output: Path, steps: list[str]) -> None:
    from src.video.media_engine_core_timed_text import _get_text_track_descriptors_on_com_thread
    adapter = ProbeAdapter()
    core = adapter._core
    window: list[int] = []
    def observe(name: str) -> None:
        steps.append(name)
        _write(output / 'progress.json', {'steps': steps})
    def exercise() -> None:
        window.append(_create_hidden_window())
        core._create_engine_on_com_thread(window[0])
        observe('production_media_engine_created_on_sta')
        _check(not _get_text_track_descriptors_on_com_thread(core), 'Fresh native engine is not empty')
        _exercise_tracks(core, observe)
    def close() -> None:
        adapter._shutdown_requested = True
        try:
            core.shutdown()  # Production cleanup: return is not proof of native leak freedom.
        finally:
            adapter._closed = True
            if window:
                _destroy_window(window.pop())
    adapter.manager.start()
    thread = adapter.manager._com_thread
    try:
        observe('production_sta_started')
        adapter.call_on_com_thread('probe_timed_text', exercise)
    finally:
        try:
            adapter.call_on_com_thread('probe_timed_text_cleanup', close)
        finally:
            adapter.manager.shutdown()
            _check(thread is not None and not thread.is_alive(), 'STA worker did not terminate')
    _check(core._shutdown_requested and not core._media_engine, 'Core shutdown state is not cleared')
    observe('core_shutdown_returned_and_worker_terminated')
    _write(output / 'events.json', list(adapter.events))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents or output.exists():
        parser.error('Output must be a NEW directory outside the source tree.')
    output.mkdir(parents=True)
    steps: list[str] = []
    result = {'schema': 'wavehelm-native-timed-text-control-v1', 'status': 'FAIL',
              'python': sys.version, 'executable': sys.executable, 'platform': sys.platform,
              'pointer_size': ct.sizeof(ct.c_void_p), 'steps': steps,
              'media_file_loaded': False, 'cue_rendering_verified': False,
              'full_com_lifecycle_verified': False, 'release_readiness': 'NOT_VERIFIED'}
    diagnostics = ProbeDiagnostics()
    root_logger = logging.getLogger()
    root_logger.addHandler(diagnostics)
    try:
        if sys.platform != 'win32' or ct.sizeof(ct.c_void_p) != 8:
            raise RuntimeError('Native Windows x64 is required; no emulation or fake service is used.')
        before = source_seals()
        _write(output / 'source-before.json', before)
        run_probe(output, steps)
        after = source_seals()
        _write(output / 'source-after.json', after)
        _check(before == after, 'Source files changed during native probe')
        result['source_unchanged'] = True
        _check(diagnostics.snapshot()['failure_count'] == 0,
               'Video/COM diagnostics contain errors; native probe cannot pass')
        result['status'] = 'NATIVE_TIMED_TEXT_CONTROL_PASS'
    except Exception as exc:  # Explicit CLI boundary: preserve error, return nonzero, never recover to PASS.
        result['error'] = f'{type(exc).__name__}: {exc}'
        (output / 'traceback.txt').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        root_logger.removeHandler(diagnostics)
    recorded = diagnostics.snapshot()
    diagnostics.close()
    result['diagnostic_failures'] = recorded['failure_count']
    if recorded['failure_count'] and result['status'] == 'NATIVE_TIMED_TEXT_CONTROL_PASS':
        result['status'] = 'FAIL'
        result['error'] = 'Video/COM error arrived before diagnostic capture closed'
    _write(output / 'diagnostics.json', recorded)
    _write(output / 'probe.json', result)
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result['status'] == 'NATIVE_TIMED_TEXT_CONTROL_PASS' else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
