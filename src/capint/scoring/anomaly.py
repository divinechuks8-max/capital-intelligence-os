"""Shared robust anomaly-detection helper (spec §28).

Compares one "window total" against a distribution of prior, non-overlapping
windows of the same length using the median/MAD (median absolute
deviation) rather than mean/stdev — a robust statistic that isn't thrown
off by a single huge historical outlier the way a z-score built on mean
and standard deviation would be.

Deliberately signal-family-agnostic: capint.scoring.insider_conviction
(Phase 3) and capint.scoring.institutional_accumulation (Phase 5) each
plug in their own "sum this metric over [start, end)" callback and get the
same bucketing + percentile/z-score treatment. When a third signal family
needs "how unusual is this relative to its own history", it reuses this
rather than re-deriving the math a third time.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal


def bucket_totals(
    window_start: datetime,
    window_days: int,
    baseline_lookback_days: int,
    window_total_fn: Callable[[datetime, datetime], Decimal],
) -> list[Decimal]:
    """Non-overlapping `window_days`-long windows immediately preceding
    `window_start`, each summed by `window_total_fn(start, end)` — so the
    caller controls what "total" means (dollars, shares, counts, ...)."""
    totals: list[Decimal] = []
    earliest = window_start - timedelta(days=baseline_lookback_days)
    bucket_end = window_start
    while True:
        bucket_start = bucket_end - timedelta(days=window_days)
        if bucket_start < earliest:
            break
        totals.append(window_total_fn(bucket_start, bucket_end))
        bucket_end = bucket_start
    return totals


def robust_percentile_and_z(baseline: list[Decimal], value: Decimal) -> tuple[float, float]:
    """Empirical percentile (fraction of baseline <= value) and a robust
    z-score using median/MAD. MAD==0 (a degenerate, e.g. all-zero, baseline)
    is handled explicitly rather than dividing by zero: equal-to-median is
    z=0, anything else is capped at +/-10 rather than +/-infinity."""
    values = [float(v) for v in baseline]
    v = float(value)
    percentile = sum(1 for x in values if x <= v) / len(values)
    median = statistics.median(values)
    mad = statistics.median([abs(x - median) for x in values])
    if mad == 0:
        z = 0.0 if v == median else (10.0 if v > median else -10.0)
    else:
        z = 0.6745 * (v - median) / mad
    return percentile, z
