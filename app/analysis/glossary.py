"""Plain-English glossary + a helper to footnote the jargon used in an alert.

Aimed at a beginner: every term that shows up in a trade-idea card can be
explained in one sentence, so nothing in a notification is unexplained jargon.
"""
from __future__ import annotations

# Keep definitions short, concrete, and free of further jargon.
GLOSSARY: dict[str, str] = {
    "call": "A call option is a bet that the stock price will go UP. It gets "
            "more valuable as the stock rises.",
    "put": "A put option is a bet that the stock price will go DOWN (or "
           "insurance against a drop).",
    "strike": "The strike is the price the option is betting the stock will "
              "reach. A $180 call profits if the stock pushes toward/past $180.",
    "premium": "The premium is the total dollars spent on the trade — how much "
               "money was put at risk on this bet.",
    "expiry": "The expiration date. After this date the option is worthless if "
              "the stock hasn't moved enough — options are a bet against a clock.",
    "open interest": "How many of this exact contract already exist. Big NEW "
                     "volume vs. small open interest = a freshly opened position.",
    "vol/OI": "Volume divided by open interest. Above 1 means today's trading "
              "is bigger than everything outstanding — a sign of fresh activity.",
    "IV": "Implied volatility — how expensive the option is. High IV = the "
          "market already expects a big move (you're paying up for it).",
    "OTM": "Out-of-the-money — the strike is beyond the current price. Cheaper, "
           "higher-risk, higher-reward; needs a real move to pay off.",
    "sweep": "An order split across multiple exchanges to fill fast — often a "
             "sign of urgency.",
    "support": "A price level the stock has bounced UP from before — a floor it "
               "may hold at.",
    "resistance": "A price level the stock has struggled to break ABOVE before "
                  "— a ceiling it may stall at.",
    "RSI": "A 0-100 momentum gauge. Above 70 = 'overbought' (ran up fast, may "
           "pull back); below 30 = 'oversold' (beaten down, may bounce).",
    "MACD": "A momentum trend gauge. Turning positive suggests upward momentum "
            "is building; negative suggests it's fading.",
    "breakout": "When price pushes ABOVE a resistance ceiling — often a bullish "
                "trigger.",
    "breakdown": "When price falls BELOW a support floor — often a bearish "
                 "warning.",
}


def explain_terms(text: str, limit: int = 6) -> list[str]:
    """Return 'term — definition' lines for glossary terms that appear in text,
    longest-term-first so 'open interest' wins over 'interest'."""
    low = text.lower()
    out: list[str] = []
    for term in sorted(GLOSSARY, key=len, reverse=True):
        if term.lower() in low:
            out.append(f"{term} — {GLOSSARY[term]}")
        if len(out) >= limit:
            break
    return out
