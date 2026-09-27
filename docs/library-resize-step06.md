# Library layout — R5I-step06

This change is limited to GUI layout and its direct shared sizing consumers.
The search field has a dedicated expandable row. Filter and sort form labelled
horizontal groups in a native WrapSizer, moving to another line when necessary.
This intentionally spends one extra row on wide windows rather than rebuilding
controls on resize. Without WrapSizer, the selector groups stack vertically.
Controls, search text, selected enum/key, focus and column preferences are not
recreated or rewritten by resizing. Individual controls still have a physical
minimum: arbitrary sub-control widths or all monitor/font combinations are not
certified. No new main-window minimum or smaller font is imposed.

## Virtual and client sizes

The shared ancestor walk calls Layout on ordinary containers and FitInside only
on actual wx Scrolled/ScrolledWindow/ScrolledCanvas instances. A method named
FitInside alone does not establish a scrolling surface. No ordinary container
is forced to have a stored virtual width, no frame Fit is performed, and no table
width setting is cleared. This is a fresh-process fix, not a hot patch of a window
already poisoned by the previous implementation.

## Native measurements

Choice width uses native GetBestSize and, where available, GetTextExtent plus
DIP-converted chrome. Only the minimum/chrome constants use FromDIP; native metrics
are not scaled twice. The old automatically assigned minimum is temporarily
removed during best-size measurement and restored before applying the new size.
Buttons and checkboxes use the same fresh-minimum discipline. Failed measurement
is logged and contained. Secondary glyph-measurement failure can use a valid
native best size. No list items or selection are changed by sizing.

## Verification

The unit suite checks shared helper semantics, scrolled positive controls,
wide/narrow requests, original control identity, real locale files and input
preservation. Test-owned wx widgets do not prove native geometry.
`tools/probe_library_layout.py --output <new directory outside source>` runs
real wx in a bounded Windows child process. It constructs the production center
shell, sidebar, Simplebook and LibraryView with an empty in-memory catalog. It
opens no user media, creates no player/COM engine, and uses no user settings or DB.
It captures 60 native rectangle states over five locale transitions, two native
font-size factors and six size transitions, including height-only change. A wide
hidden page and a page roundtrip exercise the book boundary. A positive scrolled
control must still maintain a virtual extent. The parent requires a successful
child AND a complete successful geometry report. Partial failures are preserved.
This is native sizing/retention evidence, not pixel, DPI-monitor migration,
accessibility or multimedia qualification. The actual DPI and wx version are
recorded; font factors are not relabelled simulated monitor DPI.

## Remaining status

Real Windows geometry is NOT_RUN on the Linux reviewer. Run the supplied combined
collector after extracting step06 separately. Do not overwrite step05B/public1.0.1
or delete APPDATA. Current cursor/end/seek residuals and the missing independent
review of the earlier step05B automatic ZIP remain separate. No release approval,
remote update or renewed vulnerability/ABI certification is implied.
