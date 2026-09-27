"""Cache-only seek evidence boundary shared by EOS and display consumers.

Missing legacy capability is distinct from failed/malformed advertised capability.
Wrong-source data is unknown, not success. This reads no native clock or GUI object.
"""
from __future__ import annotations
from dataclasses import dataclass
from src.video.seek_receipt import SeekReceipt

_MISSING = object()
_ERRORS = (AttributeError, OSError, ReferenceError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True, slots=True)
class SeekObservation:
    """One optional receipt or explicit unavailability; never retain exceptions."""
    supported: bool = False
    available: bool = True
    receipt: SeekReceipt | None = None
    error_type: str = ''

    def __post_init__(self) -> None:
        if type(self.supported) is not bool or type(self.available) is not bool:
            raise TypeError('Invalid seek availability')
        if type(self.error_type) is not str or len(self.error_type) > 256:
            raise TypeError('Invalid seek diagnostic')
        if self.receipt is not None and (type(self.receipt) is not SeekReceipt
                                       or not self.supported or not self.available):
            raise ValueError('Invalid seek observation receipt')

    @property
    def blocks_end(self) -> bool:
        return not self.available or (self.receipt is not None and self.receipt.blocks_end)


def read_seek_observation(backend: object | None, source: str | None) -> SeekObservation:
    """Strict dynamic backend boundary; no fallback from failure to no-pending-work."""
    try:
        getter = getattr(backend, 'get_seek_receipt', _MISSING)
        if getter is _MISSING:
            return SeekObservation()
        if not callable(getter):
            raise TypeError('Seek receipt getter is not callable')
        receipt = getter()
        if receipt is not None and (type(receipt) is not SeekReceipt or receipt.source != source):
            raise ValueError('Seek receipt has invalid type or source ownership')
        return SeekObservation(supported=True, receipt=receipt)
    except _ERRORS as exc:
        return SeekObservation(supported=True, available=False, error_type=type(exc).__name__[:256])
