# CLAUDE.md — Working agreement for AI assistants in this repo

AIwallstreet is **educational / research tooling**. It surfaces *probabilistic
setups* from unusual options flow — **not** buy/sell signals, and **not**
investment advice. Treat every interaction through that lens.

## Trading safety policy (READ FIRST)

The Robinhood Trading MCP server (`robinhood-trading`, an authenticated
connection to a **live brokerage account**) may be configured in this
environment. The account owner wants the assistant to **recommend** trades, not
**execute** them.

**Hard rules — no exceptions without an explicit, in-the-moment instruction
from the account owner naming the specific order:**

1. **Never place or cancel an order.** Do not call
   `mcp__robinhood-trading__place_equity_order` or
   `mcp__robinhood-trading__cancel_equity_order`. These are also blocked by the
   `deny` list in `.claude/settings.json`.
2. **Read-only is fine.** You *may* use the read-only `robinhood-trading`
   tools to gather the data needed to make a recommendation:
   `get_accounts`, `get_portfolio`, `get_equity_positions`,
   `get_equity_quotes`, `get_equity_tradability`, `get_equity_orders`,
   `search`, and `review_equity_order` (the latter only *previews* an order's
   cost/details — it does not submit anything).
3. **Recommendations are output, not actions.** Present trade ideas as text for
   the human to review and act on themselves — entry/exit, sizing, and rationale
   — and remind them it is not investment advice.
4. **When in doubt, ask.** If a request is ambiguous about whether it wants an
   action versus a recommendation, ask before doing anything that could move
   money.

The two layers — this behavioral policy and the `deny` list in
`.claude/settings.json` — are intended to back each other up. The deny list was
verified against the live tool names exposed by the authenticated
`robinhood-trading` server (10 tools, equity-only; no options tools are
exposed).
