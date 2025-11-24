\
"""
LOB Event Extractor
===================

This module provides LOBEventExtractor, a simple and robust parser that
turns exchange-style L2 snapshot/delta JSON lines into structured "market events".
It is intended for research, backtesting pre-processing, and quick
visualization of mid-price traces and orderbook events.

The extractor follows the behavior of the original script you provided,
but cleans corner cases, computes depths reliably, and exposes a simple API.
"""

from typing import Dict, List, Optional, Iterable, Any
from dataclasses import dataclass, asdict
import json
import math

@dataclass
class LOBEvent:
    action: str
    price: float
    volume: float
    depth: int
    index: int
    mid_price: float
    previous_vol: float
    volume_change_normalized: float

    def to_dict(self):
        return asdict(self)

class LOBEventExtractor:
    """
    Maintain an in-memory LOB from snapshot + delta lines and emit semantic events.
    Parameters
    ----------
    max_depth: int
        Only report events whose depth (rank) is < max_depth on its side.
    """
    def __init__(self, max_depth: int = 50):
        self.max_depth = max_depth
        # store price->volume mappings
        self.asks: Dict[float, float] = {}
        self.bids: Dict[float, float] = {}
        # cached sorted price lists (kept in sync manually for speed/simple correctness)
        self._sorted_asks: Optional[List[float]] = None
        self._sorted_bids: Optional[List[float]] = None
        self.last_mid_price: Optional[float] = None

    def _invalidate_sorted(self):
        self._sorted_asks = None
        self._sorted_bids = None

    def _ensure_sorted(self):
        if self._sorted_asks is None:
            # asks ascending
            self._sorted_asks = sorted(self.asks.keys())
        if self._sorted_bids is None:
            # bids descending
            self._sorted_bids = sorted(self.bids.keys(), reverse=True)

    def _mid_price(self) -> float:
        # if either side empty, return NaN to signal invalid mid
        if not self.bids or not self.asks:
            return float("nan")
        # best bid is max bid price, best ask is min ask price
        best_bid = max(self.bids.keys())
        best_ask = min(self.asks.keys())
        return (best_bid + best_ask) / 2.0

    def process_snapshot(self, asks: Iterable[Iterable[float]], bids: Iterable[Iterable[float]]):
        """
        Replace the entire LOB with a snapshot. `asks` and `bids` are iterables of [price, volume].
        """
        self.asks = {float(p): float(v) for p, v in asks}
        self.bids = {float(p): float(v) for p, v in bids}
        self._invalidate_sorted()

    def _depth_of_price(self, price: float, side: str) -> int:
        """
        Return depth index (0-based) of given price on the specified side.
        If price not present, return position where it would be inserted.
        """
        self._ensure_sorted()
        if side == "ask":
            prices = self._sorted_asks
        else:
            prices = self._sorted_bids  # descending for bids
        # linear search is fine for moderate book widths; can be optimized later
        for i, p in enumerate(prices):
            if math.isclose(p, price) or (side == "ask" and p >= price) or (side == "bid" and p <= price):
                return i
        return len(prices)

    def process_delta(self, asks: Iterable[Iterable[float]], bids: Iterable[Iterable[float]], index: int = 0) -> List[LOBEvent]:
        """
        Process a single delta message (partial updates on both sides) and return emitted events.
        Each entry in asks/bids is [price, volume] where volume is the new total at that price.
        """
        events: List[LOBEvent] = []
        # process asks (sell side)
        for p, v in asks:
            price = float(p); vol = float(v)
            prev_vol = self.asks.get(price, 0.0)
            # compute depth based on previous state (before applying this change)
            depth = self._depth_of_price(price, "ask") if prev_vol > 0.0 else self._depth_of_price(price, "ask")
            change = vol - prev_vol
            # market buy if a price level disappears (volume goes to 0)
            if vol == 0.0:
                if depth < self.max_depth:
                    events.append(LOBEvent(
                        action="market_buy",
                        price=price,
                        volume=-change,
                        depth=depth,
                        index=index,
                        mid_price=self._mid_price(),
                        previous_vol=prev_vol,
                        volume_change_normalized=(change / prev_vol) if prev_vol > 0.0 else 1.0
                    ))
                self.asks.pop(price, None)
            else:
                if depth < self.max_depth:
                    if change > 0:
                        events.append(LOBEvent("sell_limit_added", price, change, depth, index, self._mid_price(), prev_vol, (change / prev_vol) if prev_vol > 0.0 else 1.0))
                    elif change < 0:
                        events.append(LOBEvent("sell_limit_canceled", price, -change, depth, index, self._mid_price(), prev_vol, (-change / prev_vol) if prev_vol > 0.0 else 1.0))
                self.asks[price] = vol

        # process bids (buy side)
        for p, v in bids:
            price = float(p); vol = float(v)
            prev_vol = self.bids.get(price, 0.0)
            depth = self._depth_of_price(price, "bid") if prev_vol > 0.0 else self._depth_of_price(price, "bid")
            change = vol - prev_vol
            if vol == 0.0:
                if depth < self.max_depth:
                    events.append(LOBEvent("market_sell", price, -change, depth, index, self._mid_price(), prev_vol, (change / prev_vol) if prev_vol > 0.0 else 1.0))
                self.bids.pop(price, None)
            else:
                if depth < self.max_depth:
                    if change > 0:
                        events.append(LOBEvent("buy_limit_added", price, change, depth, index, self._mid_price(), prev_vol, (change / prev_vol) if prev_vol > 0.0 else 1.0))
                    elif change < 0:
                        events.append(LOBEvent("buy_limit_canceled", price, -change, depth, index, self._mid_price(), prev_vol, (-change / prev_vol) if prev_vol > 0.0 else 1.0))
                self.bids[price] = vol

        # after applying all updates, invalidate sorted lists so future depths are recalculated
        self._invalidate_sorted()
        return events

def parse_file(path: str, max_depth: int = 50):
    """
    Parse a newline-delimited JSON L2 file and yield events and mid-price history.
    The file is expected to contain objects like:
      {"type":"snapshot","data":{"a":[[price,vol],...],"b":[[price,vol],...]}} or
      {"type":"delta","data":{"a":[[price,vol],...],"b":[[price,vol],...]}}.
    Yields
    ------
    (index, list_of_events, mid_price_or_nan)
    """
    extractor = LOBEventExtractor(max_depth=max_depth)
    mid_prices = []
    with open(path, "r") as f:
        for idx, line in enumerate(f):
            l = json.loads(line)
            new_events = []
            if l.get("type") == "snapshot":
                data = l.get("data", {})
                extractor.process_snapshot(data.get("a", []), data.get("b", []))
                # snapshot may change mid-price but we do not emit events for snapshot construction
            elif l.get("type") == "delta":
                data = l.get("data", {})
                new_events = extractor.process_delta(data.get("a", []), data.get("b", []), index=idx)
            mid_price = extractor._mid_price()
            # emit only when mid_price changed (like original script)
            if math.isnan(extractor.last_mid_price) and not math.isnan(mid_price):
                changed = True
            else:
                changed = (extractor.last_mid_price is None) or (extractor.last_mid_price != mid_price)
            if changed:
                extractor.last_mid_price = mid_price
                mid_prices.append(mid_price)
                yield idx, [e.to_dict() for e in new_events], mid_price

def infer_events_from_lines(lines):
    """
    Convenience function: accepts an iterable of JSON strings (lines) and returns the accumulated events and mid-price trace.
    """
    extractor = LOBEventExtractor()
    events = []
    mid_prices = []
    for idx, line in enumerate(lines):
        l = json.loads(line)
        if l.get("type") == "snapshot":
            extractor.process_snapshot(l["data"].get("a", []), l["data"].get("b", []))
            continue
        else:
            evs = extractor.process_delta(l["data"].get("a", []), l["data"].get("b", []), index=idx)
            mid = extractor._mid_price()
            if extractor.last_mid_price is None or extractor.last_mid_price != mid:
                extractor.last_mid_price = mid
                mid_prices.append(mid)
                events.extend([e.to_dict() for e in evs])
    return events, mid_prices

if __name__ == "__main__":
    # small demo when executed directly
    import argparse, sys
    parser = argparse.ArgumentParser(description="Extract LOB events from NDJSON snapshot/delta file")
    parser.add_argument("file", help="Input NDJSON LOB file")
    parser.add_argument("--max-depth", type=int, default=50, help="Only report events with depth < max-depth")
    args = parser.parse_args()
    for idx, evs, mid in parse_file(args.file, max_depth=args.max_depth):
        for e in evs:
            print(json.dumps(e))
