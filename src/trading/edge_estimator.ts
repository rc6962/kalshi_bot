export function netEdge(edge: number, feesPerTrade: number, slippage: number = 0): number {
  // subtract both per-trade fees and slippage
  return edge - feesPerTrade - slippage;
}