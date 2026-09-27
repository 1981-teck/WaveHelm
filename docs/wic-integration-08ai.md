# 08AI — Controlled WIC application preview

## Decision and activation

This candidate integrates the measured software frame-server/WIC direction into
the actual wxPython application. It is **not Windows-qualified**, not the final
release default, and not a claim of zero leakage or a fix for an identified AMD
function. The functioning 08AD source is archived unchanged outside this tree.

`WAVEHELM_VIDEO_BACKEND=wic` explicitly selects the preview. The owner smoke
launcher always sets it. `legacy_hwnd` (also the unset default in this preview)
selects the unchanged rendering configuration for controlled rollback. Any other
value fails. There is **no error-triggered fallback** from WIC to HWND. Do not
interpret ordinary startup without the explicit flag as testing the mitigation.

No driver installation, registry writes, decoder deregistration, DXGI manager
attachment, DLL pinning or forced unloading is used by the implementation.

## Ownership, transport and presentation

The existing COM worker creates the MediaEngine with BGRA8 output and explicitly
reads the attributes back. HWND/visual/DXGI-manager attributes must be absent.
WIC factory and bitmap ownership belongs to that same worker; the engine pointer
is borrowed. Unexpected non-null failed native acquisitions are not called and
prevent a false completed-cleanup receipt. No COM release occurs in a finalizer.

A single-flight mailbox bridges this producer to the existing external wx video
surface. One tracked request and one unconsumed completion are allowed. wx must
consume before admitting another request; only the inactive pixel buffer is
written. The GUI never synchronously waits for a frame. A refused or uncertain
submission latches failure, and a response delayed beyond three seconds prevents
resubmission; an in-flight native operation is never deemed cancelled by a wait.

All WIC/MediaEngine calls run on the existing COM worker. Painting uses a borrowed
wx PaintDC HDC on the GUI thread only. The paint handler never owns or destroys a
COM bitmap. Closed/minimized/zero-client-area surfaces stop frame admission;
restore and rebind invalidate old geometry. The same engine can bind a new window
without re-creation. Source replacement, seek admission, stop, close and rebind
invalidate queued/completed frames. A native current-source BSTR is read, bounded,
compared with the committed source and freed on revision changes. This is not a
native per-frame source-epoch certificate; same-source reload and late native
callbacks still need owner qualification. Queued explicitly stale events no longer
publish old ENDED/PLAYING/ERROR notifications to the application.

S_FALSE from OnVideoStreamTick is a valid no-new-frame state. Repaint after resize,
restore or a completed seek may transfer the current frame while paused. Other
failed HRESULTs, invalid sizes, malformed source identity, transfer/copy errors
or GDI failure stop the WIC service and publish an application video error. Aspect
ratio is handled once by MediaEngine using the full bitmap destination rectangle;
no second manual letterbox transform is added.

## Explicit output and performance budget

The pixel target matches the wx client size, up to **3840 x 2160**. Exceeding either
dimension fails explicitly; the program does not silently downscale to 640x360.
This is an allocation cap, **not a statement that 4K playback is qualified**.
A BGRA image uses at most 33,177,600 bytes. The steady producer owns one WIC bitmap
and two CPU pixel buffers. Resize releases the prior bitmap before allocation;
old frames held by the GUI can transiently retain pixel arrays. A conservative
five-image bound is 165,888,000 bytes (158.2 MiB), excluding decoder/runtime memory,
small object metadata and allocator retention. Python memory counts/CPU performance
on Windows still require observation; the cap is not a whole-process RAM bound.

Image storage is reused in steady state, and copy/paint work is O(width*height).
Metadata admission is O(1), no image data is hashed in the application, and no
logging occurs per frame/in the paint handler. First-presentation receipts are
reported in the low-frequency monitor check (at most every two seconds).
Python queue envelopes, ctypes wrappers and wx PaintDC objects still allocate.
This is **not a certified no-heap or hard-real-time path**. Full performance and
allocation qualification remains open; the renderer must not be advertised as
having satisfied stricter real-time guarantees.

The GUI timer derives a bounded interval from the current monitor (24–120 Hz
pacing, explicit 60 Hz budget if unknown), skips catch-up bursts and bounds pending
work. This is **not vblank synchronization**. Smooth 60 fps, 1080p/4K, high-DPI,
multiple monitors, HDR and subtitle composition remain NOT VERIFIED on Windows.

## Shutdown corrections in scope

Native Shutdown errors now propagate into the existing FAILED_UNCERTAIN receipt
rather than a false returned job. Rebind/recreation calls native Shutdown before
release. Detached shutdown jobs retain the WIC owner, stop admission before
releasing, and run behind admitted COM work. The relevant previous test that
expected swallowed errors was intentionally changed (error-semantics contract).

The old unbounded native-event queue and the COM manager's thread-field reset after
a join timeout are not refactored by this change. They remain legacy observations;
this integration does not certify all pre-existing lifecycle behavior. The frame
mailbox does not add unbounded work to those paths.

## Owner application smoke (first acceptance stage)

The supplied `RUN_08AI_WIC_APPLICATION_SMOKE.cmd` launches **main.py --ui-backend wx**,
not a native stand-alone reproduction. It authenticates/extracts source, runs the
exact local test set, forces WIC, and isolates APPDATA/LOCALAPPDATA in a unique
qualification directory. The library is initially empty; existing WaveHelm data
and configuration are not changed by the child environment.

Open a long video and confirm motion/colours, audio/sync, pause/resume/seek,
resize/minimize/restore, and one next/previous pair through the actual interface.
These controls are MANUAL; no next-switch automation is hidden in this smoke.
Y/YES/SI are accepted, NO records failure, NOT_CHECKED remains incomplete. Prompts
have a five-minute response budget. The runner checks WIC presentation, successful
native Shutdown, at least eight delivered frames, child exit/reaping, and exact
post-run source hashes. Runtime logs and process samples are retained even after
failure. No stability verdict is assigned from a smoke.

After this smoke, use the real application for a repeated-source resource test,
complete-file/EOS/loop checks, seek/audio-only transitions, and measured supported
resolution/frame-rate/CPU/UI latency and A/V sync tests. Do not infer those results
from the standalone 08AH10 run or from portable unit tests.

## Source manifests and provenance

`SOURCE_MANIFEST_R5.json` remains the byte-identical historical manifest described
in docs/installed-resources.md; it is not relabeled as current. The delivered
**external `SOURCE_MANIFEST_08AI.json`** covers all files in this candidate.
Requirements, lockfiles, build requirements, license texts and public APIs outside
the documented error-semantics correction are not changed.

Primary API references (interpretation, not owner validation):
- Microsoft IMFMediaEngine::TransferVideoFrame (full destination, letterboxing, WIC target).
- Microsoft IMFMediaEngine::GetCurrentSource (owned BSTR, caller SysFreeString).
- Microsoft IMFMediaEngine::OnVideoStreamTick (S_OK / S_FALSE, display cadence).
- wxPython wx.PaintDC and wx.Display (GUI paint ownership and monitor mode).
