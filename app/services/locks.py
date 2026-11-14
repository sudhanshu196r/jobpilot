"""Process-wide locks for writes that must not interleave."""

import threading

# Saving jobs and scoring them: the extension posts a listing and its detail pane
# at the same moment, and a score must never be computed from a stale copy.
jobs_lock = threading.RLock()
