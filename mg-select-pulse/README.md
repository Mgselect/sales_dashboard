# MG Select Pulse

Sales-funnel, value-add and follow-up dashboard for **MG Select Hyderabad**,
built with Streamlit, pandas and Plotly.

**Data source:** the team's Google Sheet **“Leads-Opp-Bkgs-Stock”** (tabs
*Leads*, *Opportunity*, *Overall TR Data*), read with a Google service account.
The sheet is re-read automatically **every day after 09:20 and 09:30 IST**, and
the **Yesterday** section at the top shows the previous day's new leads,
opportunities, test drives, bookings and deliveries. If the sheet can't be
reached, the dashboard falls back to the files in `data/` and says so.

The page is organised in tabs — **Today · Trends · Funnel · Team · Pipeline ·
Retail & Stock · Guide**, with one filter bar at the top (date range, source,
channel, model, sales rep) that applies to every tab. It opens on the latest
complete calendar month; the **Guide** tab explains every section in plain language.

## Folder layout

```
mg-select-pulse/
  data/
    leads.xlsx            C4C Leads export (as downloaded, don't edit)
    opportunities.xlsx    C4C Opportunities export (as downloaded)
    retail.xlsx           Retail / TR register, preferred
    retail.csv            …or CSV
    retail.pdf            …or PDF (fallback)
  assets/
    logo.png              MG Select logo (transparent background), shown top-left
    logo-original.png     the logo as supplied, kept for reference
  src/
    data_loader.py        reading + cleaning all three sources
    matcher.py            fuzzy name matching, retail ↔ opportunities (not shown on the dashboard; kept for later use)
    analytics.py          funnel / KPI / value-add calculations
    benchmarks.py         L2O / TD% / O2B benchmarks by source and the source mapping
    loss_reasons.py       reads why each Lost opportunity was lost from its closing note
    insights.py           trends, cohort funnel, speed, rep scorecard, pipeline, cancellations, finance, stock, today's actions
    sheets.py             reads the Google Sheet (service account, daily 09:20/09:30 IST reads)
    reconcile.py          independent recount of every headline figure from the raw files
    style.css             all theme CSS
  .streamlit/config.toml  base light theme for Streamlit widgets
  app.py                  Streamlit entrypoint
  requirements.txt
```

**Retail auto-detection:** the app uses the first of `retail.xlsx`, `retail.csv`
or `retail.pdf` that exists in `data/`. If you have both, the xlsx wins. Delete
or rename the others if you want a specific one used.

## Setup from scratch (macOS)

The spec needs Python 3.10+. macOS ships 3.9, so this project uses
[uv](https://docs.astral.sh/uv/) to install a user-local Python 3.12 (no admin
rights needed).

```bash
# 1. Install uv (one time)
curl -LsSf https://astral.sh/uv/install.sh | sh
# open a new terminal so ~/.local/bin is on PATH

# 2. Get Python 3.12 and create the virtual environment inside the project
cd mg-select-pulse
uv python install 3.12
uv venv --python 3.12 .venv

# 3. Activate it and install dependencies
source .venv/bin/activate
uv pip install -r requirements.txt        # or: pip install -r requirements.txt
```

The alternative is to install Python 3.12 from https://www.python.org/downloads/macos/,
then run `python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

## Run

```bash
cd mg-select-pulse
source .venv/bin/activate
streamlit run app.py
```

The dashboard opens at http://localhost:8502 (set in `.streamlit/config.toml`; 8501 is used by the earlier dashboard).

## Google Sheet connection

* The sheet must be shared (Viewer) with
  `sheetreader@salesdashboard-510204.iam.gserviceaccount.com`.
* Credentials live in `.streamlit/secrets.toml` (local, never committed) — see
  `.streamlit/secrets.toml.example` for the layout.
* Read times are `REFRESH_TIMES` in `src/sheets.py` (IST). The first visit after
  each time triggers a fresh read; **Refresh now** forces one immediately.
* Tab names, the spreadsheet ID and the date rules for pasted C4C data are at the
  top of `src/sheets.py`. C4C pastes dates month-first into a day-first sheet, so
  each date column has a checked rule — read the module docstring before changing.

## Deploying on Render

1. **New → Web Service** → connect the GitHub repo.
2. **Root Directory:** `mg-select-pulse`
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:**
   `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`
5. **Environment → Environment Variables:** `PYTHON_VERSION` = `3.12.7`
6. **Environment → Secret Files → Add Secret File:** filename `service_account.json`,
   contents = the whole service-account key JSON file. The dashboard finds it at
   `/etc/secrets/service_account.json` automatically. (Alternatively, set the
   environment variable `GOOGLE_SERVICE_ACCOUNT_JSON` to the JSON text.)
7. Save and redeploy. The Google Sheet must be shared (Viewer) with the service
   account's email address.

## Deploying (Streamlit Community Cloud)

1. Put this folder in a **private** GitHub repository. `.gitignore` already
   keeps out `data/` exports, `.streamlit/secrets.toml` and any `*.json` key.
2. On https://share.streamlit.io → **Create app** → pick the repo, branch, and
   main file `app.py`. Under **Advanced settings** choose Python 3.12.
3. In **Advanced settings → Secrets**, paste the full contents of your local
   `.streamlit/secrets.toml`.
4. Deploy. The app reads the Google Sheet directly, so no data files are needed
   on the server.

## Check the numbers against the source

```bash
python -m src.reconcile                          # latest complete month (default)
python -m src.reconcile 2026-07-01 2026-09-30    # any range
```

This recounts every headline figure straight from the raw source (the Google
Sheet, or the files with `--files`) using separate code, and prints Source vs Dashboard with ✓/✗ for each, plus a
one-line "how to check in Excel". The dashboard runs the same check on every
load and shows a warning at the top only if something stops matching.

## Changing the logo

Replace `assets/logo.png` and refresh the browser. A PNG with a transparent
background looks best on the ivory page. Its display height (64 px) is set in
`.pulse-logo img` in `src/style.css`. If the file is missing, the dashboard
simply shows no logo.

## What's cleaned automatically

* **C4C exports:** the 4 junk rows above the header are skipped. The current
  exports carry malformed cell references that crash openpyxl, so a tolerant
  reader is used automatically. Excel-serial dates are converted. Test drives
  are counted by *First Test Drive Date Completed on* (when the drive happened),
  not by the opportunity's Created On. City/Source/
  Channel casing is unified ("HYDERABAD" → "Hyderabad", "DIGITAL" → "Digital"),
  and IDs lose trailing ".0".
* **Retail:** header rows repeated on each PDF page are dropped. TR DATE is
  parsed per row in any format ("01-07-2026", "24-Aug-26", "03/12/2025"…) and
  checked against the Month column. Rows outside Jan–Sep 2026 are excluded. The
  AMC "N0" typo is read as NO. "2,14,667"-style amounts are parsed, with blank
  treated as ₹0. SM spellings such as "CHandra Shekar Mekala" are merged for
  grouping, while original names are kept for display.



## When next quarter's files change

| Change | Where |
|---|---|
| New reporting window | `WINDOW_START`, `WINDOW_END`, `RETAIL_MONTHS`, `DEFAULT_START/END` in `src/data_loader.py` |
| Renamed column in C4C exports | `LEADS_REQUIRED` / `OPPS_REQUIRED` in `src/data_loader.py` (plus any direct references in `analytics.py`) |
| Renamed column in the retail register | add the new spelling to `RETAIL_COLUMN_ALIASES` in `src/data_loader.py`, with no other changes needed |
| CRE team changes (who counts as CRE in Lead routing) | `CRE_OWNERS` in `src/data_loader.py` — every other lead Owner is a sales manager |
| Benchmark targets, or which C4C Source counts under which benchmark row | `BENCHMARKS` / `SOURCE_TO_BENCHMARK` in `src/benchmarks.py` |
| Loss-reason wording (new phrases, competitor brands, new reasons) | `LOSS_RULES` in `src/loss_reasons.py` — anything unmatched shows as "Other / unclear" |
| New model line (e.g. a third MG Select model) | nothing required; it appears in filters and breakdowns automatically. To fix its chart colour position, add it to `MODEL_ORDER` in `app.py` |
| Header row moved in a C4C export | handled automatically (searches the first 15 rows for "Lead ID" / "ID") |

If a required column is missing, the app stops with a clear error that names
the column and lists the columns it did find.
