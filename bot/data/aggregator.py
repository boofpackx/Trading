"""Aggregate 1m candles into the 5m structure timeframe."""

from __future__ import annotations

from datetime import datetime, timedelta

from ..models import Candle


def aggregate(candles_1m: list[Candle], tf_seconds: int = 300) -> list[Candle]:
    """Bucket 1m candles by wall-clock boundary (e.g. 9:30, 9:35...). Only
    complete buckets should be treated as closed; the last bucket may be
    partial and is included — callers that need closed-only can drop it."""
    out: list[Candle] = []
    bucket: list[Candle] = []
    bucket_start: datetime | None = None

    def flush():
        if bucket:
            out.append(
                Candle(
                    ts=bucket_start,
                    open=bucket[0].open,
                    high=max(c.high for c in bucket),
                    low=min(c.low for c in bucket),
                    close=bucket[-1].close,
                )
            )

    for c in candles_1m:
        epoch = int(c.ts.timestamp())
        start = c.ts - timedelta(seconds=epoch % tf_seconds)
        if bucket_start is None or start != bucket_start:
            flush()
            bucket, bucket_start = [], start
        bucket.append(c)
    flush()
    return out


def closed_only(candles_1m: list[Candle], tf_seconds: int = 300) -> list[Candle]:
    """Aggregated candles whose bucket is fully formed (a following 1m candle
    exists past the bucket boundary)."""
    agg = aggregate(candles_1m, tf_seconds)
    if not agg or not candles_1m:
        return agg
    last_1m = candles_1m[-1]
    last_bucket_end = agg[-1].ts + timedelta(seconds=tf_seconds)
    if last_1m.ts + timedelta(seconds=60) < last_bucket_end:
        return agg[:-1]
    return agg
