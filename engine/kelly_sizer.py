"""
Kelly Criterion position sizer for optimal capital allocation.
Calculates the optimal fraction of capital to risk on each trade
based on win probability, win/loss ratios, and live portfolio balance.
"""

from config import (
    MAX_POSITION_CONTRACTS, MAX_EXPOSURE_PER_ASSET_USD, MIN_POSITION_USD,
    PORTFOLIO_RISK_FRACTION
)


class KellyPositionSizer:
    def __init__(self, max_fraction=0.25, safety_factor=0.5, initial_capital=10.0):
        """
        Args:
            max_fraction: Maximum fraction of capital to risk per trade (Kelly = 0.25 = 25%)
            safety_factor: Multiply Kelly fraction by this (0.5 = half-Kelly)
            initial_capital: Starting capital in USD (used as fallback if no live balance)
        """
        self.max_fraction = max_fraction
        self.safety_factor = safety_factor
        self.capital = initial_capital
        self._portfolio_balance_ref = None  # Will be set externally
        self.trade_history = []
        self.max_trades = 100

    def set_balance_ref(self, balance_ref):
        """Wire in a mutable reference to the live portfolio balance dict."""
        self._portfolio_balance_ref = balance_ref

    def get_live_balance(self):
        """Get the latest portfolio balance from the shared reference."""
        if self._portfolio_balance_ref is not None:
            val = self._portfolio_balance_ref.get("value")
            if val is not None and val > 0:
                return val
        return self.capital  # fallback to internal capital tracker

    def update_capital(self, pnl_usd):
        """Update internal capital with realized PnL (fallback tracking)."""
        self.capital += pnl_usd
        self.trade_history.append(pnl_usd)
        if len(self.trade_history) > self.max_trades:
            self.trade_history.pop(0)

    def get_win_rate(self):
        """Calculate historical win rate from realized trades."""
        if not self.trade_history:
            return 0.5  # Default: no history
        wins = sum(1 for pnl in self.trade_history if pnl > 0)
        return wins / len(self.trade_history) if self.trade_history else 0.5

    def get_avg_win_loss(self):
        """Return (avg_win_amount, avg_loss_amount) from history."""
        if not self.trade_history:
            return (0.10, 0.10)  # Default assumptions

        wins = [pnl for pnl in self.trade_history if pnl > 0]
        losses = [abs(pnl) for pnl in self.trade_history if pnl < 0]

        avg_win = sum(wins) / len(wins) if wins else 0.10
        avg_loss = sum(losses) / len(losses) if losses else 0.10
        return (avg_win, avg_loss)

    def calculate_contracts(self, entry_price, win_prob=None, avg_win=None, avg_loss=None, recent_pnl_pct=None):
        """
        Calculate number of contracts using 2% portfolio risk with optional Kelly optimization.
        Uses live portfolio balance when available, falls back to internal tracking.

        Args:
            entry_price: Cost per contract in USD
            win_prob: Estimated probability of winning (0.0 to 1.0)
            avg_win: Average win amount as fraction
            avg_loss: Average loss amount as fraction
            recent_pnl_pct: Recent performance for streak adjustment

        Returns:
            int: Number of contracts to buy
        """
        from config import PORTFOLIO_RISK_FRACTION, MIN_POSITION_USD, MAX_EXPOSURE_PER_ASSET_USD, MAX_POSITION_CONTRACTS, MIN_ACCOUNT_BALANCE
        
        # Get the effective portfolio balance (live API > internal tracker)
        effective_capital = self.get_live_balance()
        
        # Stop trading if account balance is below the minimum required limit
        if effective_capital is None or effective_capital < MIN_ACCOUNT_BALANCE:
            cap_val = effective_capital if effective_capital is not None else 0.0
            print(f"[KellySizer] Account balance: capital=${cap_val:.2f} is below minimum trading limit of ${MIN_ACCOUNT_BALANCE:.2f}. Skipping trade.")
            return 0
            
        # Stop trading if balance is less than 1 contract
        if entry_price is not None and entry_price > 0 and effective_capital < entry_price:
            print(f"[KellySizer] Insufficient balance for contract: capital=${effective_capital:.2f}, contract_price=${entry_price:.2f}. Skipping trade.")
            return 0
            
        # Default: risk 2% of portfolio per trade
        risk_amount = effective_capital * PORTFOLIO_RISK_FRACTION
        
        # If we have good win probability estimates, use Kelly for optimization
        if win_prob is not None and win_prob > 0.52 and avg_win is not None and avg_loss is not None and avg_loss > 0:
            b = avg_win / avg_loss if avg_win > 0 else 1.0
            q = 1.0 - win_prob
            if b > 0:
                kelly_fraction = (win_prob * b - q) / b
                kelly_fraction = max(0, min(self.max_fraction, kelly_fraction * self.safety_factor))
                risk_amount = effective_capital * kelly_fraction
        
        # Clamp risk amount to min/max limits
        risk_amount = max(MIN_POSITION_USD, min(risk_amount, MAX_EXPOSURE_PER_ASSET_USD))
        
        # Convert to contracts
        if entry_price > 0:
            contracts = int(risk_amount / entry_price)
        else:
            contracts = 1
        
        # Ensure at least 1 contract (now safe because we verified capital >= entry_price)
        contracts = max(1, contracts)
        
        # Apply recent performance adjustment
        if recent_pnl_pct is not None:
            if recent_pnl_pct < -0.10:  # Drawdown
                contracts = max(1, contracts - 1)
            elif recent_pnl_pct > 0.10:  # More responsive to positive performance
                contracts = contracts + 1
        
        # Cap at max allowed
        contracts = min(MAX_POSITION_CONTRACTS, contracts)
        
        print(f"[KellySizer] Portfolio: ${effective_capital:.2f}, Risk: ${risk_amount:.2f} ({PORTFOLIO_RISK_FRACTION*100:.1f}%), Contracts: {contracts}")
        
        return contracts
