"""
Latency Optimization Module for Kalshi Bot
Provides utilities to minimize latency in order execution and signal processing.
"""

import asyncio
import time
import aiohttp
from functools import wraps
from typing import Callable, Any
from config import MAX_EXPOSURE_PER_ASSET_USD, MAX_POSITION_CONTRACTS


class LatencyTracker:
    """Track and report latency metrics for critical operations."""
    
    def __init__(self):
        self.metrics = {
            "signal_evaluation": [],
            "order_placement": [],
            "websocket_message": [],
            "market_discovery": [],
            "position_sync": [],
        }
        self.max_samples = 1000
    
    def record(self, operation: str, latency_ms: float):
        """Record a latency measurement."""
        if operation in self.metrics:
            self.metrics[operation].append(latency_ms)
            if len(self.metrics[operation]) > self.max_samples:
                self.metrics[operation].pop(0)
    
    def get_stats(self, operation: str) -> dict:
        """Get latency statistics for an operation."""
        data = self.metrics.get(operation, [])
        if not data:
            return {"count": 0, "avg_ms": 0, "p50_ms": 0, "p95_ms": 0, "p99_ms": 0, "max_ms": 0}
        
        sorted_data = sorted(data)
        n = len(sorted_data)
        return {
            "count": n,
            "avg_ms": sum(data) / n,
            "p50_ms": sorted_data[n // 2],
            "p95_ms": sorted_data[int(n * 0.95)],
            "p99_ms": sorted_data[int(n * 0.99)],
            "max_ms": sorted_data[-1],
        }
    
    def print_report(self):
        """Print latency report for all operations."""
        print("\n=== LATENCY REPORT ===")
        for op in self.metrics:
            stats = self.get_stats(op)
            if stats["count"] > 0:
                print(f"{op}: avg={stats['avg_ms']:.2f}ms p50={stats['p50_ms']:.2f}ms "
                      f"p95={stats['p95_ms']:.2f}ms p99={stats['p99_ms']:.2f}ms "
                      f"max={stats['max_ms']:.2f}ms (n={stats['count']})")


# Global latency tracker instance
latency_tracker = LatencyTracker()


def measure_latency(operation: str):
    """Decorator to measure and record function latency."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                latency_ms = (time.perf_counter() - start) * 1000
                latency_tracker.record(operation, latency_ms)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                latency_ms = (time.perf_counter() - start) * 1000
                latency_tracker.record(operation, latency_ms)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator


class FastOrderCache:
    """Pre-compute order parameters to reduce latency at execution time."""
    
    def __init__(self):
        self._contract_cache = {}  # price -> contracts mapping
    
    def get_contracts(self, price: float, multiplier: float = None) -> int:
        """Get pre-computed contract count for a price."""
        cache_key = round(price, 4)
        if cache_key in self._contract_cache:
            return self._contract_cache[cache_key]
        
        # Compute and cache
        contracts = int(MAX_EXPOSURE_PER_ASSET_USD / price)
        contracts = max(1, min(MAX_POSITION_CONTRACTS, contracts))
        
        # Apply multiplier adjustment if provided
        if multiplier and multiplier > 2.5:
            multiplier_factor = min(1.0, 2.5 / multiplier)
            contracts = max(1, int(contracts * multiplier_factor))
        
        self._contract_cache[cache_key] = contracts
        return contracts
    
    def invalidate(self):
        """Clear cache (call when position limits change)."""
        self._contract_cache.clear()


# Global fast order cache
fast_order_cache = FastOrderCache()


class ConnectionPool:
    """Manage persistent connections for lower latency."""
    
    def __init__(self):
        self._sessions = {}
        self._ws_connections = {}
    
    async def get_session(self, base_url: str):
        """Get or create aiohttp session with connection pooling."""
        if base_url not in self._sessions:
            import aiohttp
            # Configure for low latency
            connector = aiohttp.TCPConnector(
                limit=10,
                limit_per_host=5,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                keepalive_timeout=30,
            )
            timeout = aiohttp.ClientTimeout(total=5, connect=2, sock_read=2)
            self._sessions[base_url] = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )
        return self._sessions[base_url]
    
    async def close_all(self):
        """Close all sessions."""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()


# Global connection pool
connection_pool = ConnectionPool()


async def warm_up_connections(kalshi_base_url: str):
    """Pre-warm connections to reduce first-request latency."""
    session = await connection_pool.get_session(kalshi_base_url)
    # Make a lightweight request to establish connection
    try:
        # Using a simple GET to the base URL instead of an authenticated endpoint
        # to avoid authentication issues during warmup
        async with session.get(kalshi_base_url + "/trade-api/v2/exchange", timeout=aiohttp.ClientTimeout(total=10)) as resp:
            await resp.text()
    except asyncio.TimeoutError:
        print("Connection warm-up timed out after 10 seconds")
    except Exception as e:
        print(f"Connection warm-up failed: {e}")  # Changed from pass to provide feedback


# Windows-safe: skip event loop policy changes, they cause NotImplementedError
class MicroOptimizations:
    """Micro-optimizations for hot paths."""
    
    @staticmethod
    def fast_normalize_price(value) -> float:
        """Optimized price normalization."""
        if value is None:
            return None
        try:
            price = float(value)
            return price / 100 if price > 100 else price
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def fast_spread_pct(bid, ask) -> float:
        """Optimized spread calculation."""
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) * 0.5
        return (ask - bid) / mid if mid > 0 else None
    
    @staticmethod
    def fast_multiplier(price: float) -> float:
        """Optimized multiplier calculation."""
        return 100.0 / price if price >= 1.0 else 1.0 / price
    
    @staticmethod
    def fast_strike_distance(spot: float, strike: float) -> float:
        """Optimized strike distance calculation."""
        if strike is None or strike <= 0:
            return 0.0
        return abs(spot - strike) / strike
    
    @staticmethod
    def fast_pnl(entry: float, current: float, side: str) -> float:
        """Optimized PnL calculation."""
        if entry is None or current is None or entry <= 0:
            return 0.0
        pnl = (current - entry) / entry
        return pnl if side == "yes" else -pnl


# Export singleton instances
__all__ = [
    "latency_tracker",
    "measure_latency",
    "fast_order_cache",
    "connection_pool",
    "warm_up_connections",
    "MicroOptimizations",
]