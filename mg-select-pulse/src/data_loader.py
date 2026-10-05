"""
Reading and cleaning of the three MG Select Pulse data sources.

Live source (preferred): the team's Google Sheet — tabs Leads, Opportunity and
Overall TR Data — see src/sheets.py. Used whenever service-account credentials
are configured.

File fallback:
    data/leads.xlsx          C4C "Leads" export
    data/opportunities.xlsx  C4C "Opportunities" export
    data/retail.xlsx|.csv|.pdf  Retail / TR register (one row per delivered vehicle)

Both sources go through the same cleaning functions below.

Every loader returns a cleaned DataFrame plus a list of human-readable notes
describing what was fixed, so the dashboard can show its working instead of
silently "correcting" data.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dateutil import parser as dateparser

# --------------------------------------------------------------------------- #
# Configuration — edit here if file names, columns or the reporting window change
# --------------------------------------------------------------------------- #

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

LEADS_FILE = DATA_DIR / "leads.xlsx"
OPPORTUNITIES_FILE = DATA_DIR / "opportunities.xlsx"
# Retail is looked up in this order; the first file that exists wins.
RETAIL_CANDIDATES = [DATA_DIR / "retail.xlsx", DATA_DIR / "retail.csv", DATA_DIR / "retail.pdf"]

# The reporting window is taken from the data itself (earliest to latest
# Created On / TR DATE), so it grows automatically as daily dumps arrive.
# The dashboard opens on the latest complete calendar month.

# C4C exports carry 4 junk rows (title, "Last Updated On", blanks) above the header.
C4C_HEADER_ROW_INDEX = 4

LEADS_REQUIRED = [
    "Lead ID", "Status", "Source", "Channel", "Created On",
    "ACS_OpportunityId", "City", "Qualification Level", "Model Line(fe)", "Owner",
]

# Lead routing: leads owned by these people went to a CRE; every other lead
# Owner is a sales manager. Names are matched case/space-insensitively.
CRE_OWNERS = ["Kothapalli Divya", "D Sai Kiran"]
ROUTE_CRE = "CRE"
ROUTE_SM = "Sales managers"
OPPS_REQUIRED = [
    "ID", "Status", "Source", "Channel", "Created On",
    "Test Drive Completed", "Assigned To", "Model Line(fe)", "Customer",
]
RETAIL_REQUIRED = [
    "TR DATE", "Month", "CUSTOMER NAME", "SM", "MODEL",
    "ACCESSORIES", "EW", "AMC", "PPF", "Source",
]
# Date columns in the C4C exports that are converted to datetime when present.
C4C_DATE_COLUMNS = [
    "Created On", "Changed On", "Booking Date", "Actual Delivery Date",
    "Invoice Date_V", "Invoice Date_V1", "First Test Drive Date Completed on",
    "Last Follow up Activity Date", "Open Activity Due Date", "Cancellation Date/Time",
    "Expected Purchase Date_Score",
]
# Optional retail columns — used when present, never required.
RETAIL_OPTIONAL = ["S.NO", "VIN", "COLOUR", "FINANCE", "Insurance"]

# Alternative spellings seen (or likely) in retail registers -> canonical name.
RETAIL_COLUMN_ALIASES = {
    "SNO": "S.NO", "S NO": "S.NO", "SL NO": "S.NO",
    "TRDATE": "TR DATE", "TR_DATE": "TR DATE", "TR DT": "TR DATE",
    "CUSTOMER": "CUSTOMER NAME", "CUSTOMERNAME": "CUSTOMER NAME",
    "SALES MANAGER": "SM", "SALES REP": "SM",
    "ACCESSORY": "ACCESSORIES", "ACC": "ACCESSORIES",
    "INSURANCE": "Insurance", "SOURCE": "Source", "MONTH": "Month",
}


class DataValidationError(Exception):
    """Raised when a source file is missing or lacks required columns."""


@dataclass
class LoadedData:
    leads: pd.DataFrame
    opportunities: pd.DataFrame
    retail: pd.DataFrame
    retail_source: str                       # which file retail came from
    notes: dict[str, list[str]] = field(default_factory=dict)
    retail_date_issues: pd.DataFrame = field(default_factory=pd.DataFrame)
    retail_file_rows: int = 0                # rows in the retail file before the window filter
    source: str = "files"                    # "sheet" or "files"
    source_label: str = ""                   # e.g. "Google Sheet · read 01 Oct 2026, 09:30 IST"
    window_start: date | None = None         # earliest date in the data
    window_end: date | None = None           # latest date in the data
    default_start: date | None = None        # period the dashboard opens on
    default_end: date | None = None
    stock: pd.DataFrame | None = None        # Stock tab (Google Sheet only)
    inv_cancelled: pd.DataFrame | None = None  # Inv Cancelled tab (Google Sheet only)


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

def _collapse_ws(value) -> str | None:
    """Trim and collapse internal whitespace; None for blanks."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = " ".join(str(value).split())
    return text or None


def person_key(name) -> str | None:
    """Canonical form of a person's name so that 'CHANDRA SHEKAR MEKALA',
    'CHandra Shekar Mekala' and 'Chandra  Shekar Mekala' all group together."""
    text = _collapse_ws(name)
    return text.title() if text else None


def excel_serial_or_text_to_datetime(series: pd.Series) -> pd.Series:
    """C4C dates arrive either as Excel serial numbers (our tolerant reader) or as
    real datetimes / text (pandas reader). Handle all three; bad values -> NaT."""
    numeric = pd.to_numeric(series, errors="coerce")
    as_serial = pd.to_datetime(numeric, unit="D", origin="1899-12-30", errors="coerce")
    as_text = pd.to_datetime(series.where(numeric.isna()), errors="coerce", dayfirst=True)
    # Serial conversion yields float noise (e.g. 08:43:31.999999); round to seconds.
    return as_serial.fillna(as_text).dt.round("s")


def unify_case(frames: list[pd.DataFrame], column: str) -> None:
    """Make categorical text consistent across datasets, in place.

    'HYDERABAD' / 'Hyderabad' or 'DIGITAL' / 'Digital' are folded to whichever
    spelling is most common across all frames, so filters and groupings don't
    split one real value into two."""
    values = pd.concat([f[column] for f in frames if column in f], ignore_index=True).dropna()
    if values.empty:
        return
    canonical = (
        values.groupby(values.str.casefold())
        .agg(lambda s: s.value_counts().index[0])
        .to_dict()
    )
    for f in frames:
        if column in f:
            f[column] = f[column].map(lambda v: canonical.get(v.casefold(), v) if isinstance(v, str) else v)


def _require_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataValidationError(
            f"{label} is missing required column(s): {', '.join(missing)}. "
            f"Columns found: {', '.join(map(str, df.columns))}"
        )


def _file_signature(path: Path) -> float:
    """Modification time, passed into cached functions so replacing a file
    invalidates the cache automatically."""
    return path.stat().st_mtime if path.exists() else 0.0


# --------------------------------------------------------------------------- #
# C4C Excel reading (Leads + Opportunities)
# --------------------------------------------------------------------------- #

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _read_xlsx_tolerant(path: Path) -> list[list]:
    """Fallback .xlsx reader for SAP C4C exports.

    C4C writes most cells without a cell reference and occasionally writes a
    broken one (e.g. r="E" with no row number), which makes openpyxl crash.
    This reader walks the sheet XML directly and places cells by position,
    using a reference only when it contains a usable column."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        sheet = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(_NS + "t")) for si in root.iter(_NS + "si")]

        rows: dict[int, dict[int, object]] = {}
        row_no = 0
        for _, el in ET.iterparse(z.open(sheet)):
            if el.tag != _NS + "row":
                continue
            row_no = int(el.get("r")) if el.get("r") else row_no + 1
            cells: dict[int, object] = {}
            col = -1
            for c in el.findall(_NS + "c"):
                m = re.match(r"([A-Z]+)", c.get("r") or "")
                col = _col_index(m.group(1)) if m else col + 1
                kind = c.get("t")
                if kind == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(_NS + "t"))
                elif kind == "s":
                    v = c.find(_NS + "v")
                    val = shared[int(v.text)] if v is not None else None
                else:
                    v = c.find(_NS + "v")
                    val = v.text if v is not None else None
                    if val is not None and kind in (None, "n"):
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                cells[col] = val
            rows[row_no] = cells
            el.clear()

    if not rows:
        return []
    width = max((max(r) for r in rows.values() if r), default=-1) + 1
    last = max(rows)
    return [[rows.get(i, {}).get(j) for j in range(width)] for i in range(1, last + 1)]


def read_c4c_export(path: Path, key_column: str) -> pd.DataFrame:
    """Read a C4C export: skip the 4 junk rows, use row index 4 as the header.

    Tries pandas/openpyxl first; if the file trips openpyxl (as current C4C
    exports do) falls back to the tolerant reader above. If the header isn't at
    row index 4 (export layout changed), searches the first 15 rows for a row
    containing `key_column`."""
    try:
        raw = pd.read_excel(path, header=None, dtype=object)
        grid = raw.where(raw.notna(), None).values.tolist()
    except Exception:
        grid = _read_xlsx_tolerant(path)

    header_idx = C4C_HEADER_ROW_INDEX
    if header_idx >= len(grid) or key_column not in [str(v).strip() for v in grid[header_idx] if v is not None]:
        header_idx = next(
            (i for i, r in enumerate(grid[:15]) if key_column in [str(v).strip() for v in r if v is not None]),
            header_idx,
        )

    header = [str(h).strip() if h not in (None, "") else f"Unnamed: {i}" for i, h in enumerate(grid[header_idx])]
    df = pd.DataFrame(grid[header_idx + 1:], columns=header)

    # Drop "Unnamed" columns and columns that are entirely empty.
    df = df.loc[:, [not c.startswith("Unnamed") for c in df.columns]]
    df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
    return df.reset_index(drop=True)


def _clean_c4c_common(df: pd.DataFrame) -> pd.DataFrame:
    """Rules shared by Leads and Opportunities."""
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(lambda v: _collapse_ws(v) if isinstance(v, str) else v)

    for col in [c for c in C4C_DATE_COLUMNS if c in df.columns]:
        df[col] = excel_serial_or_text_to_datetime(df[col])

    if "City" in df:
        df["City"] = df["City"].map(lambda v: v.title() if isinstance(v, str) else v)
    return df


def _id_to_str(value) -> str | None:
    """IDs may arrive as 5215899, 5215899.0 or '5215899.0' — normalize to '5215899'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = re.sub(r"\.0$", "", str(value).strip())
    return text or None


def finish_leads(df: pd.DataFrame, label: str = "leads") -> pd.DataFrame:
    """Clean a raw Leads table (from the C4C export or the Google Sheet)."""
    _require_columns(df, LEADS_REQUIRED, label)
    df = _clean_c4c_common(df)
    df["Lead ID"] = df["Lead ID"].map(_id_to_str)
    df["opportunity_id"] = df["ACS_OpportunityId"].map(_id_to_str)
    # Leads have no "Assigned To"; the lead Owner is the closest equivalent.
    df["rep"] = df["Owner"].map(person_key)
    cre = {person_key(n) for n in CRE_OWNERS}
    df["lead_route"] = df["rep"].map(lambda r: ROUTE_CRE if r in cre else ROUTE_SM)
    df["model"] = df["Model Line(fe)"].str.upper()
    return df


def finish_opportunities(df: pd.DataFrame, label: str = "opportunities") -> pd.DataFrame:
    """Clean a raw Opportunities table (from the C4C export or the Google Sheet)."""
    _require_columns(df, OPPS_REQUIRED, label)
    df = _clean_c4c_common(df)
    df["ID"] = df["ID"].map(_id_to_str)
    df["rep"] = df["Assigned To"].map(person_key)
    df["model"] = df["Model Line(fe)"].str.upper()
    df["test_drive_done"] = df["Test Drive Completed"].astype(str).str.strip().str.upper().eq("YES")
    return df


@st.cache_data(show_spinner=False)
def load_leads(path: str, _signature: float) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise DataValidationError(f"Leads file not found at {p}")
    return finish_leads(read_c4c_export(p, key_column="Lead ID"), "leads.xlsx")


@st.cache_data(show_spinner=False)
def load_opportunities(path: str, _signature: float) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise DataValidationError(f"Opportunities file not found at {p}")
    return finish_opportunities(read_c4c_export(p, key_column="ID"), "opportunities.xlsx")


# --------------------------------------------------------------------------- #
# Retail register (xlsx / csv / pdf)
# --------------------------------------------------------------------------- #

def _canonical_retail_column(name) -> str:
    text = " ".join(str(name or "").replace("\n", " ").split())
    upper = text.upper()
    for canon in RETAIL_REQUIRED + RETAIL_OPTIONAL:
        if upper == canon.upper():
            return canon
    return RETAIL_COLUMN_ALIASES.get(upper, text)


def _find_header_row(grid: list[list]) -> int:
    for i, row in enumerate(grid[:20]):
        cols = {_canonical_retail_column(v) for v in row}
        if {"CUSTOMER NAME", "TR DATE"} <= cols:
            return i
    return 0


def _grid_to_frame(grid: list[list]) -> pd.DataFrame:
    h = _find_header_row(grid)
    header = [_canonical_retail_column(v) or f"Unnamed: {i}" for i, v in enumerate(grid[h])]
    body = [r for r in grid[h + 1:] if any(v not in (None, "") for v in r)]
    df = pd.DataFrame(body, columns=header)
    df = df.loc[:, [c and not c.startswith("Unnamed") for c in df.columns]]
    return df


def _read_retail_pdf(path: Path) -> pd.DataFrame:
    """Extract every table on every page with pdfplumber and concatenate.

    The header row repeats at the top of each page; any row that equals the
    header (after normalizing) is dropped wherever it appears, not just on
    page 1."""
    import pdfplumber

    header: list[str] | None = None
    body: list[list] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for row in table:
                    norm = [_canonical_retail_column(v) for v in row]
                    is_header = {"CUSTOMER NAME", "TR DATE"} <= set(norm)
                    if is_header:
                        header = header or norm
                        continue
                    if header is None:
                        continue  # text above the first header row
                    body.append([(" ".join(str(v).split()) if v is not None else None) for v in row])
    if header is None:
        raise DataValidationError(f"No table with a 'CUSTOMER NAME' / 'TR DATE' header found in {path.name}")
    width = len(header)
    body = [(r + [None] * width)[:width] for r in body]
    return _grid_to_frame([header] + body)


def _read_retail_raw(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _read_retail_pdf(path)
    if suffix == ".csv":
        raw = pd.read_csv(path, header=None, dtype=str, keep_default_na=False)
    else:
        raw = pd.read_excel(path, header=None, dtype=object)
    grid = raw.where(raw.notna(), None).values.tolist()
    return _grid_to_frame(grid)


# ---- retail field parsers ------------------------------------------------- #

_TR_DATE_FORMATS = [
    "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%y", "%d/%m/%y",
    "%d-%b-%y", "%d-%b-%Y", "%d %b %Y", "%d %b %y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S",
]


def parse_month_label(value) -> pd.Period | None:
    """'Jul-26' / 'JUL-2026' / 'July 26' -> Period('2026-07')."""
    text = _collapse_ws(value)
    if not text:
        return None
    if isinstance(value, (datetime, pd.Timestamp)):
        return pd.Period(value, "M")
    for fmt in ("%b-%y", "%b-%Y", "%B-%y", "%B-%Y", "%b %y", "%B %Y", "%b %Y"):
        try:
            return pd.Period(datetime.strptime(text.title(), fmt), "M")
        except ValueError:
            continue
    return None


def parse_tr_date(value, month_hint: pd.Period | None) -> tuple[pd.Timestamp | None, str]:
    """Parse one TR DATE cell that may be in any of several formats.

    Returns (timestamp or None, how) where `how` is one of:
      'ok'           parsed, agrees with the Month column (or no Month to check)
      'month-order'  only parses consistently with Month if read month-first
      'mismatch'     parsed, but disagrees with Month — kept, flagged for review
      'unparsed'     could not be parsed; Month column used for month/quarter
    """
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() == "":
        return None, "unparsed"
    if isinstance(value, (datetime, pd.Timestamp)):
        ts = pd.Timestamp(value)
        return ts, "ok" if month_hint is None or ts.to_period("M") == month_hint else "mismatch"
    if isinstance(value, (int, float)):  # Excel serial in an xlsx register
        ts = pd.Timestamp("1899-12-30") + pd.to_timedelta(float(value), unit="D")
        return ts.normalize(), "ok" if month_hint is None or ts.to_period("M") == month_hint else "mismatch"

    text = str(value).strip()
    candidates: list[pd.Timestamp] = []
    for fmt in _TR_DATE_FORMATS:
        try:
            candidates.append(pd.Timestamp(datetime.strptime(text, fmt)))
            break
        except ValueError:
            continue
    if not candidates:
        try:
            candidates.append(pd.Timestamp(dateparser.parse(text, dayfirst=True)))
        except (ValueError, OverflowError, TypeError):
            pass
    if not candidates:
        return None, "unparsed"

    ts = candidates[0]
    if month_hint is None or ts.to_period("M") == month_hint:
        return ts, "ok"
    # Ambiguous day/month (e.g. 07/08/2026)? Try month-first before calling it a mismatch.
    try:
        alt = pd.Timestamp(dateparser.parse(text, dayfirst=False))
        if alt.to_period("M") == month_hint:
            return alt, "month-order"
    except (ValueError, OverflowError, TypeError):
        pass
    return ts, "mismatch"


def parse_yes_no(value) -> bool | None:
    """Normalize EW / AMC / PPF flags. Handles the 'N0' (zero) typo, Y/N,
    True/False and 1/0. Blank -> None (unknown, excluded from attach-rate denominators)."""
    text = _collapse_ws(value)
    if text is None:
        return None
    text = text.upper()
    if text == "N0":  # data-entry typo: zero instead of letter O
        text = "NO"
    if text in {"YES", "Y", "TRUE", "1", "DONE"}:
        return True
    if text in {"NO", "N", "FALSE", "O", "NA", "N/A", "NIL", "-"}:
        return False
    return None


def parse_rupees(value) -> tuple[float | None, bool]:
    """'5,10,000' / '₹ 85,059' / '109000' / '0' -> (amount, ok).

    Blank -> None ("not recorded"): the register leaves ACCESSORIES empty for
    months before it was tracked (Jan–May 2026), while later months write "0"
    when nothing was sold. Treating blank as ₹0 would drag averages down."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, True
    if isinstance(value, (int, float)):
        return float(value), True
    text = re.sub(r"(?i)rs\.?|inr|₹|,|\s", "", str(value))
    if text in {"", "-"}:
        return None, True
    if text == "0":
        return 0.0, True
    try:
        return float(text), True
    except ValueError:
        return 0.0, False


@st.cache_data(show_spinner=False)
def load_retail(path: str, _signature: float) -> tuple[pd.DataFrame, pd.DataFrame, list[str], int]:
    """Load and clean the retail register file."""
    p = Path(path)
    return finish_retail(_read_retail_raw(p), p.name)


def finish_retail(df: pd.DataFrame, label: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str], int]:
    """Clean a raw retail register (file or the sheet's Overall TR Data tab).

    Returns (retail rows, TR DATE issue rows for review, notes, rows read)."""
    _require_columns(df, RETAIL_REQUIRED, label)
    df = df.copy()
    file_rows = len(df)
    notes: list[str] = [f"Retail read from {label}: {len(df):,} rows."]

    # Month label is the fallback for unparseable dates.
    df["month"] = df["Month"].map(parse_month_label)

    parsed = [parse_tr_date(v, m) for v, m in zip(df["TR DATE"], df["month"])]
    df["tr_date"] = pd.to_datetime([p_[0] for p_ in parsed])
    df["tr_date_status"] = [p_[1] for p_ in parsed]
    # Rows with no usable Month label take their month from the parsed date.
    df["month"] = [m if m is not None else (d.to_period("M") if pd.notna(d) else None)
                   for m, d in zip(df["month"], df["tr_date"])]
    df = df[df["month"].notna()].copy()

    # Flags
    for col in ("EW", "AMC", "PPF"):
        raw = df[col].map(_collapse_ws)
        typo = int(raw.fillna("").str.upper().eq("N0").sum())
        if typo:
            notes.append(f"{col}: {typo} 'N0' (zero) entries read as 'NO'.")
        df[f"{col.lower()}_flag"] = raw.map(parse_yes_no).astype("boolean")

    # Accessories
    parsed_amt = df["ACCESSORIES"].map(parse_rupees)
    df["accessories_amount"] = pd.to_numeric(pd.Series([a for a, _ in parsed_amt], index=df.index), errors="coerce")
    bad_amt = int(sum(not ok for _, ok in parsed_amt))
    if bad_amt:
        notes.append(f"ACCESSORIES: {bad_amt} non-numeric value(s) treated as not recorded.")
    blank_amt = int(df["accessories_amount"].isna().sum())
    if blank_amt:
        notes.append(f"ACCESSORIES: {blank_amt} blank entries treated as not recorded (excluded from averages).")

    # Names: keep originals for display, normalized keys for grouping.
    df["customer_name_display"] = df["CUSTOMER NAME"].map(_collapse_ws)
    df["customer_key"] = df["customer_name_display"].map(lambda v: v.casefold() if v else None)
    df["sm_display"] = df["SM"].map(_collapse_ws)
    df["rep"] = df["SM"].map(person_key)
    variants = df.groupby("rep")["sm_display"].nunique()
    for rep in variants[variants > 1].index:
        spellings = sorted(df.loc[df["rep"] == rep, "sm_display"].unique())
        notes.append(f"SM spellings merged under '{rep}': {', '.join(spellings)}.")
    df["model"] = df["MODEL"].map(_collapse_ws).str.upper()
    df["Source"] = df["Source"].map(_collapse_ws)

    # TR DATE review table
    status_counts = df["tr_date_status"].value_counts().to_dict()
    notes.append(
        "TR DATE: "
        f"{status_counts.get('ok', 0)} parsed cleanly, "
        f"{status_counts.get('month-order', 0)} resolved as month-first using the Month column, "
        f"{status_counts.get('mismatch', 0)} disagree with Month, "
        f"{status_counts.get('unparsed', 0)} unparseable (Month column used)."
    )
    issue_cols = [c for c in ["S.NO", "TR DATE", "Month", "CUSTOMER NAME", "SM"] if c in df]
    issues = df.loc[df["tr_date_status"] != "ok", issue_cols + ["tr_date", "tr_date_status"]].copy()

    return df.reset_index(drop=True), issues.reset_index(drop=True), notes, file_rows


def find_retail_file() -> Path | None:
    return next((p for p in RETAIL_CANDIDATES if p.exists()), None)


def _load_from_files() -> tuple:
    leads = load_leads(str(LEADS_FILE), _file_signature(LEADS_FILE))
    opps = load_opportunities(str(OPPORTUNITIES_FILE), _file_signature(OPPORTUNITIES_FILE))
    retail_path = find_retail_file()
    if retail_path is None:
        raise DataValidationError(
            "No retail file found. Place one of: "
            + ", ".join(p.name for p in RETAIL_CANDIDATES) + f" in {DATA_DIR}"
        )
    retail_out = load_retail(str(retail_path), _file_signature(retail_path))
    return leads, opps, retail_out, retail_path.name, "Files in data/", None, None


@st.cache_data(show_spinner=False, max_entries=2)
def _clean_sheet(slot_key: str) -> tuple:
    from src import sheets
    raw = sheets.fetch(slot_key)
    leads = finish_leads(raw["leads"], f"Google Sheet tab “{sheets.TAB_LEADS}”")
    opps = finish_opportunities(raw["opportunities"], f"Google Sheet tab “{sheets.TAB_OPPORTUNITIES}”")
    retail_out = finish_retail(raw["retail"], f"Google Sheet tab “{sheets.TAB_RETAIL}”")
    return leads, opps, retail_out, raw["fetched_at"], raw.get("stock"), raw.get("inv_cancelled")


def _load_from_sheet() -> tuple:
    from src import sheets
    slot = sheets.current_slot()
    leads, opps, retail_out, fetched_at, stock, inv_cancelled = _clean_sheet(slot.isoformat())
    label = f"Google Sheet · read {fetched_at:%d %b %Y, %H:%M} IST · next read {sheets.next_slot():%d %b, %H:%M} IST"
    return leads, opps, retail_out, sheets.TAB_RETAIL, label, stock, inv_cancelled


def _latest_complete_month(last_day: date) -> tuple[date, date]:
    """The latest calendar month fully covered by data ending on `last_day`."""
    next_day = last_day + pd.Timedelta(days=1)
    if next_day.month != last_day.month:  # data runs to month end
        return last_day.replace(day=1), last_day
    prev_end = last_day.replace(day=1) - pd.Timedelta(days=1)
    return prev_end.replace(day=1), prev_end


def load_all(force_files: bool = False) -> LoadedData:
    """Load, clean, validate and cross-normalize all three sources.

    Uses the Google Sheet when credentials are configured, else the files.
    Raises DataValidationError with a readable message on missing data/columns."""
    from src import sheets
    use_sheet = sheets.is_configured() and not force_files
    if use_sheet:
        leads, opps, retail_out, retail_name, label, stock, inv_cancelled = _load_from_sheet()
    else:
        leads, opps, retail_out, retail_name, label, stock, inv_cancelled = _load_from_files()
    retail, date_issues, retail_notes, retail_rows = retail_out

    # Work on copies — the cached originals must not be mutated.
    leads, opps, retail = leads.copy(), opps.copy(), retail.copy()
    for col in ("Source", "Channel", "City"):
        unify_case([leads, opps, retail], col)

    # Reporting window = what the data covers. Retail outside it is dropped.
    created = pd.concat([leads["Created On"], opps["Created On"]]).dropna()
    window_start = created.min().date().replace(day=1)
    window_end = max(created.max().date(), retail["tr_date"].max().date() if retail["tr_date"].notna().any() else window_start)
    in_window = retail["month"].map(lambda m: m.start_time.date() <= window_end and m.end_time.date() >= window_start)
    dropped = int((~in_window).sum())
    retail = retail[in_window].reset_index(drop=True)
    if dropped:
        retail_notes.append(f"{dropped:,} retail rows outside {window_start:%b %Y}–{window_end:%b %Y} excluded.")
    default_start, default_end = _latest_complete_month(window_end)

    # Lead -> Opportunity link via ACS_OpportunityId == ID
    opp_ids = set(opps["ID"].dropna())
    leads["linked_to_opportunity"] = leads["opportunity_id"].isin(opp_ids)

    with_id = int(leads["opportunity_id"].notna().sum())
    notes = {
        "leads": [
            f"{len(leads):,} leads loaded.",
            f"{with_id:,} leads carry an ACS_OpportunityId; "
            f"{int(leads['linked_to_opportunity'].sum()):,} of those resolve to a loaded opportunity.",
        ],
        "opportunities": [f"{len(opps):,} opportunities loaded."],
        "retail": retail_notes,
    }
    return LoadedData(
        leads, opps, retail, retail_name, notes, date_issues, retail_rows,
        source="sheet" if use_sheet else "files", source_label=label,
        window_start=window_start, window_end=window_end,
        default_start=default_start, default_end=default_end,
        stock=stock, inv_cancelled=inv_cancelled,
    )
