"""Parser for a trusted analyst's weekly levels table.

The analyst publishes rows like:

    NVDA   215.24  220     240.4 235.2 225.2 / 210 204.8 194.8
    BRK B  486.4   483.7   505.9 497.6 492  / 478.1 469.9 464.3

Layout per row: TICKER, CLB36+B1 value, WEEKLY CPL value, then the weekly
levels with '/' separating resistance (above, on the left) from support
(below, on the right). Tickers may contain spaces ("BRK B") or symbols
("ES_F"), so the ticker is everything before the first number.
"""
from __future__ import annotations


def _to_float(tok: str) -> float | None:
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def parse_levels_table(text: str) -> list[dict]:
    """Parse pasted analyst rows into level dicts. Skips headers/blank lines and
    any row we can't read, so a messy paste still imports what it can."""
    rows: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.replace("/", " / ").split()

        # Ticker = leading non-numeric tokens (handles "BRK B", "ES_F").
        i = 0
        ticker_parts: list[str] = []
        while i < len(tokens) and tokens[i] != "/" and _to_float(tokens[i]) is None:
            ticker_parts.append(tokens[i])
            i += 1
        if not ticker_parts or i >= len(tokens):
            continue
        ticker = " ".join(ticker_parts).upper()

        rest = tokens[i:]
        if "/" in rest:
            cut = rest.index("/")
            left, right = rest[:cut], rest[cut + 1:]
        else:
            left, right = rest, []

        left_nums = [f for f in (_to_float(t) for t in left) if f is not None]
        right_nums = [f for f in (_to_float(t) for t in right) if f is not None]
        if not left_nums:
            continue

        clb36 = left_nums[0] if len(left_nums) >= 1 else None
        weekly_cpl = left_nums[1] if len(left_nums) >= 2 else None
        resistances = sorted(left_nums[2:], reverse=True)
        supports = sorted(right_nums, reverse=True)

        rows.append({
            "ticker": ticker, "clb36": clb36, "weekly_cpl": weekly_cpl,
            "resistances": resistances, "supports": supports,
        })
    return rows
