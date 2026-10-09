"""
MG Select Pulse — Streamlit entrypoint.

    streamlit run app.py

Sections: data-source line, filters, yesterday, funnel + conversion rates, benchmarks vs actuals, lead
routing, untouched opportunities, lost opportunities, accessories & value-adds, and a warning if figures stop matching the source files.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

import importlib
import sys


def _reload_changed_project_modules() -> None:
    """A long-running dashboard keeps imported files in memory. When a file in
    src/ is updated, reload it (in dependency order) so old and new code never mix."""
    order = ["src.analytics", "src.sheets", "src.data_loader", "src.benchmarks", "src.loss_reasons",
             "src.matcher", "src.insights", "src.advanced", "src.reconcile"]
    for name in order:
        mod = sys.modules.get(name)
        if mod is None or not getattr(mod, "__file__", None):
            continue
        mtime = Path(mod.__file__).stat().st_mtime
        if mtime > getattr(mod, "_pulse_mtime", 0):  # changed, or loaded before this check existed
            importlib.reload(mod)
            mod._pulse_mtime = mtime


from src import analytics as an  # noqa: E402
from src import benchmarks as bmk  # noqa: E402
from src import loss_reasons as lr  # noqa: E402
from src import sheets  # noqa: E402
from src import insights as ins  # noqa: E402
from src import advanced as adv  # noqa: E402
import numpy as np  # noqa: E402
from src import data_loader as dl  # noqa: E402
from src import reconcile as rc  # noqa: E402

_reload_changed_project_modules()

ROOT = Path(__file__).resolve().parent
CSS = ROOT / "src" / "style.css"
LOGO = ROOT / "assets" / "logo.png"   # MG Select logo, transparent background

# Chart palette (light theme). Deep Teal is the primary series; champagne gold is
# the second series — validated on white: 3.1:1 contrast, ΔE ≥ 16 under all
# colour-vision deficiencies. The brand's #3A7F81 is too close to Deep Teal to
# separate two series (ΔE 6.6), so it is used for hover/UI states only.
PALETTE = {
    "primary": "#206C6F",
    "hover": "#3A7F81",
    "gold": "#B08D57",
    "surface": "#FFFFFF",
    "border": "#E7E2D8",
    "grid": "#EFEBE3",
    "text": "#0F2A2B",
    "muted": "#566B6B",
}
# Series colours, fixed order; colour follows the entity (model), not its rank.
SERIES_COLORS = ["#206C6F", "#B08D57", "#6E8FA3", "#9A9A9A"]
MODEL_ORDER = ["MG_M9", "MG_CYBERSTER"]  # known models first; new ones append after

st.set_page_config(page_title="MG Select Pulse", page_icon="⚡", layout="wide")


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def inr_short(amount: float | None) -> str:
    """₹ in Indian units: ₹2.47 Cr, ₹1.52 L, ₹85,059."""
    if amount is None or pd.isna(amount):
        return "—"
    if abs(amount) >= 1e7:
        return f"₹{amount / 1e7:.2f} Cr"
    if abs(amount) >= 1e5:
        return f"₹{amount / 1e5:.2f} L"
    return f"₹{amount:,.0f}"


def pct(rate: float | None, digits: int = 1) -> str:
    return "—" if rate is None or pd.isna(rate) else f"{rate * 100:.{digits}f}%"


def esc(text) -> str:
    return html.escape(str(text))


def card(label: str, value: str, sub: str = "", flag: str = "", accent: bool = False) -> str:
    classes = "pulse-card" + (" accent" if accent else "") + (" warn" if flag else "")
    flag_html = f'<div class="flag">⚠ {esc(flag)}</div>' if flag else ""
    return (f'<div class="{classes}"><div class="label">{esc(label)}</div>'
            f'<div class="value">{value}</div><div class="sub">{sub}</div>{flag_html}</div>')


def cards(items: list[str], columns: int | None = None) -> None:
    cls = "pulse-grid" + (f" cols-{columns}" if columns else "")
    st.markdown(f'<div class="{cls}">{"".join(items)}</div>', unsafe_allow_html=True)


def section(title: str, blurb: str = "") -> None:
    p = f"<p>{blurb}</p>" if blurb else ""
    st.markdown(f'<div class="pulse-section"><h2>{esc(title)}</h2>{p}</div>', unsafe_allow_html=True)


def note(text: str, warn: bool = False) -> None:
    icon = "⚠ " if warn else "ⓘ "
    st.markdown(f'<div class="pulse-note{" warn" if warn else ""}">{icon}{text}</div>', unsafe_allow_html=True)


def source_card(title: str, frame: pd.DataFrame, noun: str) -> str:
    """A compact card: total, then one row per Source with a proportional bar."""
    counts = frame["Source"].fillna("(blank)").value_counts()
    total = int(counts.sum())
    rows = "".join(
        f'<div class="src-row"><span class="src-name">{esc(name)}</span>'
        f'<span class="src-bar"><span style="width:{c / counts.iloc[0] * 100:.1f}%"></span></span>'
        f'<span class="src-count">{int(c):,}</span>'
        f'<span class="src-pct">{"<1" if 0 < c / total < 0.005 else f"{c / total * 100:.0f}"}%</span></div>'
        for name, c in counts.items()
    ) if total else '<div class="sub">No records in this selection.</div>'
    return (f'<div class="pulse-card src-card"><div class="label">{esc(title)} · by source</div>'
            f'<div class="value">{total:,}<span class="src-unit"> {esc(noun)}</span></div>{rows}</div>')


def base_layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, system-ui, sans-serif", color=PALETTE["text"], size=13),
        margin=dict(l=10, r=20, t=16, b=10),
        hoverlabel=dict(bgcolor=PALETTE["surface"], bordercolor=PALETTE["gold"], align="left",
                        font=dict(color=PALETTE["text"], family="Inter")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(color=PALETTE["muted"])),
    )
    fig.update_xaxes(gridcolor=PALETTE["grid"], zerolinecolor=PALETTE["border"],
                     tickfont=dict(color=PALETTE["muted"]), linecolor=PALETTE["border"])
    fig.update_yaxes(gridcolor=PALETTE["grid"], zerolinecolor=PALETTE["border"],
                     tickfont=dict(color=PALETTE["muted"]), linecolor=PALETTE["border"])
    return fig


def bar_max(series: pd.Series) -> int:
    """Upper end for a progress-bar column: the column's max, at least 1 (empty tables included)."""
    m = pd.to_numeric(series, errors="coerce").max()
    return int(m) if pd.notna(m) and m >= 1 else 1


def table_height(n_rows: int) -> int:
    """Height that shows every row of an st.dataframe without an inner scrollbar."""
    return 38 + 35 * max(n_rows, 1)


def model_colors(models: list[str]) -> dict[str, str]:
    ordered = [m for m in MODEL_ORDER if m in models] + sorted(m for m in models if m not in MODEL_ORDER)
    return {m: SERIES_COLORS[i] if i < len(SERIES_COLORS) else PALETTE["muted"] for i, m in enumerate(ordered)}


# --------------------------------------------------------------------------- #
# Theme
# --------------------------------------------------------------------------- #

if CSS.exists():
    st.markdown(f"<style>{CSS.read_text()}</style>", unsafe_allow_html=True)

LOGO_HTML = (f'<img class="pulse-logo-img" src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" '
             f'alt="MG Select">' if LOGO.exists() else '<div class="pulse-logo-text">MG SELECT</div>')



# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

load_problem: str | None = None
try:
    with st.spinner("Loading the latest data…"):
        data = dl.load_all()
except dl.DataValidationError as exc:
    st.error(f"**Data problem:** {exc}")
    st.stop()
except Exception as exc:  # Google Sheet unreachable (sharing, network, quota…)
    reason = str(exc) if isinstance(exc, (sheets.SheetAccessError, ConnectionError)) else \
        f"{type(exc).__name__}: {str(exc)[:200]}"
    if dl.LEADS_FILE.exists() and dl.OPPORTUNITIES_FILE.exists() and dl.find_retail_file():
        load_problem = reason
        data = dl.load_all(force_files=True)
    else:
        st.error(f"**Couldn't read the Google Sheet.** {reason}")
        st.stop()

_intake = pd.concat([data.leads["Created On"], data.opportunities["Created On"]]).max()
_retail = data.retail["tr_date"].max()
# Cover: the first screen is just the logo; the dashboard starts below it.
st.markdown(
    f'<section class="pulse-cover"><div class="pulse-cover-logo">{LOGO_HTML}</div>'
    f'<div class="pulse-cover-rule"></div>'
    f'<a class="pulse-cover-cue" href="#dashboard" target="_self">Scroll to the dashboard<span>↓</span></a></section>'
    f'<div id="dashboard"></div>', unsafe_allow_html=True)

# Trust bar: one slim line saying how fresh the data is (and how to fix it when not live).
retail_note = f" · retail up to {_retail:%d %b}" if pd.notna(_retail) else ""
if load_problem:
    sharing = "refused access" in load_problem
    headline = ("The Google Sheet isn't shared with the dashboard any more." if sharing
                else "Couldn't reach the Google Sheet right now.")
    fix = ("<ol><li>Open the Google Sheet <b>Leads-Opp-Bkgs-Stock</b> and click <b>Share</b>.</li>"
           "<li>Add <code>sheetreader@salesdashboard-510204.iam.gserviceaccount.com</code> as a <b>Viewer</b> "
           "(untick “Notify people”).</li><li>Come back here and press <b>Refresh now</b>.</li></ol>") if sharing else \
          f"<p>{esc(load_problem)}</p><p>Check the internet connection, then press <b>Refresh now</b>.</p>"
    st.markdown(f'<div class="pulse-trust off"><span class="dot"></span><b>Not live</b>'
                f'<span class="sep">·</span>Saved data up to {_intake:%d %b %Y}{retail_note}'
                f'<details><summary>{headline} How to fix</summary>{fix}</details></div>', unsafe_allow_html=True)
    if sheets.is_configured() and st.button("↻  Refresh now", help="Try reading the Google Sheet again",
                                            key="refresh_btn"):
        sheets.cached_raw_grids.clear()
        sheets.fetch.clear()
        dl._clean_sheet.clear()
        st.rerun()
elif data.source == "files":
    st.markdown(f'<div class="pulse-trust saved"><span class="dot"></span><b>Data up to {_intake:%d %b %Y}</b>'
                f'{retail_note}<span class="sep">·</span>From the saved C4C exports and retail register'
                f'<span class="sep">·</span>Daily updates start once Zoho Sheet is connected</div>',
                unsafe_allow_html=True)
else:
    read_at = data.source_label.split("read", 1)[-1].strip() if "read" in data.source_label else ""
    src_txt = "Google Sheet" if data.source == "sheet" else "Saved files"
    st.markdown(f'<div class="pulse-trust"><span class="dot"></span><b>Live</b><span class="sep">·</span>'
                f'{src_txt}{" read " + esc(read_at) if read_at else ""}<span class="sep">·</span>'
                f'Leads &amp; opportunities up to {_intake:%d %b %Y}{retail_note}</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #

def options(*series: pd.Series) -> list[str]:
    vals = pd.concat([s.dropna() for s in series], ignore_index=True)
    return sorted(v for v in vals.unique() if str(v).strip())


leads_all, opps_all, retail_all = data.leads, data.opportunities, data.retail

intake_end = pd.concat([leads_all["Created On"], opps_all["Created On"]]).max().date()
retail_end = retail_all["tr_date"].max().date() if retail_all["tr_date"].notna().any() else None

filter_panel = st.container(border=True, key="filter_panel")
filter_panel.markdown('<div class="pulse-filterbar-label">Filters <span>· apply to every tab</span></div>',
                      unsafe_allow_html=True)
c1, c2, c3, c4, c5 = filter_panel.columns([1.3, 1, 1, 1, 1.2])
with c1:
    date_value = st.date_input(
        "Date range",
        value=(data.default_start, data.default_end),
        min_value=data.window_start,
        max_value=data.window_end,
        format="DD/MM/YYYY",
    )
with c2:
    sel_sources = st.multiselect("Source", options(leads_all["Source"], opps_all["Source"], retail_all["Source"]),
                                 placeholder="All sources")
with c3:
    sel_channels = st.multiselect("Channel", options(leads_all["Channel"], opps_all["Channel"]),
                                  placeholder="All channels")
with c4:
    sel_models = st.multiselect("Model line", options(leads_all["model"], opps_all["model"], retail_all["model"]),
                                placeholder="All models")
with c5:
    sel_reps = st.multiselect("Sales rep", options(opps_all["rep"], retail_all["rep"]), placeholder="All reps")

# A half-picked range (one date) is treated as a single day until the second is chosen.
if isinstance(date_value, (tuple, list)):
    start, end = (date_value[0], date_value[-1]) if date_value else (data.default_start, data.default_end)
else:
    start = end = date_value

ORDERED_STATUS_LABEL, ORDERED_DATE_LABEL = "Status = Booked (current status)", "Has a Booking Date (booked at any point)"
ordered_label = filter_panel.segmented_control(
    "Ordered stage counts", [ORDERED_DATE_LABEL, ORDERED_STATUS_LABEL], default=ORDERED_DATE_LABEL,
    key="ordered_mode",
    help="C4C moves a booked opportunity to Invoiced, then Delivered, so 'Status = Booked' only counts "
         "bookings not yet invoiced. The second option counts every opportunity that was ever booked.",
) or ORDERED_DATE_LABEL
ordered_mode = an.ORDERED_MODE_STATUS if ordered_label == ORDERED_STATUS_LABEL else an.ORDERED_MODE_BOOKING_DATE

filters = an.Filters(start, end, sel_sources, sel_channels, sel_models, sel_reps, ordered_mode)
leads_f = an.filter_leads(leads_all, filters)
opps_f = an.filter_opportunities(opps_all, filters)
td_f = an.filter_test_drives(opps_all, filters)
retail_f = an.filter_retail(retail_all, filters)

# ---- Source reconciliation (date range only, recomputed straight from the raw source)


@st.cache_data(show_spinner=False)
def cached_reconciliation(_data: dl.LoadedData, start_, end_, signatures: tuple) -> tuple[pd.DataFrame, list[str]]:
    return rc.reconcile(_data, start_, end_)


signatures = (data.source_label,) if data.source == "sheet" else tuple(
    dl._file_signature(p) for p in [dl.LEADS_FILE, dl.OPPORTUNITIES_FILE, dl.find_retail_file()])
with st.spinner("Reconciling against source files…"):
    recon, _ = cached_reconciliation(data, start, end, signatures)
recon_ok = bool((recon["Match"] == "✓ Match").all())
period_label = f"{start:%d %b %Y} – {end:%d %b %Y}"
# Only speak up when something is wrong: a reconciliation mismatch, or a filter
# that can't apply to every dataset. The full reconciliation table is available
# from the terminal: python -m src.reconcile <start> <end>
if not recon_ok:
    bad = ", ".join(recon.loc[recon["Match"] != "✓ Match", "Metric"])
    note(f"<b>Figures don't match the source files</b> for {period_label}: {esc(bad)}. "
         "Don't rely on these until checked — run <code>python -m src.reconcile</code> for details.", warn=True)

filter_caveats = []
if sel_channels:
    filter_caveats.append("<b>Channel</b> filters Leads and Opportunities only — the retail register has no Channel column, so Retail is not narrowed by it.")
if sel_reps:
    filter_caveats.append("<b>Sales rep</b> filters Opportunities by <i>Assigned To</i>, Retail by <i>SM</i>, and Leads by lead <i>Owner</i> (Leads have no Assigned To).")
if filter_caveats:
    note("<br>".join(filter_caveats))


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #

tab_today, tab_trends, tab_funnel, tab_team, tab_pipeline, tab_retail, tab_insights, tab_guide = st.tabs([
    'Today', 'Trends', 'Funnel', 'Team', 'Pipeline', 'Retail & Stock', 'Insights', 'Guide',
])

with tab_today:
    # --------------------------------------------------------------------------- #
    # Yesterday / this month so far — what was dumped into the sheet
    # --------------------------------------------------------------------------- #

    yesterday = sheets.now_ist().date() - pd.Timedelta(days=1)
    day = min(yesterday, intake_end)  # latest day the leads / opportunities dump covers
    today_view = st.segmented_control("Show", ["Yesterday", "This month so far"], default="Yesterday",
                                      key="today_period") or "Yesterday"
    if today_view == "Yesterday":
        p_start = p_end = day
        section(f"Yesterday · {day:%A, %d %b %Y}" if day == yesterday else f"Latest day in the data · {day:%A, %d %b %Y}",
                "New leads, opportunities, test drives, bookings and deliveries dated that day "
                "(Source / Channel / Model / Rep filters apply; the date range above doesn't).")
        when, file_tag = "that day", f"{day:%Y%m%d}"
    else:
        p_start, p_end = day.replace(day=1), day
        section(f"This month so far · {p_start:%d} – {p_end:%d %b %Y}",
                f"Everything from the 1st of {p_start:%B} up to the latest day in the data "
                "(Source / Channel / Model / Rep filters apply; the date range above doesn't).")
        when, file_tag = "this month so far", f"{p_start:%Y%m%d}-{p_end:%Y%m%d}"
    if day < yesterday and data.source == "sheet":  # when not live, the banner at the top already says so
        note(f"The data has nothing for <b>{yesterday:%d %b %Y}</b> yet — showing up to the latest day available, "
             f"<b>{day:%d %b %Y}</b>. It updates at the next scheduled read "
             f"({', '.join(t.strftime('%H:%M') for t in sheets.REFRESH_TIMES)} IST).", warn=True)

    f_day = an.Filters(p_start, p_end, sel_sources, sel_channels, sel_models, sel_reps)
    leads_d = an.filter_leads(leads_all, f_day)
    opps_d = an.filter_opportunities(opps_all, f_day)
    td_d = an.filter_test_drives(opps_all, f_day)
    booked_pool = opps_all[opps_all["Booking Date"].notna()] if "Booking Date" in opps_all else opps_all.iloc[0:0]
    booked_d = an.filter_opportunities(booked_pool, f_day, date_column="Booking Date") if len(booked_pool) else booked_pool
    retail_d = an.filter_retail(retail_all, f_day)
    n_cre = int(leads_d["lead_route"].eq(dl.ROUTE_CRE).sum())
    cards([
        card("New leads", f"{len(leads_d):,}", f"{n_cre:,} to CRE · {len(leads_d) - n_cre:,} to sales managers", accent=True),
        card("New opportunities", f"{len(opps_d):,}", f"{int(opps_d['test_drive_done'].sum()):,} already test-driven",
             accent=True),
        card("Test drives done", f"{len(td_d):,}", f"First test drive completed {when}"),
        card("Bookings", f"{len(booked_d):,}", f"Booking Date {when}"),
        card("Retail deliveries", f"{len(retail_d):,}", f"TR DATE {when}"),
    ])
    yl, yr = st.columns(2, gap="medium")
    with yl:
        st.markdown(source_card("New leads", leads_d, "leads"), unsafe_allow_html=True)
    with yr:
        st.markdown(source_card("New opportunities", opps_d, "opportunities"), unsafe_allow_html=True)

    span = f"{day:%d %b}" if p_start == p_end else f"{p_start:%d} – {p_end:%d %b}"
    with st.expander(f"List of new leads ({len(leads_d):,}) and opportunities ({len(opps_d):,}) · {span}"):
        lead_list = pd.DataFrame({
            "Created": leads_d["Created On"].dt.date, "Lead ID": leads_d["Lead ID"], "Name": leads_d.get("Name"),
            "Source": leads_d["Source"], "Channel": leads_d["Channel"], "Owner": leads_d["rep"],
            "Route": leads_d["lead_route"], "Status": leads_d["Status"],
            "Qualification": leads_d["Qualification Level"], "Model": leads_d["model"],
        })
        opp_list = pd.DataFrame({
            "Created": opps_d["Created On"].dt.date, "Opp ID": opps_d["ID"], "Customer": opps_d["Customer"],
            "Source": opps_d["Source"], "Channel": opps_d["Channel"], "Sales rep": opps_d["rep"],
            "Status": opps_d["Status"], "Model": opps_d["model"],
            "Test drive": opps_d["test_drive_done"].map({True: "Yes", False: "No"}),
        })
        st.markdown('<div class="pulse-subhead">Leads</div>', unsafe_allow_html=True)
        st.dataframe(lead_list, hide_index=True, width="stretch", height=min(table_height(len(lead_list)), 420))
        st.markdown('<div class="pulse-subhead">Opportunities</div>', unsafe_allow_html=True)
        st.dataframe(opp_list, hide_index=True, width="stretch", height=min(table_height(len(opp_list)), 420))
        st.download_button("Download leads (CSV)", lead_list.to_csv(index=False).encode("utf-8"),
                           file_name=f"leads_{file_tag}.csv", mime="text/csv")
        st.download_button("Download opportunities (CSV)", opp_list.to_csv(index=False).encode("utf-8"),
                           file_name=f"opportunities_{file_tag}.csv", mime="text/csv")

with tab_trends:
    # --------------------------------------------------------------------------- #
    # Trends — this period vs last period vs last year, and month-by-month lines
    # --------------------------------------------------------------------------- #

    st.markdown('<div class="pulse-section"><p>Each number for the selected dates, next to the period just before '
                'it and the same dates last year. ▲ / ▼ show the change.</p></div>', unsafe_allow_html=True)
    cmp = ins.compare_periods(leads_all, opps_all, retail_all, filters)


    def change_chip(new: int, old: int) -> str:
        ch = ins.pct_change(new, old)
        if ch is None:
            return '<span class="gap na">no data</span>'
        cls = "up" if ch >= 0 else "down"
        return f'<span class="gap {cls}">{"▲" if ch >= 0 else "▼"} {ch * 100:+.0f}%</span>'


    trend_cards = []
    for _, r in cmp.iterrows():
        trend_cards.append(card(
            r["Metric"], f"{int(r['This period']):,}",
            f"vs previous: {int(r['Previous period']):,} {change_chip(r['This period'], r['Previous period'])}"
            # Only compare with last year when there is data for it (C4C history starts Jan 2026).
            + (f"<br>vs last year: {int(r['Same period last year']):,} "
               f"{change_chip(r['This period'], r['Same period last year'])}" if r["Same period last year"] else ""),
            accent=True))
    cards(trend_cards)
    st.caption(f"Previous period = {cmp.attrs['prev_label']} · Same period last year = {cmp.attrs['ly_label']} "
               "(shown only where last year has data). "
               "Leads and opportunities by Created On, test drives by test-drive date, bookings by Booking Date, "
               "retail by TR DATE.")

    section("Month by month",
            "The selected months plus the months before them, so a single month is always seen in context. "
            "The dashed gold line is the same month a year earlier.")
    n_months = st.segmented_control("Months to show", [3, 6, 12], default=3,
                                    format_func=lambda n: f"Last {n} months", key="trend_months") or 3
    t_months = ins.trend_months(start, end, n_months, data.window_start)
    tt = ins.trend_table(leads_all, opps_all, retail_all, filters, t_months)
    ly = ins.trend_table(leads_all, opps_all, retail_all, filters, [m - 12 for m in t_months])
    ly_available = [m - 12 >= pd.Period(data.window_start, "M") for m in t_months]

    fig = make_subplots(rows=2, cols=3, subplot_titles=ins.TREND_METRICS, horizontal_spacing=0.07, vertical_spacing=0.18)
    labels = [m.strftime("%b %y") for m in t_months]
    for i, metric in enumerate(ins.TREND_METRICS):
        rr, cc = divmod(i, 3)
        fig.add_scatter(
            x=labels, y=tt[metric], mode="lines+markers+text", name="This year", legendgroup="cur",
            showlegend=i == 0, line=dict(color=PALETTE["primary"], width=2.5), marker=dict(size=8),
            text=[f"{v:,}" for v in tt[metric]], textposition="top center",
            textfont=dict(size=11, color=PALETTE["text"]), cliponaxis=False,
            hovertemplate=f"<b>{metric}</b> · %{{x}}: %{{y:,}}<extra></extra>", row=rr + 1, col=cc + 1)
        ly_vals = [v if ok else None for v, ok in zip(ly[metric], ly_available)]
        if any(v is not None for v in ly_vals):
            fig.add_scatter(
                x=labels, y=ly_vals, mode="lines+markers", name="Same month last year", legendgroup="ly",
                showlegend=i == 0, line=dict(color=PALETTE["gold"], width=2, dash="dash"), marker=dict(size=6),
                hovertemplate=f"<b>{metric}</b> · same month last year: %{{y:,}}<extra></extra>",
                row=rr + 1, col=cc + 1)
        fig.update_yaxes(rangemode="tozero", row=rr + 1, col=cc + 1)
    base_layout(fig, 560)
    fig.update_layout(margin=dict(l=10, r=20, t=70, b=10), legend=dict(y=1.12))
    fig.update_annotations(font=dict(size=13, color=PALETTE["text"]))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    with st.expander("Month-by-month numbers"):
        show = tt.copy()
        show["Month"] = show["Month"].map(lambda m: m.strftime("%b %Y"))
        for metric in ins.TREND_METRICS:
            prev = tt[metric].shift(1)
            show[f"{metric} vs prev. month"] = [
                "—" if pd.isna(p) or not p else f"{(v - p) / p * 100:+.0f}%" for v, p in zip(tt[metric], prev)]
        st.dataframe(show, hide_index=True, width="stretch", height=table_height(len(show)))
    note("A month that's still in progress (the current month) will look lower until it's complete.")

with tab_funnel:
    # --------------------------------------------------------------------------- #
    # 1. Funnel
    # --------------------------------------------------------------------------- #

    section("Sales funnel", "Leads → Opportunities → Test drive → Ordered → Retail, with conversion from the previous stage.")

    statuses = an.ordered_statuses(opps_all)
    booked_f = ins.bookings_in_period(opps_all, filters)
    by_booking_date = ordered_mode == an.ORDERED_MODE_BOOKING_DATE
    funnel = an.build_funnel(leads_f, opps_f, td_f, retail_f, ordered_mode, statuses,
                             bookings=booked_f if by_booking_date else None)
    names, values = zip(*funnel.stages)

    # Drawn as centred horizontal bars (not go.Funnel) so the Leads bar can be made of
    # CRE + sales-manager segments. It reads as one bar showing total leads; each
    # segment reveals its own count (and each person's) on hover, and is clickable.
    BAR_HALF = 0.32  # half the bar thickness, in y units

    widest = max(values) or 1
    bases = [(widest - v) / 2 for v in values]


    def stage_label(i: int) -> str:
        if i == 0:
            return f"<b>{values[0]:,}</b>"
        r = an.safe_rate(values[i], values[i - 1])
        if r is not None and r > 1:  # stages use different dates; a rate over 100% means nothing
            return f"<b>{values[i]:,}</b>"
        return f"<b>{values[i]:,}</b>   ·   {pct(r)} of {names[i - 1].lower()}"


    fig = go.Figure()
    trace_keys: list[str] = []  # trace index -> what was clicked ("route:CRE", "stage:2", ...)

    # Stage 0 — Leads: one teal bar labelled with the total, built from a CRE and a
    # sales-manager segment (thin white divider). Hover shows the segment's count.
    owners = an.leads_by_owner(leads_f)
    offset = bases[0]
    for route in (dl.ROUTE_CRE, dl.ROUTE_SM):
        part = owners[owners["Route"] == route]
        n = int(part["Leads"].sum())
        if not n:
            continue
        share = n / values[0] * 100
        people = "<br>".join(f"{esc(o)}: {int(c):,}" for o, c in zip(part["Owner"], part["Leads"]))
        fig.add_bar(
            name=route, y=[0], x=[n], base=[offset], orientation="h", width=2 * BAR_HALF,
            customdata=[f"route:{route}"], showlegend=False,
            marker=dict(color=PALETTE["primary"], line=dict(width=1.5, color="#FFFFFF")),
            hovertemplate=f"<b>Leads with {route}</b><br>{n:,} leads · {share:.1f}% of {values[0]:,}"
                          f"<br><br>{people}<extra></extra>",
        )
        offset += n
        trace_keys.append(f"route:{route}")
    fig.add_annotation(
        x=widest / 2, y=0, text=f"<b>{values[0]:,}</b>", showarrow=False,
        font=dict(color="#FFFFFF", size=14), captureevents=False,
    )

    # Stages 1..4 — single bars; narrow ones carry their label outside.
    for i in range(1, len(values)):
        v = values[i]
        fig.add_bar(
            y=[i], x=[v], base=[bases[i]], orientation="h", width=2 * BAR_HALF, showlegend=False,
            customdata=[f"stage:{i}"],
            marker=dict(color=PALETTE["primary"], line=dict(width=0)),
            text=[stage_label(i)], textposition="inside" if v >= 0.35 * widest else "outside",
            insidetextanchor="middle", textfont=dict(size=14),
            insidetextfont=dict(color="#FFFFFF"), outsidetextfont=dict(color=PALETTE["text"]),
            cliponaxis=False,
            hovertemplate=f"<b>{names[i]}</b><br>{v:,}<br>"
                          + (f"{pct(an.safe_rate(v, values[i - 1]))} of previous stage" if v <= values[i - 1]
                             else "More than the previous stage — includes customers from earlier months")
                          + f"<br>{pct(an.safe_rate(v, values[0]))} of leads<extra></extra>",
        )
        trace_keys.append(f"stage:{i}")

    # Connectors: soft trapezoids between consecutive stages.
    for i in range(len(values) - 1):
        x0, x1 = bases[i], bases[i] + values[i]
        x2, x3 = bases[i + 1] + values[i + 1], bases[i + 1]
        fig.add_shape(
            type="path", layer="below", line=dict(width=0), fillcolor="#EAF2F1",
            path=f"M {x0},{i + BAR_HALF} L {x1},{i + BAR_HALF} L {x2},{i + 1 - BAR_HALF} L {x3},{i + 1 - BAR_HALF} Z",
        )

    base_layout(fig, 400)
    fig.update_layout(barmode="overlay", bargap=0, showlegend=False, hovermode="closest")
    fig.update_yaxes(
        tickmode="array", tickvals=list(range(len(names))), ticktext=list(names),
        autorange="reversed", showgrid=False, tickfont=dict(color=PALETTE["text"], size=13),
    )
    fig.update_xaxes(visible=False, range=[-0.02 * widest, widest * 1.02])
    fig.update_traces(unselected=dict(marker=dict(opacity=0.45)))
    fig.update_layout(clickmode="event+select", dragmode=False)
    event = st.plotly_chart(
        fig, width="stretch", config={"displayModeBar": False, "doubleClick": "reset"},
        on_select="rerun", selection_mode="points", key="funnel_chart",
    )
    st.caption("Hover the Leads bar for the CRE / sales-manager split; click any bar (or either part of Leads) "
               "to see it by source. "
               "Click the empty area of the chart to close.")


    def clicked_key(evt) -> str | None:
        """The key of the bar the user clicked, if any."""
        try:
            points = evt.selection.points
        except AttributeError:
            return None
        if not points:
            return None
        pt = points[0]
        cd = pt.get("customdata")
        if isinstance(cd, list):
            cd = cd[0] if cd else None
        if isinstance(cd, str):
            return cd
        idx = pt.get("curve_number")
        return trace_keys[idx] if isinstance(idx, int) and idx < len(trace_keys) else None


    key = clicked_key(event)
    if key:
        if key.startswith("route:"):
            route = key.split(":", 1)[1]
            panel = source_card(f"Leads with {route}", leads_f[leads_f["lead_route"] == route], "leads")
        else:
            stage = int(key.split(":", 1)[1])
            stage_frames = {
                1: (opps_f, "opportunities"),
                2: (td_f, "test drives"),
                3: (booked_f if by_booking_date else opps_f[an.ordered_mask(opps_f, ordered_mode, statuses)], "orders"),
                4: (retail_f, "retail units"),
            }
            frame, noun = stage_frames[stage]
            panel = source_card(names[stage], frame, noun)
        st.markdown(f'<div class="src-wrap">{panel}</div>', unsafe_allow_html=True)

    rate_help = {
        "Lead → Opportunity": (funnel.opportunities, funnel.leads, "opportunities per lead"),
        "Opportunity → Test Drive": (funnel.test_drives, funnel.opportunities, "test drives done ÷ opportunities created, same dates"),
        "Test Drive → Ordered": (funnel.ordered, funnel.test_drives, "bookings made ÷ test drives done, same dates"),
        "Ordered → Retail": (funnel.retail, funnel.ordered, "deliveries ÷ bookings made, same dates"),
    }
    rate_cards = []
    for label, rate in funnel.rates.items():
        num, den, desc = rate_help[label]
        if rate is not None and rate > 1:  # never show a step rate above 100%
            rate_cards.append(card(label, "—", f"{num:,} vs {den:,} — more than the previous stage, because it "
                                               "includes customers who came in before these dates"))
        else:
            rate_cards.append(card(label, pct(rate), f"{num:,} of {den:,} — {desc}", accent=True))
    st.markdown('<div class="pulse-subhead">Stage to next stage</div>', unsafe_allow_html=True)
    cards(rate_cards)
    st.caption("Each stage is counted on its own date (test drives on the test-drive date, bookings on the booking "
               "date, retail on the delivery date), so these are “this period” ratios. To follow the same customers "
               "from start to finish, see “Where do they end up?” below — the benchmarks use that view, so their "
               "rates can differ from these.")

    # Overall conversion: how many of the period's leads / opportunities / test drives
    # each later stage represents.
    overall = [
        ("Lead → Test Drive", funnel.test_drives, funnel.leads, "test drives per lead"),
        ("Lead → Ordered", funnel.ordered, funnel.leads, "orders per lead"),
        ("Lead → Retail", funnel.retail, funnel.leads, "retails per lead"),
        ("Opportunity → Ordered", funnel.ordered, funnel.opportunities, "orders per opportunity"),
        ("Opportunity → Retail", funnel.retail, funnel.opportunities, "retails per opportunity"),
        ("Test Drive → Retail", funnel.retail, funnel.test_drives, "retails per test drive"),
    ]
    overall_cards = []
    for label, num, den, desc in overall:
        rate = an.safe_rate(num, den)
        overall_cards.append(card(label, "—" if rate is not None and rate > 1 else pct(rate),
                                  f"{num:,} vs {den:,} — includes customers from earlier dates" if rate is not None and rate > 1
                                  else f"{num:,} of {den:,} — {desc}"))
    st.markdown('<div class="pulse-subhead">Overall conversion</div>', unsafe_allow_html=True)
    cards(overall_cards, columns=3)

    with st.expander("All stage-to-stage conversion rates (with counts)"):
        matrix = funnel.conversion_matrix()
        st.dataframe(
            matrix, hide_index=True, width="stretch", height=table_height(len(matrix)),
            column_config={
                "From count": st.column_config.NumberColumn(format="localized"),
                "To count": st.column_config.NumberColumn(format="localized"),
                "Rate %": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        st.caption("Rate = To count ÷ From count. Counts are the reconciled funnel figures above; "
                   "each stage uses its own date field, so these are period ratios, not a followed cohort.")

    xc = an.delivered_crosscheck(opps_all, retail_f, filters)
    sub = (f"C4C records {pct(xc['coverage'], 0)} of the deliveries in the retail register "
           f"({xc['difference']:+,} gap). Opportunities dated by <i>{esc(xc['date_column'])}</i>."
           if xc["retail"] else "No retail rows in this selection.")
    cross_card = (
        '<div class="pulse-card"><div class="label">Cross-check · Delivered in C4C vs Retail register</div>'
        '<div class="pulse-compare">'
        f'<div><div class="num">{xc["opp_delivered"]:,}</div><div class="cap">Opportunities · Status = Delivered</div></div>'
        f'<div><div class="num">{xc["retail"]:,}</div><div class="cap">Retail register ({esc(data.retail_source)})</div></div>'
        f'</div><div class="sub">{sub}</div></div>'
    )
    cards([cross_card])
    note(
        f"<b>Ordered</b> = {esc(funnel.ordered_definition)}. <b>Retail</b> comes from the retail register, "
        "not from Opportunities' Delivered status. The opportunity data only contains opportunities <i>created</i> "
        f"from {data.window_start:%b %Y}, so vehicles retailed in this period from older opportunities "
        "are missing on the C4C side — a gap in the cross-check is expected, not necessarily an error."
    )

    # --------------------------------------------------------------------------- #
    # Cohort funnel — follow the same leads / opportunities through every stage
    # --------------------------------------------------------------------------- #

    section("Where do they end up?",
            "Takes the leads (or opportunities) that came in each month and follows <b>those same customers</b> "
            "to today: did they become an opportunity, take a test drive, book, get delivered? Because it's the "
            "same group all the way through, percentages can never go above 100%. Recent months are still in progress.")
    cohort_mode = st.segmented_control("Start from", ["Leads", "Opportunities (incl. walk-ins with no lead)"],
                                       default="Leads", key="cohort_mode") or "Leads"
    c_months = ins.trend_months(start, end, 3, data.window_start)
    if cohort_mode == "Leads":
        coh = ins.lead_cohort(leads_all, opps_all, filters, c_months)
        base_col, stage_cols = "Leads", ["Became opportunity", "Test drive", "Booked", "Delivered / invoiced"]
    else:
        coh = ins.opportunity_cohort(opps_all, filters, c_months)
        base_col, stage_cols = "Opportunities", ["Test drive", "Booked", "Delivered / invoiced", "Lost", "Still open"]

    # Headline: the selected period as one group
    sel = coh[coh["Month"].map(lambda m: m.start_time.date() <= end and m.end_time.date() >= start)]
    base_n = int(sel[base_col].sum())
    cohort_cards = [card(f"{base_col.split(' (')[0]} that came in", f"{base_n:,}",
                         f"{start:%d %b} – {end:%d %b %Y}", accent=True)]
    for col_ in stage_cols:
        n_ = int(sel[col_].sum())
        cohort_cards.append(card(col_, pct(an.safe_rate(n_, base_n)), f"{n_:,} of {base_n:,}"))
    cards(cohort_cards)

    # Month-by-month table as percentages of each month's group
    coh_view = pd.DataFrame({"Month": coh["Month"].map(lambda m: m.strftime("%b %Y")), base_col: coh[base_col]})
    for col_ in stage_cols:
        coh_view[col_ + " %"] = [(n_ / b * 100) if b else None for n_, b in zip(coh[col_], coh[base_col])]
    st.dataframe(
        coh_view, hide_index=True, width="stretch", height=table_height(len(coh_view)),
        column_config={c + " %": st.column_config.ProgressColumn(c, min_value=0, max_value=100, format="%.0f%%")
                       for c in stage_cols},
    )
    if cohort_mode == "Leads":
        note("Only opportunities that came <i>from a lead</i> (linked by ACS_OpportunityId) can be followed here. "
             "Walk-ins and referrals often become an opportunity directly — switch to <b>Opportunities</b> to include them.")



    # --------------------------------------------------------------------------- #
    # Benchmarks vs actuals (L2O, TD%, O2B by source)
    # --------------------------------------------------------------------------- #

    section(
        "Benchmarks vs actuals",
        "Lead-to-opportunity (L2O), test-drive % (TD%) and opportunity-to-booking (O2B) for each source, "
        "against the dealership benchmarks.",
    )

    bench = bmk.benchmark_table(leads_f, opps_f)
    has_data = bench[(bench["Leads"] > 0) | (bench["Opportunities"] > 0)]
    plotted = has_data[has_data["L2O benchmark"].notna()]


    def gap_chip(gap) -> str:
        if gap is None or pd.isna(gap):
            return '<span class="gap na">—</span>'
        pts = gap * 100
        if pts >= 0:
            return f'<span class="gap up">▲ +{pts:.1f} pts</span>'
        return f'<span class="gap down">▼ {pts:.1f} pts</span>'


    # Headline: the Total row against its benchmark.
    total = bench[bench["Benchmark row"] == bmk.TOTAL_ROW].iloc[0]
    cards([
        card(f"Total {kpi}", pct(total[kpi]),
             f"Benchmark {pct(total[f'{kpi} benchmark'], 0)} · {gap_chip(total[f'{kpi} gap'])}", accent=True)
        for kpi in bmk.KPIS
    ], columns=3)

    if not plotted.empty:
        titles = {"L2O": "L2O — lead to opportunity", "TD%": "TD% — opportunities test-driven",
                  "O2B": "O2B — opportunity to booking"}
        fig = make_subplots(rows=1, cols=3, shared_yaxes=True, horizontal_spacing=0.06,
                            subplot_titles=[titles[k] for k in bmk.KPIS])
        rows_ = plotted["Benchmark row"].tolist()
        for col, kpi in enumerate(bmk.KPIS, start=1):
            actual = (pd.to_numeric(plotted[kpi]) * 100).tolist()
            target = (pd.to_numeric(plotted[f"{kpi} benchmark"]) * 100).tolist()
            hover = [
                f"<b>{esc(r)}</b> · {kpi}<br>Actual {a:.1f}%  ·  Benchmark {t:.0f}%<br>"
                f"{'▲' if a >= t else '▼'} {a - t:+.1f} pts<br>"
                + (f"{int(o):,} opps of {int(l):,} leads" if kpi == "L2O"
                   else f"{int(n):,} of {int(o):,} opportunities")
                for r, a, t, l, o, n in zip(
                    rows_, actual, target, plotted["Leads"], plotted["Opportunities"],
                    plotted["Test drives"] if kpi == "TD%" else plotted["Booked"])
            ]
            fig.add_bar(
                x=actual, y=rows_, orientation="h", name="Actual", legendgroup="a", showlegend=col == 1,
                marker=dict(color=PALETTE["primary"], cornerradius=4), width=0.55,
                hovertext=hover, hoverinfo="text", row=1, col=col,
            )
            fig.add_scatter(
                x=target, y=rows_, mode="markers", name="Benchmark", legendgroup="b", showlegend=col == 1,
                marker=dict(symbol="line-ns", size=26, line=dict(width=4, color=PALETTE["gold"])),
                hovertext=hover, hoverinfo="text", row=1, col=col,
            )
            # Actual % in a fixed column at the right edge — never collides with a benchmark tick.
            fig.add_scatter(
                x=[122] * len(rows_), y=rows_, mode="text", text=[f"<b>{a:.0f}%</b>" for a in actual],
                textposition="middle left", textfont=dict(color=PALETTE["text"], size=12),
                showlegend=False, hoverinfo="skip", row=1, col=col,
            )
            fig.update_xaxes(range=[0, 125], ticksuffix="%", tickvals=[0, 25, 50, 75, 100], row=1, col=col)
        base_layout(fig, 90 + 48 * len(rows_))
        fig.update_layout(margin=dict(l=10, r=20, t=60, b=10), legend=dict(y=1.16), bargap=0.3)
        fig.update_yaxes(autorange="reversed", tickfont=dict(color=PALETTE["text"], size=13))
        fig.update_annotations(font=dict(size=12, color=PALETTE["muted"]))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # Comparison table
    body = []
    for _, r in bench.iterrows():
        is_total = r["Benchmark row"] == bmk.TOTAL_ROW
        empty = r["Leads"] == 0 and r["Opportunities"] == 0
        cells = [f'<td class="name">{esc(r["Benchmark row"])}<div class="srcs">{esc(r["Sources"])}</div></td>',
                 f'<td>{int(r["Leads"]):,}</td><td>{int(r["Opportunities"]):,}</td>']
        for kpi in bmk.KPIS:
            if empty:
                cells.append('<td colspan="1" class="muted">No data</td>')
                continue
            target = r[f"{kpi} benchmark"]
            cells.append(
                f'<td><div class="act">{pct(r[kpi])}</div>'
                f'<div class="bm">{"vs " + pct(target, 0) if target is not None and not pd.isna(target) else "no benchmark"}</div>'
                f'{gap_chip(r[f"{kpi} gap"])}</td>'
            )
        body.append(f'<tr class="{"total" if is_total else ""}{" empty" if empty else ""}">{"".join(cells)}</tr>')
    with st.expander("Benchmark comparison by source", expanded=False):
        st.markdown(
            '<div class="bm-wrap"><table class="bm-table"><thead><tr><th>Source</th><th>Leads</th><th>Opps</th>'
            '<th>L2O</th><th>TD%</th><th>O2B</th></tr></thead><tbody>' + "".join(body) + "</tbody></table></div>",
            unsafe_allow_html=True,
        )

    with st.expander("Actuals by individual C4C source"):
        detail = bmk.source_detail(leads_f, opps_f)
        for kpi in bmk.KPIS:
            detail[kpi] = pd.to_numeric(detail[kpi]) * 100
        st.dataframe(
            detail, hide_index=True, width="stretch", height=table_height(len(detail)),
            column_config={k: st.column_config.NumberColumn(format="%.1f%%") for k in bmk.KPIS},
        )

    note(
        "<b>How actuals are calculated</b> (leads and opportunities <i>created</i> in the selected period, same source): "
        "<b>L2O</b> = opportunities ÷ leads · <b>TD%</b> = opportunities with Test Drive Completed = Yes ÷ opportunities · "
        "<b>O2B</b> = opportunities booked at any point (Booking Date present, not cancelled) ÷ opportunities. "
        "Digital includes Dealer Digital and Avention; Tele-in includes Inbound_call. "
        "Recent opportunities may still book later, so O2B for the latest weeks can rise."
    )

with tab_team:
    # --------------------------------------------------------------------------- #
    # Rep scorecard — one row per sales rep
    # --------------------------------------------------------------------------- #

    section("Sales rep scorecard",
            "Everyone's numbers for the selected dates in one place. Sorted by retail, then bookings. "
            "Bars show who leads each column. TDs = test drives · Cancelled = bookings since cancelled · "
            "Untouched = open opportunities with no follow-up logged.")
    sc = ins.rep_scorecard(leads_all, opps_all, retail_all, filters, an.export_as_of(opps_all))
    flagged: list[str] = []
    if not sc.empty:
        # Bookings per 100 opportunities, and a flag for reps with lots of opportunities but few bookings.
        sc.insert(sc.columns.get_loc("Bookings") + 1, "Book rate",
                  [b / o * 100 if o else None for b, o in zip(sc["Bookings"], sc["Opportunities"])])
        team_rate = an.safe_rate(int(sc["Bookings"].sum()), int(sc["Opportunities"].sum())) or 0
        busy = sc["Opportunities"] >= max(20, sc["Opportunities"].median() * 1.5)
        weak = sc["Book rate"].fillna(0) < team_rate * 100 / 2
        flagged = sc.loc[busy & weak, "Sales rep"].tolist()
        sc["Sales rep"] = [f"⚠ {r}" if r in flagged else r for r in sc["Sales rep"]]
    for c_ in ["Test drive %", "Book rate", "EW %", "Acc. / car (₹L)"]:
        if c_ in sc:
            fmt = "{:.2f}" if "₹" in c_ else "{:.0f}%"
            sc[c_] = sc[c_].map(lambda v: "—" if v is None or pd.isna(v) else fmt.format(v))
    if sc.empty:
        st.caption("No activity for this selection.")
    else:
        st.dataframe(
            sc, hide_index=True, width="stretch", height=table_height(len(sc)),
            column_config={
                "Sales rep": st.column_config.TextColumn(pinned=True),  # stays visible when scrolling sideways
                "Retail": st.column_config.ProgressColumn(min_value=0, max_value=bar_max(sc["Retail"]), format="%d"),
                "Bookings": st.column_config.ProgressColumn(min_value=0, max_value=bar_max(sc["Bookings"]), format="%d"),
                "Book rate": st.column_config.TextColumn(help="Bookings per 100 opportunities in the period"),
                "Test drive %": st.column_config.TextColumn(),
                "EW %": st.column_config.TextColumn(),
                "Acc. / car (₹L)": st.column_config.TextColumn(),
            },
        )
        if flagged:
            note(f"<b>⚠ Needs a look:</b> {esc(', '.join(flagged))} — lots of opportunities but under half the team's "
                 f"booking rate ({team_rate * 100:.0f} per 100). Worth checking follow-ups and test drives.", warn=True)
        st.caption("Opportunities, test drive %, lost and never-touched: opportunities created in the period · "
                   "Test drives done: by test-drive date · Bookings: by Booking Date · Retail, accessories, EW: retail register.")

    # --------------------------------------------------------------------------- #
    # Lead routing — CRE vs sales managers
    # --------------------------------------------------------------------------- #

    section(
        "Lead routing",
        "Leads in the period split by who owns them: the CRE team ("
        + ", ".join(map(esc, dl.CRE_OWNERS)) + ") or the sales managers.",
    )

    routing = an.lead_routing(leads_f).set_index("Route")
    handovers = an.cre_handovers(leads_f, dl.CRE_OWNERS)


    def route_card(route: str, accent: bool) -> str:
        if route not in routing.index:
            return card(f"Leads with {route}", "0", "No leads in this selection", accent=accent)
        r = routing.loc[route]
        mix = " · ".join(f"{int(r[s_]):,} {s_.lower()}" for s_ in an.LEAD_STATUSES if int(r[s_]))
        return card(f"Leads with {route}", f"{int(r['Leads']):,}",
                    f"{r['Share of leads %']:.1f}% of {len(leads_f):,} leads<br>{mix}", accent=accent)


    route_cards = [route_card(dl.ROUTE_CRE, accent=True), route_card(dl.ROUTE_SM, accent=True)]
    if "Created By" in leads_f:  # the Google Sheet's Leads tab has no Created By column
        route_cards.append(card("Handed over by CRE", f"{handovers:,}",
                                "Created by a CRE, now owned by a sales manager (minimum — many leads have no Created By)"))
    cards(route_cards, columns=len(route_cards))

    owner_table = an.leads_by_owner(leads_f)
    with st.expander(f"Leads by owner ({len(owner_table)} people)", expanded=False):
        st.dataframe(
            owner_table, hide_index=True, width="stretch", height=table_height(len(owner_table)),
            column_config={"Leads": st.column_config.NumberColumn(format="localized")},
        )
    note(
        "C4C's <b>Owner</b> is the lead's <i>current</i> owner. When a CRE qualifies a lead and passes it on, "
        "Owner changes to the sales manager — so leads still with the CRE are mostly on Hold or Cancelled, "
        "and the CRE's successful hand-offs appear under sales managers. The export has no field recording "
        "the first owner."
    )

    # --------------------------------------------------------------------------- #
    # Untouched opportunities — open opportunities with no / stale follow-up
    # --------------------------------------------------------------------------- #

    as_of = an.export_as_of(opps_all)
    section(
        "Untouched opportunities",
        f"Open opportunities (status “Under Follow-up”) created in the selected period, as of the export date "
        f"<b>{as_of:%d %b %Y}</b>.",
    )
    cold_after = st.segmented_control(
        "Going cold after", [3, 7, 14, 30], default=7, format_func=lambda d: f"{d} days", key="cold_after",
        help="An opportunity that has been followed up before, but not within this many days of the export date.",
    ) or 7
    fu = an.follow_up_view(opps_f, as_of, cold_after)
    n_open = len(fu)
    never = fu[fu["follow_up_state"] == an.TOUCH_NEVER]
    cold = fu[fu["follow_up_state"] == an.TOUCH_COLD]
    n_overdue = int(fu["overdue"].sum())


    def share(n: int) -> str:
        return f"{pct(an.safe_rate(n, n_open), 0)} of open" if n_open else "—"


    cards([
        card("Open opportunities", f"{n_open:,}", f"{int((fu['follow_up_state'] == an.TOUCH_ACTIVE).sum()):,} followed up "
             f"in the last {cold_after} days", accent=True),
        card("Never touched", f"{len(never):,}",
             f"{share(len(never))} · <b>{int(never['hot'].sum()):,} Hot</b><br>No follow-up call, visit or note logged",
             flag="Needs a first call" if len(never) else ""),
        card("Going cold", f"{len(cold):,}", f"{share(len(cold))}<br>Last follow-up more than {cold_after} days ago"),
        card("Overdue follow-up", f"{n_overdue:,}", f"{share(n_overdue)}<br>Scheduled activity due before {as_of:%d %b}"),
    ])

    by_rep_fu = an.follow_up_by_rep(fu)
    by_rep_fu["Oldest untouched (days)"] = by_rep_fu["Oldest untouched (days)"].map(
        lambda v: "—" if pd.isna(v) else f"{int(v)}")
    st.markdown('<div class="pulse-subhead">By sales rep</div>', unsafe_allow_html=True)
    st.dataframe(
        by_rep_fu, hide_index=True, width="stretch", height=table_height(len(by_rep_fu)),
        column_config={
            "Never touched": st.column_config.ProgressColumn(
                min_value=0, max_value=bar_max(by_rep_fu["Never touched"]), format="%d"),
            "Oldest untouched (days)": st.column_config.TextColumn(),
        },
    )

    with st.expander(f"List of never-touched opportunities ({len(never):,})", expanded=False):
        listing = never.sort_values(["hot", "days_open"], ascending=[False, False])
        listing_view = pd.DataFrame({
            "Opp ID": listing["ID"],
            "Customer": listing["Customer"],
            "Sales rep": listing["rep"],
            "Qualification": listing.get("ZQualificationLevel"),
            "Source": listing["Source"],
            "Model": listing["model"],
            "Created": listing["Created On"].dt.date,
            "Days open": listing["days_open"],
            "Next activity due": listing[an.NEXT_DUE].dt.date if an.NEXT_DUE in listing else None,
        })
        st.dataframe(listing_view, hide_index=True, width="stretch",
                     height=min(table_height(len(listing_view)), 520))
        st.download_button(
            "Download list (CSV)", listing_view.to_csv(index=False).encode("utf-8"),
            file_name=f"never_touched_opportunities_{as_of:%Y%m%d}.csv", mime="text/csv",
        )

with tab_pipeline:
    # --------------------------------------------------------------------------- #
    # Pipeline — what's likely to buy next
    # --------------------------------------------------------------------------- #

    pipe_as_of = an.export_as_of(opps_all)
    section("What's likely to buy next",
            f"Every open opportunity (status “Under Follow-up”), grouped by how hot it is and when the customer said "
            f"they expect to buy — as of <b>{pipe_as_of:%d %b %Y}</b>. Source / Channel / Model / Rep filters apply; "
            "the date range doesn't (the pipeline is today's position).")
    pipe = ins.pipeline_view(opps_all, filters, pipe_as_of)
    hot = pipe[pipe["Qualification"].eq("Hot")]
    cards([
        card("Open opportunities", f"{len(pipe):,}",
             f"{len(hot):,} Hot · {int(pipe['Qualification'].eq('Warm').sum()):,} Warm · "
             f"{int(pipe['Qualification'].eq('Cold').sum()):,} Cold", accent=True),
        card("Hot — expected in next 7 days", f"{int(hot['Expected'].eq('Next 7 days').sum()):,}",
             "Most likely bookings this week", accent=True),
        card("Hot — expected in 8–30 days", f"{int(hot['Expected'].eq('8–30 days').sum()):,}", "Bookings for the coming weeks"),
        card("Expected date already passed", f"{int(pipe['Expected'].eq('Overdue (date passed)').sum()):,}",
             "Out-of-date — update the expected date in C4C or close",
             flag="Pipeline needs cleaning" if pipe["Expected"].eq("Overdue (date passed)").mean() > 0.3 else ""),
    ])
    grid = pd.crosstab(pipe["Qualification"], pipe["Expected"]).reindex(
        index=[q for q in ["Hot", "Warm", "Cold", "Not set"] if q in set(pipe["Qualification"])],
        columns=ins.EXPECT_ORDER, fill_value=0)
    st.markdown('<div class="pulse-subhead">How hot × when they expect to buy</div>', unsafe_allow_html=True)
    st.dataframe(grid.reset_index().rename(columns={"Qualification": "How hot"}), hide_index=True, width="stretch",
                 height=table_height(len(grid)))
    with st.expander("How long have open opportunities been open?"):
        age_grid = pd.crosstab(pipe["Qualification"], pipe["Age"]).reindex(columns=ins.AGE_ORDER, fill_value=0)
        st.dataframe(age_grid.reset_index().rename(columns={"Qualification": "How hot"}), hide_index=True, width="stretch")
    with st.expander(f"Hot opportunities expected in the next 30 days "
                     f"({int(hot['Expected'].isin(['Next 7 days', '8–30 days']).sum()):,})"):
        due = hot[hot["Expected"].isin(["Next 7 days", "8–30 days"])].sort_values("Expected Purchase Date_Score")
        due_view = pd.DataFrame({
            "Opp ID": due["ID"], "Customer": due["Customer"], "Sales rep": due["rep"], "Source": due["Source"],
            "Model": due["model"], "Expected purchase": due["Expected Purchase Date_Score"].dt.date,
            "Test drive": due["test_drive_done"].map({True: "Yes", False: "No"}),
            "Last follow-up": due["Last Follow up Activity Date"].dt.date,
            "Next activity due": due["Open Activity Due Date"].dt.date if "Open Activity Due Date" in due else None,
        })
        st.dataframe(due_view, hide_index=True, width="stretch", height=min(table_height(len(due_view)), 480))
        st.download_button("Download list (CSV)", due_view.to_csv(index=False).encode("utf-8"),
                           file_name=f"hot_pipeline_{pipe_as_of:%Y%m%d}.csv", mime="text/csv")


    # --------------------------------------------------------------------------- #
    # Booking cancellations
    # --------------------------------------------------------------------------- #

    section("Booking cancellations",
            "Of the bookings made each month (by Booking Date), how many have since been cancelled "
            "(status “Booking Cancelled” or “Intend to Cancel”).")
    cx_months = ins.trend_months(start, end, 12, data.window_start)
    cx = ins.cancellation_trend(opps_all, filters, cx_months)
    cx_sel = cx[cx["Month"].map(lambda m: m.start_time.date() <= end and m.end_time.date() >= start)]
    b_n, c_n = int(cx_sel["Bookings"].sum()), int(cx_sel["Cancelled"].sum())
    avg12 = an.safe_rate(int(cx["Cancelled"].sum()), int(cx["Bookings"].sum()))
    cards([
        card("Bookings made", f"{b_n:,}", f"{start:%d %b} – {end:%d %b %Y}", accent=True),
        card("Since cancelled", f"{c_n:,}", f"{pct(an.safe_rate(c_n, b_n))} of those bookings"),
        card("Cancellation rate, last 12 months", pct(avg12), f"{int(cx['Cancelled'].sum()):,} of {int(cx['Bookings'].sum()):,} bookings"),
    ], columns=3)
    # Hover box for each month's cancelled bookings: who, where from, which model, and the
    # reason if C4C / the sheet ever carries one (any column with "reason" in its name).
    reason_col = next((c_ for c_ in opps_all.columns if "reason" in c_.lower()), None)

    def top_counts(series: pd.Series, n: int = 3) -> str:
        vc = series.fillna("(blank)").value_counts()
        more = f" · +{len(vc) - n} more" if len(vc) > n else ""
        return " · ".join(f"{esc(k)} {v}" for k, v in vc.head(n).items()) + more

    cx_hover = []
    for m, n_b, n_c in zip(cx["Month"], cx["Bookings"], cx["Cancelled"]):
        if not n_c:
            cx_hover.append(f"<b>{m.strftime('%b %Y')}</b><br>No cancellations of {n_b} bookings")
            continue
        mb = ins.bookings_in_period(opps_all, an.Filters(m.start_time.date(), m.end_time.date(), sel_sources,
                                                         sel_channels, sel_models, sel_reps))
        mc = mb[mb["Status"].isin(ins.CANCELLED_STATUSES)]
        reasons = mc[reason_col].dropna().astype(str).str.strip() if reason_col else pd.Series(dtype=str)
        reasons = reasons[reasons.ne("")]
        cx_hover.append(
            f"<b>{m.strftime('%b %Y')} · {n_c} cancelled of {n_b} bookings ({n_c / n_b * 100:.0f}%)</b><br>"
            f"By source: {top_counts(mc['Source'])}<br>By rep: {top_counts(mc['rep'])}<br>"
            f"By model: {top_counts(mc['model'])}<br>"
            + (f"Reasons: {top_counts(reasons)}" if len(reasons) else "<i>Reason: not recorded in C4C</i>"))

    fig = go.Figure()
    fig.add_bar(x=[m.strftime("%b %y") for m in cx["Month"]], y=cx["Bookings"] - cx["Cancelled"], name="Still booked / delivered",
                marker=dict(color=PALETTE["primary"], cornerradius=3),
                hovertemplate="%{x}: %{y} kept<extra></extra>")
    fig.add_bar(x=[m.strftime("%b %y") for m in cx["Month"]], y=cx["Cancelled"], name="Cancelled",
                marker=dict(color=PALETTE["gold"], cornerradius=3),
                text=[f"{p:.0f}%" if p == p and p is not None else "" for p in cx["Cancellation %"]], textposition="outside",
                textfont=dict(color=PALETTE["text"], size=11), cliponaxis=False,
                customdata=cx_hover, hovertemplate="%{customdata}<extra></extra>")
    base_layout(fig, 320)
    fig.update_layout(barmode="stack", bargap=0.35, legend=dict(y=1.12))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption("Label on each bar = share of that month's bookings now cancelled. "
               "Hover the gold part for who, which source and which model.")
    cancelled_sel = ins.bookings_in_period(opps_all, filters)
    cancelled_sel = cancelled_sel[cancelled_sel["Status"].isin(ins.CANCELLED_STATUSES)]
    cx_l, cx_r = st.columns(2, gap="medium")
    with cx_l:
        st.markdown(source_card("Cancelled bookings", cancelled_sel, "cancelled"), unsafe_allow_html=True)
    with cx_r:
        by_rep_cx = cancelled_sel["rep"].value_counts().rename_axis("Sales rep").reset_index(name="Cancelled")
        st.markdown('<div class="pulse-subhead">By sales rep</div>', unsafe_allow_html=True)
        st.dataframe(by_rep_cx, hide_index=True, width="stretch", height=table_height(len(by_rep_cx)))
    if getattr(data, "inv_cancelled", None) is not None and len(getattr(data, "inv_cancelled", None)):
        with st.expander(f"Why cars were cancelled after invoicing ({len(getattr(data, "inv_cancelled", None))}, from the “Inv Cancelled” tab)"):
            ic = getattr(data, "inv_cancelled", None)
            st.dataframe(pd.DataFrame({
                "Cancelled month": ic.get("Cancelled Month"), "Customer": ic.get("Customer Name"),
                "Sales rep": ic.get("Sm Name"), "Model": ic.get("Model"), "Source": ic.get("Source"),
                "Reason": ic.get("Remark"),
            }), hide_index=True, width="stretch")
    note("C4C doesn't record <i>why</i> a booking was cancelled (the notes on cancelled bookings just say “Booked” etc.). "
         "Reasons are only available for invoice cancellations, from the “Inv Cancelled” tab. Adding a short reason "
         "when cancelling a booking in C4C would make this section much more useful.")

    # --------------------------------------------------------------------------- #
    # Lost opportunities — why they were lost
    # --------------------------------------------------------------------------- #

    section(
        "Lost opportunities",
        "Why opportunities created in the selected period were closed as Lost — read from the closing note "
        "written on each one (C4C's own loss-reason field is empty in the export).",
    )
    lost = lr.lost_view(opps_f)
    reasons = lr.reason_summary(lost)
    n_lost = len(lost)
    dup = int(lost["loss_reason"].eq("Bought from MG (duplicate record)").sum())
    not_contact = int(lost["loss_reason"].str.startswith("Not contactable").sum())
    cards([
        card("Lost", f"{n_lost:,}", f"{pct(an.safe_rate(n_lost, len(opps_f)), 0)} of {len(opps_f):,} opportunities created",
             accent=True),
        card("Not contactable", f"{not_contact:,}", f"{pct(an.safe_rate(not_contact, n_lost), 0)} of lost · closed after "
             "repeated unanswered calls"),
        card("Test drive done, still lost", f"{int(lost['test_drive_done'].sum()):,}",
             f"{pct(an.safe_rate(int(lost['test_drive_done'].sum()), n_lost), 0)} of lost had a test drive"),
        card("Not really lost", f"{dup:,}", "Customer bought from MG under another number / name — duplicate record",
             flag="Check these records" if dup else ""),
    ])

    if n_lost:
        chart = reasons.sort_values("Lost")
        examples = {
            r: "<br>".join("• " + esc(str(t)[:90]) + ("…" if len(str(t)) > 90 else "")
                           for t in lost.loc[lost["loss_reason"] == r, lr.NOTES_COLUMN].head(2))
            for r in chart["Reason"]
        }
        fig = go.Figure(go.Bar(
            x=chart["Lost"], y=chart["Reason"], orientation="h",
            marker=dict(color=PALETTE["primary"], cornerradius=4),
            text=[f"{n}  ·  {p_:.0f}%" for n, p_ in zip(chart["Lost"], chart["Share %"])],
            textposition="outside", cliponaxis=False, textfont=dict(color=PALETTE["text"]),
            hovertext=[f"<b>{esc(r)}</b><br>{n} lost · {p_:.1f}% · {td} had a test drive<br><br>{examples[r]}"
                       f"<br><i>Click to see all notes</i>"
                       for r, n, p_, td in zip(chart["Reason"], chart["Lost"], chart["Share %"], chart["Test drive done"])],
            hoverinfo="text",
        ))
        base_layout(fig, max(220, 40 * len(chart) + 40))
        fig.update_layout(margin=dict(l=10, r=90, t=10, b=10), clickmode="event+select", dragmode=False)
        fig.update_xaxes(showticklabels=False, showgrid=False)
        fig.update_yaxes(tickfont=dict(color=PALETTE["text"], size=13))
        fig.update_traces(unselected=dict(marker=dict(opacity=0.45)))
        loss_event = st.plotly_chart(fig, width="stretch", config={"displayModeBar": False},
                                     on_select="rerun", selection_mode="points", key="loss_chart")
        st.caption("Hover a reason for example notes; click it to list every lost opportunity with that reason.")

        pts = getattr(getattr(loss_event, "selection", None), "points", None) or []
        picked_reason = pts[0].get("y") if pts else None
        if picked_reason:
            rows_ = lost[lost["loss_reason"] == picked_reason]
            st.markdown(f'<div class="pulse-subhead">{esc(picked_reason)} · {len(rows_)} opportunities</div>',
                        unsafe_allow_html=True)
            st.dataframe(pd.DataFrame({
                "Opp ID": rows_["ID"], "Customer": rows_["Customer"], "Sales rep": rows_["rep"],
                "Source": rows_["Source"], "Test drive": rows_["test_drive_done"].map({True: "Yes", False: "No"}),
                "Closed by": rows_["closed_by"].fillna("—"), "Closing note": rows_[lr.NOTES_COLUMN],
            }), hide_index=True, width="stretch", height=min(table_height(len(rows_)), 420))

        with st.expander("Loss reasons by sales rep", expanded=False):
            by_rep_loss = pd.crosstab(lost["rep"], lost["loss_reason"]).reindex(
                columns=[r for r in lr.REASON_ORDER if r in set(lost["loss_reason"])], fill_value=0)
            by_rep_loss.insert(0, "Lost", by_rep_loss.sum(axis=1))
            by_rep_loss = by_rep_loss.sort_values("Lost", ascending=False).reset_index().rename(columns={"rep": "Sales rep"})
            st.dataframe(by_rep_loss, hide_index=True, width="stretch", height=table_height(len(by_rep_loss)))

        with st.expander(f"All lost opportunities with closing notes ({n_lost:,})", expanded=False):
            all_lost = pd.DataFrame({
                "Opp ID": lost["ID"], "Customer": lost["Customer"], "Sales rep": lost["rep"], "Source": lost["Source"],
                "Model": lost["model"], "Created": lost["Created On"].dt.date,
                "Test drive": lost["test_drive_done"].map({True: "Yes", False: "No"}),
                "Reason": lost["loss_reason"], "Closed by": lost["closed_by"].fillna("—"),
                "Closing note": lost[lr.NOTES_COLUMN],
            }).sort_values(["Reason", "Created"])
            st.dataframe(all_lost, hide_index=True, width="stretch", height=min(table_height(len(all_lost)), 520))
            st.download_button("Download lost opportunities (CSV)", all_lost.to_csv(index=False).encode("utf-8"),
                               file_name="lost_opportunities.csv", mime="text/csv")

    note(
        "<b>How reasons are assigned:</b> each Lost opportunity's closing note is matched against keyword rules "
        "(e.g. “not contactable”, “postponed”, “budget”, a competitor brand) — the first match wins, and anything "
        "unmatched shows as “Other / unclear”. Click a reason to read the actual notes behind it. "
        "<b>Follow-up counts per opportunity can't be calculated from this export</b>: C4C keeps only the latest "
        "activity on each opportunity. Most “not contactable” notes record the closing team's standard "
        "4-call attempt, not the sales rep's follow-ups."
    )

with tab_retail:
    section("Accessories & value-adds", "Finance view from the retail register — filtered by TR DATE, source, model and rep.")

    k = an.value_add_kpis(retail_f)
    cards([
        card("Accessories revenue", inr_short(k["accessories_total"]),
             f"{k['units']:,} retail units · {k['units_with_accessories']:,} with accessories", accent=True),
        card("Avg accessories / unit", inr_short(k["accessories_avg"]),
             f"Total ÷ {k['accessories_known']:,} units with an accessories entry (incl. ₹0)", accent=True),
        card("EW attach", pct(k["ew_rate"]), f"of {k['ew_known']:,} units with an EW entry", accent=True),
        card("AMC attach", pct(k["amc_rate"]), f"of {k['amc_known']:,} units with an AMC entry", accent=True),
        card("PPF attach", pct(k["ppf_rate"]), f"of {k['ppf_known']:,} units with a PPF entry", accent=True),
    ])

    unrecorded = int(retail_f["accessories_amount"].isna().sum())
    if unrecorded:
        note(f"<b>{unrecorded:,} of {k['units']:,} retail units</b> in this range have no accessories / EW / AMC / PPF "
             "entry — the retail register only started recording these from <b>June 2026</b>. They count as retail "
             "units, but are left out of the averages and attach rates above.", warn=True)

    left, right = st.columns([1, 1.25], gap="medium")

    with left:
        st.markdown('<div class="pulse-subhead">EW / AMC / PPF taken, by model</div>', unsafe_allow_html=True)
        by_model = an.value_add_breakdown(retail_f, "model")
        if by_model.empty:
            st.caption("No retail units in this selection.")
        else:
            colors = model_colors(by_model["model"].tolist())
            fig = go.Figure()
            for _, row in by_model.set_index("model").loc[list(colors)].reset_index().iterrows():
                y = [row["EW %"], row["AMC %"], row["PPF %"]]
                fig.add_bar(
                    name=f"{row['model']} ({int(row['Units'])} units)",
                    x=["EW", "AMC", "PPF"], y=y,
                    marker=dict(color=colors[row["model"]], cornerradius=4, line=dict(width=0)),
                    text=["n/a" if pd.isna(v) else f"{v:.0f}%" for v in y], textposition="outside",
                    textfont=dict(color=PALETTE["text"]),
                    hovertemplate=f"<b>{row['model']}</b><br>%{{x}} attach: %{{y:.1f}}%<extra></extra>",
                )
            base_layout(fig, 320)
            fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.08)
            fig.update_yaxes(range=[0, 110], ticksuffix="%", title=None)
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            small = by_model[by_model["Units"] < 10]["model"].tolist()
            if small:
                note(f"Small sample: {', '.join(map(esc, small))} has fewer than 10 units — treat its rates as indicative.", warn=True)

        model_table = by_model.rename(columns={"model": "Model"}).copy()
        model_table["Accessories ₹"] = model_table["Accessories ₹"] / 1e5
        model_table["Avg ₹ / unit"] = model_table["Avg ₹ / unit"] / 1e5
        st.dataframe(
            model_table, hide_index=True, width="stretch",
            column_config={
                "Accessories ₹": st.column_config.NumberColumn("Accessories (₹ L)", format="%.2f"),
                "Avg ₹ / unit": st.column_config.NumberColumn("Avg / unit (₹ L)", format="%.2f"),
                "EW %": st.column_config.NumberColumn(format="%.0f%%"),
                "AMC %": st.column_config.NumberColumn(format="%.0f%%"),
                "PPF %": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )

    with right:
        st.markdown('<div class="pulse-subhead">By sales rep (SM)</div>', unsafe_allow_html=True)
        by_rep = an.value_add_breakdown(retail_f, "rep")
        if by_rep.empty:
            st.caption("No retail units in this selection.")
        else:
            chart = by_rep.sort_values("Accessories ₹")

            def yes(frame: pd.DataFrame, col: str) -> int:
                return int(frame[col].fillna(False).sum())

            def known(frame: pd.DataFrame, col: str) -> int:
                """Units with a Yes/No entry (EW/AMC/PPF weren't recorded before Jun 2026)."""
                return int(frame[col].notna().sum())

            def rep_hover(rep: str) -> str:
                """Hover: value-adds sold, then retail units by lead source."""
                d = retail_f[retail_f["rep"] == rep]
                n = len(d)
                lines = [
                    f"<b>{esc(rep)}</b> · {n} retail unit{'s' if n != 1 else ''}",
                    f"Accessories {inr_short(d['accessories_amount'].sum())} "
                    f"({int(d['accessories_amount'].gt(0).sum())} of {int(d['accessories_amount'].notna().sum())} recorded units)",
                    f"EW {yes(d, 'ew_flag')}/{known(d, 'ew_flag')} · AMC {yes(d, 'amc_flag')}/{known(d, 'amc_flag')} · "
                    f"PPF {yes(d, 'ppf_flag')}/{known(d, 'ppf_flag')}",
                    "",
                    "<b>By source</b>",
                ]
                for src_name, g in d.groupby(d["Source"].fillna("(blank)")):
                    lines.append(f"{esc(src_name)}: {len(g)} unit{'s' if len(g) != 1 else ''} · "
                                 f"{inr_short(g['accessories_amount'].sum())}")
                lines.append("<i>Click for the full split</i>")
                return "<br>".join(lines)

            fig = go.Figure(go.Bar(
                x=chart["Accessories ₹"], y=chart["rep"], orientation="h",
                marker=dict(color=PALETTE["primary"], cornerradius=4),
                text=[inr_short(v) for v in chart["Accessories ₹"]], textposition="outside",
                textfont=dict(color=PALETTE["text"]), cliponaxis=False,
                hovertext=[rep_hover(r) for r in chart["rep"]], hoverinfo="text",
            ))
            base_layout(fig, max(260, 34 * len(chart) + 40))
            fig.update_layout(margin=dict(l=10, r=70, t=10, b=10), clickmode="event+select", dragmode=False)
            fig.update_xaxes(showticklabels=False, showgrid=False)
            fig.update_traces(unselected=dict(marker=dict(opacity=0.45)))
            rep_event = st.plotly_chart(
                fig, width="stretch", config={"displayModeBar": False},
                on_select="rerun", selection_mode="points", key="rep_chart",
            )
            st.caption("Hover a rep for a quick split; click for the full value-add breakdown by source.")

            rep_points = getattr(getattr(rep_event, "selection", None), "points", None) or []
            picked = rep_points[0].get("y") if rep_points else None
            if picked:
                d = retail_f[retail_f["rep"] == picked]
                n = len(d)

                def attach(col: str) -> str:
                    k_, kn = yes(d, col), known(d, col)
                    return f"{k_}/{kn} <span class='src-pct'>({k_ / kn * 100:.0f}%)</span>" if kn else "not recorded"

                summary = (
                    f'<div class="va-grid">'
                    f'<div><div class="cap">Accessories</div><div class="num">{inr_short(d["accessories_amount"].sum())}</div>'
                    f'<div class="cap2">{int(d["accessories_amount"].gt(0).sum())} of {int(d["accessories_amount"].notna().sum())} recorded units · avg {inr_short(d["accessories_amount"].mean() if d["accessories_amount"].notna().any() else None)}</div></div>'
                    f'<div><div class="cap">EW</div><div class="num">{attach("ew_flag")}</div></div>'
                    f'<div><div class="cap">AMC</div><div class="num">{attach("amc_flag")}</div></div>'
                    f'<div><div class="cap">PPF</div><div class="num">{attach("ppf_flag")}</div></div>'
                    f'</div>'
                )
                by_src = (
                    d.assign(Source=d["Source"].fillna("(blank)"))
                    .groupby("Source")
                    .agg(units=("rep", "size"), acc=("accessories_amount", "sum"),
                         ew=("ew_flag", lambda x: int(x.fillna(False).sum())),
                         amc=("amc_flag", lambda x: int(x.fillna(False).sum())),
                         ppf=("ppf_flag", lambda x: int(x.fillna(False).sum())))
                    .sort_values(["units", "acc"], ascending=False)
                )
                rows = "".join(
                    f"<tr><td>{esc(src_name)}</td><td>{r.units}</td><td>{inr_short(r.acc)}</td>"
                    f"<td>{r.ew}</td><td>{r.amc}</td><td>{r.ppf}</td></tr>"
                    for src_name, r in by_src.iterrows()
                )
                table = ("<table class='va-table'><thead><tr><th>Source</th><th>Units</th><th>Accessories</th>"
                         f"<th>EW</th><th>AMC</th><th>PPF</th></tr></thead><tbody>{rows}</tbody></table>")
                st.markdown(
                    f'<div class="src-wrap wide"><div class="pulse-card src-card">'
                    f'<div class="label">{esc(picked)} · value-adds</div>'
                    f'<div class="value">{n}<span class="src-unit"> retail units</span></div>'
                    f'{summary}<div class="pulse-subhead" style="margin:14px 0 6px">By lead source</div>{table}'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

        rep_table = by_rep.rename(columns={"rep": "Sales rep"}).copy()
        rep_table["Accessories ₹"] = rep_table["Accessories ₹"] / 1e5
        rep_table["Avg ₹ / unit"] = rep_table["Avg ₹ / unit"] / 1e5
        st.dataframe(
            rep_table, hide_index=True, width="stretch", height=table_height(len(rep_table)),
            column_config={
                "Accessories ₹": st.column_config.NumberColumn("Accessories (₹ L)", format="%.2f"),
                "Avg ₹ / unit": st.column_config.NumberColumn("Avg / unit (₹ L)", format="%.2f"),
                "EW %": st.column_config.NumberColumn(format="%.0f%%"),
                "AMC %": st.column_config.NumberColumn(format="%.0f%%"),
                "PPF %": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )

    # --------------------------------------------------------------------------- #
    # Finance & insurance
    # --------------------------------------------------------------------------- #

    section("Finance & insurance",
            "How customers paid for the cars delivered in the selected dates, and how many took finance and "
            "insurance through us (from the retail register's FINANCE and Insurance columns).")
    fm = ins.finance_mix(retail_f)
    counts = fm["finance_counts"]
    known = fm["finance_known"]
    if known:
        # One base for every share: deliveries whose FINANCE column is filled in.
        mix = [(lbl, int(counts.get(lbl, 0))) for lbl in ["In-house finance", "Outside finance", "Cash", "Leasing"]]
        mix += [(lbl, int(n)) for lbl, n in counts.items() if lbl not in dict(mix)]
        mix = [(lbl, n) for lbl, n in mix if n]
        mix_colors = {"In-house finance": PALETTE["primary"], "Outside finance": PALETTE["gold"],
                      "Cash": "#6E8FA3", "Leasing": "#9A9A9A"}
        fig = go.Figure()
        for lbl, n in mix:
            share = n / known * 100
            fig.add_bar(y=["How they paid"], x=[share], orientation="h", name=lbl,
                        marker=dict(color=mix_colors.get(lbl, "#C9C2B4"), line=dict(width=2, color="#FFFFFF")),
                        text=[f"<b>{lbl}</b> {share:.0f}%" if share >= 9 else ""], textposition="inside",
                        insidetextanchor="middle", textfont=dict(color="#FFFFFF", size=13),
                        hovertemplate=f"<b>{lbl}</b><br>{n:,} of {known:,} deliveries · {share:.0f}%<extra></extra>")
        base_layout(fig, 110)
        fig.update_layout(barmode="stack", showlegend=False, margin=dict(l=0, r=0, t=6, b=6))
        fig.update_xaxes(range=[0, 100], visible=False)
        fig.update_yaxes(visible=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    loans = fm["loans"]
    cards([
        card("In-house finance", pct(an.safe_rate(int(counts.get('In-house finance', 0)), known), 0),
             f"{int(counts.get('In-house finance', 0)):,} of {known:,} deliveries", accent=True),
        card("Outside finance", pct(an.safe_rate(int(counts.get('Outside finance', 0)), known), 0),
             f"{int(counts.get('Outside finance', 0)):,} of {known:,} deliveries — finance income we missed"),
        card("Paid cash", pct(an.safe_rate(int(counts.get('Cash', 0)), known), 0),
             f"{int(counts.get('Cash', 0)):,} of {known:,} deliveries"),
        card("In-house insurance", pct(fm["insurance_in_house"], 0), f"of {fm['insurance_known']:,} deliveries", accent=True),
    ])
    if loans:
        st.caption(f"Of the {loans:,} customers who took a loan, {pct(fm['in_house_share_of_loans'], 0)} took it through us.")
    with st.expander("Finance & insurance by sales rep and model"):
        fr_l, fr_r = st.columns(2, gap="medium")
        with fr_l:
            st.markdown('<div class="pulse-subhead">By sales rep</div>', unsafe_allow_html=True)
            st.dataframe(ins.finance_by(retail_f, "rep").rename(columns={"rep": "Sales rep"}), hide_index=True,
                         width="stretch", column_config={c: st.column_config.NumberColumn(format="%.0f%%")
                                                         for c in ["In-house finance % of loans", "In-house insurance %"]})
        with fr_r:
            st.markdown('<div class="pulse-subhead">By model</div>', unsafe_allow_html=True)
            st.dataframe(ins.finance_by(retail_f, "model").rename(columns={"model": "Model"}), hide_index=True,
                         width="stretch", column_config={c: st.column_config.NumberColumn(format="%.0f%%")
                                                         for c in ["In-house finance % of loans", "In-house insurance %"]})


    # --------------------------------------------------------------------------- #
    # Stock & payments
    # --------------------------------------------------------------------------- #

    section("Stock & payments",
            "Cars on order, in transit and at the dealership, from the “Stock” tab — today's position "
            "(Model and Rep filters apply; the date range doesn't).")
    if getattr(data, "stock", None) is None or getattr(data, "stock", None).empty:
        note("Stock appears once the dashboard reads the daily sheet (the “Stock” tab) — coming with the Zoho Sheet connection.")
    else:
        if getattr(data, "stock_as_of", ""):
            note(f"The Google Sheet can't be read right now, so this is the <b>last saved stock</b> "
                 f"({esc(data.stock_as_of)}).", warn=True)
        sv = ins.stock_view(getattr(data, "stock", None), filters)
        at_dealer = sv["Location"].isin(["Showroom", "Kkp"])
        free = sv[sv["Sale status"].eq("FREE")]
        bbnd = sv[sv["Sale status"].eq("BBND")]
        cards([
            card("Cars in stock pipeline", f"{len(sv):,}",
                 f"{int(at_dealer.sum()):,} at the dealership · {int(sv['Location'].eq('Intransit').sum()):,} in transit · "
                 f"{int(sv['Location'].eq('Not Dispatched').sum()):,} not dispatched yet", accent=True),
            card("Free (unsold)", f"{len(free):,}",
                 f"Oldest {int(free['Age since received (days)'].max()):,} days since received" if free["Age since received (days)"].notna().any()
                 else "None received yet"),
            card("Booked, not yet delivered", f"{len(bbnd):,}", f"Balance to collect {inr_short(bbnd['Balance ₹'].sum())}", accent=True),
            card("Payment received so far", inr_short(sv["Received ₹"].sum()), f"Balance pending {inr_short(sv['Balance ₹'].sum())}"),
        ])
        st.markdown('<div class="pulse-subhead">How long cars have been with us × sale status</div>', unsafe_allow_html=True)
        age_status = pd.crosstab(sv["Sale status (plain)"], sv["Age band"]).reindex(columns=ins.STOCK_AGE_ORDER, fill_value=0)
        st.dataframe(age_status.reset_index().rename(columns={"Sale status (plain)": "Sale status"}), hide_index=True,
                     width="stretch", height=table_height(len(age_status)))
        this_month = pd.Period(pd.Timestamp.today(), "M")
        due = sv[sv["Planned delivery"].notna() & sv["Planned delivery"].map(lambda d: pd.Period(d, "M") <= this_month)]
        with st.expander(f"Deliveries planned up to the end of {this_month.strftime('%B')} ({len(due):,})"):
            st.dataframe(pd.DataFrame({
                "Planned delivery": due["Planned delivery"].dt.date, "Customer": due["Customer"],
                "Sales rep": due["Sales Manager"], "Model": due["Model"], "Colour": due["Colour"],
                "Received": due["Received ₹"].map(inr_short), "Balance": due["Balance ₹"].map(inr_short),
                "Payment expected": due["Payment expected (as typed)"], "Purchase mode": due["Purchase mode"],
            }).sort_values("Planned delivery"), hide_index=True, width="stretch")
        with st.expander(f"All stock ({len(sv):,} cars)"):
            st.dataframe(sv[["Model", "Colour", "Location", "Sale status (plain)", "Age since received (days)",
                             "Customer", "Sales Manager", "Planned delivery (as typed)", "Received ₹", "Balance ₹",
                             "Purchase mode", "Remarks"]], hide_index=True, width="stretch")

with tab_insights:
    # --------------------------------------------------------------------------- #
    # Insights — advanced, visual analytics
    # --------------------------------------------------------------------------- #

    MIN_SHOW = 10  # bars with fewer closed opportunities than this are left out

    def takeaway(text: str) -> None:
        st.markdown(f'<div class="takeaway">💡 {text}</div>', unsafe_allow_html=True)

    # Patterns need more than a month of data to be reliable: these views look back
    # over several months ending with the selected end date (other filters apply).
    look_back = st.segmented_control("Analyse the last", [3, 6, 12], default=6,
                                     format_func=lambda n: f"{n} months", key="insight_months") or 6
    ins_start = max((pd.Period(end, "M") - (look_back - 1)).start_time.date(), data.window_start)
    f_ins = an.Filters(ins_start, end, sel_sources, sel_channels, sel_models, sel_reps)
    opps_ins = an.filter_opportunities(opps_all, f_ins)
    st.caption(f"Charts below use opportunities created {ins_start:%d %b %Y} – {end:%d %b %Y} "
               f"({len(opps_ins):,} opportunities). The lead calendar uses the date range in the filters.")

    # 0. Patterns found automatically --------------------------------------------------
    section("Patterns we found",
            "The dashboard scans every split (source, test drive, how hot, model, weekday, and source × test drive) "
            "and picks the groups whose booking rate is furthest from the average — weighted by how many "
            "opportunities they cover, so one-off flukes don't show up.")
    found = adv.biggest_patterns(opps_ins, top=3)
    if not found:
        st.caption("Not enough closed opportunities in this selection to find patterns.")
    else:
        pat_cards = []
        for p_ in found:
            better = p_["Diff"] >= 0
            ratio = p_["Rate"] / p_["Avg"] if p_["Avg"] else None
            times = (f"{ratio:.1f}× the average" if better and ratio and ratio >= 1.5 else
                     f"1 in {round(100 / p_['Rate'])} books" if not better and p_["Rate"] >= 1 else
                     "almost none book" if not better else f"{p_['Diff']:+.0f} points vs average")
            pat_cards.append(
                f'<div class="pulse-card pat-card {"good" if better else "bad"}">'
                f'<div class="pat-tag">{"▲ Books more" if better else "▼ Books less"}</div>'
                f'<div class="pat-head">{esc(p_["Group"])}</div>'
                f'<div class="pat-num">{p_["Rate"]:.0f}%<span> booked · {times}</span></div>'
                f'<div class="sub">Average {p_["Avg"]:.0f}% · based on {p_["n"]:,} closed opportunities</div></div>')
        cards(pat_cards, columns=3)

    # 1. What drives bookings ----------------------------------------------------
    section("What makes a customer book?",
            "Of the opportunities from the period above that are now closed (booked or lost), "
            "the share that booked — split by test drive, how hot they were, and source. "
            "Teal = books better than average, gold = books worse.")
    drv, avg_rate, n_closed = adv.booking_drivers(opps_ins)
    if drv.empty:
        st.caption("Not enough closed opportunities in this selection.")
    else:
        # Three compact cards (one per factor). Each row's bar is the booking rate on a
        # shared scale; teal = at or above the average, gold = below. The thin dark
        # marker on every track is the average.
        scale = max(50.0, float(np.ceil(drv["Booking rate %"].max() / 10) * 10))
        avg_left = avg_rate / scale * 100
        HEADLINES = {"Test drive": "Did they take a test drive?", "How hot": "How hot did the rep mark them?",
                     "Source": "Where did they come from?"}
        drv_cards = []
        for fac in ["Test drive", "How hot", "Source"]:
            d = drv[drv["Factor"] == fac].sort_values("Booking rate %", ascending=False)
            if d.empty:
                continue
            rows = "".join(
                f'<div class="drv-row" title="{esc(r["Group"])}: {r["Booking rate %"]:.0f}% booked of '
                f'{int(r["Opportunities"]):,} closed opportunities">'
                f'<span class="drv-name">{esc(r["Group"])}</span>'
                f'<span class="drv-track"><span class="drv-fill{"" if r["Booking rate %"] >= avg_rate else " below"}" '
                f'style="width:{min(r["Booking rate %"] / scale * 100, 100):.1f}%"></span>'
                f'<span class="drv-avg" style="left:{avg_left:.1f}%"></span></span>'
                f'<span class="drv-val{"" if r["Booking rate %"] >= avg_rate else " below"}">'
                f'{r["Booking rate %"]:.0f}%</span></div>'
                for _, r in d.iterrows())
            drv_cards.append(f'<div class="pulse-card drv-card"><div class="label">{esc(fac)}</div>'
                             f'<div class="drv-q">{esc(HEADLINES[fac])}</div>{rows}</div>')
        cards(drv_cards, columns=3)
        st.markdown(f'<div class="drv-legend"><span class="drv-key"></span> Above average '
                    f'<span class="drv-key below"></span> Below average '
                    f'<span class="drv-key-avg"></span> Average {avg_rate:.0f}% · bars run 0–{scale:.0f}% · '
                    f'hover a row for the numbers</div>', unsafe_allow_html=True)
        td = drv[drv["Factor"] == "Test drive"].set_index("Group")["Booking rate %"]
        src_ = drv[drv["Factor"] == "Source"].sort_values("Booking rate %")
        bits = []
        if {"Test drive done", "No test drive"} <= set(td.index) and td["No test drive"] > 0:
            bits.append(f"A test drive makes a booking <b>{td['Test drive done'] / td['No test drive']:.1f}× more likely</b> "
                        f"({td['Test drive done']:.0f}% vs {td['No test drive']:.0f}%).")
        if len(src_) >= 2:
            bits.append(f"Best source: <b>{esc(src_.iloc[-1]['Group'])}</b> ({src_.iloc[-1]['Booking rate %']:.0f}%); "
                        f"weakest: <b>{esc(src_.iloc[0]['Group'])}</b> ({src_.iloc[0]['Booking rate %']:.0f}%).")
        takeaway(" ".join(bits) + f" Based on {n_closed:,} closed opportunities.")

    # 1b. Test drive effect by source ------------------------------------------------
    section("Where does a test drive matter most?",
            "Booking rate for each source with and without a test drive. The longer the line, the more a test "
            "drive changes the outcome for that source.")
    tde = adv.td_effect_by_source(opps_ins)
    if tde.empty:
        st.caption("Not enough closed opportunities in this selection.")
    else:
        d = tde.sort_values("Lift")
        fig = go.Figure()
        for _, r in d.iterrows():
            fig.add_shape(type="line", x0=r["Without test drive %"], x1=r["With test drive %"], y0=r["Source"],
                          y1=r["Source"], line=dict(color="#D9D3C7", width=4), layer="below")
        fig.add_scatter(x=d["Without test drive %"], y=d["Source"], mode="markers+text", name="No test drive",
                        marker=dict(size=14, color=PALETTE["gold"], line=dict(width=2, color="#FFFFFF")),
                        text=[f"{v:.0f}%" for v in d["Without test drive %"]],
                        textposition=["middle left" if a <= b else "middle right"
                                      for a, b in zip(d["Without test drive %"], d["With test drive %"])],
                        textfont=dict(size=12, color=PALETTE["muted"]), customdata=d["Without n"],
                        hovertemplate="<b>%{y}</b> · no test drive<br>%{x:.0f}% booked of %{customdata}<extra></extra>")
        fig.add_scatter(x=d["With test drive %"], y=d["Source"], mode="markers+text", name="With test drive",
                        marker=dict(size=14, color=PALETTE["primary"], line=dict(width=2, color="#FFFFFF")),
                        text=[f"<b>{v:.0f}%</b>" for v in d["With test drive %"]],
                        textposition=["middle right" if a <= b else "middle left"
                                      for a, b in zip(d["Without test drive %"], d["With test drive %"])],
                        textfont=dict(size=12, color=PALETTE["text"]), customdata=d["With n"],
                        hovertemplate="<b>%{y}</b> · with test drive<br>%{x:.0f}% booked of %{customdata}<extra></extra>")
        base_layout(fig, 60 + 44 * len(d))
        fig.update_layout(legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=30, t=40, b=10))
        fig.update_xaxes(range=[-6, 106], ticksuffix="%", showgrid=True, gridcolor=PALETTE["grid"])
        fig.update_yaxes(showgrid=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        top_ = tde.iloc[0]
        flat = tde[tde["Lift"].abs() < 10]["Source"].tolist()
        takeaway(f"<b>{esc(top_['Source'])}</b>: a test drive lifts booking from {top_['Without test drive %']:.0f}% to "
                 f"<b>{top_['With test drive %']:.0f}%</b>. For phone and digital leads, the job is to get them in for "
                 f"a drive." + (f" For {esc(', '.join(flat))} it hardly matters — they come in ready to buy." if flat else ""))

    # 1c. Test drive speed --------------------------------------------------------------
    section("How soon should the test drive happen?",
            "Booking rate by how many days after the opportunity was created the first test drive took place.")
    tds = adv.td_speed(opps_ins)
    tds = tds[tds["Opportunities"] >= MIN_SHOW]
    if tds.empty:
        st.caption("Not enough closed opportunities in this selection.")
    else:
        colors_ = [PALETTE["gold"] if w == "No test drive" else PALETTE["primary"] for w in tds["When"]]
        fig = go.Figure(go.Bar(
            x=tds["When"], y=tds["Booking rate %"], marker=dict(color=colors_, cornerradius=6), width=0.55,
            text=[f"<b>{v:.0f}%</b>" for v in tds["Booking rate %"]], textposition="outside", cliponaxis=False,
            textfont=dict(size=13, color=PALETTE["text"]), customdata=tds["Opportunities"],
            hovertemplate="<b>%{x}</b><br>%{y:.0f}% booked · %{customdata} closed opportunities<extra></extra>"))
        base_layout(fig, 300)
        fig.update_layout(showlegend=False, margin=dict(l=10, r=10, t=30, b=10))
        fig.update_yaxes(range=[0, max(tds["Booking rate %"].max() * 1.2, 10)], ticksuffix="%")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        r_ = tds.set_index("When")["Booking rate %"]
        if {"Same day", "4–7 days"} <= set(r_.index):
            takeaway(f"Test drive the <b>same day → {r_['Same day']:.0f}% book</b>; wait 4–7 days → only "
                     f"{r_['4–7 days']:.0f}%. Getting the drive done in the first 3 days matters more than anything else. "
                     "(Part of the same-day figure is customers already in the showroom.)")

    # 1d. Weekday × source ------------------------------------------------------------------
    section("Which day and source convert best?",
            "Booking rate by the weekday the opportunity was created and where it came from. Darker = books more. "
            f"Blank = fewer than {adv.MIN_CELL} opportunities.")
    wk_rate, wk_n = adv.weekday_source(opps_ins)
    if wk_rate.dropna(how="all").empty:
        st.caption("Not enough closed opportunities in this selection.")
    else:
        fig = go.Figure(go.Heatmap(
            z=wk_rate.values, x=list(wk_rate.columns), y=list(wk_rate.index),
            colorscale=[[0, "#F4F1EA"], [0.5, "#7FB0AE"], [1, "#1B5F62"]], zmin=0, zmax=80, xgap=4, ygap=4,
            text=[["" if pd.isna(v) else f"{v:.0f}%" for v in row] for row in wk_rate.values],
            texttemplate="%{text}", textfont=dict(size=12), customdata=wk_n.values,
            hovertemplate="<b>%{y} · %{x}</b><br>%{z:.0f}% booked of %{customdata:.0f}<extra></extra>",
            showscale=False))
        base_layout(fig, 90 + 50 * len(wk_rate))
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10))
        fig.update_xaxes(side="top", showgrid=False)
        fig.update_yaxes(autorange="reversed", showgrid=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        by_day = adv._closed_with_outcome(opps_ins).groupby(opps_ins["Created On"].dt.day_name().str[:3])["booked"].agg(["mean", "size"])
        by_day = by_day[by_day["size"] >= 30]
        if len(by_day) >= 2:
            best, worst = by_day["mean"].idxmax(), by_day["mean"].idxmin()
            takeaway(f"Opportunities created on <b>{best}</b> book most ({by_day.loc[best, 'mean'] * 100:.0f}%), "
                     f"<b>{worst}</b> least ({by_day.loc[worst, 'mean'] * 100:.0f}%, from {int(by_day.loc[worst, 'size'])} "
                     "opportunities). On the weakest day, check whether follow-ups are being dropped.")

    # 1e. What lost deals have in common -------------------------------------------------------
    section("What do lost deals have in common?",
            "How often each trait appears among opportunities that booked (teal) and those that didn't (gold). "
            "The bigger the gap, the more that trait goes with losing the deal.")
    lt = adv.lost_traits(opps_ins).dropna()
    if lt.empty:
        st.caption("Not enough closed opportunities in this selection.")
    else:
        d = lt.sort_values("Gap")
        fig = go.Figure()
        for _, r in d.iterrows():
            fig.add_shape(type="line", x0=r["Booked %"], x1=r["Not booked %"], y0=r["Trait"], y1=r["Trait"],
                          line=dict(color="#D9D3C7", width=4), layer="below")
        fig.add_scatter(x=d["Booked %"], y=d["Trait"], mode="markers", name="Among booked",
                        marker=dict(size=14, color=PALETTE["primary"], line=dict(width=2, color="#FFFFFF")),
                        hovertemplate="<b>%{y}</b><br>%{x:.0f}% of booked opportunities<extra></extra>")
        fig.add_scatter(x=d["Not booked %"], y=d["Trait"], mode="markers+text", name="Among not booked",
                        marker=dict(size=14, color=PALETTE["gold"], line=dict(width=2, color="#FFFFFF")),
                        text=[f"{v:.0f}% vs {b:.0f}%" for v, b in zip(d["Not booked %"], d["Booked %"])],
                        textposition="middle right", textfont=dict(size=12, color=PALETTE["muted"]),
                        hovertemplate="<b>%{y}</b><br>%{x:.0f}% of opportunities that didn't book<extra></extra>")
        base_layout(fig, 60 + 44 * len(d))
        fig.update_layout(legend=dict(orientation="h", y=1.08, x=0), margin=dict(l=10, r=90, t=40, b=10))
        fig.update_xaxes(range=[-3, 103], ticksuffix="%", showgrid=True, gridcolor=PALETTE["grid"])
        fig.update_yaxes(showgrid=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        t0 = lt.iloc[0]
        takeaway(f"<b>{t0['Not booked %']:.0f}%</b> of deals that didn't book had “{esc(t0['Trait'].lower())}”, "
                 f"against {t0['Booked %']:.0f}% of those that booked — the clearest warning sign.")

    # 1f. Rep strengths by source ------------------------------------------------------------------
    section("Who closes which kind of customer?",
            "Each rep's booking rate by source. Darker = books more. Use it to route leads: give phone and digital "
            "leads to the reps who convert them. Blank = fewer than 5 opportunities.")
    rs_rate, rs_n = adv.rep_source_strengths(opps_ins)
    if rs_rate.empty:
        st.caption("Not enough closed opportunities in this selection.")
    else:
        fig = go.Figure(go.Heatmap(
            z=rs_rate.values, x=list(rs_rate.columns), y=list(rs_rate.index),
            colorscale=[[0, "#F4F1EA"], [0.5, "#7FB0AE"], [1, "#1B5F62"]], zmin=0, zmax=80, xgap=4, ygap=4,
            text=[["" if pd.isna(v) else f"{v:.0f}%" for v in row] for row in rs_rate.values],
            texttemplate="%{text}", textfont=dict(size=12), customdata=rs_n.values,
            hovertemplate="<b>%{y}</b> · %{x}<br>%{z:.0f}% booked of %{customdata:.0f}<extra></extra>",
            showscale=False))
        base_layout(fig, 90 + 40 * len(rs_rate))
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10))
        fig.update_xaxes(side="top", showgrid=False)
        fig.update_yaxes(autorange="reversed", showgrid=False)
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        bits = []
        for grp in [g for g in ["Phone", "Digital"] if g in rs_rate]:
            col_ = rs_rate[grp].dropna()
            if len(col_) >= 3:
                bits.append(f"{grp} leads: best <b>{esc(col_.idxmax())}</b> ({col_.max():.0f}%), "
                            f"weakest {esc(col_.idxmin())} ({col_.min():.0f}%)")
        if bits:
            takeaway(". ".join(bits) + ".")

    # 2. Source quality map ---------------------------------------------------------
    section("Which sources bring good customers?",
            "Each bubble is a source: further right = more opportunities, higher up = more of them book, "
            "bigger bubble = more bookings. Top-right is where you want to be.")
    sq = adv.source_quality(opps_ins)
    if len(sq) < 2:
        st.caption("Not enough data in this selection.")
    else:
        fig = go.Figure(go.Scatter(
            x=sq["Opportunities"], y=sq["Booking rate %"], mode="markers+text", text=sq["Source"],
            textposition="top center", textfont=dict(color=PALETTE["text"], size=12), cliponaxis=False,
            marker=dict(size=(sq["Bookings"].clip(lower=1) ** 0.5) * 6 + 10, color=PALETTE["primary"], opacity=0.85,
                        line=dict(width=2, color="#FFFFFF")),
            customdata=sq[["Bookings", "Closed"]].values,
            hovertemplate="<b>%{text}</b><br>%{x:,} opportunities · %{y:.0f}% of closed ones booked"
                          "<br>%{customdata[0]:,} bookings<extra></extra>"))
        fig.add_hline(y=avg_rate, line=dict(color=PALETTE["gold"], width=1.5, dash="dash"),
                      annotation_text=f"average {avg_rate:.0f}%", annotation_position="top left",
                      annotation_font=dict(color=PALETTE["muted"], size=11))
        base_layout(fig, 440)
        fig.update_xaxes(title="Opportunities (volume)")
        fig.update_yaxes(title="Booking rate", ticksuffix="%", rangemode="tozero")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        big_weak = sq[(sq["Opportunities"] >= sq["Opportunities"].median()) & (sq["Booking rate %"] < avg_rate)]
        best = sq.sort_values("Booking rate %").iloc[-1]
        msg = f"<b>{esc(best['Source'])}</b> converts best ({best['Booking rate %']:.0f}%)."
        if len(big_weak):
            msg += (" High volume but below-average booking rate: <b>" + ", ".join(map(esc, big_weak["Source"])) +
                    "</b> — worth reviewing how these leads are followed up.")
        takeaway(msg)

    # 3. When do customers book? -----------------------------------------------------
    section("When do customers book?",
            "For opportunities that booked: how soon after the opportunity was created. "
            "Once the line flattens, the chance of a booking is mostly gone.")
    ttb, n_b = adv.time_to_book(opps_ins)
    if not n_b:
        st.caption("No bookings in this selection.")
    else:
        fig = go.Figure(go.Scatter(x=ttb["Days"], y=ttb["Share %"], mode="lines", fill="tozeroy",
                                   line=dict(color=PALETTE["primary"], width=3), fillcolor="rgba(32,108,111,0.12)",
                                   hovertemplate="Within %{x} days: %{y:.0f}% of bookings<extra></extra>"))
        for dmark in (0, 7, 30, 60):
            v = float(ttb.loc[ttb["Days"] == dmark, "Share %"].iloc[0])
            fig.add_scatter(x=[dmark], y=[v], mode="markers+text", text=[f"{v:.0f}% by day {dmark}" if dmark else f"{v:.0f}% same day"],
                            textposition="bottom right",
                            marker=dict(size=10, color=PALETTE["gold"], line=dict(width=2, color="#FFFFFF")),
                            textfont=dict(color=PALETTE["text"], size=12), showlegend=False, hoverinfo="skip")
        base_layout(fig, 340)
        fig.update_layout(showlegend=False)
        fig.update_xaxes(title="Days after the opportunity was created", tickvals=[0, 7, 14, 30, 45, 60, 90], range=[-3, 93])
        fig.update_yaxes(title="Share of bookings", ticksuffix="%", range=[0, 105])
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        s0, s7, s30 = (float(ttb.loc[ttb["Days"] == d_, "Share %"].iloc[0]) for d_ in (0, 7, 30))
        takeaway(f"<b>{s0:.0f}%</b> of bookings happen the same day, <b>{s7:.0f}%</b> within a week and "
                 f"<b>{s30:.0f}%</b> within 30 days. An opportunity still open after a month is unlikely to book — "
                 f"follow up hard in week one. ({n_b:,} bookings)")

    # 4. Lead calendar ----------------------------------------------------------------
    section("Lead calendar",
            "Leads per day — darker = more leads. Spot busy days, quiet days and campaign spikes at a glance.")
    cal = adv.lead_calendar(leads_f, start, end)
    if cal["Leads"].sum() == 0:
        st.caption("No leads in this selection.")
    else:
        order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        weeks = list(dict.fromkeys(cal["Week of"]))
        z = cal.pivot_table(index="Weekday", columns="Week of", values="Leads", aggfunc="sum").reindex(index=order, columns=weeks)
        dates = cal.pivot_table(index="Weekday", columns="Week of", values="Date", aggfunc="first").reindex(index=order, columns=weeks)
        fig = go.Figure(go.Heatmap(
            z=z.values, x=[f"w/c {w}" for w in weeks], y=order, xgap=3, ygap=3,
            colorscale=[[0, "#F1F6F5"], [0.5, "#5FA3A5"], [1, "#0F3D3F"]],
            customdata=np.vectorize(lambda d: "" if pd.isna(d) else pd.Timestamp(d).strftime("%a %d %b"))(dates.values),
            text=[["" if pd.isna(v) else f"{int(v)}" for v in row] for row in z.values],
            texttemplate="%{text}", textfont=dict(size=11),
            hovertemplate="%{customdata}: %{z:,} leads<extra></extra>", colorbar=dict(title="Leads", thickness=12)))
        base_layout(fig, 330)
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        by_wd = cal.groupby("Weekday")["Leads"].mean().reindex(order)
        peak = cal.loc[cal["Leads"].idxmax()]
        takeaway(f"Busiest weekday on average: <b>{by_wd.idxmax()}</b> ({by_wd.max():.0f} leads/day); quietest: "
                 f"<b>{by_wd.idxmin()}</b> ({by_wd.min():.0f}). Biggest single day: <b>{peak['Date']:%a %d %b}</b> "
                 f"with {int(peak['Leads']):,} leads.")

    # 5. Fair rep comparison -------------------------------------------------------------
    section("Fair rep comparison",
            "Each dot is a sales rep. Across = how many bookings you'd expect from the leads they were given "
            "(a Referral is more likely to book than an Event lead). Up = how many they actually booked. "
            "Above the gold line = doing better than expected.")
    rx = adv.rep_expected_vs_actual(opps_ins)
    if rx.empty:
        st.caption("Not enough closed opportunities per rep in this selection (needs 10+ each).")
    else:
        top = float(max(rx["Expected"].max(), rx["Actual"].max())) * 1.15
        fig = go.Figure()
        fig.add_scatter(x=[0, top], y=[0, top], mode="lines", line=dict(color=PALETTE["gold"], dash="dash", width=2),
                        hoverinfo="skip", showlegend=False)
        rx = rx.sort_values("Expected")
        fig.add_scatter(x=rx["Expected"], y=rx["Actual"], mode="markers+text", text=rx["Sales rep"],
                        textposition=["top center" if i % 2 == 0 else "bottom center" for i in range(len(rx))], textfont=dict(size=11, color=PALETTE["text"]), cliponaxis=False,
                        marker=dict(size=13, color=PALETTE["primary"], line=dict(width=2, color="#FFFFFF")),
                        customdata=rx[["Closed", "Difference"]].values, showlegend=False,
                        hovertemplate="<b>%{text}</b><br>Expected %{x:.1f} · actual %{y} bookings"
                                      "<br>%{customdata[1]:+.1f} vs expected · %{customdata[0]} closed<extra></extra>")
        base_layout(fig, 460)
        fig.update_xaxes(title="Expected bookings (from their lead mix)", range=[0, top])
        fig.update_yaxes(title="Actual bookings", range=[0, top])
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        rx = rx.sort_values("Difference", ascending=False)
        best, worst = rx.iloc[0], rx.iloc[-1]
        takeaway(f"<b>{esc(best['Sales rep'])}</b> is furthest above expectations ({best['Difference']:+.1f} bookings); "
                 f"<b>{esc(worst['Sales rep'])}</b> is furthest below ({worst['Difference']:+.1f}). "
                 "This accounts for lead quality, so it's a fairer comparison than raw bookings.")

    # 6. Colour demand vs stock ----------------------------------------------------------
    section("Colour demand vs stock",
            "Bookings in the period above by colour, next to unsold cars in stock by colour "
            "(stock needs the daily sheet's Stock tab).")
    cd = adv.colour_demand_vs_stock(opps_ins, getattr(data, "stock", None))
    if cd.empty or cd["Bookings"].sum() == 0:
        st.caption("No bookings with a colour in this selection.")
    else:
        fig = go.Figure()
        fig.add_bar(y=cd["Colour"], x=cd["Bookings"], orientation="h", name="Bookings",
                    marker=dict(color=PALETTE["primary"], cornerradius=4), text=cd["Bookings"], textposition="outside",
                    textfont=dict(color=PALETTE["text"]), cliponaxis=False,
                    hovertemplate="%{y}: %{x} bookings<extra></extra>")
        if "Unsold stock" in cd:
            fig.add_bar(y=cd["Colour"], x=cd["Unsold stock"], orientation="h", name="Unsold stock",
                        marker=dict(color=PALETTE["gold"], cornerradius=4), text=cd["Unsold stock"],
                        textposition="outside", textfont=dict(color=PALETTE["text"]), cliponaxis=False,
                        hovertemplate="%{y}: %{x} unsold in stock<extra></extra>")
        base_layout(fig, max(260, 52 * len(cd) + 60))
        fig.update_layout(barmode="group", bargap=0.3, margin=dict(l=10, r=50, t=40, b=10))
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
        top_c = cd.iloc[0]
        msg = f"Most booked colour: <b>{esc(top_c['Colour'])}</b> ({int(top_c['Bookings'])} bookings)."
        if "Unsold stock" in cd:
            short = cd[(cd["Bookings"] > 0) & (cd["Unsold stock"] < cd["Bookings"] / 4)]
            if len(short):
                msg += " Low unsold stock vs demand: <b>" + ", ".join(map(esc, short["Colour"])) + "</b>."
        else:
            msg += " Stock comparison appears once the daily sheet (Zoho) is connected."
        takeaway(msg)


with tab_guide:
    # --------------------------------------------------------------------------- #
    # Guide — plain-language help for the team
    # --------------------------------------------------------------------------- #

    section("How to use this dashboard",
            "Start with <b>Today</b> every morning. The filters at the top apply to every tab.")
    GUIDE_TABS = [
        ("Today", "The short daily review: yesterday's (or this month's) new leads, opportunities, test drives, "
                  "bookings and deliveries."),
        ("Trends", "Is this period better or worse than the one before, and than the same time last year? Pick one "
                   "month and you still see the months around it."),
        ("Funnel", "Leads → opportunities → test drives → bookings → retail. <i>Where do they end up?</i> follows the same "
                   "customers all the way through. Benchmarks compare with targets."),
        ("Team", "Each sales rep's numbers side by side, who owns the leads (CRE or sales managers), and opportunities "
                 "nobody has followed up."),
        ("Pipeline", "What's likely to book next (open opportunities by how hot they are and when they expect to buy), "
                     "booking cancellations, and why opportunities were lost."),
        ("Insights", "Patterns in the data: what makes customers book, which sources and days convert, how fast a "
                     "test drive needs to happen, why deals are lost, each rep's strengths, and colour demand vs stock."),
        ("Retail & Stock", "Accessories and EW / AMC / PPF sold, finance and insurance taken through us, and the stock "
                           "and payments position."),
    ]
    st.markdown('<div class="guide-grid">' + "".join(
        f'<div class="guide-item"><div class="guide-title">{esc(t)}</div><div class="guide-text">{d}</div></div>'
        for t, d in GUIDE_TABS) + "</div>", unsafe_allow_html=True)

    section("Words used on this dashboard")
    GLOSSARY = [
        ("Lead", "An enquiry in C4C (Leads tab). Counted on the day it was created."),
        ("Opportunity", "A qualified enquiry being worked by a sales rep (Opportunity tab)."),
        ("Test drive", "An opportunity whose first test drive was completed — counted on the test-drive date."),
        ("Booking / Ordered", "An opportunity with a Booking Date. “Status = Booked” only counts bookings not yet invoiced."),
        ("Retail", "A car delivered to the customer — from the retail register (Overall TR Data), by TR DATE."),
        ("L2O · TD% · O2B", "Lead-to-opportunity rate · share of opportunities that took a test drive · opportunity-to-booking rate."),
        ("CRE", "Customer relationship executives (Kothapalli Divya, D Sai Kiran) who receive and qualify leads first."),
        ("Hot / Warm / Cold", "How ready the customer is to buy, as set in C4C."),
        ("Never touched", "An open opportunity with no follow-up call, visit or note logged in C4C."),
        ("Cohort", "A group of customers who came in during the same month, followed through every later stage."),
        ("Median", "The typical value: half are faster, half are slower."),
    ]
    st.dataframe(pd.DataFrame(GLOSSARY, columns=["Term", "Meaning"]), hide_index=True, width="stretch",
                 height=table_height(len(GLOSSARY)))

    section("KPI dictionary",
            "One definition per number. Where two tabs show a similar number, this says why they differ.")
    KPI_DICT = [
        ("Leads", "Count of leads", "Created On", "Today, Trends, Funnel"),
        ("Opportunities", "Count of opportunities", "Created On", "Today, Trends, Funnel, Team"),
        ("Test drives", "Opportunities whose first test drive was completed", "First Test Drive Date", "Today, Trends, Funnel, Team"),
        ("Bookings (= Funnel “Ordered”)", "Opportunities with a Booking Date in the selected dates, including ones "
         "later cancelled", "Booking Date", "Today, Trends, Funnel, Team"),
        ("Retail", "Cars delivered, from the retail register", "TR DATE", "Today, Trends, Funnel, Retail"),
        ("Cancelled", "Bookings now in “Booking Cancelled” or “Intend to Cancel”", "Booking Date", "Team, Pipeline"),
        ("Funnel step rates (e.g. Opportunity → Test Drive)", "This period's later stage ÷ this period's earlier stage. "
         "Not the same customers — shown as “—” when above 100%", "Each stage's own date", "Funnel"),
        ("TD% (benchmarks)", "Of the opportunities created in the dates, the share that took a test drive (same customers)",
         "Created On", "Funnel → Benchmarks"),
        ("O2B (benchmarks)", "Of the opportunities created in the dates, the share booked at any point and not cancelled "
         "(same customers)", "Created On", "Funnel → Benchmarks"),
        ("Book rate (scorecard)", "Bookings ÷ opportunities × 100, for the selected dates", "Booking Date / Created On", "Team"),
        ("Booking rate (Insights)", "Of opportunities that have closed (booked or lost), the share that booked",
         "Created On, last 3 / 6 / 12 months", "Insights"),
    ]
    st.dataframe(pd.DataFrame(KPI_DICT, columns=["Number", "How it's counted", "Dated by", "Where it appears"]),
                 hide_index=True, width="stretch", height=table_height(len(KPI_DICT)))

    section("What good dealership dashboards track — and where it is here",
            "Collected from dealer-CRM providers and sales-dashboard guides.")
    PRACTICES = [
        ("Speed to lead — reply fast (within 5 minutes is ~9× more likely to convert than after 30)",
         "Not shown — needs C4C's Activities export (call times)", "Needs data"),
        ("Lead-source conversion all the way to a sale", "Funnel → Where do they end up? (filter by Source) · Benchmarks", "✓"),
        ("Stalled / ageing deals and follow-up completion", "Team → Untouched opportunities · Pipeline → expected date passed", "✓"),
        ("Sales rep leaderboard", "Team → Sales rep scorecard", "✓"),
        ("Pace to target and forecast", "Pipeline → What's likely to buy next (forecast). Pace to target needs monthly targets", "Needs targets"),
        ("Cohort view (follow the same group through every stage)", "Funnel → Where do they end up?", "✓"),
        ("Finance & insurance penetration", "Retail & Stock → Finance & insurance", "✓"),
        ("Inventory ageing", "Retail & Stock → Stock & payments", "✓"),
        ("Booking cancellations", "Pipeline → Booking cancellations", "✓ (reasons not recorded in C4C)"),
        ("A short daily review: a few key numbers each day", "Today → Yesterday / This month so far", "✓"),
        ("Cost per lead / per sale by source", "Needs marketing spend per source", "Needs data"),
    ]
    st.dataframe(pd.DataFrame(PRACTICES, columns=["Good practice", "Where to find it", "Status"]), hide_index=True,
                 width="stretch", height=table_height(len(PRACTICES)))
    note("<b>Where the numbers come from:</b> history up to September 2026 comes from the C4C Leads and "
         "Opportunities exports and the retail register. New days will come from the team's Zoho Sheet, read every "
         "day after 09:20 and 09:30 IST. Every headline number is re-counted from the raw files on each load; a "
         "warning appears at the top if anything stops matching.")

