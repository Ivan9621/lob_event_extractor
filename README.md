# lob_event_extractor

A tiny, focused library to **extract market events from Limit Order Book (LOB) snapshot/delta streams**.
It transforms exchange-style NDJSON (newline delimited JSON) files containing `snapshot` and `delta`
messages into structured "market events" (limit adds/cancels, and market buys/sells) and a mid-price time series.

This repo is set up to be published on GitHub as a minimal research utility. It contains:
- a Python package `lob_event_extractor` with a single `LOBEventExtractor` and convenience functions,
- `README.md` with detailed explanation of the algorithm and API,
- MIT license.

---

## Why this library? (short)
Many high-frequency and microstructure studies start from exchange L2 feeds that supply full snapshots and deltas.
This small tool helps convert such feeds into a compact, human-readable sequence of _events_ useful for
analysis, labeling, or feeding into models.

## How it works (detailed)
1. The parser maintains an in-memory LOB represented as two dictionaries: `asks` (price -> volume) and `bids` (price -> volume).
2. When a **snapshot** message arrives the in-memory LOB is replaced with the snapshot data.
3. When a **delta** (partial update) message arrives the parser iterates over changed price levels on both sides:
   - For each price level we compute `previous_vol` (0 if the price did not exist before) and the `change = new_vol - previous_vol`.
   - If `new_vol == 0` we treat that as the level being removed. If the removal occurs within `max_depth` levels from the top we mark this as a `market_buy` (asks removed) or `market_sell` (bids removed) event. This mirrors the interpretation that the level vanished because aggressive liquidity consumed it.
   - If `new_vol > previous_vol` we label this `*_limit_added` (buy_limit_added or sell_limit_added).
   - If `new_vol < previous_vol` we label this `*_limit_canceled` (buy_limit_canceled or sell_limit_canceled).
4. The extractor calculates a **depth index** for each price level (0-based) by sorting the side's price ladder and finding the price position. Only events with `depth < max_depth` are emitted to avoid noise deep in the book.
5. After applying a delta, the extractor invalidates cached sorted lists. Mid-price is computed as `(best_bid + best_ask) / 2`. The parser only yields events when the mid-price changes (same behaviour as original script).

**Notes / Limitations**:
- The extractor assumes price levels are exact matches (floating equality). For real feeds you might want to quantize prices or use nearest-tolerance matching.
- Depth is computed with simple sorting and linear scan. If you have very wide books and care about performance, replace this with a balanced tree or `bisect` on maintained sorted arrays.
- The heuristic for `market_buy/market_sell` is "level disappearance". Some exchanges may send explicit trade reports; this tool uses only LOB changes.

## API / Usage

### Basic usage (as a script)
```bash
python -m lob_event_extractor.extractor path/to/your_file.ndjson --max-depth 50
```

### Using the library in Python
```python
from lob_event_extractor import LOBEventExtractor, parse_file, infer_events_from_lines

# iterate file and receive mid-price updates with events
for idx, events, mid_price in parse_file("2025-10-05_SOLUSDT_ob200.data", max_depth=50):
    # events is a list of dictionaries (possibly empty)
    for e in events:
        print(e)
    # mid_price is the current mid-price after processing the line
```

### Programmatic API
- `LOBEventExtractor(max_depth=50)` - create an extractor instance. Use `process_snapshot` and `process_delta` to feed raw messages.
- `parse_file(path, max_depth=50)` - generator yielding `(index, list_of_events, mid_price)` whenever mid-price changes.
- `infer_events_from_lines(lines)` - convenience that takes an iterable of JSON-lines and returns `(events, mid_prices)` aggregated.

## Development / Publishing
- License: MIT (included)
- Tests: none included by default; recommended to add pytest-based tests for your exact feed format
- Publishing: just push to GitHub. For PyPI packaging add `setup.cfg`/`pyproject.toml` if desired.

---

## License
MIT
