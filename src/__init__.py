# -*- coding: utf-8 -*-
"""WaveHelm package root.

This file is intentionally minimal.

Rationale:
- Ensures `import src.*` works reliably even when the project root is added to `sys.path`
  by standalone tools (e.g., ABI harness / smoke tests).
- Keeps compatibility with namespace-package behavior; no side effects on import.
"""

__all__: list[str] = []
