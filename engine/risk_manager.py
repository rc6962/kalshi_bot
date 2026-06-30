# engine/risk_manager.py

from config import MAX_POSITION_CONTRACTS, MAX_EXPOSURE_PER_ASSET_USD, MIN_MULTIPLIER, MAX_CONTRACTS_PER_TRADE
from engine.kelly_sizer import KellyPositionSizer
from engine.macro_regime import MacroRegimeDetector

class RiskManager:
    def __init__(self, global_exposure_dict=None, global_reservations_dict=None):
        # Track performance for dynamic position sizing
        self.recent_pnl = []
        self.max_recent_trades = 50
        self.kelly_sizer = KellyPositionSizer(max_fraction=0.25, safety_factor=0.5)
        self._portfolio_balance_ref = None
        # Track current exposure per asset to enforce limits (shared dictionary)
        self.asset_exposure = global_exposure_dict if global_exposure_dict is not None else {}
        self.daily_loss_usd = 0.0
        # Pre-trade reservation system (shared across ALL EventLoops for cross-asset global cap)
        self._global_reservations = global_reservations_dict if global_reservations_dict is not None else {}
        # Macro regime detector for sizing adjustments
        self.macro_regime = MacroRegimeDetector()
        # Portfolio-level lock for reservation race condition prevention
        self._reservation_lock = False

    def set_balance_ref(self, balance_ref):
        """Wire in live portfolio balance for dynamic position sizing."""
        self._portfolio_balance_ref = balance_ref
        self.kelly_sizer.set_balance_ref(balance_ref)
    
    def set_asset_exposure(self, asset_name, value_usd):
        """Update the current exposure for an asset."""
        self.asset_exposure[asset_name] = value_usd
    
    def get_asset_exposure(self, asset_name):
        """Get the current exposure for an asset."""
        return self.asset_exposure.get(asset_name, 0.0)

    def get_real_open_contracts(self, asset_name, portfolio_positions_ref):
        """Fetch the true number of open contracts directly from the Kalshi live API state."""
        if not portfolio_positions_ref or "value" not in portfolio_positions_ref:
            return 0
            
        total_contracts = 0
        prefix = f"KX{asset_name.upper()}15M"
        for pos in portfolio_positions_ref["value"]:
            ticker = pos.get("ticker", "")
            if ticker.startswith(prefix):
                total_contracts += pos.get("count", 0)
                
        return total_contracts
    
    def can_add_position(self, asset_name, contract_price, num_contracts):
        """Check if adding a position would exceed the maximum per asset or global limit."""
        from config import MAX_EXPOSURE_PER_ASSET_USD, MAX_GLOBAL_EXPOSURE_USD
        current_exposure = self.get_asset_exposure(asset_name)
        new_exposure = current_exposure + (contract_price * num_contracts)
        
        # Check asset-level limit
        if new_exposure > MAX_EXPOSURE_PER_ASSET_USD:
            return False
            
        # Check global limit
        total_global_exposure = sum(self.asset_exposure.values())
        new_global_exposure = total_global_exposure - current_exposure + new_exposure
        return new_global_exposure <= MAX_GLOBAL_EXPOSURE_USD
    
    def calculate_max_contracts_for_asset(self, asset_name, contract_price):
        """Calculate the maximum number of contracts that can be bought without exceeding limits."""
        from config import MAX_EXPOSURE_PER_ASSET_USD, MAX_GLOBAL_EXPOSURE_USD
        current_exposure = self.get_asset_exposure(asset_name)
        
        # Limit based on asset-level exposure limit
        remaining_asset_exposure = MAX_EXPOSURE_PER_ASSET_USD - current_exposure
        
        # Limit based on global exposure limit
        total_global_exposure = sum(self.asset_exposure.values())
        remaining_global_exposure = MAX_GLOBAL_EXPOSURE_USD - total_global_exposure
        
        # We can only allocate up to the tighter of the two limits
        remaining_exposure = min(remaining_asset_exposure, remaining_global_exposure)
        
        if remaining_exposure <= 0 or contract_price <= 0:
            return 0
        max_contracts_based_on_exposure = int(remaining_exposure / contract_price)
        return min(MAX_CONTRACTS_PER_TRADE, max_contracts_based_on_exposure)
    
    def calculate_multiplier(self, spot_price, strike_price):
        """
        Calculate multiplier based on distance between spot and strike.
        
        Args:
            spot_price: Current market price
            strike_price: Option strike price
        
        Returns:
            Multiplier factor (higher when spot is far from strike)
        """
        # Handle None values
        if spot_price is None or strike_price is None:
            return 1.0
        
        # Ensure numeric types
        try:
            spot_float = float(spot_price)
            strike_float = float(strike_price)
        except (ValueError, TypeError):
            return 1.0
        
        if spot_float <= 0 or strike_float <= 0:
            return 1.0
        
        # Calculate percentage difference
        diff_pct = abs(spot_float - strike_float) / strike_float
        
        # Multiplier increases with distance from strike
        # Base multiplier of 1.0, increasing up to 3.0 for large moves
        multiplier = 1.0 + min(2.0, diff_pct * 10)
        
        return multiplier
        
    def calculate_expected_value(self, win_prob, price):
        """
        Calculates the expected value per contract.
        EV = (win_prob * (1.0 - price - taker_fee)) - ((1 - win_prob) * (price + taker_fee))
        If EV > 0, the trade is mathematically profitable over the long run.
        """
        if win_prob is None or price is None or price <= 0:
            return -1.0
            
        from config import TAKER_FEE_PER_CONTRACT
        
        profit_if_win = 1.0 - price - TAKER_FEE_PER_CONTRACT
        loss_if_lose = price + TAKER_FEE_PER_CONTRACT
        
        ev = (win_prob * profit_if_win) - ((1.0 - win_prob) * loss_if_lose)
        return ev
        
    def reserve_exposure(self, asset_name: str, exposure_usd: float) -> bool:
        """Pre-trade reservation: lock exposure before order placement to prevent race conditions.

        Uses _global_reservations (shared across ALL EventLoops) so cross-asset
        global caps are enforced even during parallel entry.
        """
        from config import MAX_EXPOSURE_PER_ASSET_USD, MAX_GLOBAL_EXPOSURE_USD

        current_asset = self.get_asset_exposure(asset_name)

        # Per-asset cap
        if current_asset + exposure_usd > MAX_EXPOSURE_PER_ASSET_USD:
            print(f"[RiskManager] RESERVE BLOCKED for {asset_name}: "
                  f"${current_asset:.2f} current + ${exposure_usd:.2f} exceeds ${MAX_EXPOSURE_PER_ASSET_USD} per-asset cap")
            return False

        # Global cap: confirmed exposure + ALL reservations across every asset
        confirmed_global = sum(self.asset_exposure.values())
        reserved_global = sum(self._global_reservations.values())
        if confirmed_global + reserved_global + exposure_usd > MAX_GLOBAL_EXPOSURE_USD:
            print(f"[RiskManager] RESERVE BLOCKED: "
                  f"${confirmed_global:.2f} confirmed + ${reserved_global:.2f} reserved + "
                  f"${exposure_usd:.2f} exceeds ${MAX_GLOBAL_EXPOSURE_USD} global cap")
            return False

        self._global_reservations[asset_name] = self._global_reservations.get(asset_name, 0.0) + exposure_usd
        print(f"[RiskManager] Reserved ${exposure_usd:.2f} for {asset_name} "
              f"(asset: ${current_asset + self._global_reservations[asset_name]:.2f}, "
              f"reserved global: ${sum(self._global_reservations.values()):.2f}, "
              f"confirmed global: ${confirmed_global:.2f})")
        return True

    def confirm_exposure(self, asset_name: str, exposure_usd: float):
        """Move reserved exposure to confirmed (post-fill)."""
        reserved = self._global_reservations.get(asset_name, 0.0)
        if reserved >= exposure_usd:
            self._global_reservations[asset_name] = reserved - exposure_usd
        self.set_asset_exposure(asset_name, self.get_asset_exposure(asset_name) + exposure_usd)

    def release_exposure(self, asset_name: str, exposure_usd: float | None = None):
        """Release reserved exposure (order not filled / cancelled)."""
        if exposure_usd is None:
            self._global_reservations.pop(asset_name, None)
        else:
            reserved = self._global_reservations.get(asset_name, 0.0)
            self._global_reservations[asset_name] = max(0.0, reserved - exposure_usd)

    def calculate_contracts(self, price, asset_name, multiplier=None, recent_pnl_pct=None, win_prob=None, regime=None, current_open_contracts=0, portfolio_positions_ref=None):
        """
        Calculate position size using Kelly Criterion for optimal growth.
        
        Args:
            price: Contract price in USD
            asset_name: Name of the asset (for exposure tracking)
            multiplier: Option multiplier (higher = more leverage)
            recent_pnl_pct: Recent performance adjustment
            win_prob: Probability of winning from ML/EV/rule-based estimator (None = use historical)
            regime: Current market regime (RANGE, TREND, HIGH_VOL, SHOCK)
            current_open_contracts: Locally tracked contracts
            portfolio_positions_ref: Live reference to Kalshi portfolio positions
        
        Returns:
            Number of contracts to trade
        """
        # Get historical win/loss ratios
        avg_win, avg_loss = self.kelly_sizer.get_avg_win_loss()
        win_rate = self.kelly_sizer.get_win_rate()
        
        from config import ENABLE_ML_SIZING
        
        # Use provided win_prob if available, otherwise fall back to historical win rate or 0.55
        if ENABLE_ML_SIZING and win_prob is not None:
            effective_win_prob = win_prob
            
            # If confidence is weak and regime is weak, hard block
            if effective_win_prob < 0.50 and regime in ["RANGE", "WEAK"]:
                print(f"[RiskManager] ML BLOCK: Weak confidence ({effective_win_prob:.2f}) in {regime} regime.")
                return 0
        elif win_prob is not None and win_prob > 0.5:
            effective_win_prob = win_prob
        else:
            effective_win_prob = max(0.55, win_rate) if win_rate > 0.5 else 0.55

        # Regime-based win_prob adjustment
        if not ENABLE_ML_SIZING:
            if regime == "HIGH_VOL":
                effective_win_prob *= 0.97
            elif regime == "RANGE":
                effective_win_prob = min(0.65, effective_win_prob * 1.02)

        # Macro regime sizing multiplier
        macro_mult = self.macro_regime.get_sizing_multiplier()
        if macro_mult < 1.0:
            print(f"[RiskManager] Macro regime sizing: {macro_mult:.2f}x (reducing size)")
        
        # Calculate optimal contracts using Kelly
        contracts = self.kelly_sizer.calculate_contracts(
            entry_price=price,
            win_prob=effective_win_prob,
            avg_win=avg_win,
            avg_loss=avg_loss,
            recent_pnl_pct=recent_pnl_pct
        )
        
        # Apply multiplier adjustment (higher multiplier = more leverage = smaller position)
        if multiplier is not None and multiplier > MIN_MULTIPLIER:
            multiplier_factor = min(1.0, MIN_MULTIPLIER / multiplier)
            contracts = max(1, int(contracts * multiplier_factor)) if contracts > 0 else 0

        # Macro regime sizing multiplier (floor at 1 — never truncate to 0)
        if macro_mult < 1.0 and contracts > 0:
            contracts = max(1, int(contracts * macro_mult))
        
        # Cap at maximum allowed per trade
        contracts = min(MAX_CONTRACTS_PER_TRADE, contracts)
        
        # Enforce asset-specific exposure limit
        max_contracts_for_asset = self.calculate_max_contracts_for_asset(asset_name, price)
        contracts = min(contracts, max_contracts_for_asset)
        
        # --- STAGE 4: EXPLICIT RISK CAPS ---
        from config import MAX_CONTRACTS_PER_TRADE_CAP, MAX_DOLLARS_PER_TRADE_CAP, MAX_OPEN_CONTRACTS_PER_MARKET_CAP, MAX_DAILY_LOSS_CAP
        
        if contracts > MAX_CONTRACTS_PER_TRADE_CAP:
            print(f"[RiskManager] CLAMP: {contracts} exceeds MAX_CONTRACTS_PER_TRADE_CAP ({MAX_CONTRACTS_PER_TRADE_CAP})")
            contracts = MAX_CONTRACTS_PER_TRADE_CAP
            
        max_dollars_contracts = int(MAX_DOLLARS_PER_TRADE_CAP / price) if price > 0 else 0
        if contracts > max_dollars_contracts:
            print(f"[RiskManager] CLAMP: {contracts} (${contracts*price:.2f}) exceeds MAX_DOLLARS_PER_TRADE_CAP (${MAX_DOLLARS_PER_TRADE_CAP})")
            contracts = max_dollars_contracts
            
        # Hard sync against live API holdings
        real_open = self.get_real_open_contracts(asset_name, portfolio_positions_ref)
        effective_open = max(current_open_contracts, real_open)
        
        if effective_open + contracts > MAX_OPEN_CONTRACTS_PER_MARKET_CAP:
            allowed = max(0, MAX_OPEN_CONTRACTS_PER_MARKET_CAP - effective_open)
            if contracts > allowed:
                print(f"[RiskManager] CLAMP: {contracts} + {effective_open} open (API: {real_open}) exceeds MAX_OPEN_CONTRACTS_PER_MARKET_CAP ({MAX_OPEN_CONTRACTS_PER_MARKET_CAP})")
                contracts = allowed
                
        if self.daily_loss_usd >= MAX_DAILY_LOSS_CAP:
            print(f"[RiskManager] CLAMP: Daily loss limit reached (${self.daily_loss_usd:.2f} >= ${MAX_DAILY_LOSS_CAP}). Halting trading.")
            contracts = 0

        # Max concurrent positions across all assets
        from config import MAX_CONCURRENT_POSITIONS
        if portfolio_positions_ref and "value" in portfolio_positions_ref:
            active_count = len(portfolio_positions_ref["value"])
            if active_count >= MAX_CONCURRENT_POSITIONS:
                print(f"[RiskManager] BLOCK: {active_count} positions already open (max {MAX_CONCURRENT_POSITIONS})")
                contracts = 0
        # ------------------------------------
        
        print(f"[RiskManager] Asset: {asset_name}, Price: ${price:.2f}, Max contracts for exposure: {max_contracts_for_asset}, Final: {contracts}, regime={regime}, win_prob={effective_win_prob:.3f}")
        print(f"[RiskManager] Current exposure for {asset_name}: ${self.get_asset_exposure(asset_name):.2f}, Proposed new exposure: ${(self.get_asset_exposure(asset_name) + price * contracts):.2f}")
        
        return contracts
    
    def update_performance(self, pnl_pct, profit_usd=0.0):
        """Track recent PnL for dynamic sizing and update Kelly capital"""
        self.recent_pnl.append(pnl_pct)
        if len(self.recent_pnl) > self.max_recent_trades:
            self.recent_pnl.pop(0)
            
        # Track daily loss hard cap
        if profit_usd < 0:
            self.daily_loss_usd += abs(profit_usd)
        
        # Update Kelly sizer with actual profit/loss
        if profit_usd != 0:
            self.kelly_sizer.update_capital(profit_usd)
    
    def get_recent_performance(self):
        """Get average recent PnL percentage"""
        if not self.recent_pnl:
            return 0.0
        return sum(self.recent_pnl) / len(self.recent_pnl)
    
    def should_reduce_risk(self):
        """Check if we should reduce risk due to drawdown"""
        if len(self.recent_pnl) < 5:
            return False
        recent_avg = sum(self.recent_pnl[-5:]) / 5
        return recent_avg < -0.05  # Reduce risk if last 5 trades avg -5%
    
    def should_increase_risk(self):
        """Check if we can increase risk due to profits"""
        if len(self.recent_pnl) < 10:
            return False
        recent_avg = sum(self.recent_pnl[-10:]) / 10
        return recent_avg > 0.05  # Increase risk if last 10 trades avg +5%