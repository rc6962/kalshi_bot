export function sizePosition(equity: number, riskPerTradePct: number, stopDistancePoints: number, minSize = 1, maxSize = 10000): number {
  const riskPerTrade = equity * riskPerTradePct;
  if (stopDistancePoints <= 0) return minSize;
  const size = Math.floor(riskPerTrade / stopDistancePoints);
  if (!isFinite(size) || size < minSize) return minSize;
  if (size > maxSize) return maxSize;
  return size;
}