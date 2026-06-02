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

1. **Never place, modify, or cancel an order.** Do not call any
   `robinhood-trading` tool that places, replaces, cancels, or otherwise
   mutates orders or positions (e.g. anything named like `place_order`,
   `buy`, `sell`, `submit_order`, `cancel_order`, `replace_order`,
   `place_*_order`, transfers, withdrawals, etc.).
2. **Read-only is fine.** You *may* use read-only `robinhood-trading` tools
   (quotes, positions, buying power, account/portfolio info, order history) to
   gather the data needed to make a recommendation.
3. **Recommendations are output, not actions.** Present trade ideas as text for
   the human to review and act on themselves — entry/exit, sizing, and rationale
   — and remind them it is not investment advice.
4. **When in doubt, ask.** If a request is ambiguous about whether it wants an
   action versus a recommendation, ask before doing anything that could move
   money.

A complementary `deny` rule in `.claude/settings.json` should be added once the
account owner authenticates and can see the exact order-placing tool names (see
`.claude/settings.json` for the template). The two layers — this behavioral
policy and the permission deny-list — are intended to back each other up.
