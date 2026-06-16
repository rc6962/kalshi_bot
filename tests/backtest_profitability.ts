import { shouldTakeTrade } from '../src/trading/decision_engine';
import { calculatePositionSize } from '../src/trading/position_sizing';
import { netEdge } from '../src/trading/edge_estimator';

type TradeSample = {
  equity: number;
  edge: number;
  fees: number;
  probability?: number;
  price: number;
  stopTicks: number;
};

export function backtestSample(samples: TradeSample[]) {
  let equity = 100000; // starting equity
  let results: number[] = [];
  for (const s of samples) {
    const edgeAfterFees = netEdge(s.edge, s.fees, 0);
    const take = shouldTakeTrade({ id:'t', entryPrice:s.price, direction:'long', edge: edgeAfterFees, feesPerTrade:s.fees, probability:s.probability } as any, equity, s.stopTicks);
    if (take) {
      const size = calculatePositionSize(equity, 0.01, s.stopTicks);
      equity += edgeAfterFees * size;
    }
    results.push(equity);
  }
  return results;
}