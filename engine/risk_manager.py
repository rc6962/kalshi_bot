# engine/risk_manager.py

from config import MAX_POSITION_CONTRACTS, MAX_EXPOSURE_PER_ASSET_USD, MIN_MULTIPLIER, MAX_CONTRACTS_PER_TRADE
from engine.kelly_sizer import KellyPositionSizer

class RiskManager:
    def __init__(self, global_exposure_dict=None):
        # Track performance for dynamic position sizing
        self.recent_pnl = []
        self.max_recent_trades = 50
        self.kelly_sizer = KellyPositionSizer(max_fraction=0.25, safety_factor=0.5)
        self._portfolio_balance_ref = None
        # Track current exposure per asset to enforce limits (shared dictionary)
        self.asset_exposure = global_exposure_dict if global_exposure_dict is not None else {}

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
        
    def calculate_contracts(self, price, asset_name, multiplier=None, recent_pnl_pct=None, win_prob=0.55):
        """
        Calculate position size using Kelly Criterion for optimal growth.
        
        Args:
            price: Contract price in USD
            asset_name: Name of the asset (for exposure tracking)
            multiplier: Option multiplier (higher = more leverage)
            recent_pnl_pct: Recent performance adjustment
            win_prob: Probability of winning from ML model
        
        Returns:
            Number of contracts to trade
        """
        # Get historical win/loss ratios
        avg_win, avg_loss = self.kelly_sizer.get_avg_win_loss()
        win_rate = self.kelly_sizer.get_win_rate()
        
        # Use provided win_prob if available, otherwise use historical win rate
        effective_win_prob = win_prob if win_prob > 0.5 else max(0.5, win_rate)
        
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
            contracts = max(1, int(contracts * multiplier_factor))
        
        # Cap at maximum allowed per trade
        contracts = min(MAX_CONTRACTS_PER_TRADE, contracts)
        
        # Enforce asset-specific exposure limit
        max_contracts_for_asset = self.calculate_max_contracts_for_asset(asset_name, price)
        contracts = min(contracts, max_contracts_for_asset)
        
        print(f"[RiskManager] Asset: {asset_name}, Price: ${price:.2f}, Max contracts for exposure: {max_contracts_for_asset}, Final: {contracts}")
        print(f"[RiskManager] Current exposure for {asset_name}: ${self.get_asset_exposure(asset_name):.2f}, Proposed new exposure: ${(self.get_asset_exposure(asset_name) + price * contracts):.2f}")
        
        return contracts
    
    def update_performance(self, pnl_pct, profit_usd=0.0):
        """Track recent PnL for dynamic sizing and update Kelly capital"""
        self.recent_pnl.append(pnl_pct)
        if len(self.recent_pnl) > self.max_recent_trades:
            self.recent_pnl.pop(0)
        
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