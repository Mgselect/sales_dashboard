"""
Source-to-dashboard reconciliation.

Recounts the dashboard's headline numbers straight from the raw source (the
Google Sheet's raw cell values, or the raw files) using a SEPARATE code path
from data_loader/sheets/analytics, then compares:

  * Google Sheet: raw cell values from the API; dates converted by this
    module's own small rules (written independently of src/sheets.py).

  * Leads / Opportunities: raw cell values, filtered on the raw "Created On"
    value exactly like an Excel date filter — no cleaning, no case-folding.
    A raw XML <row> count also confirms no rows were lost on read.
  * Retail (PDF): the PDF's plain text lines are regex-parsed — independent of
    the pdfplumber table extraction the dashboard uses.
    Retail (xlsx/csv): raw cells, filtered on the raw Month label.

Only the date range is applied here (no Source/Channel/Model/Rep filters), so
every number can be checked by hand with a single date filter in Excel.

Run from the project folder:  .venv/bin/python -m src.reconcile [YYYY-MM-DD YYYY-MM-DD] [--files]
"""

from __future__ import annotations

import re
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from src import analytics as an
from src import data_loader as dl
from src import insights as ins

_EXCEL_EPOCH = date(1899, 12, 30)


def _serial(d: date) -> float:
    return float((d - _EXCEL_EPOCH).days)


def _raw_grid(path: Path) -> list[list]:
    """Raw cell values. openpyxl if the file is standard; the tolerant XML
    reader for C4C exports that openpyxl rejects."""
    try:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    except Exception:
        return dl._read_xlsx_tolerant(path)


def _raw_xml_row_count(path: Path) -> int | None:
    """Number of <row> elements that contain at least one cell, straight from the XML."""
    try:
        with zipfile.ZipFile(path) as z:
            sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
            xml = z.read(sheet).decode("utf-8", errors="ignore")
        return len(re.findall(r"<row\b[^>]*>(?=\s*<c\b)", xml))
    except Exception:
        return None


def _raw_table(path: Path, key: str) -> tuple[list[str], list[list]]:
    grid = _raw_grid(path)
    h = next(i for i, r in enumerate(grid[:20]) if key in [str(v).strip() for v in r if v is not None])
    header = [str(v).strip() if v is not None else "" for v in grid[h]]
    rows = [r for r in grid[h + 1:] if any(v not in (None, "") for v in r)]
    return header, rows


def num_or_none(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _in_period(raw, start: date, end: date) -> bool:
    """Excel-style date filter on a raw cell (serial number or datetime)."""
    if isinstance(raw, (int, float)):
        return _serial(start) <= raw < _serial(end + timedelta(days=1))
    if isinstance(raw, datetime):
        return start <= raw.date() <= end
    return False


# --------------------------------------------------------------------------- #
# Google Sheet raw cells -> (header, rows), dates as serial day numbers
# --------------------------------------------------------------------------- #

_SWAP_IF_STORED = {"Booking Date", "Invoice Date_V", "Last Follow up Activity Date", "Open Activity Due Date",
                   "First Test Drive Date Completed on", "Actual Delivery Date"}
_MONTH_FIRST_TEXT = _SWAP_IF_STORED | {"Changed On"}
_TEXT_FORMATS_DAY_FIRST = ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%y", "%d-%b-%Y", "%d-%m-%Y %H:%M")
_TEXT_FORMATS_MONTH_FIRST = ("%m-%d-%Y", "%m/%d/%Y", "%m-%d-%Y %I:%M %p", "%m-%d-%Y %H:%M", "%m-%d-%Y %I:%M:%S %p")


def _sheet_text_date(text: str, month_first: bool) -> float | None:
    """A date the sheet kept as text, tried against a fixed list of formats."""
    for fmt in (_TEXT_FORMATS_MONTH_FIRST if month_first else _TEXT_FORMATS_DAY_FIRST):
        try:
            return float((datetime.strptime(text.strip(), fmt).date() - _EXCEL_EPOCH).days)
        except ValueError:
            continue
    return None


def _sheet_month(v) -> str:
    if isinstance(v, (int, float)) and v != "":
        d = _EXCEL_EPOCH + timedelta(days=int(v))
        return f"{d:%b}-{d.day:02d}"            # "Aug-25" is stored as 25 Aug
    return str(v).strip()


def _sheet_date(col: str, v, month_label: str | None = None) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        d = _EXCEL_EPOCH + timedelta(days=int(v))
        if col in _SWAP_IF_STORED and d.day <= 12:
            d = date(d.year, d.day, d.month)
        if col == "TR DATE" and month_label and d.day <= 12 and d.strftime("%b-%y") != month_label:
            alt = date(d.year, d.day, d.month)
            if alt.strftime("%b-%y") == month_label:
                d = alt
        return float((d - _EXCEL_EPOCH).days)
    return _sheet_text_date(str(v), month_first=col in _MONTH_FIRST_TEXT)


def _sheet_table(grid: list[list], key: str) -> tuple[list[str], list[list]]:
    hi = next(i for i, r in enumerate(grid[:15]) if key in [str(c).strip() for c in r])
    header = [str(c).strip() for c in grid[hi]]
    # A duplicated column name: only the first column with that name is used
    header = [h if h not in header[:i] else f"{h} #{i}" for i, h in enumerate(header)]
    rows = [list(r) + [""] * (len(header) - len(r)) for r in grid[hi + 1:] if any(str(c).strip() for c in r)]
    mi = header.index("Month") if "Month" in header else None
    out = []
    for r in rows:
        r = [None if v == "" else v for v in r[: len(header)]]
        if mi is not None:
            r[mi] = _sheet_month(r[mi]) if r[mi] is not None else None
        for c in ("Created On", "Changed On", "TR DATE", *_SWAP_IF_STORED):
            if c in header:
                i = header.index(c)
                r[i] = _sheet_date(c, r[i], r[mi] if mi is not None else None)
        out.append(r)
    return header, out


# --------------------------------------------------------------------------- #
# Independent source counts
# --------------------------------------------------------------------------- #

def source_counts_c4c(start: date, end: date, leads_tbl=None, opps_tbl=None) -> dict:
    """Counts from raw Leads / Opportunities tables (header, rows). Defaults to the files."""
    out: dict = {}
    from_files = leads_tbl is None

    header, rows = leads_tbl or _raw_table(dl.LEADS_FILE, "Lead ID")
    ci = header.index("Created On")
    out["leads_file_rows"] = len(rows)
    out["leads_xml_rows"] = _raw_xml_row_count(dl.LEADS_FILE) if from_files else None
    out["has_created_by"] = "Created By" in header
    out["leads"] = sum(_in_period(r[ci], start, end) for r in rows)
    oi = header.index("Owner")
    cre = {" ".join(n.split()).casefold() for n in dl.CRE_OWNERS}
    in_p = [r for r in rows if _in_period(r[ci], start, end)]
    out["leads_cre"] = sum(" ".join(str(r[oi] or "").split()).casefold() in cre for r in in_p)
    out["leads_sm"] = len(in_p) - out["leads_cre"]
    cbi = header.index("Created By") if "Created By" in header else None
    out["cre_handovers"] = sum(
        " ".join(str(r[cbi] or "").split()).casefold() in cre
        and " ".join(str(r[oi] or "").split()).casefold() not in cre
        for r in in_p
    ) if cbi is not None else 0

    header, rows = opps_tbl or _raw_table(dl.OPPORTUNITIES_FILE, "ID")
    ci, si, ti = header.index("Created On"), header.index("Status"), header.index("Test Drive Completed")
    bi = header.index("Booking Date") if "Booking Date" in header else None
    out["delivery_date_column"] = next((c for c in an.DELIVERY_DATE_COLUMNS if c in header), "Created On")
    di = header.index(out["delivery_date_column"])
    tdi = header.index(an.TEST_DRIVE_DATE_COLUMN) if an.TEST_DRIVE_DATE_COLUMN in header else None
    in_p = [r for r in rows if _in_period(r[ci], start, end)]
    out["opps_file_rows"] = len(rows)
    out["opps_xml_rows"] = _raw_xml_row_count(dl.OPPORTUNITIES_FILE) if from_files else None
    out["opportunities"] = len(in_p)
    out["test_drives_created_in_period"] = sum(str(r[ti]).strip() == "Yes" for r in in_p)
    out["test_drives"] = (
        sum(str(r[ti]).strip() == "Yes" and _in_period(r[tdi], start, end) for r in rows)
        if tdi is not None else out["test_drives_created_in_period"]
    )
    # Consistency: every "Yes" should carry a test-drive date and every "No" none.
    out["td_flag_date_conflicts"] = (
        sum((str(r[ti]).strip() == "Yes") != (r[tdi] not in (None, "")) for r in rows) if tdi is not None else 0
    )
    out["booked"] = sum(str(r[si]).strip() == "Booked" for r in in_p)
    if bi is not None:  # bookings dated in the period (any creation date), and how many are now cancelled
        bk_p = [r for r in rows if _in_period(r[bi], start, end)]
        out["bookings_by_date"] = len(bk_p)
        out["bookings_cancelled"] = sum(str(r[si]).strip() in ("Booking Cancelled", "Intend to Cancel") for r in bk_p)
    out["booking_confirmed"] = sum(str(r[si]).strip() == "Booking Confirmed" for r in in_p)
    if bi is not None:
        out["ever_booked"] = sum(r[bi] not in (None, "") and str(r[si]).strip() != "Booking Cancelled" for r in in_p)
    out["delivered_c4c"] = sum(
        str(r[si]).strip() == "Delivered" and _in_period(r[di], start, end) for r in rows
    )
    # Untouched opportunities (created in period, still open), as of the latest Created On in the data.
    col = {c: header.index(c) for c in (an.FOLLOW_UP_DATE, an.FOLLOW_UP_TYPE, an.FOLLOW_UP_NOTES, an.NEXT_DUE)
           if c in header}
    as_of = max(int(float(r[ci])) for r in rows if num_or_none(r[ci]) is not None)
    blank = lambda r, c: c not in col or r[col[c]] in (None, "")
    open_rows = [r for r in in_p if str(r[si]).strip() == an.OPEN_STATUS]
    out["open"] = len(open_rows)
    out["lost"] = sum(str(r[si]).strip() == "Lost" for r in in_p)
    out["never_touched"] = sum(blank(r, an.FOLLOW_UP_DATE) and blank(r, an.FOLLOW_UP_TYPE) and blank(r, an.FOLLOW_UP_NOTES)
                               for r in open_rows)
    out["overdue"] = sum(num_or_none(r[col[an.NEXT_DUE]]) is not None and int(float(r[col[an.NEXT_DUE]])) < as_of
                         for r in open_rows) if an.NEXT_DUE in col else 0
    return out


# Text-line patterns for the retail PDF:
#   "<S.NO> <TR DATE> <Mon-YY> <VIN> ... [ACCESSORIES] <EW> <AMC> <PPF> <Source>"
# Older rows have blank EW/AMC/PPF, so a second, flag-less pattern is tried.
_PDF_PREFIX = r"^(?P<sno>\d+)\s+(?P<trdate>\S+)\s+(?P<month>[A-Za-z]{3}-\d{2})\s+(?P<vin>[A-Z0-9]{17})\s+(?P<rest>.*?)\s+"
_PDF_LINE = re.compile(
    _PDF_PREFIX + r"(?:(?P<acc>[\d,]+)\s+)?(?P<ew>YES|NO|N0)\s+(?P<amc>YES|NO|N0)\s+(?P<ppf>YES|NO|N0)\s+(?P<source>\S.*)$",
    re.IGNORECASE,
)
_PDF_LINE_NO_FLAGS = re.compile(
    _PDF_PREFIX + r"(?:(?P<acc>[\d,]+)\s+)?(?P<ins>IN|OUT)\s+(?P<source>[A-Za-z_\- ]+)$",
    re.IGNORECASE,
)


def _month_labels(start: date, end: date) -> set[str]:
    labels, d = set(), date(start.year, start.month, 1)
    while d <= end:
        labels.add(d.strftime("%b-%y"))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return labels


def _window_labels(start: date, end: date) -> set[str]:
    return {m.title() for m in _month_labels(start, end)}


def _simple_date(raw) -> date | None:
    """Deliberately simple, separate TR DATE reader: a fixed list of formats."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, (int, float)) and not pd.isna(raw):
        return _EXCEL_EPOCH + timedelta(days=int(raw))
    text = str(raw or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d-%b-%y", "%d-%b-%Y", "%d/%m/%y", "%d-%m-%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def source_counts_retail(path: Path | None, start: date, end: date, window: tuple[date, date],
                         sheet_tbl=None) -> dict:
    """Retail counted by each row's TR DATE (Month label only if TR DATE is unreadable)."""
    labels = _month_labels(start, end)
    recs: list[dict] = []
    unparsed_lines = 0
    if sheet_tbl is not None:
        header, rows = sheet_tbl
        idx = {h.strip().upper(): i for i, h in enumerate(header)}
        for r in rows:
            recs.append({"month": r[idx["MONTH"]], "trdate": r[idx["TR DATE"]], "acc": r[idx["ACCESSORIES"]],
                         "ew": r[idx["EW"]], "amc": r[idx["AMC"]], "ppf": r[idx["PPF"]]})
    elif path.suffix.lower() == ".pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                for line in (page.extract_text() or "").splitlines():
                    m = _PDF_LINE.match(line.strip()) or _PDF_LINE_NO_FLAGS.match(line.strip())
                    if m:
                        recs.append(m.groupdict())
                    elif re.match(r"^\d+\s+\S+\s+[A-Za-z]{3}-\d{2}\s", line.strip()):
                        unparsed_lines += 1  # looks like a data row but didn't fit the pattern
    else:
        header, rows = _raw_table(path, "CUSTOMER NAME") if path.suffix.lower() != ".csv" else (None, None)
        if header is None:
            raw = pd.read_csv(path, dtype=str, keep_default_na=False)
            header, rows = list(raw.columns), raw.values.tolist()
        idx = {h.strip().upper(): i for i, h in enumerate(header)}
        for r in rows:
            recs.append({
                "month": r[idx["MONTH"]], "trdate": r[idx["TR DATE"]], "acc": r[idx["ACCESSORIES"]],
                "ew": r[idx["EW"]], "amc": r[idx["AMC"]], "ppf": r[idx["PPF"]],
            })

    # Filter on each row's own TR DATE, exactly like an Excel date filter. Only a
    # TR DATE that can't be read at all falls back to its Month label.
    in_window = [r for r in recs if str(r["month"]).strip().title() in _window_labels(*window)]
    sel = []
    for r in in_window:
        d = _simple_date(r["trdate"])
        if d is not None:
            if start <= d <= end:
                sel.append(r)
        elif str(r["month"]).strip().title() in labels:
            sel.append(r)

    def yes(col):
        return sum(str(r.get(col) or "").strip().upper() == "YES" for r in sel)

    def amount(v):
        if v in (None, "") or (isinstance(v, float) and pd.isna(v)):
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        return float(re.sub(r"[^\d.]", "", str(v)) or 0)

    return {
        "retail": len(sel),
        "accessories_total": sum(amount(r.get("acc")) for r in sel),
        "ew_yes": yes("ew"), "amc_yes": yes("amc"), "ppf_yes": yes("ppf"),
        "retail_file_rows": len(recs),
        "unparsed_lines": unparsed_lines,
    }


# --------------------------------------------------------------------------- #
# Comparison
# --------------------------------------------------------------------------- #

def reconcile(data: dl.LoadedData, start: date, end: date) -> tuple[pd.DataFrame, list[str]]:
    """Return (table of Metric / Source file / Dashboard / Match / How to check, notes)."""
    f = an.Filters(start, end)
    leads_f = an.filter_leads(data.leads, f)
    opps_f = an.filter_opportunities(data.opportunities, f)
    retail_f = an.filter_retail(data.retail, f)
    statuses = an.ordered_statuses(data.opportunities)
    kpi = an.value_add_kpis(retail_f)
    fu_view = an.follow_up_view(opps_f, an.export_as_of(data.opportunities), 7)

    window = (data.window_start, data.window_end)
    if data.source == "sheet":
        from src import sheets
        grids = sheets.cached_raw_grids(sheets.current_slot().isoformat())  # same download as the dashboard
        src = source_counts_c4c(start, end, _sheet_table(grids[sheets.TAB_LEADS], "Lead ID"),
                                _sheet_table(grids[sheets.TAB_OPPORTUNITIES], "ID"))
        rsrc = source_counts_retail(None, start, end, window, _sheet_table(grids[sheets.TAB_RETAIL], "CUSTOMER NAME"))
        leads_name, opps_name, retail_name = (f"Sheet “{sheets.TAB_LEADS}”", f"Sheet “{sheets.TAB_OPPORTUNITIES}”",
                                              f"Sheet “{sheets.TAB_RETAIL}”")
    else:
        src = source_counts_c4c(start, end)
        retail_path = dl.find_retail_file()
        rsrc = source_counts_retail(retail_path, start, end, window)
        leads_name, opps_name, retail_name = "leads.xlsx", "opportunities.xlsx", retail_path.name
    period = f"{start:%d/%m/%Y}–{end:%d/%m/%Y}"
    ordered_src = src["booked"] + (src["booking_confirmed"] if "Booking Confirmed" in statuses else 0)

    rows = [
        ("Leads — rows in file", src["leads_file_rows"], len(data.leads), f"{leads_name}: total data rows"),
        ("Leads — Created On in period", src["leads"], len(leads_f), f"{leads_name}: filter Created On {period}"),
        ("  Leads with CRE (current Owner)", src["leads_cre"], int(leads_f["lead_route"].eq(dl.ROUTE_CRE).sum()),
         "…then filter Owner = " + " / ".join(dl.CRE_OWNERS)),
        ("  Leads with sales managers (current Owner)", src["leads_sm"], int(leads_f["lead_route"].eq(dl.ROUTE_SM).sum()),
         "…then filter Owner = everyone else"),
        ("Opportunities — rows in file", src["opps_file_rows"], len(data.opportunities), f"{opps_name}: total data rows"),
        ("Opportunities — Created On in period", src["opportunities"], len(opps_f), f"{opps_name}: filter Created On {period}"),
        ("Test drives done in period (funnel stage)", src["test_drives"], len(an.filter_test_drives(data.opportunities, f)),
         f"{opps_name}: Test Drive Completed = Yes, First Test Drive Date Completed on {period}"),
        ("  ref: created in period with Test Drive = Yes", src["test_drives_created_in_period"],
         int(opps_f["test_drive_done"].sum()), f"…filter Created On {period}, then Test Drive Completed = Yes"),
        ("Ordered (Status = " + " / ".join(statuses) + ")", ordered_src,
         int(an.ordered_mask(opps_f, an.ORDERED_MODE_STATUS, statuses).sum()), "…then filter Status = Booked"),
    ]
    if src["has_created_by"]:
        rows.insert(5, ("  CRE-created leads now with sales managers", src["cre_handovers"],
                        an.cre_handovers(leads_f, dl.CRE_OWNERS), "…Created By = a CRE, Owner = not a CRE"))
    if "ever_booked" in src:
        rows.append(("Ordered (has Booking Date, not cancelled)", src["ever_booked"],
                     int(an.ordered_mask(opps_f, an.ORDERED_MODE_BOOKING_DATE, statuses).sum()),
                     "…then Booking Date non-blank, Status ≠ Booking Cancelled"))
    rows += [
        (f"C4C Delivered ({src['delivery_date_column']} in period)", src["delivered_c4c"],
         an.delivered_crosscheck(data.opportunities, retail_f, f)["opp_delivered"],
         f"{opps_name}: Status = Delivered, {src['delivery_date_column']} {period}"),
        ("Open opportunities (Under Follow-up)", src["open"], len(fu_view),
         "…Created On in period, Status = Under Follow-up"),
        ("  never touched (no follow-up logged)", src["never_touched"],
         int(fu_view["follow_up_state"].eq(an.TOUCH_NEVER).sum()),
         "…Last Follow up Activity Date, Activity and Notes all blank"),
        ("  overdue follow-up", src["overdue"], int(fu_view["overdue"].sum()),
         "…Open Activity Due Date before the export date"),
        ("Bookings (Booking Date in period)", src.get("bookings_by_date", 0),
         len(ins.bookings_in_period(data.opportunities, f)), f"{opps_name}: filter Booking Date {period}"),
        ("  of which now cancelled", src.get("bookings_cancelled", 0),
         int(ins.bookings_in_period(data.opportunities, f)["Status"].isin(ins.CANCELLED_STATUSES).sum()),
         "…then Status = Booking Cancelled / Intend to Cancel"),
        ("Lost opportunities", src["lost"], int(opps_f["Status"].eq("Lost").sum()),
         "…Created On in period, Status = Lost"),
        ("Retail — rows in file (all months)", rsrc["retail_file_rows"], data.retail_file_rows,
         f"{retail_name}: total data rows"),
        ("Retail units", rsrc["retail"], kpi["units"], f"{retail_name}: filter TR DATE {period}"),
        ("Accessories total (₹)", round(rsrc["accessories_total"]), round(kpi["accessories_total"]), "…sum of ACCESSORIES"),
        ("EW = YES", rsrc["ew_yes"], int(retail_f["ew_flag"].sum()), "…count EW = YES"),
        ("AMC = YES", rsrc["amc_yes"], int(retail_f["amc_flag"].sum()), "…count AMC = YES (N0 counts as NO)"),
        ("PPF = YES", rsrc["ppf_yes"], int(retail_f["ppf_flag"].sum()), "…count PPF = YES"),
    ]
    if data.source == "sheet" and data.stock is not None and sheets.TAB_STOCK in grids:
        g = grids[sheets.TAB_STOCK]
        hi = next(i for i, r in enumerate(g[:10]) if "Vin Number" in [str(c).strip() for c in r])
        vi = [str(c).strip() for c in g[hi]].index("Vin Number")
        n_stock = sum(1 for r in g[hi + 1:] if len(r) > vi and str(r[vi]).strip())
        rows.append(("Stock — cars listed", n_stock, len(data.stock), "Sheet “Stock”: rows with a VIN"))
    table = pd.DataFrame(rows, columns=["Metric", "Source file", "Dashboard", "How to check in Excel"])
    table["Match"] = (table["Source file"] == table["Dashboard"]).map({True: "✓ Match", False: "✗ Differs"})
    table = table[["Metric", "Source file", "Dashboard", "Match", "How to check in Excel"]]

    notes: list[str] = []
    for label, xml_rows, file_rows in (("leads.xlsx", src["leads_xml_rows"], src["leads_file_rows"]),
                                       ("opportunities.xlsx", src["opps_xml_rows"], src["opps_file_rows"])):
        if xml_rows is not None:
            # XML rows include the title, "Last Updated On" and header rows.
            notes.append(f"{label}: {xml_rows:,} non-empty XML rows = 3 heading rows + {xml_rows - 3:,} data rows "
                         f"({'✓' if xml_rows - 3 == file_rows else '✗'} vs {file_rows:,} read).")
    notes.append(
        f"{opps_name}: Test Drive Completed vs test-drive date — "
        + ("✓ every Yes has a date and every No has none." if src["td_flag_date_conflicts"] == 0
           else f"✗ {src['td_flag_date_conflicts']} row(s) disagree (Yes without a date, or No with one).")
    )
    if rsrc["unparsed_lines"]:
        notes.append(f"{retail_name}: {rsrc['unparsed_lines']} data line(s) in the PDF text could not be "
                     "parsed by the independent check — review manually.")
    return table, notes


if __name__ == "__main__":
    loaded = dl.load_all(force_files="--files" in sys.argv)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    s = date.fromisoformat(args[0]) if len(args) > 1 else loaded.default_start
    e = date.fromisoformat(args[1]) if len(args) > 1 else loaded.default_end
    print(f"Source: {loaded.source_label}")
    tbl, nts = reconcile(loaded, s, e)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_colwidth", 70)
    print(f"Reconciliation {s} → {e}\n")
    print(tbl.to_string(index=False))
    print()
    for n in nts:
        print("-", n)
    print("\nALL MATCH" if (tbl["Match"] == "✓ Match").all() else "\nMISMATCHES FOUND")
