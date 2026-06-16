# scanner/market_scanner.py
"""
Market Scanner — Layer 1 of the info-arb pipeline.

Polls Kalshi REST API for open markets across multiple series/categories,
filters for tradeable candidates, and returns structured MarketCandidate objects
for the model engine to evaluate.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

# How often to refresh the full market list (seconds)
SCAN_INTERVAL_SECONDS = 60

# Only consider markets closing within this window (seconds)
MAX_EXPIRY_SECONDS = 7 * 24 * 3600   # 7 days
MIN_EXPIRY_SECONDS = 30 * 60          # 30 minutes (don't enter too close to expiry)

# Minimum daily dollar volume to consider a market liquid enough
MIN_VOLUME_USD = 200

# Maximum allowed bid-ask spread as a fraction of mid-price
MAX_SPREAD_FRACTION = 0.25

# Series tickers to scan — covers the main verticals for arb opportunities
SERIES_TO_SCAN = [
    # Crypto (existing)
    "KXBTC", "KXETH", "KXSOL",
    # Economics
    "KXCPI", "KXPCE", "KXFED", "KXUNRATE",
    # Politics / events
    "KXPOTUS",
    # Sports (broad sweep)
    "KXNBA", "KXNFL", "KXMLB",
    # Weather
    "KXTEMP",
]


@dataclass
class MarketCandidate:
    """A Kalshi market that passed initial filters and is ready for model evaluation."""
    ticker: str
    title: str
    series: str
    yes_bid: float          # Kalshi implied YES probability (bid side)
    yes_ask: float          # Kalshi implied YES probability (ask side)
    yes_mid: float          # Mid-price = implied market probability
    expiry_ts: int          # Unix timestamp of market close
    time_remaining_sec: int
    volume_24h: float       # Dollar volume last 24h
    open_interest: float    # Open interest in dollars
    category: str           # "crypto" | "econ" | "sports" | "weather" | "politics"
    raw_market: dict = field(default_factory=dict)  # Full market dict for debugging


def _classify_series(series: str) -> str:
    if series.startswith("KXBTC") or series.startswith("KXETH") or series.startswith("KXSOL"):
        return "crypto"
    if series.startswith("KXCPI") or series.startswith("KXPCE") or series.startswith("KXFED") or series.startswith("KXUNRATE"):
        return "econ"
    if series.startswith("KXNBA") or series.startswith("KXNFL") or series.startswith("KXMLB"):
        return "sports"
    if series.startswith("KXTEMP"):
        return "weather"
    return "politics"


def _parse_price(raw) -> Optional[float]:
    """Normalize Kalshi price: cents (int) → dollars (float), or pass-through float."""
    if raw is None:
        return None
    val = float(raw)
    # Kalshi returns prices in cents when > 1 but sometimes in dollars
    if val > 1.0:
        val = val / 100.0
    return round(val, 4)


def _extract_market_prices(market: dict):
    """Extract yes_bid, yes_ask from market dict. Returns (bid, ask) or (None, None)."""
    yes_bid = _parse_price(
        market.get("yes_bid") or market.get("yes_bid_price") or market.get("best_yes_bid")
    )
    yes_ask = _parse_price(
        market.get("yes_ask") or market.get("yes_ask_price") or market.get("best_yes_ask")
    )
    # Some endpoints return last_price as the only price
    last = _parse_price(market.get("last_price") or market.get("yes_price"))
    if yes_bid is None and last is not None:
        yes_bid = last
    if yes_ask is None and last is not None:
        yes_ask = last
    return yes_bid, yes_ask


class MarketScanner:
    """
    Scans Kalshi for tradeable markets across multiple series.
    Call `scan()` periodically to get fresh candidates.
    """

    def __init__(self, kalshi_client):
        self.kalshi = kalshi_client
        self._last_scan: float = 0
        self._cached: list[MarketCandidate] = []

    async def scan(self, force: bool = False) -> list[MarketCandidate]:
        """
        Returns list of MarketCandidates. Uses cache unless SCAN_INTERVAL has elapsed
        or force=True.
        """
        now = time.time()
        if not force and (now - self._last_scan) < SCAN_INTERVAL_SECONDS:
            return self._cached

        candidates = []
        for series in SERIES_TO_SCAN:
            try:
                series_candidates = await self._scan_series(series)
                candidates.extend(series_candidates)
            except Exception as e:
                print(f"[MarketScanner] Error scanning {series}: {e}")

        self._cached = candidates
        self._last_scan = now
        print(f"[MarketScanner] Scan complete: {len(candidates)} tradeable candidates across {len(SERIES_TO_SCAN)} series")
        return candidates

    async def _scan_series(self, series: str) -> list[MarketCandidate]:
        """Fetch all open markets for a given series ticker from Kalshi."""
        now = int(time.time())
        min_close = now + MIN_EXPIRY_SECONDS
        max_close = now + MAX_EXPIRY_SECONDS

        path = (
            f"/events?series_ticker={series}"
            f"&with_nested_markets=true"
            f"&min_close_ts={min_close}"
            f"&max_close_ts={max_close}"
            f"&limit=100"
        )

        try:
            data = await asyncio.wait_for(
                self.kalshi.authenticated_request("GET", path),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            print(f"[MarketScanner] Timeout fetching {series}")
            return []

        events = data.get("events", [])
        candidates = []
        now_ts = int(time.time())

        for event in events:
            markets = event.get("markets", [])
            for market in markets:
                candidate = self._evaluate_market(market, series, now_ts)
                if candidate is not None:
                    candidates.append(candidate)

        return candidates

    def _evaluate_market(self, market: dict, series: str, now_ts: int) -> Optional[MarketCandidate]:
        """Apply filters and build a MarketCandidate. Returns None if filtered out."""
        ticker = market.get("ticker") or market.get("market_ticker")
        if not ticker:
            return None

        # Parse expiry
        close_ts = market.get("close_time") or market.get("expiry_time") or market.get("strike_date")
        if isinstance(close_ts, str):
            import datetime
            try:
                dt = datetime.datetime.fromisoformat(close_ts.replace("Z", "+00:00"))
                close_ts = int(dt.timestamp())
            except Exception:
                return None
        if not close_ts:
            return None

        time_remaining = int(close_ts) - now_ts
        if time_remaining < MIN_EXPIRY_SECONDS or time_remaining > MAX_EXPIRY_SECONDS:
            return None

        # Parse prices
        yes_bid, yes_ask = _extract_market_prices(market)
        if yes_bid is None or yes_ask is None:
            return None
        if not (0.01 <= yes_bid <= 0.99 and 0.01 <= yes_ask <= 0.99):
            return None
        if yes_ask < yes_bid:
            return None

        # Spread filter
        mid = (yes_bid + yes_ask) / 2
        spread_frac = (yes_ask - yes_bid) / mid if mid > 0 else 1.0
        if spread_frac > MAX_SPREAD_FRACTION:
            return None

        # Volume filter
        volume = float(market.get("volume") or market.get("volume_24h") or 0)
        # Kalshi volume may be in cents
        if volume > 10000:
            volume = volume / 100.0
        if volume < MIN_VOLUME_USD:
            return None

        open_interest = float(market.get("open_interest") or 0)
        if open_interest > 100000:
            open_interest = open_interest / 100.0

        title = market.get("title") or market.get("subtitle") or ticker
        category = _classify_series(series)

        return MarketCandidate(
            ticker=ticker,
            title=title,
            series=series,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            yes_mid=round(mid, 4),
            expiry_ts=int(close_ts),
            time_remaining_sec=time_remaining,
            volume_24h=volume,
            open_interest=open_interest,
            category=category,
            raw_market=market,
        )
