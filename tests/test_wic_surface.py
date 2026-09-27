"""UI boundary scheduling/paint tests; not a substitute for owner wx/Windows smoke."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.ui_wx import wic_surface as ui
from src.video.wic_native import WicError
from tests.test_wic_pipeline import fixture


class Timer:
    def __init__(self) -> None:
        self.periods=[]; self.stops=0; self.accept=True
    def Start(self, ms):
        self.periods.append(ms)
        return self.accept
    def Stop(self):
        self.stops+=1


class Panel:
    def __init__(self) -> None:
        self.size=(2,2); self.visible=True; self.refreshes=0; self.bindings=[]; self.unbindings=[]
    def Bind(self,*args): self.bindings.append(args)
    def Unbind(self,*args,**kwargs): self.unbindings.append((args,kwargs)); return True
    def SetBackgroundStyle(self,style): self.style=style
    def GetHandle(self): return 7
    def GetClientSize(self): return self.size
    def IsShownOnScreen(self): return self.visible
    def Refresh(self,eraseBackground=True): self.refreshes+=1


class Wx:
    EVT_PAINT='paint'; EVT_TIMER='timer'; BG_STYLE_PAINT=1
    def __init__(self):
        self.timer=Timer(); self.iconized=False; self.calls=[]; self.paints=0; self.clears=0
    def Timer(self,panel): return self.timer
    def AutoBufferedPaintDC(self,panel):
        self.paints+=1
        return SimpleNamespace(
            GetHandle=lambda:0x100000123,
            SetBackground=lambda x:None,
            Clear=lambda:self._clear(),
        )
    def _clear(self): self.clears+=1
    def Brush(self,colour): return colour
    def CallAfter(self,callback,*args): self.calls.append((callback,args))
    def GetTopLevelParent(self,panel): return SimpleNamespace(IsIconized=lambda:self.iconized)


@pytest.fixture
def driver(monkeypatch):
    service,renderer,queue,state=fixture()
    wx,panel=Wx(),Panel(); paints=[]; reports=[]
    adapter=SimpleNamespace(get_frame_pipeline=lambda hwnd:service if hwnd==7 else None,
                            report_frame_error=reports.append)
    monkeypatch.setattr(ui,'GdiPainter',lambda:SimpleNamespace(paint=lambda *args:paints.append(args)))
    result=ui.WicSurfaceDriver(
        wx, panel,
        pipeline_resolver=adapter.get_frame_pipeline,
        error_reporter=adapter.report_frame_error,
    )
    return SimpleNamespace(d=result,s=service,r=renderer,q=queue,wx=wx,panel=panel,
                           paints=paints,reports=reports,state=state,adapter=adapter)


def test_gui_does_not_wait_for_native_producer(driver) -> None:
    f=driver
    f.d._tick(1)
    assert len(f.q.items)==1 and not f.r.calls and not f.paints
    f.d.on_paint()
    assert not f.paints  # In-flight does not expose partial pixels.
    f.q.run(); f.d._tick(2); f.d.on_paint()
    assert len(f.paints)==1 and f.s.presented==1
    assert f.wx.clears==1  # Only the earlier no-frame paint cleared to black.
    assert len(f.q.items)==1  # Next frame scheduled without blocking the buffered paint.


def test_valid_frame_is_not_precleared_before_buffered_blit(driver) -> None:
    f=driver
    f.d._tick(1); f.q.run(); f.d._tick(2)
    before=f.wx.clears
    f.d.on_paint()
    assert f.wx.paints==1 and len(f.paints)==1
    assert f.wx.clears==before
    assert f.paints[0][0]==0x100000123


def test_missing_or_obsolete_frame_clears_buffer_to_black(driver) -> None:
    f=driver
    f.d.on_paint()
    assert f.wx.clears==1 and not f.paints
    f.d._tick(1); f.q.run(); f.d._tick(2)
    f.panel.size=(3,3); f.d.on_paint()
    assert f.wx.clears==2 and not f.paints


@pytest.mark.parametrize('reason',['hidden','iconized','zero'])
def test_suspension_and_restore_are_bounded(driver,reason) -> None:
    f=driver; f.d._tick(1); f.q.run(); f.d._tick(2); f.q.run()
    if reason=='hidden': f.panel.visible=False
    elif reason=='iconized': f.wx.iconized=True
    else: f.panel.size=(0,0)
    f.d._tick(3)
    assert f.d.suspended and f.s.current() is None
    before=f.s._revision
    f.d._tick(4)
    assert f.s._revision==before and not f.q.items
    f.panel.visible=True; f.wx.iconized=False; f.panel.size=(2,2)
    f.d._tick(5)
    assert not f.d.suspended and len(f.q.items)==1


def test_no_paint_after_source_invalidation_or_geometry_change(driver) -> None:
    f=driver; f.d._tick(1); f.q.run(); f.d._tick(2); f.d.on_paint()
    assert len(f.paints)==1
    f.panel.size=(3,3); f.d.on_paint(); assert len(f.paints)==1
    f.panel.size=(2,2); f.s.invalidate(); f.d.on_paint(); assert len(f.paints)==1


def test_native_error_stops_timer_reports_once_and_never_paints(driver) -> None:
    f=driver; f.d._tick(1); failure=WicError('copy failed'); f.s.fail(failure)
    f.d._tick(2); f.d.on_timer(); f.d.on_paint()
    assert f.d.reported is failure and f.wx.timer.stops==1
    assert len(f.wx.calls)==1 and not f.paints
    callback,args=f.wx.calls.pop(); callback(*args)
    assert f.reports==[failure]


def test_gdi_failure_is_deferred_until_paintdc_scope_ends(driver) -> None:
    f=driver; f.d._tick(1); f.q.run(); f.d._tick(2)
    def fail(*args): raise WicError('paint failed')
    f.d.painter.paint=fail
    f.d.on_paint()
    assert len(f.wx.calls)==1 and not f.reports and f.s.error() is not None


def test_late_callbacks_after_close_cannot_touch_panel_or_submit(driver) -> None:
    f=driver; f.d._tick(1); f.d.close(); f.d.close()
    before=f.wx.paints
    f.d.on_paint(); f.d.on_timer(); f.q.run()
    assert f.wx.paints==before and len(f.panel.unbindings)==2
    assert f.d.pipeline is None and not f.q.items


def test_timer_deadline_has_no_catchup_burst(driver,monkeypatch) -> None:
    f=driver; now=[1.0]
    monkeypatch.setattr(ui.time,'monotonic',lambda:now[0])
    f.d.on_timer(); f.q.run()
    for _ in range(20): f.d.on_timer()
    assert f.s.requests==1
    now[0]=100.0; f.d.on_timer()
    assert f.s.requests==2


@pytest.mark.parametrize('hz,expected',[(24,42),(30,33),(60,17),(120,8),(144,8),(360,8),(0,17)])
def test_display_rate_is_explicit_bounded_not_vsync(hz,expected) -> None:
    class Display:
        @staticmethod
        def GetFromWindow(panel): return 0
        def __init__(self,index): self.index=index
        def GetCurrentMode(self): return SimpleNamespace(refresh=hz)
    assert ui.display_interval(SimpleNamespace(Display=Display),Panel())==expected


def test_start_refusal_is_not_silent(monkeypatch) -> None:
    wx=Wx(); wx.timer.accept=False
    monkeypatch.setattr(ui,'GdiPainter',lambda:object())
    panel=Panel()
    with pytest.raises(WicError):
        ui.WicSurfaceDriver(
            wx, panel,
            pipeline_resolver=lambda hwnd: None,
            error_reporter=lambda error: None,
        )
    assert wx.timer.stops==1 and len(panel.unbindings)==2


def test_monitor_rate_change_checks_timer_return(driver,monkeypatch) -> None:
    f=driver; f.d._tick(1); f.wx.timer.accept=False
    monkeypatch.setattr(ui,'display_interval',lambda *args:8)
    f.d.next_monitor_query=0
    with pytest.raises(WicError): f.d._tick(4)


def test_missing_controller_pipeline_does_not_submit(driver) -> None:
    f=driver
    f.d.pipeline_resolver=lambda hwnd:None
    f.d._tick(1)
    assert f.d.pipeline is None and not f.q.items


def test_explicit_resolver_is_retried_until_live_pipeline_exists(driver) -> None:
    f=driver
    calls=[]
    f.d.pipeline_resolver=lambda hwnd: calls.append(hwnd) or None
    f.d._tick(1)
    assert calls == [7] and f.s.requests == 0
    f.d.pipeline_resolver=lambda hwnd: f.s if hwnd == 7 else None
    f.d._tick(2)
    assert f.d.pipeline is f.s and f.s.requests == 1
