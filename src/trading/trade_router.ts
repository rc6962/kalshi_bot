import { shouldTakeTrade } from './decision_engine';
import { calculateDynamicStops } from './exit_engine';
import { sizePosition } from './position_sizer';

export type TradeRequest = {
  id: string;
  direction: 'long'|'short';
  entryPrice: number;
  equity: number;
  stopDistance?: number;
  currentPrice: number;
  highs: number[];
  lows: number[];
  closes: number[];
  feesPerTrade: number;
  riskPerTradePct: number;
};

export function evaluateTrade(req: TradeRequest) {
  const {
    id, direction, entryPrice, equity, currentPrice, highs, lows, closes
  } = req;

  // Simple stop distance proxy (could be ATR-based in real usage)
  const stopDistance = req.stopDistance ?? Math.abs(entryPrice - currentPrice) * 0.02;

  // Decide if we should take the trade
  const signal = {
    id,
    entryPrice,
    direction,
    edge: direction === 'long' ? (currentPrice - entryPrice) : (entryPrice - currentPrice),
    feesPerTrade: req.feesPerTrade,
    probability: undefined
  };

  const take = shouldTakeTrade(signal as any, equity, stopDistance);
  if (!take) return null;

  // Determine size
  const size = sizePosition(equity, req.riskPerTradePct, stopDistance);
  // Return a minimal trade representation
  return {
    id,
    direction,
    entryPrice,
    size,
    stopDistance
  };
}