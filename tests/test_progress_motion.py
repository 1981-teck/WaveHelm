"""Causal cursor policy with explicit clock samples; no native rendering claim."""
from dataclasses import replace
import math

import pytest

from src.playback_observation import ClockValue, ClockOrigin, ReadingStatus
from src.ui_wx.progress_motion import ProgressMotion, MotionMode
from tests.test_playback_view import make_source, make_sample


def sample(position=4.0, stamp=10.0, sequence=1, **kwargs):
    return make_sample(make_source(), position=position, stamp=stamp, sequence=sequence, **kwargs)


def begin():
    motion = ProgressMotion()
    a, b = sample(), sample(4.25, 10.25, 2)
    assert motion.advance(a, 4.0, 10.0, enabled=True).position == 4.0
    assert motion.advance(b, 4.25, 10.25, enabled=True).position == 4.0
    return motion, b


@pytest.mark.parametrize('elapsed', [0.0, .033, .05, .1, .2, .25, .3, .5])
def test_interpolates_only_between_observed_endpoints(elapsed):
    motion, b = begin()
    result = motion.advance(b, 4.25, 10.25 + elapsed, enabled=True)
    assert result.position == pytest.approx(4.0 + min(elapsed, .25))
    assert result.position <= b.clock.position.seconds
    assert result.mode is (MotionMode.INTERPOLATED if elapsed < .25 else MotionMode.OBSERVED)


def test_repeated_wakeups_never_rearm_a_segment():
    motion, b = begin()
    for step in range(1, 251):
        result = motion.advance(b, 4.25, 10.25 + step / 1000, enabled=True)
        assert result.position == pytest.approx(4.0 + step / 1000)
    assert result.position == 4.25


@pytest.mark.parametrize('delay', [.75, 1.0, 12.0, 1000000.0])
def test_missing_updates_never_extrapolate(delay):
    motion, b = begin()
    result = motion.advance(b, 4.25, 10.25 + delay, enabled=True)
    assert result.position == 4.25 and result.mode is MotionMode.FROZEN


@pytest.mark.parametrize('change', ['source','revision','index','state','generation','stream','duration','origin'])
def test_every_semantic_discontinuity_snaps_to_new_observation(change):
    motion, b = begin()
    c = sample(4.5, 10.5, 3)
    if change == 'source': c = replace(c, path='new.mp4', clock=replace(c.clock, source='new.mp4'))
    if change == 'revision': c = replace(c, revision=c.revision + 1)
    if change == 'index': c = replace(c, index=3)
    if change == 'state': c = replace(c, state='PLAYING_AUDIO')
    if change == 'generation': c = replace(c, clock=replace(c.clock, generation=2))
    if change == 'stream': c = replace(c, stream='new:2')
    if change == 'duration': c = replace(c, clock=replace(c.clock, duration=ClockValue.read(20., duration=True)))
    if change == 'origin': c = replace(c, clock=replace(c.clock, origin=ClockOrigin.AUDIO_MIXER))
    assert motion.advance(c, 4.5, 10.5, enabled=True).position == 4.5


@pytest.mark.parametrize('position', [0.0, 2.0, 4.25, 5.0, 90.0])
def test_backwards_stationary_or_large_step_is_not_smoothed(position):
    motion, b = begin()
    c = sample(position, 10.5, 3)
    result = motion.advance(c, position, 10.5, enabled=True)
    assert result.position == position and result.mode is MotionMode.OBSERVED


@pytest.mark.parametrize('status', [ReadingStatus.UNAVAILABLE, ReadingStatus.ERROR, ReadingStatus.STALE])
@pytest.mark.parametrize('field', ['position','duration'])
def test_invalid_pair_cancels_even_an_active_segment(status, field):
    motion, b = begin()
    bad = replace(b, clock=replace(b.clock, **{field: ClockValue(status)}))
    result = motion.advance(bad, 4.25, 10.3, enabled=True)
    assert result.mode is MotionMode.FROZEN and result.position == 4.25
    # A recovered first sample rebases; old motion is not resumed.
    assert motion.advance(b, 4.25, 10.35, enabled=True).position == 4.25


@pytest.mark.parametrize('stamp', [float('nan'),float('inf'),-1.0,True,'10.3',10.0])
def test_bad_or_regressive_clock_cancels(stamp):
    motion, b = begin()
    result = motion.advance(b, 4.25, stamp, enabled=True)
    assert result.mode is MotionMode.FROZEN and result.position == 4.25


@pytest.mark.parametrize('reason', ['pause','disabled','missing','legacy','future','slow_read'])
def test_disabled_missing_and_unqualified_clocks_do_not_animate(reason):
    motion, b = begin()
    if reason == 'pause': b = replace(b, state='PAUSED_VIDEO')
    if reason == 'missing': b = None
    if reason == 'legacy': b = replace(b, clock=replace(b.clock, origin=ClockOrigin.LEGACY))
    if reason == 'future': b = replace(b, clock=replace(b.clock, finished_at=10.5))
    if reason == 'slow_read': b = replace(b, clock=replace(b.clock, started_at=10.0))
    result = motion.advance(b, 4.25, 10.3, enabled=reason != 'disabled')
    assert result.mode is MotionMode.FROZEN and result.position == 4.25


@pytest.mark.parametrize('gap', [.001,.01,.51,1.0])
def test_implausible_sample_gap_rebases(gap):
    motion = ProgressMotion(); a = sample()
    motion.advance(a,4.0,10.0,enabled=True)
    b = sample(4.25,10.0+gap,2)
    assert motion.advance(b,4.25,10.0+gap,enabled=True).position == 4.25


def test_late_delivery_shortens_blend_and_never_extends_age():
    motion = ProgressMotion(); a = sample()
    motion.advance(a,4.0,10.0,enabled=True)
    b = sample(4.25,10.25,2)
    motion.advance(b,4.25,10.70,enabled=True)
    assert motion.advance(b,4.25,10.74,enabled=True).position == pytest.approx(4.20)
    assert motion.advance(b,4.25,10.75,enabled=True).position == 4.25


def test_sustained_irregular_delivery_remains_bounded_and_monotone():
    motion = ProgressMotion(); previous_visual = 0.0
    for index in range(100):
        stamp = 100.0 + .2 * index
        current = sample(index * .2, stamp, index + 1, duration=100.0)
        for offset in [0., .01, .06, .12, .19]:
            rendered = motion.advance(current, index * .2, stamp+offset, enabled=True)
            assert previous_visual <= rendered.position <= current.clock.position.seconds
            previous_visual = rendered.position
    assert not hasattr(motion, '__dict__')


def test_reset_clears_old_motion_and_new_zero_is_observed():
    motion,b = begin(); motion.reset()
    zero = sample(0.0,10.4,1)
    assert motion.advance(zero,0.0,10.4,enabled=True).position == 0.0
    assert motion.advance(None,None,10.5,enabled=False).position is None
