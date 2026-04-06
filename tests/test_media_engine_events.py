from __future__ import annotations

import ctypes
import logging

import src.video.component_adapter.media_engine_events as media_engine_events


class FakeGuidObject:
    Data1 = 0x12345678
    Data2 = 0x9ABC
    Data3 = 0xDEF0
    Data4 = bytes.fromhex('1122334455667788')


class FakeIfaceGuid:
    _iid_ = FakeGuidObject()


class FakeIfaceString:
    IID = '{12345678-9ABC-DEF0-1122-334455667788}'


class Adapter:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    def on_media_engine_event(self, event, param1, param2):
        if self.fail:
            raise ValueError('boom')
        self.calls.append((event, param1, param2))



def test_safe_helpers_cover_success_and_fallback_paths():
    assert media_engine_events._safe_int('12') == 12
    assert media_engine_events._safe_int('bad') == 0

    buffer = ctypes.create_string_buffer(b'0123456789ABCDEF')
    ptr = ctypes.addressof(buffer)
    assert media_engine_events._safe_read_16bytes(ptr) == b'0123456789ABCDEF'
    assert media_engine_events._safe_read_16bytes(0) is None



def test_iid_bytes_from_iface_supports_guid_object_and_string(caplog):
    caplog.set_level(logging.DEBUG, logger=media_engine_events.logger.name)
    expected = bytes.fromhex('78563412BC9AF0DE1122334455667788')
    assert media_engine_events._iid_bytes_from_iface(FakeIfaceGuid) == expected
    assert media_engine_events._iid_bytes_from_iface(FakeIfaceString) == expected
    assert media_engine_events._iid_bytes_from_iface(object()) is None
    assert 'IID object extraction fallback' in caplog.text



def test_notify_com_eventnotify_returns_s_ok_and_swallows_adapter_errors():
    adapter = Adapter()
    notify = media_engine_events._MediaEngineNotifyCOM(adapter)
    assert notify.EventNotify('7', '8', '9') == int(media_engine_events.S_OK)
    assert adapter.calls == [(7, 8, 9)]

    failing = media_engine_events._MediaEngineNotifyCOM(Adapter(fail=True))
    assert failing.EventNotify('1', '2', '3') == int(media_engine_events.S_OK)
