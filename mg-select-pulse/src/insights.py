"""
Forward-looking and trend views for MG Select Pulse: trends, cohort funnels,
speed, rep scorecard, pipeline, booking cancellations, finance mix, stock and
the daily action list.

Pure pandas, built on the same filter functions and definitions as
src/analytics.py, so every number here is consistent with the rest of the page:
  leads / opportunities  -> Created On
  test drives            -> First Test Drive Date Completed on
  bookings               -> Booking Date
  retail                 -> TR DATE (retail register)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd

from src import analytics as an

CANCELLED_STATUSES = ["Booking Cancelled", "Intend to Cancel"]
DELIVERED_STATUSES = ["Delivered", "Invoiced"]
TREND_METRICS = ["Leads", "Opportunities", "Test drives", "Bookings", "Retail"]


# --------------------------------------------------------------------------- #
# Period helpers
# --------------------------------------------------------------------------- #

def months_between(start: date, end: date) -> list[pd.Period]:
    return list(pd.period_range(pd.Period(start, "M"), pd.Period(end, "M"), freq="M"))


def trend_months(start: date, end: date, min_months: int = 3, data_start: date | None = None) -> list[pd.Period]:
    """The selected months, extended backwards so at least `min_months` show
    (e.g. picking September shows July, August and September)."""
    first, last = pd.Period(start, "M"), pd.Period(end, "M")
    first = min(first, last - (min_months - 1))
    if data_start is not None:
        first = max(first, pd.Period(data_start, "M"))
    return list(pd.period_range(first, last, freq="M"))


def _with_dates(f: an.Filters, start, end) -> an.Filters:
    return replace(f, start=pd.Timestamp(start).date(), end=pd.Timestamp(end).date())


# --------------------------------------------------------------------------- #
# Counts for any period (same definitions as the funnel)
# --------------------------------------------------------------------------- #

def bookings_in_period(opps: pd.DataFrame, f: an.Filters) -> pd.DataFrame:
    if "Booking Date" not in opps:
        return opps.iloc[0:0]
    return an.filter_opportunities(opps[opps["Booking Date"].notna()], f, date_column="Booking Date")


def period_counts(leads, opps, retail, f: an.Filters) -> dict[str, int]:
    return {
        "Leads": len(an.filter_leads(leads, f)),
        "Opportunities": len(an.filter_opportunities(opps, f)),
        "Test drives": len(an.filter_test_drives(opps, f)),
        "Bookings": len(bookings_in_period(opps, f)),
        "Retail": len(an.filter_retail(retail, f)),
    }


def trend_table(leads, opps, retail, f: an.Filters, months: list[pd.Period]) -> pd.DataFrame:
    """Month x metric counts; Source/Channel/Model/Rep filters from `f` apply."""
    rows = []
    for m in months:
        c = period_counts(leads, opps, retail, _with_dates(f, m.start_time, m.end_time))
        rows.append({"Month": m, **c})
    return pd.DataFrame(rows)


def compare_periods(leads, opps, retail, f: an.Filters) -> pd.DataFrame:
    """Selected period vs the period just before it (same length) vs the same
    dates last year."""
    start, end = pd.Timestamp(f.start), pd.Timestamp(f.end)
    length = (end - start).days + 1
    # Whole months compare with whole months (Sep vs Aug), otherwise same number of days.
    whole_months = start.day == 1 and (end + pd.Timedelta(days=1)).day == 1
    if whole_months:
        n = len(months_between(start.date(), end.date()))
        prev_start = (pd.Period(start, "M") - n).start_time
        prev_end = (pd.Period(start, "M") - 1).end_time.normalize()
    else:
        prev_end = start - pd.Timedelta(days=1)
        prev_start = prev_end - pd.Timedelta(days=length - 1)
    ly_start, ly_end = start - pd.DateOffset(years=1), end - pd.DateOffset(years=1)
    cur = period_counts(leads, opps, retail, f)
    prev = period_counts(leads, opps, retail, _with_dates(f, prev_start, prev_end))
    ly = period_counts(leads, opps, retail, _with_dates(f, ly_start, ly_end))
    out = pd.DataFrame({"Metric": TREND_METRICS,
                        "This period": [cur[m] for m in TREND_METRICS],
                        "Previous period": [prev[m] for m in TREND_METRICS],
                        "Same period last year": [ly[m] for m in TREND_METRICS]})
    out.attrs["prev_label"] = f"{prev_start:%d %b} – {prev_end:%d %b %Y}"
    out.attrs["ly_label"] = f"{ly_start:%d %b} – {ly_end:%d %b %Y}"
    return out


def pct_change(new: float, old: float) -> float | None:
    return None if not old else (new - old) / old


# --------------------------------------------------------------------------- #
# Cohort funnels — follow the same group through every stage
# --------------------------------------------------------------------------- #

def _opp_outcomes(opps: pd.DataFrame) -> pd.DataFrame:
    o = opps[["ID", "Status", "test_drive_done", "Booking Date"]].copy()
    o["booked"] = o["Booking Date"].notna()
    o["cancelled"] = o["Status"].isin(CANCELLED_STATUSES)
    o["delivered"] = o["Status"].isin(DELIVERED_STATUSES)
    return o.set_index("ID")


def lead_cohort(leads, opps, f: an.Filters, months: list[pd.Period]) -> pd.DataFrame:
    """For leads created in each month: how many have since become an
    opportunity, had a test drive, booked and been delivered/invoiced.
    Follows each lead through its ACS_OpportunityId, so no stage can exceed 100%."""
    out = _opp_outcomes(opps)
    rows = []
    for m in months:
        L = an.filter_leads(leads, _with_dates(f, m.start_time, m.end_time))
        linked = L["opportunity_id"].map(lambda i: i if i in out.index else None).dropna()
        o = out.loc[linked.values] if len(linked) else out.iloc[0:0]
        rows.append({"Month": m, "Leads": len(L), "Became opportunity": len(o),
                     "Test drive": int(o["test_drive_done"].sum()), "Booked": int(o["booked"].sum()),
                     "Delivered / invoiced": int(o["delivered"].sum())})
    return pd.DataFrame(rows)


def opportunity_cohort(opps, f: an.Filters, months: list[pd.Period]) -> pd.DataFrame:
    """For opportunities created in each month (incl. walk-ins with no lead):
    test drive, booked, delivered/invoiced, lost, cancelled, still open."""
    rows = []
    for m in months:
        O = an.filter_opportunities(opps, _with_dates(f, m.start_time, m.end_time))
        rows.append({"Month": m, "Opportunities": len(O),
                     "Test drive": int(O["test_drive_done"].sum()),
                     "Booked": int(O["Booking Date"].notna().sum()) if "Booking Date" in O else 0,
                     "Delivered / invoiced": int(O["Status"].isin(DELIVERED_STATUSES).sum()),
                     "Lost": int(O["Status"].eq("Lost").sum()),
                     "Booking cancelled": int(O["Status"].isin(CANCELLED_STATUSES).sum()),
                     "Still open": int(O["Status"].eq(an.OPEN_STATUS).sum())})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Speed — how fast each step happens
# --------------------------------------------------------------------------- #

SPEED_STEPS = ["Lead → opportunity", "Opportunity → first test drive", "Opportunity → booking"]


def speed_frame(leads, opps, f: an.Filters) -> pd.DataFrame:
    """One row per step taken by records created in the period, with days taken,
    rep and source. Same-day = 0 days."""
    rows = []
    L = an.filter_leads(leads, f)
    o_created = opps.set_index("ID")["Created On"]
    for _, r in L[L["opportunity_id"].isin(o_created.index)].iterrows():
        d = (o_created[r["opportunity_id"]].normalize() - r["Created On"].normalize()).days
        rows.append({"Step": SPEED_STEPS[0], "Days": max(d, 0), "Rep": r["rep"], "Source": r["Source"]})
    O = an.filter_opportunities(opps, f)
    td = O.dropna(subset=["First Test Drive Date Completed on"])
    for _, r in td.iterrows():
        d = (r["First Test Drive Date Completed on"].normalize() - r["Created On"].normalize()).days
        rows.append({"Step": SPEED_STEPS[1], "Days": max(d, 0), "Rep": r["rep"], "Source": r["Source"]})
    bk = O.dropna(subset=["Booking Date"]) if "Booking Date" in O else O.iloc[0:0]
    for _, r in bk.iterrows():
        d = (r["Booking Date"].normalize() - r["Created On"].normalize()).days
        rows.append({"Step": SPEED_STEPS[2], "Days": max(d, 0), "Rep": r["rep"], "Source": r["Source"]})
    return pd.DataFrame(rows, columns=["Step", "Days", "Rep", "Source"])


def speed_summary(sf: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for step in SPEED_STEPS:
        d = sf.loc[sf["Step"] == step, "Days"]
        rows.append({"Step": step, "Count": len(d),
                     "Median days": float(d.median()) if len(d) else None,
                     "Same day %": (d.eq(0).mean() * 100) if len(d) else None,
                     "Within 3 days %": (d.le(3).mean() * 100) if len(d) else None,
                     "Over 7 days %": (d.gt(7).mean() * 100) if len(d) else None})
    return pd.DataFrame(rows)


def speed_by(sf: pd.DataFrame, by: str) -> pd.DataFrame:
    if sf.empty:
        return pd.DataFrame(columns=[by])
    t = sf.groupby([by, "Step"])["Days"].median().unstack()
    n = sf.groupby([by, "Step"]).size().unstack().fillna(0).astype(int)
    out = pd.DataFrame(index=t.index)
    for step in SPEED_STEPS:
        if step in t:
            out[f"{step} (median days)"] = t[step]
            out[f"{step} (count)"] = n[step]
    return out.reset_index()


# --------------------------------------------------------------------------- #
# Rep scorecard
# --------------------------------------------------------------------------- #

def rep_scorecard(leads, opps, retail, f: an.Filters, as_of: pd.Timestamp) -> pd.DataFrame:
    O = an.filter_opportunities(opps, f)
    TD = an.filter_test_drives(opps, f)
    B = bookings_in_period(opps, f)
    R = an.filter_retail(retail, f)
    fu = an.follow_up_view(an.filter_opportunities(opps, f), as_of, 7)
    reps = sorted(set(O["rep"].dropna()) | set(R["rep"].dropna()) | set(B["rep"].dropna()))
    rows = []
    for rep in reps:
        o, r, b = O[O["rep"] == rep], R[R["rep"] == rep], B[B["rep"] == rep]
        acc = r["accessories_amount"]
        ew = r["ew_flag"].dropna()
        rows.append({
            "Sales rep": rep,
            "Opportunities": len(o),
            "Test drive %": (o["test_drive_done"].mean() * 100) if len(o) else None,
            "Test drives done": int((TD["rep"] == rep).sum()),
            "Bookings": len(b),
            "Booking cancelled": int(b["Status"].isin(CANCELLED_STATUSES).sum()),
            "Retail": len(r),
            "Accessories / car (₹ L)": (acc.mean() / 1e5) if acc.notna().any() else None,
            "EW %": (ew.mean() * 100) if len(ew) else None,
            "Lost": int(o["Status"].eq("Lost").sum()),
            "Never touched (open)": int(((fu["rep"] == rep) & fu["follow_up_state"].eq(an.TOUCH_NEVER)).sum()),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["Retail", "Bookings", "Opportunities"], ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Pipeline — open opportunities and when they're expected to buy
# --------------------------------------------------------------------------- #

EXPECT_ORDER = ["Overdue (date passed)", "Next 7 days", "8–30 days", "31–90 days", "Later", "No date"]
AGE_ORDER = ["0–7 days", "8–30 days", "31–60 days", "61–90 days", "90+ days"]


def pipeline_view(opps: pd.DataFrame, f: an.Filters, as_of: pd.Timestamp) -> pd.DataFrame:
    """Every open opportunity (any creation date — the pipeline is today's
    state), with Source/Channel/Model/Rep filters applied."""
    o = opps[opps["Status"].eq(an.OPEN_STATUS)]
    o = o[an._isin_if(o, "Source", f.sources) & an._isin_if(o, "Channel", f.channels)
          & an._isin_if(o, "model", f.models) & an._isin_if(o, "rep", f.reps)].copy()
    exp = o.get("Expected Purchase Date_Score", pd.Series(pd.NaT, index=o.index))
    days_to = (exp.dt.normalize() - as_of).dt.days
    o["Expected"] = pd.cut(days_to, [-10**6, -1, 7, 30, 90, 10**6], labels=EXPECT_ORDER[:5]).astype(object)
    o.loc[exp.isna(), "Expected"] = "No date"
    age = (as_of - o["Created On"].dt.normalize()).dt.days
    o["Age"] = pd.cut(age, [-1, 7, 30, 60, 90, 10**6], labels=AGE_ORDER).astype(object)
    o["Qualification"] = o.get("ZQualificationLevel", pd.Series(index=o.index, dtype=object)).fillna("Not set")
    o["Days open"] = age
    return o


# --------------------------------------------------------------------------- #
# Booking cancellations
# --------------------------------------------------------------------------- #

def cancellation_trend(opps, f: an.Filters, months: list[pd.Period]) -> pd.DataFrame:
    """Bookings made each month (Booking Date) and how many of them are now
    cancelled / intend-to-cancel."""
    rows = []
    for m in months:
        b = bookings_in_period(opps, _with_dates(f, m.start_time, m.end_time))
        c = int(b["Status"].isin(CANCELLED_STATUSES).sum())
        rows.append({"Month": m, "Bookings": len(b), "Cancelled": c,
                     "Cancellation %": (c / len(b) * 100) if len(b) else None})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Finance & insurance (retail register)
# --------------------------------------------------------------------------- #

FINANCE_LABELS = {"IN": "In-house finance", "OUT": "Outside finance", "CASH": "Cash", "LEASING": "Leasing"}


def finance_mix(retail: pd.DataFrame) -> dict:
    fin = retail.get("FINANCE", pd.Series(index=retail.index, dtype=object)).fillna("").str.strip().str.upper()
    ins = retail.get("Insurance", pd.Series(index=retail.index, dtype=object)).fillna("").str.strip().str.upper()
    known_fin = fin[fin != ""]
    known_ins = ins[ins != ""]
    counts = known_fin.map(lambda v: FINANCE_LABELS.get(v, v.title())).value_counts()
    loans = known_fin.isin(["IN", "OUT"])
    return {
        "units": len(retail),
        "finance_known": len(known_fin),
        "finance_counts": counts,
        "in_house_share_of_all": (known_fin.eq("IN").mean()) if len(known_fin) else None,
        "in_house_share_of_loans": (known_fin[loans].eq("IN").mean()) if loans.any() else None,
        "loans": int(loans.sum()),
        "insurance_known": len(known_ins),
        "insurance_in_house": (known_ins.eq("IN").mean()) if len(known_ins) else None,
    }


def finance_by(retail: pd.DataFrame, by: str) -> pd.DataFrame:
    if retail.empty:
        return pd.DataFrame(columns=[by])
    r = retail.assign(_fin=retail["FINANCE"].fillna("").str.strip().str.upper(),
                      _ins=retail["Insurance"].fillna("").str.strip().str.upper())
    g = r.groupby(by)
    out = pd.DataFrame({
        "Retail": g.size(),
        "In-house finance": g["_fin"].apply(lambda s: int(s.eq("IN").sum())),
        "Outside finance": g["_fin"].apply(lambda s: int(s.eq("OUT").sum())),
        "Cash": g["_fin"].apply(lambda s: int(s.eq("CASH").sum())),
        "In-house finance % of loans": g["_fin"].apply(
            lambda s: s[s.isin(["IN", "OUT"])].eq("IN").mean() * 100 if s.isin(["IN", "OUT"]).any() else None),
        "In-house insurance %": g["_ins"].apply(lambda s: s[s != ""].eq("IN").mean() * 100 if (s != "").any() else None),
    })
    return out.reset_index().sort_values("Retail", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Stock
# --------------------------------------------------------------------------- #

STOCK_AGE_ORDER = ["0–30 days", "31–60 days", "61–90 days", "90+ days", "Not received yet"]
SALE_STATUS_LABELS = {"BBND": "Booked, not delivered", "FREE": "Free (unsold)", "ALLOCATED": "Allocated",
                      "NOT DISPATCHED": "Not dispatched", "INTRANSIT": "In transit"}


def stock_view(stock: pd.DataFrame, f: an.Filters) -> pd.DataFrame:
    s = stock.copy()
    if f.models:
        s = s[s["Model"].isin(f.models)]
    if f.reps:
        s = s[s["Sales Manager"].isin(f.reps) | s["Sales Manager"].isna()]
    s["Sale status (plain)"] = s["Sale status"].map(lambda v: SALE_STATUS_LABELS.get(v, (v or "—").title()))
    age = s["Age since received (days)"]
    s["Age band"] = pd.cut(age, [-1, 30, 60, 90, 10**6], labels=STOCK_AGE_ORDER[:4]).astype(object)
    s.loc[age.isna(), "Age band"] = "Not received yet"
    return s


# --------------------------------------------------------------------------- #
# Today's actions
# --------------------------------------------------------------------------- #

def todays_actions(opps, retail, stock, f: an.Filters, as_of: pd.Timestamp) -> list[dict]:
    """A short, ranked list of things to act on today. Each item: count, title,
    what to do, and which tab has the detail."""
    pipe = pipeline_view(opps, f, as_of)
    never = pipe[pipe["Last Follow up Activity Date"].isna() & pipe["Last Follow up Activity"].isna()
                 & pipe["Post Activity Notes of the Last Completed Activity"].isna()]
    hot_never = never[never["Qualification"].eq("Hot")]
    due_week = pipe[pipe["Expected"].eq("Next 7 days") & pipe["Qualification"].eq("Hot")]
    overdue_fu = (pipe[pipe["Open Activity Due Date"].dt.normalize().lt(as_of) & pipe["Qualification"].eq("Hot")]
                  if "Open Activity Due Date" in pipe else pipe.iloc[0:0])
    stale = pipe[pipe["Expected"].eq("Overdue (date passed)")]
    items = [
        {"count": len(hot_never), "title": "Hot opportunities never contacted",
         "action": "Call these first — no call, visit or note has been logged yet.", "where": "Team → Untouched"},
        {"count": len(due_week), "title": "Hot opportunities expected to buy in the next 7 days",
         "action": "Confirm test drive / booking appointments.", "where": "Pipeline"},
        {"count": len(overdue_fu), "title": "Hot opportunities with a follow-up past its due date",
         "action": "Complete or reschedule the scheduled call / visit.", "where": "Team → Untouched"},
        {"count": len(stale), "title": "Open opportunities with an expected purchase date already passed",
         "action": "Update the expected date in C4C, or close the opportunity.", "where": "Pipeline"},
    ]
    if stock is not None and len(stock):
        s = stock_view(stock, f)
        free_old = s[s["Sale status"].eq("FREE") & s["Age since received (days)"].gt(60)]
        bbnd_due = s[s["Sale status"].eq("BBND") & s["Balance ₹"].gt(0)]
        items += [
            {"count": len(free_old), "title": "Unsold cars in stock for more than 60 days",
             "action": "Push these with offers / priority allocation.", "where": "Retail & Stock"},
            {"count": len(bbnd_due), "title": "Booked cars waiting for balance payment",
             "action": f"₹{bbnd_due['Balance ₹'].sum() / 1e7:.2f} Cr to collect — chase payment dates.",
             "where": "Retail & Stock"},
        ]
    return items
