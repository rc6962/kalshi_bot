import { shouldTakeTrade } from '../src/trading/decision_engine';

describe('profitability gate', () => {
  test('positive net edge passes gate', () => {
    const s = { id: 't1', entryPrice: 100, direction: 'long' as const, edge: 2, feesPerTrade: 0.5, probability: 0.6 };
    expect(shouldTakeTrade(s, 1000, 1)).toBe(true);
  });

  test('negative edge fails gate', () => {
    const s = { id: 't2', entryPrice: 100, direction: 'short' as const, edge: 0.2, feesPerTrade: 0.5, probability: 0.8 };
    expect(shouldTakeTrade(s, 1000, 1)).toBe(false);
  });
});