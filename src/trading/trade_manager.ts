import { shouldTakeTrade } from './decision_engine';
import { calculatePositionSize, computeATRStops } from './position_sizing';
import { netEdge } from './edge_estimator';

type MarketBar = {high:number; low:number; close:number};
type TradeParams = {
  equity: number;
  price: number;
  direction: 'long'|'short';
  stopTicks: number;
  atrPeriod?: number;
  feesPerTrade: number;
  slippage?: number;
};

export function tryOpenTrade(signal: any, bars: MarketBar[], equity: number) {
  // derive ATR-based stops
  const atrPeriod = 14;
  const currentPrice = signal.price;
  const { stop, target } = computeATRStops(bars, atrPeriod, currentPrice);

  const stopTicks = Math.max(1, Math.abs(currentPrice - stop));
  const size = calculatePositionSize(equity, 0.01, stopTicks);

  const edgeAfterFees = netEdge(signal.edge, signal.feesPerTrade, signal.slippage ?? 0);
  if (!edgeAfterFees) return null;

  if (!shouldTakeTrade({ ...signal, edge: edgeAfterFees }, equity, stopTicks)) {
    return null;
  }

  return {
    direction: signal.direction,
    price: currentPrice,
    size,
    stop: stop,
    target: target
  };
}