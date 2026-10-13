"""Keep a Windows laptop awake while the data engine runs (no-op elsewhere).

SetThreadExecutionState is per-thread: it is called from the long-lived scheduler thread and released
automatically when that thread or the process ends. It does not stop sleep when the lid is closed if Windows
is set to sleep on lid close; that is a power setting the user controls.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


def prevent_sleep() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ok = ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        if ok:
            log.info("keeping the system awake while the data engine runs")
        return bool(ok)
    except Exception:  # never let power management stop the engine
        log.exception("could not set the execution state")
        return False
