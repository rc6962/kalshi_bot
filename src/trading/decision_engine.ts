// Define the Signal type
type Signal = {
  id: string;
  entryPrice: number;
  direction: 'long' | 'short';
  edge: number;
  feesPerTrade: number;
  probability?: number;
};

// Define shouldTakeTrade function to determine if a trade should be taken
export function shouldTakeTrade(signal: Signal, equity: number, stopDistance: number) {
  // Check if the net edge after fees is positive
  const netEdge = signal.edge - signal.feesPerTrade;
  
  // Only take trades with positive net edge
  if (netEdge <= 0) {
    console.log(`Trade rejected: negative net edge (${netEdge})`);
    return false;
  }
  
  // Ensure the trade fits within our risk parameters
  const maxRiskPerTrade = equity * 0.02; // Risk at most 2% of equity per trade
  const potentialLoss = stopDistance; // Simplified: stop distance represents potential loss
  
  if (potentialLoss > maxRiskPerTrade) {
    console.log(`Trade rejected: potential loss (${potentialLoss}) exceeds max risk (${maxRiskPerTrade})`);
    return false;
  }
  
  // If probability is provided, ensure it meets minimum threshold
  if (signal.probability !== undefined && signal.probability < 0.55) {
    console.log(`Trade rejected: low probability (${signal.probability})`);
    return false;
  }
  
  console.log(`Trade approved: net edge ${netEdge}, potential loss ${potentialLoss}`);
  return true;
}

// Extend shouldTakeTrade to factor fees/slippage already included via netEdge
// If you have an explicit fees/slippage model, incorporate here as well:
export function effectiveEdgeAfterCosts(s: Signal): number {
  return s.edge - s.feesPerTrade;
}