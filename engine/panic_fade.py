import time
from collections import deque

class PanicFadeDetector:
    def __init__(self, panic_threshold=0.04, lookback_seconds=60):
        self.panic_threshold = panic_threshold
        self.lookback_seconds = lookback_seconds
        self._prices: deque[tuple[float, float]] = deque(maxlen=120)

    def update(self, price: float):
        now = time.time()
        self._prices.append((now, price))

    def get_velocity(self) -> float | None:
        if len(self._prices) < 5:
            return None
        cutoff = time.time() - self.lookback_seconds
        recent = [(t, p) for t, p in self._prices if t >= cutoff]
        if len(recent) < 5:
            return None
        start_price = recent[0][1]
        end_price = recent[-1][1]
        if start_price <= 0:
            return None
        return (end_price - start_price) / start_price

    def is_panic(self) -> tuple[bool, str | None]:
        velocity = self.get_velocity()
        if velocity is None:
            return False, None
        if velocity > self.panic_threshold:
            return True, "NO"
        if velocity < -self.panic_threshold:
            return True, "YES"
        return False, None

    def get_fade_signal(self) -> str | None:
        in_panic, fade_side = self.is_panic()
        if in_panic and fade_side:
            return f"ENTER_{fade_side}"
        return None
