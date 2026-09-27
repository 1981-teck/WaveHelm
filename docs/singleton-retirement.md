# Generic singleton metaclass retirement — R5I-step07O

## Decision and exact compatibility boundary

REMOVE `src/utils/singleton.py` and its `ThreadSafeSingleton` metaclass. This retires
one unused implementation; it does not remove singleton behavior from the player.
The deleted file was packaged through automatic `src*` discovery, so its import
path was reachable even though it had no supported consumer found in this source.
This is an intentional import-path compatibility change, not a transparent alias.

All source, tests, tools, build/CI configuration and documentation in exact step07N
were searched for the class, module, file path, import statements, metaclass usage
and explicit dynamic references. The only class reference outside the definition
was cleanup planning. The old internal `SOURCE_MANIFEST_R5.json` lists the file but
is explicitly historical, not current authority. `src/utils/__init__.py` is a
minimal package marker with no re-export. There are no discovered application,
test, tool or documented API clients. This evidence cannot exclude arbitrary
computed imports, unknown external subclasses, pickles or extensions.

The module imported `threading`, allocated its own Lock and kept a class-instance
map. No repository client used that lock or map. Audio configuration, database,
service-container and video-adapter ownership are implemented independently. Their
source files and existing behavioral tests are preserved; no replacement metaclass,
shared lock, wrapper, singleton conversion or hidden fallback is introduced.

## Alternatives considered

A compatibility shim would retain the unused generic API and its implementation
burden. Keeping it was possible, but retirement was selected in this bounded
cleanup because no promised compatibility contract or consumer was found.
There is no universal replacement import. External clients of this exact metaclass
must retain the previous checkpoint or explicitly migrate their instance ownership
before adopting this candidate. Do not substitute an active WaveHelm service
factory as though it were a drop-in metaclass. The filename alone did not authorize
the removal; the consumer review and packaging tests are part of the decision.

## Installation and migration

For a source trial, extract the complete new source ZIP to a NEW directory.
Copying over an old checkout can leave `src/utils/singleton.py` or adjacent legacy
bytecode behind, so overlay copying is not a valid retirement verification.
Do not delete anything from the working application, user-data directories or
existing verification environment as part of this checkpoint. No auto-updater runs.

The candidate wheel and sdist must both omit the retired module while preserving
all other application payloads. Local release evidence checks a clean offline
installation and an N-to-O wheel upgrade in separate disposable environments.
Those packaging tests omit dependencies and are not GUI or physical playback tests.
The installed entrypoint stays `wavehelm = main:main`; the development application
version stays `1.0.2.dev23`. This engineering checkpoint is not publication approval.

## Tests and observation limits

New tests cover source/import absence, stale bytecode absence, explicit consumer
references, the unchanged utils namespace, a retained helper, real configuration
reuse/reset/error contracts and independent database class construction policy.
Before removal, exactly the source-presence and successful-import expectations
fail; the other ten cases pass. No pre-existing application test is changed.
Existing DbCore, adapter, bootstrap and packaging tests provide adjacent coverage.
Raw command logs, counts, patch replay, source inventories and package inspection
are in the accompanying report/evidence, not inferred from this document.

The full application runtime, physical devices/GUI, current dependency advisory
scan and complete static/security/coverage campaigns remain separate. Prior Windows
F/G/G2 and L/M acceptance remains CLOSED in its own scope, not attributed to a
new execution of O. Current source/evidence inventories supersede the historic
R5 manifest without rewriting old evidence.
