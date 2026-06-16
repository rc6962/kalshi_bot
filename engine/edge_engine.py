# engine/edge_engine.py
"""
Edge Engine — Layer 3 of the info-arb pipeline.

Takes a raw arbitrage opportunity (from any model) and:
1. Verifies the net edge after fees and slippage
2. Applies Kelly criterion sizing (1/4 Kelly for safety)
3. Returns a trade signal if the math passes

The key insight: we don't need to predict the future.
We just need our model's probability to be more accurate than
the market's implied probability by enough to cover fees.
"""

from dataclasses import dataclass
from typing import Optional


# ─── Fee / Cost Parameters ──────────────────────────────────────────────────

# Kalshi charges a fee based on contract value and market.
# Conservative estimate: 2% per side (maker + potential taker)
KALSHI_FEE_PER_SIDE = 0.02

# Slippage estimate: we may not get mid-price, especially on illiquid markets
SLIPPAGE_ESTIMATE = 0.01

# Total round-trip cost: paid on entry; exit is free if settled at resolution
TOTAL_COST_ESTIMATE = KALSHI_FEE_PER_SIDE + SLIPPAGE_ESTIMATE  # 3%

# Minimum net edge AFTER fees to execute a trade
MIN_NET_EDGE = 0.08  # 8%

# ─── Position Sizing ─────────────────────────────────────────────────────────

# Maximum USD to put into a single trade
MAX_POSITION_USD = 10.00

# Minimum USD position (don't trade for less than this)
MIN_POSITION_USD = 0.50

# Kelly fraction — we use 1/4 Kelly to reduce variance
KELLY_FRACTION = 0.25


@dataclass
class TradeSignal:
    """A fully validated, sized trade ready for execution."""
    ticker: str
    side: str               # "yes" | "no"
    fair_prob: float        # Our model's probability of YES
    kalshi_prob: float      # Kalshi's implied probability (mid-price)
    raw_edge: float         # Edge before fees
    net_edge: float         # Edge after fees and slippage
    position_usd: float     # Dollar amount to risk
    contracts: int          # Number of contracts to buy
    entry_price: float      # Expected entry price (what we'll pay per contract)
    strategy: str           # "polymarket_arb" | "economic" | "sports"
    metadata: dict          # For logging — stores the full opportunity object fields


class EdgeEngine:
    """
    Validates opportunities and produces trade signals.
    Completely stateless — call evaluate() on each opportunity.
    """

    def evaluate(
        self,
        ticker: str,
        side: str,              # "yes" or "no"
        fair_prob: float,       # Model's fair probability of YES
        kalshi_yes_bid: float,
        kalshi_yes_ask: float,
        strategy: str = "polymarket_arb",
        metadata: dict = None,
    ) -> Optional[TradeSignal]:
        """
        Returns a TradeSignal if the opportunity passes all filters, else None.

        Parameters
        ----------
        ticker       : Kalshi market ticker
        side         : "yes" to buy YES contracts, "no" to buy NO contracts
        fair_prob    : Model's estimate of P(YES at settlement) — the truth
        kalshi_yes_bid, kalshi_yes_ask : Current Kalshi prices
        strategy     : Which model produced this signal (for logging)
        metadata     : Dict of extra fields to log
        """
        if metadata is None:
            metadata = {}

        kalshi_mid = (kalshi_yes_bid + kalshi_yes_ask) / 2

        if side == "yes":
            # We're buying YES — we pay the ask
            entry_price = kalshi_yes_ask
            # Our edge is: what we think it's worth minus what we pay, minus fees
            raw_edge = fair_prob - entry_price
        else:
            # We're buying NO — we pay (1 - yes_bid)
            entry_price = 1.0 - kalshi_yes_bid
            # Our edge: what we think NO is worth minus what we pay
            raw_edge = (1.0 - fair_prob) - entry_price

        net_edge = raw_edge - TOTAL_COST_ESTIMATE

        if net_edge < MIN_NET_EDGE:
            print(
                f"[EdgeEngine] SKIP {ticker} {side.upper()}: "
                f"raw_edge={raw_edge:.1%} net_edge={net_edge:.1%} < "
                f"min={MIN_NET_EDGE:.1%}"
            )
            return None

        # Sanity check: entry price must be valid
        if entry_price <= 0 or entry_price >= 1.0:
            print(f"[EdgeEngine] SKIP {ticker}: invalid entry price {entry_price}")
            return None

        # Kelly criterion sizing
        # Kelly formula for a binary bet:
        #   f* = (p * b - q) / b
        # where p = win prob, q = 1 - p, b = net odds (payout per dollar risked)
        # For a Kalshi YES contract: win = (1 - entry_price), lose = entry_price
        win_prob = fair_prob if side == "yes" else (1.0 - fair_prob)
        lose_prob = 1.0 - win_prob
        net_odds = (1.0 - entry_price) / entry_price  # payout ratio

        if net_odds <= 0:
            return None

        kelly_full = (win_prob * net_odds - lose_prob) / net_odds
        kelly_fraction = kelly_full * KELLY_FRACTION
        kelly_fraction = max(0.0, min(kelly_fraction, 1.0))  # clamp to [0, 1]

        position_usd = MAX_POSITION_USD * kelly_fraction
        position_usd = max(MIN_POSITION_USD, min(MAX_POSITION_USD, position_usd))

        # Number of contracts = dollars / cost per contract
        contracts = max(1, int(position_usd / entry_price))

        # Re-cap position at max
        actual_usd = contracts * entry_price
        if actual_usd > MAX_POSITION_USD:
            contracts = max(1, int(MAX_POSITION_USD / entry_price))
            actual_usd = contracts * entry_price

        print(
            f"[EdgeEngine] SIGNAL {ticker} {side.upper()}: "
            f"fair={fair_prob:.1%} kalshi={kalshi_mid:.1%} "
            f"net_edge={net_edge:.1%} kelly={kelly_fraction:.1%} "
            f"contracts={contracts} @ ${entry_price:.3f} = ${actual_usd:.2f}"
        )

        return TradeSignal(
            ticker=ticker,
            side=side,
            fair_prob=fair_prob,
            kalshi_prob=kalshi_mid,
            raw_edge=raw_edge,
            net_edge=net_edge,
            position_usd=actual_usd,
            contracts=contracts,
            entry_price=entry_price,
            strategy=strategy,
            metadata=metadata,
        )

    def evaluate_opportunity(self, opp) -> Optional[TradeSignal]:
        """
        Convenience wrapper for PolymarketOpportunity objects.
        Automatically determines which side to trade.
        """
        # Determine the better side
        if opp.edge_buy_kalshi >= opp.edge_sell_kalshi:
            side = "yes"
        else:
            side = "no"

        metadata = {
            "poly_slug": opp.poly_slug,
            "poly_title": opp.poly_title,
            "poly_yes_price": opp.poly_yes_price,
            "poly_volume": opp.poly_volume,
            "kalshi_title": opp.kalshi_title,
            "time_remaining_sec": opp.kalshi_time_remaining,
            "edge_buy": opp.edge_buy_kalshi,
            "edge_sell": opp.edge_sell_kalshi,
        }

        return self.evaluate(
            ticker=opp.kalshi_ticker,
            side=side,
            fair_prob=opp.fair_prob,
            kalshi_yes_bid=opp.kalshi_yes_bid,
            kalshi_yes_ask=opp.kalshi_yes_ask,
            strategy="polymarket_arb",
            metadata=metadata,
        )
