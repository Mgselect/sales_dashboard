"""
Source-wise KPI benchmarks (L2O, TD%, O2B) and their comparison with actuals.

Edit BENCHMARKS / SOURCE_TO_BENCHMARK below when targets or source names change.

Definitions (for the selected period and filters, by source):
  L2O  = opportunities created ÷ leads created
  TD%  = share of those opportunities with Test Drive Completed = Yes
  O2B  = share of those opportunities booked at any point
         (Booking Date present, status not "Booking Cancelled")
"""

from __future__ import annotations

import pandas as pd

from src.analytics import ORDERED_MODE_BOOKING_DATE, ordered_mask, safe_rate

# Benchmark row -> (L2O, TD%, O2B), as fractions.
BENCHMARKS: dict[str, tuple[float, float, float]] = {
    "Walk-in":  (1.00, 0.90, 0.25),
    "Tele-in":  (1.00, 0.70, 0.15),
    "Elite":    (1.00, 0.35, 0.05),
    "Referral": (1.00, 1.00, 0.40),
    "Digital":  (0.25, 0.40, 0.07),
    "Event":    (0.70, 0.40, 0.05),
    "Tele-out": (0.90, 0.70, 0.15),
}
TOTAL_BENCHMARK = (0.50, 0.66, 0.18)
TOTAL_ROW = "Total"

# C4C Source value -> benchmark row. Sources not listed get no benchmark.
SOURCE_TO_BENCHMARK = {
    "Walk-in": "Walk-in",
    "Tele-in": "Tele-in",
    "Inbound_call": "Tele-in",      # inbound calls from print ads
    "Elite Hub": "Elite",
    "Referral": "Referral",
    "Digital": "Digital",
    "Dealer Digital": "Digital",    # Select Hyperlocal Meta / Google
    "Avention": "Digital",          # CarWale aggregator
    "Event": "Event",
}

KPIS = ["L2O", "TD%", "O2B"]


def _actuals(leads: pd.DataFrame, opps: pd.DataFrame) -> dict:
    n_leads, n_opps = len(leads), len(opps)
    n_td = int(opps["test_drive_done"].sum())
    n_booked = int(ordered_mask(opps, ORDERED_MODE_BOOKING_DATE, []).sum())
    return {
        "Leads": n_leads, "Opportunities": n_opps, "Test drives": n_td, "Booked": n_booked,
        "L2O": safe_rate(n_opps, n_leads),
        "TD%": safe_rate(n_td, n_opps),
        "O2B": safe_rate(n_booked, n_opps),
    }


def _row(name: str, bm: tuple[float, float, float] | None, leads: pd.DataFrame, opps: pd.DataFrame,
         sources: str) -> dict:
    row = {"Benchmark row": name, "Sources": sources, **_actuals(leads, opps)}
    for kpi, target in zip(KPIS, bm or (None, None, None)):
        row[f"{kpi} benchmark"] = target
        actual = row[kpi]
        row[f"{kpi} gap"] = (actual - target) if actual is not None and target is not None else None
    return row


def benchmark_table(leads: pd.DataFrame, opps: pd.DataFrame) -> pd.DataFrame:
    """One row per benchmark row (sources grouped as mapped), plus unmapped
    sources and a Total. Inputs are the already-filtered leads/opportunities."""
    lead_grp = leads["Source"].map(SOURCE_TO_BENCHMARK)
    opp_grp = opps["Source"].map(SOURCE_TO_BENCHMARK)
    rows = []
    for name, bm in BENCHMARKS.items():
        srcs = sorted(s for s, g in SOURCE_TO_BENCHMARK.items() if g == name)
        rows.append(_row(name, bm, leads[lead_grp == name], opps[opp_grp == name], " + ".join(srcs) or "—"))
    unmapped = sorted(set(leads.loc[lead_grp.isna(), "Source"].dropna()) | set(opps.loc[opp_grp.isna(), "Source"].dropna()))
    for src in unmapped:
        rows.append(_row(src, None, leads[leads["Source"] == src], opps[opps["Source"] == src], src + " (no benchmark)"))
    rows.append(_row(TOTAL_ROW, TOTAL_BENCHMARK, leads, opps, "All sources"))
    return pd.DataFrame(rows)


def source_detail(leads: pd.DataFrame, opps: pd.DataFrame) -> pd.DataFrame:
    """Actuals for each individual C4C Source, with the benchmark it is judged against."""
    srcs = sorted(set(leads["Source"].dropna()) | set(opps["Source"].dropna()))
    rows = []
    for src in srcs:
        grp = SOURCE_TO_BENCHMARK.get(src)
        rows.append({"Source": src, "Benchmark row": grp or "—",
                     **_actuals(leads[leads["Source"] == src], opps[opps["Source"] == src])})
    cols = ["Source", "Benchmark row", "Leads", "Opportunities", "Test drives", "Booked", *KPIS]
    return pd.DataFrame(rows, columns=cols)  # keeps its columns even when no source matches the filters
