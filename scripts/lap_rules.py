"""Lap rules shared by the tools (standard library only), so every script means the same thing
by a clean lap and by fade."""
import statistics as st

CLEAN_FACTOR = 1.07     # a clean lap is no slower than this times the session's median full lap


def clean_flags(full_lap_times):
    """True for each full lap that is clean: no slower than CLEAN_FACTOR times the session's
    median full lap (drops laps with traffic, a slow-down or a stop)."""
    if not full_lap_times:
        return []
    med = st.median(full_lap_times)
    return [t <= med * CLEAN_FACTOR for t in full_lap_times]


def third(n):
    """How many laps make the first and the last third of n clean laps; 0 when n < 3 (no fade)."""
    return max(1, n // 3) if n >= 3 else 0


def fade(clean_times):
    """Average of the last third of the clean laps minus the first third (positive = slower late
    in the session), or None with fewer than three clean laps."""
    k = third(len(clean_times))
    return st.mean(clean_times[-k:]) - st.mean(clean_times[:k]) if k else None
