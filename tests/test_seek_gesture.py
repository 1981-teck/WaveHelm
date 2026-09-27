"""Pure intent contracts: frozen duration, invalid input, ownership and one commit."""
from dataclasses import replace
import pytest
from src.ui_wx.seek_gesture import SeekGesture, GesturePhase, ratio_value
from src.ui_wx.playback_presentation import DisplayReading
from src.controller.playback_view import PlaybackView
from src.controller.component_player.playback_state_manager import PlayerState as S
from src.controller.seek_observation import SeekObservation
from src.playback_observation import ReadingStatus as R


def context(state=S.PLAYING_VIDEO):
    return object(), PlaybackView(2,state,'same.mp4',0,'Same',True,False,False), DisplayReading(2.,10.,R.KNOWN)


def test_preview_is_separate_from_observed_pair_and_commits_once():
    owner,view,reading=context(); g=SeekGesture()
    assert g.begin(owner,view,reading,1.)
    for i in range(41): assert g.preview(i/50)
    assert g.preview_seconds==8.0 and reading.position==2.0
    assert g.take_target(owner,view,reading,2.)==8.0
    assert g.take_target(owner,view,reading,2.) is None


@pytest.mark.parametrize('field,value',[('revision',3),('path','other.mp4'),('index',1),
    ('state',S.PAUSED_VIDEO),('state',S.LOADING),('is_video',False)])
def test_changed_context_cancels_release(field,value):
    owner,view,reading=context();g=SeekGesture();assert g.begin(owner,view,reading,1.)
    assert g.take_target(owner,replace(view,**{field:value}),reading,2.) is None
    assert not g.active


def test_replacement_controller_or_duration_cannot_retarget_preview():
    owner,view,reading=context();g=SeekGesture();assert g.begin(owner,view,reading,1.)
    assert not g.matches(object(),view,reading,2.)
    assert not g.matches(owner,view,replace(reading,duration=120.),2.)
    assert g.preview(.5) and g.preview_seconds==5.0


@pytest.mark.parametrize('value',[None,True,'0.5',float('nan'),float('inf'),-float('inf'),10**400],
                         ids=['none','bool','str','nan','inf','neg-inf','overflow'])
def test_invalid_ratio_cancels_instead_of_seeking(value):
    owner,view,reading=context();g=SeekGesture();assert g.begin(owner,view,reading,1.)
    assert not g.preview(value) and not g.active


@pytest.mark.parametrize('state',[S.IDLE,S.LOADING,S.STOPPED,S.ERROR])
def test_inactive_context_refuses_begin(state):
    owner,view,reading=context(state);g=SeekGesture();assert not g.begin(owner,view,reading,1.)


@pytest.mark.parametrize('field,value',[('duration',0.),('duration',-1.),('duration',float('inf')),
    ('position',None),('position',float('nan')),('position',-1.),('status',R.ERROR),('status',R.STALE)])
def test_unqualified_pair_cannot_anchor_input(field,value):
    owner,view,reading=context();g=SeekGesture()
    assert not g.begin(owner,view,replace(reading,**{field:value}),1.)


@pytest.mark.parametrize('state',[S.PAUSED_AUDIO,S.PAUSED_VIDEO])
def test_paused_retained_pair_can_seek_without_resuming(state):
    owner,view,reading=context(state);g=SeekGesture()
    assert g.begin(owner,view,replace(reading,status=R.UNAVAILABLE),1.)
    assert g.preview(0.) and g.preview_seconds==0.


@pytest.mark.parametrize('now',[0.,122.,float('nan'),float('inf')])
def test_expired_or_backward_clock_invalidates_intent(now):
    owner,view,reading=context();g=SeekGesture();assert g.begin(owner,view,reading,1.)
    assert not g.matches(owner,view,reading,now)


def test_pending_or_unavailable_receipt_refuses_gesture():
    owner,view,reading=context();g=SeekGesture()
    view=replace(view,seek=SeekObservation(True,False,error_type='RuntimeError'))
    assert not g.begin(owner,view,reading,1.)


@pytest.mark.parametrize('value,expected',[(0.,0.),(1.,1.),(-1.,0.),(2.,1.),(.5,.5)])
def test_finite_endpoint_clamping(value,expected):
    assert ratio_value(value)==expected


@pytest.mark.parametrize('field', ['position', 'duration'])
def test_overflowing_clock_field_is_refused_without_exception(field):
    owner, view, reading = context(); g = SeekGesture()
    assert not g.begin(owner, view, replace(reading, **{field: 10**400}), 1.0)


@pytest.mark.parametrize('now', [True, -1.0, None, '1', 10**400],
                         ids=['bool', 'negative', 'none', 'text', 'huge'])
def test_invalid_start_clock_refuses_intent(now):
    owner, view, reading = context(); g = SeekGesture()
    assert not g.begin(owner, view, reading, now)
