"""
All metric, KPI and funnel calculations for MG Select Pulse.

Pure pandas — no Streamlit calls here, so everything is testable from a
plain Python shell.

Date-filter convention (shown on the dashboard too): every dataset is filtered
on its OWN activity date — Leads and Opportunities on "Created On", Retail on
"TR DATE". This is stage activity within a period, not a followed cohort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

# Opportunity statuses that count as "Ordered" in the default (spec) definition.
ORDERED_BASE_STATUSES = ["Booked"]
# "Booking Confirmed" is added only if it has real volume in the export.
BOOKING_CONFIRMED_MIN_ROWS = 5
DELIVERED_STATUS = "Delivered"
# Date used to place C4C "Delivered" opportunities in a period, first available wins.
DELIVERY_DATE_COLUMNS = ["Actual Delivery Date", "Invoice Date_V"]
# Test drives are dated by when the drive happened, not when the opportunity was created.
TEST_DRIVE_DATE_COLUMN = "First Test Drive Date Completed on"

ORDERED_MODE_STATUS = "status"         # Status == Booked (+ Booking Confirmed if volume)
ORDERED_MODE_BOOKING_DATE = "booking"  # any opportunity with a Booking Date, not cancelled


@dataclass
class Filters:
    start: date
    end: date
    sources: list[str] = field(default_factory=list)   # empty list = all
    channels: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    reps: list[str] = field(default_factory=list)
    ordered_mode: str = ORDERED_MODE_STATUS


def safe_rate(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #

def _in_range(dates: pd.Series, f: Filters) -> pd.Series:
    d = dates.dt.normalize()
    return d.ge(pd.Timestamp(f.start)) & d.le(pd.Timestamp(f.end))


def _isin_if(df: pd.DataFrame, column: str, selected: list[str]) -> pd.Series:
    if not selected or column not in df:
        return pd.Series(True, index=df.index)
    return df[column].isin(selected)


def filter_leads(leads: pd.DataFrame, f: Filters) -> pd.DataFrame:
    mask = (
        _in_range(leads["Created On"], f)
        & _isin_if(leads, "Source", f.sources)
        & _isin_if(leads, "Channel", f.channels)
        & _isin_if(leads, "model", f.models)
        & _isin_if(leads, "rep", f.reps)      # lead Owner stands in for the rep
    )
    return leads[mask]


def filter_opportunities(opps: pd.DataFrame, f: Filters, date_column: str = "Created On") -> pd.DataFrame:
    mask = (
        _in_range(opps[date_column], f)
        & _isin_if(opps, "Source", f.sources)
        & _isin_if(opps, "Channel", f.channels)
        & _isin_if(opps, "model", f.models)
        & _isin_if(opps, "rep", f.reps)
    )
    return opps[mask]


def filter_test_drives(opps: pd.DataFrame, f: Filters) -> pd.DataFrame:
    """Opportunities whose test drive was completed inside the period (by test-drive
    date), with Source/Channel/Model/Rep filters. Falls back to Created On +
    Test Drive Completed = Yes if the export has no test-drive date column."""
    if TEST_DRIVE_DATE_COLUMN in opps:
        done = opps[opps["test_drive_done"] & opps[TEST_DRIVE_DATE_COLUMN].notna()]
        return filter_opportunities(done, f, date_column=TEST_DRIVE_DATE_COLUMN)
    return filter_opportunities(opps[opps["test_drive_done"]], f)


def filter_retail(retail: pd.DataFrame, f: Filters) -> pd.DataFrame:
    """TR DATE in range. Rows whose TR DATE couldn't be parsed fall back to
    their Month label: included if that month overlaps the selected range.
    The retail register has no Channel column, so the Channel filter can't apply."""
    month_start = retail["month"].map(lambda m: m.start_time if m is not None else pd.NaT)
    month_end = retail["month"].map(lambda m: m.end_time if m is not None else pd.NaT)
    by_month = (
        retail["tr_date"].isna()
        & month_end.ge(pd.Timestamp(f.start))
        & month_start.le(pd.Timestamp(f.end) + pd.Timedelta(days=1))
    )
    mask = (
        (_in_range(retail["tr_date"], f) | by_month)
        & _isin_if(retail, "Source", f.sources)
        & _isin_if(retail, "model", f.models)
        & _isin_if(retail, "rep", f.reps)
    )
    return retail[mask]


# --------------------------------------------------------------------------- #
# Funnel
# --------------------------------------------------------------------------- #

def ordered_statuses(all_opps: pd.DataFrame) -> list[str]:
    """Booked, plus Booking Confirmed only if it has real volume in the export
    (decided on the whole file so the definition doesn't flip with filters)."""
    statuses = list(ORDERED_BASE_STATUSES)
    if int(all_opps["Status"].eq("Booking Confirmed").sum()) >= BOOKING_CONFIRMED_MIN_ROWS:
        statuses.append("Booking Confirmed")
    return statuses


def ordered_mask(opps: pd.DataFrame, mode: str, statuses: list[str]) -> pd.Series:
    if mode == ORDERED_MODE_BOOKING_DATE and "Booking Date" in opps:
        return opps["Booking Date"].notna() & opps["Status"].ne("Booking Cancelled")
    return opps["Status"].isin(statuses)


@dataclass
class Funnel:
    leads: int
    opportunities: int
    test_drives: int
    ordered: int
    retail: int
    ordered_definition: str

    @property
    def stages(self) -> list[tuple[str, int]]:
        return [
            ("Leads", self.leads),
            ("Opportunities", self.opportunities),
            ("Test Drive Completed", self.test_drives),
            ("Ordered", self.ordered),
            ("Retail", self.retail),
        ]

    @property
    def rates(self) -> dict[str, float | None]:
        return {
            "Lead → Opportunity": safe_rate(self.opportunities, self.leads),
            "Opportunity → Test Drive": safe_rate(self.test_drives, self.opportunities),
            "Test Drive → Ordered": safe_rate(self.ordered, self.test_drives),
            "Ordered → Retail": safe_rate(self.retail, self.ordered),
        }

    def conversion_matrix(self) -> pd.DataFrame:
        """Every stage-to-later-stage rate: one row per (from, to) pair, with the
        counts behind it so each percentage can be checked by hand."""
        stages = self.stages
        rows = []
        for i, (from_name, from_n) in enumerate(stages):
            for to_name, to_n in stages[i + 1:]:
                rows.append({
                    "From": from_name, "To": to_name,
                    "From count": from_n, "To count": to_n,
                    "Rate %": (safe_rate(to_n, from_n) or 0) * 100 if from_n else None,
                })
        return pd.DataFrame(rows)


def build_funnel(leads: pd.DataFrame, opps: pd.DataFrame, test_drives: pd.DataFrame,
                 retail: pd.DataFrame, mode: str, statuses: list[str]) -> Funnel:
    """All inputs already filtered (test_drives via filter_test_drives)."""
    if mode == ORDERED_MODE_BOOKING_DATE:
        definition = "Opportunities with a Booking Date (booked at any point), excluding Booking Cancelled"
    else:
        definition = "Opportunity Status = " + " or ".join(f"“{s}”" for s in statuses)
    return Funnel(
        leads=len(leads),
        opportunities=len(opps),
        test_drives=len(test_drives),
        ordered=int(ordered_mask(opps, mode, statuses).sum()),
        retail=len(retail),
        ordered_definition=definition,
    )


def delivered_crosscheck(all_opps: pd.DataFrame, retail_filtered: pd.DataFrame, f: Filters) -> dict:
    """Opportunities.Status = Delivered vs the retail register, same period.

    Delivered opportunities are dated by Actual Delivery Date (falls back to
    Created On if that column is absent) so both sides use a delivery date."""
    date_col = next((c for c in DELIVERY_DATE_COLUMNS if c in all_opps), "Created On")
    delivered = all_opps[all_opps["Status"].eq(DELIVERED_STATUS)]
    delivered = filter_opportunities(delivered.dropna(subset=[date_col]), f, date_column=date_col)
    opp_count, retail_count = len(delivered), len(retail_filtered)
    return {
        "opp_delivered": opp_count,
        "retail": retail_count,
        "difference": retail_count - opp_count,
        "coverage": safe_rate(opp_count, retail_count),
        "date_column": date_col,
    }


# --------------------------------------------------------------------------- #
# Lead routing (CRE vs sales managers)
# --------------------------------------------------------------------------- #

LEAD_STATUSES = ["Qualified", "Hold", "Cancelled", "Fresh"]


def _status_counts(g) -> pd.DataFrame:
    counts = g["Status"].value_counts().unstack(fill_value=0)
    for st in LEAD_STATUSES:
        if st not in counts:
            counts[st] = 0
    other = [c for c in counts.columns if c not in LEAD_STATUSES]
    counts = counts[LEAD_STATUSES + other]
    return counts


def lead_routing(leads: pd.DataFrame) -> pd.DataFrame:
    """Per route (by the lead's CURRENT Owner): leads, share, and status mix.

    C4C's Owner is the current owner — a lead the CRE qualifies and passes on
    shows the sales manager as Owner — so a per-route conversion rate would
    understate the CRE. Status mix is shown instead."""
    if leads.empty:
        return pd.DataFrame(columns=["Route", "Leads", "Share of leads %"] + LEAD_STATUSES)
    g = leads.groupby("lead_route")
    out = pd.DataFrame({"Leads": g.size()})
    out["Share of leads %"] = out["Leads"] / len(leads) * 100
    out = out.join(_status_counts(g))
    return out.reset_index().rename(columns={"lead_route": "Route"})


def cre_handovers(leads: pd.DataFrame, cre_owners: list[str]) -> int:
    """Leads created by a CRE that are now owned by a sales manager — a lower
    bound on CRE → sales-manager hand-offs (many leads have no Created By)."""
    if "Created By" not in leads:
        return 0
    cre = {" ".join(n.split()).title() for n in cre_owners}
    created_by = leads["Created By"].map(lambda v: " ".join(str(v).split()).title() if isinstance(v, str) else None)
    return int((created_by.isin(cre) & ~leads["rep"].isin(cre)).sum())


def leads_by_owner(leads: pd.DataFrame) -> pd.DataFrame:
    """Per current lead Owner within each route: leads and status mix."""
    if leads.empty:
        return pd.DataFrame(columns=["Route", "Owner", "Leads"] + LEAD_STATUSES)
    g = leads.groupby(["lead_route", "rep"])
    out = pd.DataFrame({"Leads": g.size()}).join(_status_counts(g))
    out = out.reset_index().rename(columns={"lead_route": "Route", "rep": "Owner"})
    return out.sort_values(["Route", "Leads"], ascending=[True, False]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Untouched opportunities (follow-up hygiene)
# --------------------------------------------------------------------------- #

OPEN_STATUS = "Under Follow-up"
FOLLOW_UP_DATE = "Last Follow up Activity Date"
FOLLOW_UP_TYPE = "Last Follow up Activity"
FOLLOW_UP_NOTES = "Post Activity Notes of the Last Completed Activity"
NEXT_DUE = "Open Activity Due Date"

TOUCH_NEVER = "Never touched"
TOUCH_COLD = "Going cold"
TOUCH_ACTIVE = "Active"


def export_as_of(opps: pd.DataFrame) -> pd.Timestamp:
    """The data's snapshot date: the latest Created On (the last day dumped).
    Ages are measured from here, not from today, so data that hasn't been
    refreshed yet isn't made to look neglected."""
    return opps["Created On"].max().normalize()


def follow_up_view(opps: pd.DataFrame, as_of: pd.Timestamp, cold_after_days: int) -> pd.DataFrame:
    """Open opportunities with their follow-up state.

    Never touched : no follow-up date, type or notes logged at all
    Going cold    : followed up before, but not in the last `cold_after_days` days
    Active        : followed up within `cold_after_days` days
    Overdue       : (separate flag) the scheduled next activity's due date has passed
    """
    op = opps[opps["Status"].eq(OPEN_STATUS)].copy()
    blank = lambda c: op[c].isna() if c in op else pd.Series(True, index=op.index)
    never = blank(FOLLOW_UP_DATE) & blank(FOLLOW_UP_TYPE) & blank(FOLLOW_UP_NOTES)
    last = op[FOLLOW_UP_DATE].dt.normalize() if FOLLOW_UP_DATE in op else pd.Series(pd.NaT, index=op.index)
    op["days_open"] = (as_of - op["Created On"].dt.normalize()).dt.days
    op["days_since_follow_up"] = (as_of - last).dt.days
    cold = ~never & (op["days_since_follow_up"].isna() | op["days_since_follow_up"].gt(cold_after_days))
    op["follow_up_state"] = TOUCH_ACTIVE
    op.loc[cold, "follow_up_state"] = TOUCH_COLD
    op.loc[never, "follow_up_state"] = TOUCH_NEVER
    due = op[NEXT_DUE].dt.normalize() if NEXT_DUE in op else pd.Series(pd.NaT, index=op.index)
    op["overdue"] = due.lt(as_of)
    op["hot"] = op.get("ZQualificationLevel", pd.Series(index=op.index, dtype=object)).eq("Hot")
    return op


def follow_up_by_rep(view: pd.DataFrame) -> pd.DataFrame:
    if view.empty:
        return pd.DataFrame(columns=["Sales rep", "Open", "Never touched", "Hot never touched",
                                     "Going cold", "Overdue", "Oldest untouched (days)"])
    never = view["follow_up_state"].eq(TOUCH_NEVER)
    g = view.assign(never=never, hot_never=never & view["hot"],
                    cold=view["follow_up_state"].eq(TOUCH_COLD),
                    never_age=view["days_open"].where(never)).groupby("rep")
    out = pd.DataFrame({
        "Open": g.size(),
        "Never touched": g["never"].sum().astype(int),
        "Hot never touched": g["hot_never"].sum().astype(int),
        "Going cold": g["cold"].sum().astype(int),
        "Overdue": g["overdue"].sum().astype(int),
        "Oldest untouched (days)": g["never_age"].max(),
    }).reset_index().rename(columns={"rep": "Sales rep"})
    return out.sort_values(["Never touched", "Going cold"], ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Accessories & value-adds
# --------------------------------------------------------------------------- #

def _attach_rate(flags: pd.Series) -> float | None:
    known = flags.dropna()
    return safe_rate(int(known.sum()), len(known))


def value_add_kpis(retail: pd.DataFrame) -> dict:
    """Accessories figures use only units with an ACCESSORIES entry (blank = not
    recorded); attach rates use only units with a Yes/No entry."""
    units = len(retail)
    acc = retail["accessories_amount"]
    total = float(acc.sum())  # NaN (not recorded) is skipped
    acc_known = int(acc.notna().sum())
    return {
        "units": units,
        "accessories_total": total,
        "accessories_known": acc_known,
        "accessories_avg": safe_rate(total, acc_known),
        "units_with_accessories": int(acc.gt(0).sum()),
        "ew_rate": _attach_rate(retail["ew_flag"]),
        "amc_rate": _attach_rate(retail["amc_flag"]),
        "ppf_rate": _attach_rate(retail["ppf_flag"]),
        "ew_known": int(retail["ew_flag"].notna().sum()),
        "amc_known": int(retail["amc_flag"].notna().sum()),
        "ppf_known": int(retail["ppf_flag"].notna().sum()),
    }


def value_add_breakdown(retail: pd.DataFrame, by: str) -> pd.DataFrame:
    """One row per `by` value: units, accessories revenue/average and attach rates."""
    if retail.empty:
        return pd.DataFrame(columns=[by, "Units", "Accessories ₹", "Avg ₹ / unit", "EW %", "AMC %", "PPF %"])
    g = retail.groupby(by, dropna=False)
    out = pd.DataFrame({
        "Units": g.size(),
        "Accessories ₹": g["accessories_amount"].sum(),
        "Avg ₹ / unit": g["accessories_amount"].mean(),
        "EW %": g["ew_flag"].apply(_attach_rate),
        "AMC %": g["amc_flag"].apply(_attach_rate),
        "PPF %": g["ppf_flag"].apply(_attach_rate),
    }).reset_index()
    for col in ("EW %", "AMC %", "PPF %"):
        out[col] = pd.to_numeric(out[col]) * 100
    return out.sort_values("Accessories ₹", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Match quality
# --------------------------------------------------------------------------- #

def match_rate_by_month(retail: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Confident-match rate per retail month — older-month deliveries are more
    likely to come from opportunities created before the export window."""
    if retail.empty:
        return pd.DataFrame(columns=["Month", "Retail units", "Confident", "Possible", "Unmatched", "Confident %"])
    df = pd.DataFrame({"Month": retail["month"].astype(str), "band": matches.loc[retail.index, "match_band"]})
    t = pd.crosstab(df["Month"], df["band"])
    for b in ("Confident", "Possible", "Unmatched"):
        if b not in t:
            t[b] = 0
    t = t[["Confident", "Possible", "Unmatched"]]
    t.insert(0, "Retail units", t.sum(axis=1))
    t["Confident %"] = t["Confident"] / t["Retail units"] * 100
    t = t.reset_index()
    t["_order"] = pd.PeriodIndex(t["Month"], freq="M")
    return t.sort_values("_order").drop(columns="_order").reset_index(drop=True)
