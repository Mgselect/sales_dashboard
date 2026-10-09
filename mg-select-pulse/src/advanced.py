"""
Advanced, visual analytics for the Insights tab.

All built from the same opportunities / leads / stock data as the rest of the
dashboard. "Booked" = the opportunity has a Booking Date and wasn't cancelled.
Outcome-based views use *closed* opportunities only (not "Under Follow-up"),
so open ones that may still book don't drag rates down.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from src import analytics as an

CANCELLED = ["Booking Cancelled", "Intend to Cancel"]
MIN_GROUP = 15  # groups smaller than this are too noisy to show


def _closed_with_outcome(opps: pd.DataFrame) -> pd.DataFrame:
    c = opps[opps["Status"].ne(an.OPEN_STATUS)].copy()
    c["booked"] = c["Booking Date"].notna() & ~c["Status"].isin(CANCELLED) if "Booking Date" in c else False
    return c


# --------------------------------------------------------------------------- #
# 1. What drives bookings
# --------------------------------------------------------------------------- #

def booking_drivers(opps_f: pd.DataFrame) -> tuple[pd.DataFrame, float, int]:
    """Booking rate for each value of a few factors, from closed opportunities.
    Returns (rows: Factor, Group, Opportunities, Booking rate %), overall %, n."""
    c = _closed_with_outcome(opps_f)
    if c.empty:
        return pd.DataFrame(columns=["Factor", "Group", "Opportunities", "Booking rate %"]), 0.0, 0
    c["Test drive"] = c["test_drive_done"].map({True: "Test drive done", False: "No test drive"})
    c["How hot"] = c.get("ZQualificationLevel", pd.Series(index=c.index, dtype=object)).fillna("Not set")
    rows = []
    for factor in ["Test drive", "How hot", "Source"]:
        g = c.groupby(factor)["booked"].agg(["size", "mean"])
        for grp, r in g[g["size"] >= MIN_GROUP].iterrows():
            rows.append({"Factor": factor, "Group": grp, "Opportunities": int(r["size"]),
                         "Booking rate %": r["mean"] * 100})
    return pd.DataFrame(rows), c["booked"].mean() * 100, len(c)


# --------------------------------------------------------------------------- #
# 2. Source quality map
# --------------------------------------------------------------------------- #

def source_quality(opps_f: pd.DataFrame) -> pd.DataFrame:
    c = _closed_with_outcome(opps_f)
    allo = opps_f.groupby("Source").size().rename("Opportunities")
    g = c.groupby("Source")["booked"].agg(["size", "mean", "sum"]).rename(
        columns={"size": "Closed", "mean": "rate", "sum": "Bookings"})
    out = pd.concat([allo, g], axis=1).fillna(0).reset_index().rename(columns={"index": "Source"})
    out["Booking rate %"] = out["rate"] * 100
    out = out[out["Closed"] >= 5]
    return out.drop(columns="rate").sort_values("Opportunities", ascending=False)


# --------------------------------------------------------------------------- #
# 3. When do customers book?
# --------------------------------------------------------------------------- #

def time_to_book(opps_f: pd.DataFrame, max_days: int = 90) -> tuple[pd.DataFrame, int]:
    """Cumulative share of bookings made within N days of the opportunity being created."""
    b = opps_f[opps_f["Booking Date"].notna()] if "Booking Date" in opps_f else opps_f.iloc[0:0]
    d = (b["Booking Date"].dt.normalize() - b["Created On"].dt.normalize()).dt.days.clip(lower=0)
    if d.empty:
        return pd.DataFrame(columns=["Days", "Share %"]), 0
    days = np.arange(0, max_days + 1)
    return pd.DataFrame({"Days": days, "Share %": [(d <= x).mean() * 100 for x in days]}), len(d)


def same_day_bookings(opps_f: pd.DataFrame) -> pd.DataFrame:
    """Bookings made on the day the opportunity was created, per source."""
    b = opps_f[opps_f["Booking Date"].notna()] if "Booking Date" in opps_f else opps_f.iloc[0:0]
    same = (b["Booking Date"].dt.normalize() - b["Created On"].dt.normalize()).dt.days.le(0)
    g = pd.DataFrame({"Source": b["Source"].fillna("(blank)"), "same": same}).groupby("Source")["same"] \
        .agg(Bookings="size", Same_day="sum").rename(columns={"Same_day": "Same day"})
    g["Same day %"] = g["Same day"] / g["Bookings"] * 100
    return g.sort_values("Same day", ascending=False).reset_index()


# --------------------------------------------------------------------------- #
# 4. Lead calendar
# --------------------------------------------------------------------------- #

def lead_calendar(leads_f: pd.DataFrame, start, end) -> pd.DataFrame:
    """Leads per day, for a weekday x week heat-map."""
    days = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    counts = leads_f["Created On"].dt.normalize().value_counts()
    df = pd.DataFrame({"Date": days, "Leads": [int(counts.get(d, 0)) for d in days]})
    df["Weekday"] = df["Date"].dt.day_name().str[:3]
    df["Week of"] = (df["Date"] - pd.to_timedelta(df["Date"].dt.weekday, unit="D")).dt.strftime("%d %b")
    return df


# --------------------------------------------------------------------------- #
# 5. Fair rep comparison: actual vs expected bookings
# --------------------------------------------------------------------------- #

def rep_expected_vs_actual(opps_f: pd.DataFrame) -> pd.DataFrame:
    """Expected bookings = each closed opportunity's source booking rate, summed per
    rep. A rep above the diagonal books more than their lead mix would predict."""
    c = _closed_with_outcome(opps_f)
    if c.empty:
        return pd.DataFrame(columns=["Sales rep", "Closed", "Expected", "Actual"])
    src_rate = c.groupby("Source")["booked"].mean()
    c["expected"] = c["Source"].map(src_rate).fillna(c["booked"].mean())
    g = c.groupby("rep").agg(Closed=("booked", "size"), Expected=("expected", "sum"), Actual=("booked", "sum"))
    g = g[g["Closed"] >= 10].reset_index().rename(columns={"rep": "Sales rep"})
    g["Difference"] = g["Actual"] - g["Expected"]
    return g.sort_values("Difference", ascending=False)


# --------------------------------------------------------------------------- #
# 6. Colour demand vs stock
# --------------------------------------------------------------------------- #

COLOUR_WORDS = ["black", "white", "grey", "red", "yellow", "cyan", "beige", "blue", "green", "silver"]


def colour_family(v) -> str | None:
    text = str(v or "").lower().replace("gray", "grey")
    # "Pearl White with Black roof" -> White (body colour comes first)
    hits = [(text.find(w), w) for w in COLOUR_WORDS if re.search(rf"\b{w}\b", text)]
    return min(hits)[1].title() if hits else None


def colour_demand_vs_stock(opps_f: pd.DataFrame, stock: pd.DataFrame | None) -> pd.DataFrame:
    b = opps_f[opps_f["Booking Date"].notna() & ~opps_f["Status"].isin(CANCELLED)] if "Booking Date" in opps_f else opps_f.iloc[0:0]
    demand = b["Exterior Color 1"].map(colour_family).value_counts() if "Exterior Color 1" in b else pd.Series(dtype=int)
    out = pd.DataFrame({"Bookings": demand})
    if stock is not None and len(stock):
        unsold = stock[stock["Customer"].isna() & ~stock["Cancelled"]]
        out["Unsold stock"] = unsold["Colour"].map(colour_family).value_counts()
    out = out.fillna(0).astype(int)
    out.index.name = "Colour"
    return out.sort_values("Bookings", ascending=False).reset_index()


# --------------------------------------------------------------------------- #
# Patterns — combinations that change the booking chance
# --------------------------------------------------------------------------- #

MIN_CELL = 8  # fewer closed opportunities than this in a cell is too noisy to show

SOURCE_GROUPS = {
    "Walk-in": "Walk-in", "Referral": "Referral", "Elite Hub": "Elite",
    "Tele-in": "Phone", "Inbound_call": "Phone", "TELE-IN API": "Phone",
    "Digital": "Digital", "Dealer Digital": "Digital", "Avention": "Digital", "Event": "Event",
}
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def td_effect_by_source(opps_f: pd.DataFrame) -> pd.DataFrame:
    """Booking rate with and without a test drive, for each source."""
    c = _closed_with_outcome(opps_f)
    rows = []
    for src, g in c.groupby("Source"):
        yes, no = g[g["test_drive_done"]], g[~g["test_drive_done"]]
        if len(yes) < MIN_CELL or len(no) < MIN_CELL:
            continue
        rows.append({"Source": src, "With test drive %": yes["booked"].mean() * 100, "With n": len(yes),
                     "Without test drive %": no["booked"].mean() * 100, "Without n": len(no)})
    out = pd.DataFrame(rows, columns=["Source", "With test drive %", "With n", "Without test drive %", "Without n"])
    out["Lift"] = out["With test drive %"] - out["Without test drive %"]
    return out.sort_values("Lift", ascending=False)


TD_SPEED_BANDS = ["Same day", "1–3 days", "4–7 days", "8–30 days", "Over 30 days"]


def td_speed(opps_f: pd.DataFrame) -> pd.DataFrame:
    """Booking rate by how many days after the opportunity the first test drive happened
    (plus 'No test drive')."""
    c = _closed_with_outcome(opps_f)
    gap = (c["First Test Drive Date Completed on"].dt.normalize() - c["Created On"].dt.normalize()).dt.days \
        if "First Test Drive Date Completed on" in c else pd.Series(np.nan, index=c.index)
    band = pd.cut(gap.clip(lower=0), [-1, 0, 3, 7, 30, 10_000], labels=TD_SPEED_BANDS).astype(object)
    band = band.where(c["test_drive_done"] & gap.notna(), None)
    band = band.where(~c["test_drive_done"] | band.notna(), "Test drive, date unknown")
    band = band.fillna("No test drive")
    g = c.groupby(band)["booked"].agg(["size", "mean"])
    order = [b for b in TD_SPEED_BANDS + ["No test drive"] if b in g.index]
    g = g.loc[order]
    return pd.DataFrame({"When": g.index, "Opportunities": g["size"].values, "Booking rate %": g["mean"].values * 100})


def weekday_source(opps_f: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Booking rate (and counts) by weekday the opportunity was created × source group."""
    c = _closed_with_outcome(opps_f)
    c["Day"] = pd.Categorical(c["Created On"].dt.day_name().str[:3], WEEKDAYS)
    c["Source group"] = c["Source"].map(SOURCE_GROUPS).fillna("Other")
    rate = c.pivot_table(index="Source group", columns="Day", values="booked", aggfunc="mean", observed=False) * 100
    n = c.pivot_table(index="Source group", columns="Day", values="booked", aggfunc="size", observed=False).fillna(0)
    order = n.sum(axis=1).sort_values(ascending=False).index
    return rate.reindex(order).where(n.reindex(order) >= MIN_CELL), n.reindex(order)


def lost_traits(opps_f: pd.DataFrame) -> pd.DataFrame:
    """How common each trait is among opportunities that booked vs those that didn't."""
    c = _closed_with_outcome(opps_f)
    if c.empty:
        return pd.DataFrame(columns=["Trait", "Booked %", "Not booked %", "Gap"])
    hot = c.get("ZQualificationLevel", pd.Series(index=c.index, dtype=object)).isin(["Hot", "Warm"])
    traits = {
        "No test drive": ~c["test_drive_done"],
        "Not marked Hot / Warm": ~hot,
        "Digital or Event source": c["Source"].map(SOURCE_GROUPS).isin(["Digital", "Event"]),
        "Phone lead (Tele-in / Inbound call)": c["Source"].map(SOURCE_GROUPS).eq("Phone"),
        "Cyberster enquiry": c["model"].eq("MG_CYBERSTER"),
        "Created on a Monday": c["Created On"].dt.weekday.eq(0),
    }
    rows = []
    for name, mask in traits.items():
        b, nb = mask[c["booked"]], mask[~c["booked"]]
        rows.append({"Trait": name, "Booked %": b.mean() * 100 if len(b) else np.nan,
                     "Not booked %": nb.mean() * 100 if len(nb) else np.nan})
    out = pd.DataFrame(rows)
    out["Gap"] = out["Not booked %"] - out["Booked %"]
    return out.sort_values("Gap", ascending=False)


def rep_source_strengths(opps_f: pd.DataFrame, min_cell: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each rep's booking rate per source group (closed opportunities)."""
    c = _closed_with_outcome(opps_f)
    c["Source group"] = c["Source"].map(SOURCE_GROUPS).fillna("Other")
    rate = c.pivot_table(index="rep", columns="Source group", values="booked", aggfunc="mean") * 100
    n = c.pivot_table(index="rep", columns="Source group", values="booked", aggfunc="size").fillna(0)
    keep = n.sum(axis=1) >= 10
    cols = n.sum().sort_values(ascending=False).index
    rate, n = rate.loc[keep, cols], n.loc[keep, cols]
    order = n.sum(axis=1).sort_values(ascending=False).index
    rate = rate.reindex(order).where(n.reindex(order) >= min_cell).dropna(how="all")
    return rate, n.reindex(rate.index)


def biggest_patterns(opps_f: pd.DataFrame, top: int = 3, min_n: int = 30) -> list[dict]:
    """Scan simple splits (source, test drive, how hot, model, weekday, source × test drive)
    and return the groups whose booking rate is furthest from the average."""
    c = _closed_with_outcome(opps_f)
    if len(c) < min_n:
        return []
    avg = c["booked"].mean() * 100
    c["Test drive"] = c["test_drive_done"].map({True: "with a test drive", False: "without a test drive"})
    c["Weekday"] = "created on a " + c["Created On"].dt.day_name()
    c["Model "] = c["model"].str.replace("MG_", "").str.title() + " enquiries"
    c["How hot"] = c.get("ZQualificationLevel", pd.Series(index=c.index, dtype=object)).fillna("Not set") \
        .map(lambda v: f"marked {v}" if v != "Not set" else "with no Hot/Warm/Cold set")
    c["Source × TD"] = c["Source"].astype(str) + " leads " + c["Test drive"]
    found = []
    for col in ["Source", "Test drive", "How hot", "Model ", "Weekday", "Source × TD"]:
        g = c.groupby(col)["booked"].agg(["size", "mean"])
        for grp, r in g[g["size"] >= min_n].iterrows():
            rate = r["mean"] * 100
            label = f"{grp} opportunities" if col == "Source" else (
                f"Opportunities {grp}" if col in ("Test drive", "How hot", "Weekday") else grp)
            found.append({"Group": label, "Rate": rate, "n": int(r["size"]), "Diff": rate - avg, "Avg": avg,
                          "Split": col, "Score": abs(rate - avg) * np.sqrt(r["size"])})
    # Strongest effect, weighted by how many opportunities it covers; one per kind of split.
    found.sort(key=lambda d: d["Score"], reverse=True)
    picked: list[dict] = []
    for f_ in found:
        if any(f_["Split"] == p["Split"] or f_["Group"].split()[0] == p["Group"].split()[0] for p in picked):
            continue
        picked.append(f_)
        if len(picked) == top:
            break
    return picked
