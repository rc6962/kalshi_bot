# models/polymarket_model.py
"""
Polymarket Model — Layer 2 of the info-arb pipeline.

Fetches live Polymarket market prices (no API key required) and attempts to
map them to matching Kalshi markets by fuzzy title/keyword matching.

When both platforms are pricing the same event, any divergence in implied
probability is a potential arbitrage opportunity.

Polymarket API docs: https://docs.polymarket.com/
CLOB prices:        https://clob.polymarket.com/markets
Gamma (metadata):   https://gamma-api.polymarket.com/markets
"""

import asyncio
import time
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp

# Public Polymarket endpoints — no API key needed
POLYMARKET_GAMMA_URL = "https://gamma-api.polymarket.com/markets"
POLYMARKET_CLOB_URL = "https://clob.polymarket.com/markets"

# How long to cache the full Polymarket market list before re-fetching (seconds)
POLY_CACHE_TTL = 120

# Minimum Polymarket daily volume to consider a market liquid
POLY_MIN_VOLUME = 5000  # USD

# Minimum Polymarket open interest
POLY_MIN_OPEN_INTEREST = 1000  # USD


@dataclass
class PolymarketOpportunity:
    """A matched Kalshi↔Polymarket pair with computed divergence."""
    kalshi_ticker: str
    kalshi_title: str
    kalshi_yes_bid: float      # Kalshi best bid (what you'll pay to buy YES)
    kalshi_yes_ask: float      # Kalshi best ask
    kalshi_yes_mid: float      # Kalshi mid-price = implied market probability

    poly_slug: str
    poly_title: str
    poly_yes_price: float      # Polymarket YES price (0–1) = fair probability estimate
    poly_volume: float         # Polymarket 24h volume USD
    poly_open_interest: float

    edge_buy_kalshi: float     # Edge if we BUY YES on Kalshi (poly_yes > kalshi_yes_ask)
    edge_sell_kalshi: float    # Edge if we BUY NO on Kalshi (kalshi_yes_bid > poly_yes)
    best_edge: float           # max(|edge_buy|, |edge_sell|)
    best_side: str             # "YES" | "NO"
    fair_prob: float           # Our model's fair probability = poly_yes_price
    kalshi_time_remaining: int


def _normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace for fuzzy matching."""
    t = title.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _keyword_overlap(a: str, b: str) -> float:
    """Returns fraction of words in `a` that appear in `b`. Range 0–1."""
    words_a = set(_normalize_title(a).split())
    words_b = set(_normalize_title(b).split())
    # Ignore very short stop words
    stop = {"the", "a", "an", "of", "in", "on", "at", "to", "is", "will", "by", "for", "and", "or", "vs"}
    words_a -= stop
    words_b -= stop
    if not words_a:
        return 0.0
    overlap = len(words_a & words_b) / len(words_a)
    return overlap


MATCH_THRESHOLD = 0.50  # At least 50% keyword overlap to consider a match


class PolymarketModel:
    """
    Fetches Polymarket prices and tries to match them to Kalshi candidates.
    Returns PolymarketOpportunity objects for any matched pairs with sufficient divergence.
    """

    def __init__(self):
        self._poly_markets: list[dict] = []
        self._last_fetch: float = 0
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20, connect=5)
            )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_polymarket_markets(self) -> list[dict]:
        """
        Fetch active Polymarket markets from the Gamma API.
        Returns raw market dicts with title, price, volume.
        """
        now = time.time()
        if (now - self._last_fetch) < POLY_CACHE_TTL and self._poly_markets:
            return self._poly_markets

        session = await self._get_session()
        markets = []

        try:
            params = {
                "active": "true",
                "closed": "false",
                "limit": 500,
                "offset": 0,
            }
            async with session.get(POLYMARKET_GAMMA_URL, params=params) as resp:
                if resp.status != 200:
                    print(f"[PolymarketModel] Gamma API returned {resp.status}")
                    return self._poly_markets  # Return cached if available

                data = await resp.json(content_type=None)
                if isinstance(data, list):
                    markets = data
                elif isinstance(data, dict):
                    markets = data.get("markets", data.get("data", []))

            print(f"[PolymarketModel] Fetched {len(markets)} Polymarket markets")
            self._poly_markets = markets
            self._last_fetch = now

        except Exception as e:
            print(f"[PolymarketModel] Error fetching markets: {e}")
            # Return cached data if available
            return self._poly_markets

        return markets

    def _extract_poly_price(self, market: dict) -> Optional[float]:
        """
        Extract YES price (0–1) from a Polymarket market dict.
        Polymarket uses outcomePrices or bestAsk/bestBid fields.
        """
        # Try outcomePrices (array of ["0.60", "0.40"] for YES/NO)
        prices = market.get("outcomePrices")
        if prices and isinstance(prices, list) and len(prices) >= 1:
            try:
                return float(prices[0])
            except (ValueError, TypeError):
                pass

        # Try bestBid/bestAsk — use mid as price
        best_bid = market.get("bestBid") or market.get("best_bid")
        best_ask = market.get("bestAsk") or market.get("best_ask")
        if best_bid is not None and best_ask is not None:
            try:
                return (float(best_bid) + float(best_ask)) / 2
            except (ValueError, TypeError):
                pass

        # Last trade price
        last = market.get("lastTradePrice") or market.get("last_trade_price")
        if last is not None:
            try:
                return float(last)
            except (ValueError, TypeError):
                pass

        return None

    async def find_opportunities(
        self,
        kalshi_candidates: list,  # list[MarketCandidate]
        min_edge: float = 0.08,   # 8% minimum net edge after fees
        kalshi_fee: float = 0.02, # Kalshi charges ~2% maker fee
    ) -> list[PolymarketOpportunity]:
        """
        Match Kalshi candidates against Polymarket markets.
        Returns opportunities where the price divergence exceeds min_edge.
        """
        poly_markets = await self._fetch_polymarket_markets()

        if not poly_markets:
            print("[PolymarketModel] No Polymarket data available")
            return []

        # Pre-filter Polymarket: active, liquid binary markets
        active_poly = []
        for pm in poly_markets:
            # Skip if not active
            if not pm.get("active", True):
                continue
            if pm.get("closed", False) or pm.get("resolved", False):
                continue
            # Only binary (YES/NO) markets
            outcomes = pm.get("outcomes") or []
            if isinstance(outcomes, str):
                try:
                    import json
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []
            if len(outcomes) != 2:
                continue
            # Liquidity filter
            volume = float(pm.get("volume24hr") or pm.get("volume") or 0)
            if volume < POLY_MIN_VOLUME:
                continue
            price = self._extract_poly_price(pm)
            if price is None or not (0.02 <= price <= 0.98):
                continue
            active_poly.append((pm, price))

        print(f"[PolymarketModel] {len(active_poly)} active liquid Polymarket binary markets")

        opportunities = []

        for kalshi_cand in kalshi_candidates:
            best_match_pm = None
            best_match_price = None
            best_overlap = 0.0

            kalshi_title = kalshi_cand.title

            for pm, poly_price in active_poly:
                poly_title = pm.get("question") or pm.get("title") or pm.get("description") or ""
                overlap = _keyword_overlap(kalshi_title, poly_title)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match_pm = pm
                    best_match_price = poly_price

            if best_overlap < MATCH_THRESHOLD or best_match_pm is None:
                continue

            # We have a match. Compute edges.
            poly_yes = best_match_price

            # Edge if buying YES on Kalshi (Polymarket thinks it's worth more)
            # Net cost to buy YES on Kalshi = kalshi_yes_ask + kalshi_fee
            edge_buy = poly_yes - (kalshi_cand.yes_ask + kalshi_fee)

            # Edge if buying NO on Kalshi (Polymarket thinks YES is worth less)
            # Net cost to buy NO on Kalshi = (1 - kalshi_yes_bid) + kalshi_fee
            edge_sell = (1.0 - poly_yes) - ((1.0 - kalshi_cand.yes_bid) + kalshi_fee)

            best_edge = max(edge_buy, edge_sell)
            best_side = "YES" if edge_buy >= edge_sell else "NO"

            if best_edge < min_edge:
                continue

            poly_volume = float(
                best_match_pm.get("volume24hr") or best_match_pm.get("volume") or 0
            )
            poly_oi = float(best_match_pm.get("liquidityNum") or best_match_pm.get("liquidity") or 0)
            poly_slug = best_match_pm.get("slug") or best_match_pm.get("conditionId", "unknown")

            opp = PolymarketOpportunity(
                kalshi_ticker=kalshi_cand.ticker,
                kalshi_title=kalshi_title,
                kalshi_yes_bid=kalshi_cand.yes_bid,
                kalshi_yes_ask=kalshi_cand.yes_ask,
                kalshi_yes_mid=kalshi_cand.yes_mid,
                poly_slug=poly_slug,
                poly_title=best_match_pm.get("question") or best_match_pm.get("title", ""),
                poly_yes_price=poly_yes,
                poly_volume=poly_volume,
                poly_open_interest=poly_oi,
                edge_buy_kalshi=edge_buy,
                edge_sell_kalshi=edge_sell,
                best_edge=best_edge,
                best_side=best_side,
                fair_prob=poly_yes,
                kalshi_time_remaining=kalshi_cand.time_remaining_sec,
            )
            opportunities.append(opp)

            print(
                f"[PolymarketModel] MATCH ({best_overlap:.0%} overlap): "
                f"Kalshi '{kalshi_title}' ↔ Poly '{opp.poly_title}'\n"
                f"  Kalshi={kalshi_cand.yes_mid:.1%}  Poly={poly_yes:.1%}  "
                f"Side={best_side}  Edge={best_edge:.1%}"
            )

        opportunities.sort(key=lambda x: x.best_edge, reverse=True)
        print(f"[PolymarketModel] Found {len(opportunities)} arb opportunities with edge >= {min_edge:.0%}")
        return opportunities
