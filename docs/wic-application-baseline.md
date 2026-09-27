# WIC application baseline

## Decision

The Windows application now selects the software frame-server/WIC video backend by default. `WAVEHELM_VIDEO_BACKEND=legacy_hwnd` remains an explicit diagnostic override only. Invalid backend values fail; WIC errors do not trigger an automatic return to HWND.

This promotion follows Windows owner evidence from the real wx application, not only the stand-alone diagnostic programs. The legacy HWND path remains in source for controlled comparison and recovery work, but it is not the normal runtime path.

## Evidence supporting promotion

The 08AM Windows application run used Python 3.12.10 on Windows 11 and completed 13 source loads with 12 manual NEXT actions. The run reported no traceback, native MediaEngine shutdown succeeded, the process exited normally, and post-run source bytes were unchanged. The mini-player and external-video progress controls were owner-confirmed stable after the 08AM repaint fix.

Resource samples did not show the monotonic growth seen on the legacy HWND path. FIRST_VIDEO measured 1,684 handles and 59 threads. AFTER_4 / AFTER_8 / AFTER_12 measured 1,789/74, 1,731/54 and 1,790/65 respectively; the sequence rises and falls rather than accumulating per source. After a 20-second dwell the process measured 1,785 handles and 64 threads. Closing only the video window while leaving WaveHelm alive reduced the process to 1,581 handles and 37 threads, below the FIRST_VIDEO counts.

Private memory likewise moved from about 915.9 MiB at FIRST_VIDEO to about 1,053.0 MiB after the 12-switch dwell, then fell to about 860.5 MiB after the video window closed while the application remained alive. These totals demonstrate recovery of substantial video-lifetime resources without terminating WaveHelm. They do not prove that every residual allocation or handle is released.

Earlier native WIC endurance evidence also exercised 25 source loads / 24 replacements in one MediaEngine and did not reproduce the legacy pattern of ten persistent `amdxx64.dll` threads plus hundreds of Event handles per source. That diagnostic evidence supports the application result but does not replace application qualification.

## Runtime contract

- Default when `WAVEHELM_VIDEO_BACKEND` is unset: `wic`.
- Explicit WIC: `WAVEHELM_VIDEO_BACKEND=wic`.
- Explicit legacy diagnostic path: `WAVEHELM_VIDEO_BACKEND=legacy_hwnd`.
- Any other value: fail immediately.
- Renderer errors: fail closed; no automatic fallback to the legacy path.

The WIC implementation keeps the existing single-flight UI/COM handoff, bounded viewport allocation, source/seek/rebind invalidation, explicit shutdown ownership and double-buffered wx presentation introduced and hardened through steps 08AI–08AM.

## Remaining limits

Promotion to the default backend does **not** establish all release claims. Still required or separately qualified:

- extended long-duration playback beyond the completed source-switch endurance windows;
- supported 1080p/4K and high-frame-rate performance envelopes;
- CPU and power cost of software frame-server/WIC presentation;
- fullscreen/high-DPI/multi-monitor behavior;
- long seek/EOS/loop/audio-video synchronization campaigns;
- complete release-candidate dependency, SBOM, vulnerability, packaging and Windows installer/signing checks where applicable;
- attribution of any small residual handle/memory deltas to concrete owners.

Do not describe the legacy HWND resource issue as a proved vendor-driver defect. The evidence demonstrates a strong path-dependent difference and a practical WIC mitigation on the tested system; it does not identify a specific faulty AMD function.
