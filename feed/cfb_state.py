import time
from collections import deque

_cfb_data: dict[str, dict] = {}
# Rolling history for momentum computation: index_id -> deque of (timestamp, value)
_cfb_history: dict[str, deque] = {}


def update(index_id: str, value: float, avg_60s: float | None = None):
    now = time.time()
    _cfb_data[index_id] = {
        "value": value,
        "avg_60s": avg_60s,
        "updated_at": now,
    }
    # Maintain rolling history for momentum calculations
    if index_id not in _cfb_history:
        _cfb_history[index_id] = deque(maxlen=600)  # 10 min at 1Hz
    _cfb_history[index_id].append((now, value))


def get(index_id: str) -> dict | None:
    return _cfb_data.get(index_id)


def get_value(index_id: str) -> float | None:
    d = _cfb_data.get(index_id)
    return d["value"] if d else None


def get_avg_60s(index_id: str) -> float | None:
    d = _cfb_data.get(index_id)
    return d["avg_60s"] if d else None


def get_all() -> dict[str, dict]:
    return dict(_cfb_data)


def get_history(index_id: str) -> list[tuple[float, float]]:
    """Return the rolling RTI history as a list of (timestamp, value) tuples."""
    return list(_cfb_history.get(index_id, deque()))


def compute_rti_momentum_bps(
    index_id: str, lookback_seconds: float = 180.0
) -> float | None:
    """
    Compute CFB RTI momentum as bps/sec over the rolling window.
    Returns None if insufficient history.

    Momentum is computed as: (current_value - start_value) / start_value * 10000 / elapsed
    This gives the rate of price change in basis points per second.
    """
    history = _cfb_history.get(index_id)
    if not history or len(history) < 5:
        return None

    now = time.time()
    cutoff = now - lookback_seconds
    recent = [(t, v) for t, v in history if t >= cutoff]

    if len(recent) < 5:
        return None

    start_t, start_v = recent[0]
    end_t, end_v = recent[-1]

    if start_v <= 0:
        return None

    elapsed = end_t - start_t
    if elapsed < 1.0:
        return None

    momentum_bps = ((end_v - start_v) / start_v * 10000) / elapsed
    return momentum_bps


def get_rti_value_vs_strike(index_id: str, strike: float) -> float | None:
    """
    Return the ratio of current RTI value to the strike.
    > 1.0 means RTI is above strike (bullish for YES)
    < 1.0 means RTI is below strike (bearish for YES)
    Returns None if no data or strike is invalid.
    """
    if strike <= 0:
        return None
    value = get_value(index_id)
    if value is None:
        return None
    return value / strike
