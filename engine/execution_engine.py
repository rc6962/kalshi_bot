# engine/execution_engine.py

class ExecutionEngine:
    def __init__(self, client):
        self.client = client

    async def execute(self, ticker, side, contracts, price=None, action="buy"):
        """Place a market order through the KalshiClient.

        Args:
            ticker: Market ticker (e.g. "BTC-28JUN26-100000-YES")
            side: "yes" or "no"
            contracts: Number of contracts to trade
            price: Optional limit price in dollars
            action: "buy" (default) or "sell"
        """
        return await self.client.place_market_order(
            ticker=ticker,
            side=side,
            contracts=contracts,
            price=price,
            action=action,
        )
