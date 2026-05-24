#!/usr/bin/env python3
"""
Seeded auto-player for PM Wars — OG Edition.

Drives the real game logic from pm_wars.py (no UI, no input()) with a simple
greedy strategy, then documents the full 60-day run day by day. Reproducible
via SEED.

Strategy in a nutshell:
  - Home base = Anthropic (95% token quality clears every client's quality bar).
  - Keep the craft slot busy: build the highest-budget product any active
    client currently wants.
  - When a product is finished, fly to the best-paying client and sell it
    (the next product keeps crafting during the sales trip — a pipeline).
  - Pour surplus cash into the 2%/day compounding debt, keeping a working
    reserve so we can always afford the next sales trip.
"""

import random
from collections import Counter, defaultdict
import pm_wars as g

SEED          = 7
TRAVEL_RESERVE = 35_000   # don't spend below this buying tokens (keeps a trip affordable)
DEBT_RESERVE   = 150_000  # keep this much working capital before paying down debt
                          # (must absorb a wasted sales trip without stranding us)
HOME           = "Anthropic"


def networth(s):
    return s["cash"] - s["debt"]


def _covered(s):
    """How many of each product we already hold or are building (don't overproduce a SKU)."""
    cov = Counter(p["name"] for p in s["products"])
    if s["crafting"]:
        cov[s["crafting"]["name"]] += 1
    return cov


def choose_target(s):
    """Highest-budget unfilled contract, matching production to real demand."""
    cov = _covered(s)
    demand = defaultdict(list)
    for c in s["active_clients"]:
        for prod, info in c["current_wants"].items():
            demand[prod].append(info["budget"])
    best = None
    for prod, budgets in demand.items():
        budgets.sort(reverse=True)
        idx = cov.get(prod, 0)          # skip contracts our shelf/craft already covers
        if idx < len(budgets):
            b = budgets[idx]
            if best is None or b > best[0]:
                best = (b, prod)
    return best[1] if best else None


def buy_for(s, name):
    """Top up inventory (from current provider) to cover a recipe. Returns list of buys or None."""
    prices = s["provider_prices"][s["location"]]
    recipe = g.PRODUCTS[name]["recipe"]
    to_buy, total = {}, 0
    for t, n in recipe.items():
        held = s["tokens"].get(t, {"qty": 0})["qty"]
        need = max(0, n - held)
        to_buy[t] = need
        total += prices[t] * need
    if sum(to_buy.values()) > g.token_free(s):
        return None
    if total > s["cash"] - TRAVEL_RESERVE:
        return None
    bought = []
    for t, need in to_buy.items():
        if need > 0:
            g.do_buy_tokens(s, t, need)
            bought.append(f"{need}M {t}")
    return bought


def best_sale_client(s):
    """Return (client_idx, revenue) for the best sellable (product, client) pair, else None."""
    best = None
    for ci, c in enumerate(s["active_clients"]):
        for p in s["products"]:
            info = c["current_wants"].get(p["name"])
            if not info or p["quality"] < info["min_quality"]:
                continue
            bonus = min(p["quality"] / info["min_quality"], g.QUALITY_BONUS_CAP)
            rev = int(info["budget"] * bonus)
            if best is None or rev > best[1]:
                best = (ci, rev)
    return best


def sell_all_here(s, acts):
    """At our current client location, sell every product they want (best revenue first)."""
    here = s["location"]
    ci = next((i for i, c in enumerate(s["active_clients"]) if c["name"] == here), None)
    if ci is None:
        return
    while True:
        c = s["active_clients"][ci]
        cand = None
        for pi, p in enumerate(s["products"]):
            info = c["current_wants"].get(p["name"])
            if info and p["quality"] >= info["min_quality"]:
                bonus = min(p["quality"] / info["min_quality"], g.QUALITY_BONUS_CAP)
                rev = int(info["budget"] * bonus)
                if cand is None or rev > cand[1]:
                    cand = (pi, rev, p["name"])
        if not cand:
            break
        g.do_sell_product(s, cand[0], ci)
        acts.append(f"SOLD {cand[2]} → ${cand[1]:,}")


def status(s):
    if s["crafting"]:
        c = s["crafting"]
        craft = f"{c['name']} ({c['days_left']}d, {c['quality']:.0%})"
    else:
        craft = "—"
    prods = ", ".join(f"{p['name']} {p['quality']:.0%}" for p in s["products"]) or "—"
    return craft, prods


def main():
    random.seed(SEED)
    s = g.new_game()
    rows = []

    def snapshot(acts):
        craft, prods = status(s)
        rows.append({
            "day":   s["day"],
            "loc":   s["location"],
            "acts":  "; ".join(acts) if acts else "—",
            "event": s["last_event"] or "",
            "cash":  s["cash"],
            "debt":  s["debt"],
            "nw":    networth(s),
            "craft": craft,
            "prods": prods,
        })
        s["last_event"] = None
        s["message"] = None

    # Day 1 starting snapshot
    snapshot(["start at Anthropic"])

    guard = 0
    while s["day"] < g.MAX_DAYS and guard < 500:
        guard += 1
        acts = []

        if s["location"] == HOME:
            # 1) keep the craft slot busy
            if not s["crafting"]:
                target = choose_target(s)
                if target:
                    bought = buy_for(s, target)
                    if bought is not None:
                        if bought:
                            acts.append("bought " + ", ".join(bought))
                        g.do_craft(s, target)
                        acts.append(f"craft {target}")
            # 2) if holding a finished product, fly to the best buyer
            if s["products"]:
                bs = best_sale_client(s)
                if bs and s["cash"] >= g.TRAVEL_COST:
                    dest = s["active_clients"][bs[0]]["name"]
                    acts.append(f"fly → {dest}")
                    g.do_travel(s, dest, "client")
                    snapshot(acts)
                    continue
            # 3) otherwise let a day pass (craft progresses)
            acts.append("wait")
            g.advance_days(s, 1)
            snapshot(acts)
            continue
        else:
            # at a client: sell, pay down debt, fly home
            sell_all_here(s, acts)
            if s["debt"] > 0 and s["cash"] > DEBT_RESERVE:
                pay = s["cash"] - DEBT_RESERVE
                g.do_pay_debt(s, pay)
                acts.append(f"pay debt ${pay:,}")
            if s["location"] != HOME and s["cash"] >= g.TRAVEL_COST:
                acts.append("fly → Anthropic")
                g.do_travel(s, HOME, "provider")
                snapshot(acts)
                continue
            acts.append("wait")
            g.advance_days(s, 1)
            snapshot(acts)
            continue

    # ---- console log ----
    print(f"PM WARS — full playthrough (seed {SEED})\n")
    for r in rows:
        print(f"Day {r['day']:>2}/60 | 📍{r['loc']:<18} | "
              f"cash ${r['cash']:>9,} | debt ${r['debt']:>9,} | nw ${r['nw']:>10,}")
        print(f"         actions: {r['acts']}")
        if r["event"]:
            print(f"         event:   {r['event']}")
        print(f"         status:  crafting {r['craft']} | shelf: {r['prods']}")
        print()

    nw = networth(s)
    if   nw >= 1_000_000: grade = "🏆 UNICORN"
    elif nw >=   500_000: grade = "🌟 SERIES A"
    elif nw >=   100_000: grade = "✅ RAMEN PROFITABLE"
    elif nw >=         0: grade = "😅 BROKE EVEN"
    else:                 grade = "💀 BANKRUPT"
    print("=" * 60)
    print(f"FINAL — Day {s['day']}: cash ${s['cash']:,} | debt ${s['debt']:,} | "
          f"net worth ${nw:,}  →  {grade}")
    print("=" * 60)

    write_markdown(rows, s, nw, grade)


def write_markdown(rows, s, nw, grade):
    lines = []
    lines.append("# PM Wars — Full Playthrough Log\n")
    lines.append(f"_Auto-played by a greedy strategy bot (`playthrough_sim.py`, seed {SEED}) "
                 "driving the real game logic in `pm_wars.py`._\n")
    lines.append("**Strategy:** base at Anthropic (95% quality clears every client's bar), "
                 "always keep the craft slot busy building the highest-budget product in demand, "
                 "pipeline sales trips while the next product builds, and pour surplus cash into "
                 "the 2%/day compounding debt.\n")
    lines.append("## Day-by-day\n")
    lines.append("| Day | Location | Actions | Event | Cash | Debt | Net Worth |")
    lines.append("|----:|----------|---------|-------|-----:|-----:|----------:|")
    for r in rows:
        event = r["event"].replace("|", "\\|") if r["event"] else ""
        acts = r["acts"].replace("|", "\\|")
        lines.append(f"| {r['day']} | {r['loc']} | {acts} | {event} | "
                     f"${r['cash']:,} | ${r['debt']:,} | ${r['nw']:,} |")
    lines.append("")
    lines.append("## Result\n")
    lines.append(f"- **Final day:** {s['day']}")
    lines.append(f"- **Cash:** ${s['cash']:,}")
    lines.append(f"- **Debt:** ${s['debt']:,}")
    lines.append(f"- **Net worth:** ${nw:,}")
    lines.append(f"- **Grade:** {grade}")
    lines.append("")
    with open("PLAYTHROUGH.md", "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
