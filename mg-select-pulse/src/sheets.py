"""
Live data from the team's Google Sheet ("Leads-Opp-Bkgs-Stock").

The sheet is read with a Google service account. Credentials are looked up in
this order:
    1. st.secrets["gcp_service_account"]   (Streamlit Cloud / .streamlit/secrets.toml)
    2. GOOGLE_SERVICE_ACCOUNT_JSON environment variable (the key JSON as text)
    3. the file named by the PULSE_SERVICE_ACCOUNT_FILE environment variable
    4. a Render Secret File named service_account.json (/etc/secrets/…), or
       secrets/service_account.json in the project folder
If none is found, the dashboard falls back to the files in data/.

Reading is scheduled: the cached copy is refreshed the first time the dashboard
is opened after each REFRESH_TIMES slot (09:20 and 09:30 IST), so the morning's
dump is picked up even if the team finishes pasting a few minutes late.

--- Why dates need care -----------------------------------------------------
C4C exports dates month-first (05-06-2026 = 6 May). When pasted into the sheet,
Google turns every value it *can* read as a date into a real date — and for the
ambiguous ones (both parts <= 12) it reads them the other way round — while the
rest (e.g. 05-20-2026) stay as text. Each column was checked against the
verified C4C exports / the PDF register and the sheet's own Month column:

  Created On                  stored dates are correct; text is day-first
  Changed On                  stored dates are correct; text is month-first
  other C4C date columns      stored dates with day <= 12 are swapped back; text month-first
  TR DATE                     stored date OR its swap — whichever matches the row's Month
  Month ("Aug-25")            Google stores it as 25 Aug; read back as month + year
"""

from __future__ import annotations

import os
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IST = ZoneInfo("Asia/Kolkata")

SPREADSHEET_ID = "1qfzXE2zRz_DK-XbO8s7He-xKaO8cz9VA79XI5r33RE4"
TAB_LEADS = "Leads"
TAB_OPPORTUNITIES = "Opportunity"
TAB_RETAIL = "Overall TR Data"
# Optional tabs — used when present; the dashboard still works without them.
TAB_STOCK = "Stock"
TAB_INV_CANCELLED = "Inv Cancelled"
OPTIONAL_TABS = (TAB_STOCK, TAB_INV_CANCELLED)

# Daily read slots, Indian time.
REFRESH_TIMES = [time(9, 20), time(9, 30)]

# Date columns and how their stored values must be read (see module docstring).
AS_IS_DAY_FIRST = {"Created On"}
AS_IS_MONTH_FIRST = {"Changed On"}
SWAP_MONTH_FIRST = {
    "Booking Date", "Actual Delivery Date", "Invoice Date_V", "Invoice Date_V1",
    "Last Follow up Activity Date", "Open Activity Due Date", "First Test Drive Date Completed on",
    "Cancellation Date/Time", "Expected Purchase Date_Score",
}
DATE_COLUMNS = AS_IS_DAY_FIRST | AS_IS_MONTH_FIRST | SWAP_MONTH_FIRST
ID_COLUMNS = {"Lead ID", "ACS_OpportunityId", "ID"}
EXCEL_EPOCH = pd.Timestamp("1899-12-30")


class SheetAccessError(Exception):
    """The sheet can't be read for a reason the team needs to fix (sharing, missing tab…)."""


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #

# Places a hosting service may keep the key file (checked in order).
SERVICE_ACCOUNT_FILES = [
    "/etc/secrets/service_account.json",          # Render "Secret Files"
    str(PROJECT_ROOT / "service_account.json"),   # Render also copies secret files to the app's root
    str(PROJECT_ROOT / "secrets" / "service_account.json"),
]


def _service_account_info() -> dict | None:
    """The Google service-account key, from (in order): Streamlit secrets, the
    GOOGLE_SERVICE_ACCOUNT_JSON environment variable (the whole JSON as text),
    the file named by PULSE_SERVICE_ACCOUNT_FILE, or a key file in one of
    SERVICE_ACCOUNT_FILES. None if the dashboard has no key."""
    import json

    try:
        if "gcp_service_account" in st.secrets:
            return dict(st.secrets["gcp_service_account"])
    except Exception:  # no secrets.toml at all
        pass
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        return json.loads(raw)
    candidates = ([os.environ["PULSE_SERVICE_ACCOUNT_FILE"]] if os.environ.get("PULSE_SERVICE_ACCOUNT_FILE") else []) \
        + SERVICE_ACCOUNT_FILES
    for path in map(Path, candidates):
        if path.exists():
            return json.loads(path.read_text())
    return None


def spreadsheet_id() -> str:
    try:
        return st.secrets.get("sheet", {}).get("id", SPREADSHEET_ID)
    except Exception:
        return SPREADSHEET_ID


# The team has moved from Google Sheets to Zoho Sheet. History up to Sep 2026 comes
# from the saved files (see src/snapshot.py); daily Zoho reads are added later.
# Set PULSE_USE_GOOGLE_SHEET=1 to read the Google Sheet again.
GOOGLE_SHEET_ENABLED = os.environ.get("PULSE_USE_GOOGLE_SHEET", "").strip() == "1"


def is_configured() -> bool:
    return GOOGLE_SHEET_ENABLED and _service_account_info() is not None


# --------------------------------------------------------------------------- #
# Refresh schedule
# --------------------------------------------------------------------------- #

def now_ist() -> datetime:
    return datetime.now(IST)


def current_slot(now: datetime | None = None) -> datetime:
    """The most recent scheduled read time that has passed (IST).
    Before 09:20 it is yesterday's 09:30 read."""
    now = now or now_ist()
    today = [datetime.combine(now.date(), t, IST) for t in REFRESH_TIMES]
    passed = [s for s in today if s <= now]
    if passed:
        return passed[-1]
    return datetime.combine(now.date() - timedelta(days=1), REFRESH_TIMES[-1], IST)


def next_slot(now: datetime | None = None) -> datetime:
    now = now or now_ist()
    for day in (now.date(), now.date() + timedelta(days=1)):
        for t in REFRESH_TIMES:
            s = datetime.combine(day, t, IST)
            if s > now:
                return s
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Value conversion
# --------------------------------------------------------------------------- #

def _to_serial(ts: pd.Timestamp | None) -> float | None:
    """Excel-style serial day number — the form the rest of the loader expects."""
    if ts is None or pd.isna(ts):
        return None
    return (ts - EXCEL_EPOCH) / pd.Timedelta(days=1)


def _serial_to_ts(value) -> pd.Timestamp:
    return EXCEL_EPOCH + pd.to_timedelta(float(value), unit="D")


def _swapped(ts: pd.Timestamp) -> pd.Timestamp | None:
    return ts.replace(month=ts.day, day=ts.month) if ts.day <= 12 else None


def _date_cell(value, column: str) -> float | None:
    """One C4C date cell -> serial day number, honouring the column's true order."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):  # a real date cell (Google stored a serial)
        ts = _serial_to_ts(value)
        if column in SWAP_MONTH_FIRST and ts.day <= 12:
            ts = _swapped(ts)
        return _to_serial(ts)
    text = str(value).strip()
    if not text:
        return None
    ts = pd.to_datetime(text, format="mixed", dayfirst=column in AS_IS_DAY_FIRST, errors="coerce")
    return _to_serial(ts)


def _month_cell(value):
    """'Month' column: typed as 'Aug-25' (Aug 2025), Google stores it as the date
    25 August — so the day of that date is the year."""
    if isinstance(value, (int, float)) and value != "":
        ts = _serial_to_ts(value)
        return f"{ts:%b}-{ts.day:02d}"
    return str(value).strip() or None


def _tr_date_cell(value, month_label: str | None):
    """TR DATE: the stored date, or its day/month swap — whichever falls in the
    row's Month. Text (e.g. '25-8-2025', '05-May-26') is read day-first."""
    if value is None or value == "":
        return None
    if not isinstance(value, (int, float)):
        ts = pd.to_datetime(str(value).strip(), format="mixed", dayfirst=True, errors="coerce")
        return _to_serial(ts)
    ts = _serial_to_ts(value)
    alt = _swapped(ts)
    target = pd.to_datetime(month_label, format="%b-%y", errors="coerce") if month_label else pd.NaT
    if alt is not None and pd.notna(target) and (ts.year, ts.month) != (target.year, target.month) \
            and (alt.year, alt.month) == (target.year, target.month):
        ts = alt
    return _to_serial(ts)


def _clean_cell(value, column: str):
    if column in DATE_COLUMNS:
        return _date_cell(value, column)
    if column == "Month":
        return _month_cell(value)
    if value == "" or value is None:
        return None
    if column in ID_COLUMNS and isinstance(value, (int, float)):
        return str(int(value))
    if isinstance(value, str):
        return value.strip() or None
    return value


def _dedupe_header(header: list[str]) -> list[str]:
    """If a column name appears twice (e.g. someone renames a column by mistake),
    the FIRST one keeps the name and later ones become 'Name (col AA)', so a
    mislabelled column can never replace the real data."""
    seen: set[str] = set()
    out = []
    for i, h in enumerate(header):
        if h and h in seen:
            n, letters = i + 1, ""
            while n:
                n, r = divmod(n - 1, 26)
                letters = chr(65 + r) + letters
            h = f"{h} (col {letters})"
        seen.add(h)
        out.append(h)
    return out


def _grid_to_frame(grid: list[list], key: str) -> pd.DataFrame:
    header_idx = next(i for i, r in enumerate(grid[:15]) if key in [str(c).strip() for c in r])
    header = _dedupe_header([str(c).strip() for c in grid[header_idx]])
    rows = [list(r) + [""] * (len(header) - len(r)) for r in grid[header_idx + 1:]]
    rows = [r[: len(header)] for r in rows if any(str(c).strip() for c in r)]
    keep = [i for i, h in enumerate(header) if h]
    cols = [header[i] for i in keep]
    raw_tr = [r[header.index("TR DATE")] for r in rows] if "TR DATE" in header else None
    df = pd.DataFrame([[_clean_cell(r[i], header[i]) for i in keep] for r in rows], columns=cols)
    if raw_tr is not None:  # needs the row's (already cleaned) Month label
        df["TR DATE"] = [_tr_date_cell(v, m) for v, m in zip(raw_tr, df["Month"])]
    return df


# --------------------------------------------------------------------------- #
# Optional tabs: Stock and Inv Cancelled
# --------------------------------------------------------------------------- #

def _num(v) -> float | None:
    if isinstance(v, (int, float)) and v != "":
        return float(v)
    text = str(v).replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None  # e.g. "CANCELLED"


def _day_date(v) -> pd.Timestamp | None:
    """Dates the team types (day-first, e.g. '15-Oct-2026', '30-06-2026')."""
    if isinstance(v, (int, float)) and v != "":
        return _serial_to_ts(v).normalize()
    text = str(v).strip()
    if not text:
        return None
    ts = pd.to_datetime(text, format="mixed", dayfirst=True, errors="coerce")
    return None if pd.isna(ts) else ts.normalize()


def stock_frame(grid: list[list]) -> pd.DataFrame | None:
    """Stock tab -> one row per vehicle. Columns are taken by name; the two
    'Ageing' columns (from PO date, from received date) are told apart by order."""
    hi = next((i for i, r in enumerate(grid[:10]) if "Vin Number" in [str(c).strip() for c in r]), None)
    if hi is None:
        return None
    header = [str(c).strip() for c in grid[hi]]
    def col(name, nth=0):
        idx = [i for i, h in enumerate(header) if h.lower() == name.lower()]
        return idx[nth] if len(idx) > nth else None
    c = {
        "vin": col("Vin Number"), "model": col("Model"), "colour": col("Color"),
        "stock_status": col("Stock Status") or col("Stock Location"), "vehicle_status": col("VECHILE STATUS") or col("Vehicle Status"),
        "customer": col("Customer Name"), "sm": col("Sales Manager"),
        "age_po": col("Ageing", 0), "age_received": col("Ageing", 1),
        "tr_planned": col("T/R Date"), "received": col("Received Payment"), "balance": col("Balance Payment"),
        "pay_expected": col("Expected Payment Date"), "mode": col("Mode of Purchase"), "remarks": col("REMARKS"),
    }
    out = []
    for r in grid[hi + 1:]:
        r = list(r) + [""] * (len(header) - len(r))
        get = lambda k: r[c[k]] if c[k] is not None else ""
        if not str(get("vin")).strip():
            continue
        out.append({
            "VIN": str(get("vin")).strip(),
            "Model": str(get("model")).strip().upper() or None,
            "Colour": str(get("colour")).strip().title() or None,
            "Location": str(get("stock_status")).strip().title() or None,
            "Sale status": str(get("vehicle_status")).strip().upper() or None,
            "Customer": str(get("customer")).strip() or None,
            "Sales Manager": " ".join(str(get("sm")).split()).title() or None,
            "Age since PO (days)": _num(get("age_po")),
            "Age since received (days)": _num(get("age_received")),
            "Planned delivery": _day_date(get("tr_planned")),
            "Planned delivery (as typed)": str(get("tr_planned")).strip() or None,
            "Received ₹": _num(get("received")),
            "Balance ₹": _num(get("balance")),
            "Cancelled": any(str(get(k)).strip().upper() == "CANCELLED" for k in ("received", "balance", "tr_planned")),
            "Payment expected (as typed)": str(get("pay_expected")).strip() or None,
            "Purchase mode": str(get("mode")).strip().title() or None,
            "Remarks": str(get("remarks")).strip() or None,
        })
    return pd.DataFrame(out)


def inv_cancelled_frame(grid: list[list]) -> pd.DataFrame | None:
    hi = next((i for i, r in enumerate(grid[:10]) if "Remark" in [str(c).strip() for c in r]), None)
    if hi is None:
        return None
    header = [str(c).strip() for c in grid[hi]]
    rows = [list(r) + [""] * (len(header) - len(r)) for r in grid[hi + 1:] if any(str(x).strip() for x in r)]
    df = pd.DataFrame([r[: len(header)] for r in rows], columns=header)
    for d in ("Booking Date", "Inv Date"):
        if d in df:
            df[d] = df[d].map(_day_date)
    # An invoice can't come before its booking: such a date was stored with day and
    # month swapped (the pasted-date problem) — swap it back when that fixes it.
    if {"Booking Date", "Inv Date"} <= set(df.columns):
        def fix(row):
            inv, bk = row["Inv Date"], row["Booking Date"]
            if pd.notna(inv) and pd.notna(bk) and inv < bk and inv.day <= 12:
                alt = inv.replace(month=inv.day, day=inv.month)
                return alt if alt >= bk else inv
            return inv
        df["Inv Date"] = df.apply(fix, axis=1)
    if "Cancelled Month" in df:
        df["Cancelled Month"] = df["Cancelled Month"].map(_month_cell)
    return df


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #

def raw_grids() -> dict[str, list[list]]:
    """Unformatted cell values for the three tabs (used by the reconciliation too)."""
    import gspread

    info = _service_account_info()
    if info is None:
        raise RuntimeError("Google service-account credentials not found.")
    import time as _time

    last_error: Exception | None = None
    for attempt in range(4):  # brief network drops (e.g. phone hotspot) shouldn't force the file fallback
        try:
            gc = gspread.service_account_from_dict(info)
            book = gc.open_by_key(spreadsheet_id())
            grids = {
                tab: book.worksheet(tab).get_all_values(value_render_option="UNFORMATTED_VALUE")
                for tab in (TAB_LEADS, TAB_OPPORTUNITIES, TAB_RETAIL)
            }
            present = {ws.title for ws in book.worksheets()}
            for tab in OPTIONAL_TABS:
                if tab in present:
                    grids[tab] = book.worksheet(tab).get_all_values(value_render_option="UNFORMATTED_VALUE")
            return grids
        except gspread.exceptions.WorksheetNotFound as exc:
            raise SheetAccessError(f"A required tab is missing from the Google Sheet: {exc}. "
                                   f"Expected tabs: {TAB_LEADS}, {TAB_OPPORTUNITIES}, {TAB_RETAIL}.") from exc
        except PermissionError as exc:  # HTTP 403 — retrying won't help
            raise SheetAccessError(
                f"Google refused access to the sheet. It is no longer shared with the dashboard's account "
                f"{info.get('client_email')}. In the Google Sheet click Share, add that address as a Viewer, "
                f"untick 'Notify people', then press Refresh now.") from exc
        except gspread.exceptions.SpreadsheetNotFound as exc:
            raise SheetAccessError("The Google Sheet wasn't found — it may have been deleted or its link changed "
                                   "(spreadsheet ID in src/sheets.py / secrets).") from exc
        except Exception as exc:  # network / transport / quota errors — worth retrying
            last_error = exc
            _time.sleep(2 * (attempt + 1))
    raise ConnectionError(f"Couldn't reach Google after 4 tries — check the internet connection "
                          f"({type(last_error).__name__}).") from last_error


@st.cache_data(show_spinner=False, max_entries=2)
def cached_raw_grids(slot_key: str) -> dict[str, list[list]]:
    """One download of the raw tabs per refresh slot — shared by the dashboard and
    the reconciliation, so the sheet is only read once per slot."""
    return raw_grids()


@st.cache_data(show_spinner=False, max_entries=2)
def fetch(slot_key: str) -> dict:
    """Read and normalise the tabs. Cached per refresh slot, so the sheet is
    hit once after 09:20 and once after 09:30 IST (or when 'Refresh now' is used)."""
    grids = cached_raw_grids(slot_key)
    return {
        "leads": _grid_to_frame(grids[TAB_LEADS], "Lead ID"),
        "opportunities": _grid_to_frame(grids[TAB_OPPORTUNITIES], "ID"),
        "retail": _grid_to_frame(grids[TAB_RETAIL], "CUSTOMER NAME"),
        "stock": stock_frame(grids[TAB_STOCK]) if TAB_STOCK in grids else None,
        "inv_cancelled": inv_cancelled_frame(grids[TAB_INV_CANCELLED]) if TAB_INV_CANCELLED in grids else None,
        "fetched_at": now_ist(),
        "slot": slot_key,
    }
