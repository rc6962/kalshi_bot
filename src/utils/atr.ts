export function computeATR(highs: number[], lows: number[], closes: number[], period: number = 14): number[] {
  // Basic ATR calculation: TR = max(high - low, abs(high - prevClose), abs(low - prevClose))
  const trs: number[] = [];
  for (let i = 1; i < highs.length; i++) {
    const tr = Math.max(
      highs[i] - lows[i],
      Math.abs(highs[i] - closes[i - 1]),
      Math.abs(lows[i] - closes[i - 1])
    );
    trs.push(tr);
  }
  // Simple moving average of TR
  const atr: number[] = [];
  let sum = 0;
  for (let i = 0; i < trs.length; i++) {
    sum += trs[i];
    if (i >= period) sum -= trs[i - period];
    if (i >= period - 1) atr.push(sum / period);
  }
  return atr;
}