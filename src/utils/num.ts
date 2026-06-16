export function clamp(n:number, min:number, max:number){ return Math.max(min, Math.min(max, n)); }
export function toFixed(n:number, digits=2){ return Number(n.toFixed(digits)); }