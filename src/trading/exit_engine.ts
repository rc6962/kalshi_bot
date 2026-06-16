import { computeATR } from './../utils/atr';

export type ExitParams = {
  atrPeriod?: number;
  atrMultStop?: number;
  atrMultTarget?: number;
  currentPrice: number;
  highs: number[];
  lows: number[];
  closes: number[];
  positionSize?: number;
};

export function calculateDynamicStops(params: ExitParams) {
  const { highs, lows, closes, atrPeriod = 14, atrMultStop = 1.5, atrMultTarget = 2.5 } = params;
  if (highs.length < atrPeriod + 1) return null;

  const atrs = computeATR(highs, lows, closes, atrPeriod);
  const latestATR = atrs[atrs.length - 1] ?? atrPeriod; // fallback

  // Example: for a long, stop is currentPrice - ATR*multStop, target is currentPrice + ATR*multTarget
  // For short, reverse signs
  const price = params.currentPrice;
  const stopLong = price - latestATR * atrMultStop;
  const targetLong = price + latestATR * atrMultTarget;

  const stopShort = price + latestATR * atrMultStop;
  const targetShort = price - latestATR * atrMultTarget;

  return {
    stopLong,
    targetLong,
    stopShort,
    targetShort,
    atr: latestATR
  };
}