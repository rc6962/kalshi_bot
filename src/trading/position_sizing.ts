export function calcATR(prices: {high:number; low:number; close:number}[], period = 14): number {
  // Simple ATR calculation
  if (prices.length < period + 1) return 0;
  let trs: number[] = [];
  for (let i = prices.length - period; i < prices.length; i++) {
    const cur = prices[i];
    const prev = prices[i-1];
    const tr = Math.max(cur.high - cur.low, Math.abs(cur.high - prev.close), Math.abs(cur.low - prev.close));
    trs.push(tr);
  }
  const mean = trs.reduce((a,b)=>a+b,0) / trs.length;
  return mean;
}

export function calculatePositionSize(equity: number, riskPerTradePct: number, stopDistanceInTicks: number): number {
  // riskPerTradePct e.g., 0.01 for 1%
  const risk = equity * riskPerTradePct;
  if (stopDistanceInTicks <= 0) return 0;
  // size proportional to how many units you can risk per stop
  const size = risk / stopDistanceInTicks;
  // clamp to sensible range
  const minSize = 1;
  const maxSize = 100000;
  return Math.max(minSize, Math.min(maxSize, Math.floor(size)));
}

export function computeATRStops(prices: {high:number; low:number; close:number}[], atrPeriod: number, price: number): {stop:number; target:number} {
  const atr = calcATR(prices, atrPeriod);
  // e.g., stop 1.5x ATR away, target 3x ATR away
  const stopDistance = atr * 1.5;
  const targetDistance = atr * 3.0;
  return { stop: price - stopDistance, target: price + targetDistance };
}