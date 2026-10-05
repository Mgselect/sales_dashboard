"""
Fuzzy matching of retail register rows to Opportunities.

There is no shared ID between retail.xlsx/pdf and opportunities.xlsx, so each
retail CUSTOMER NAME is compared with every Opportunity "Customer" using
rapidfuzz's token_sort_ratio (word-order-insensitive, so "NOMULA INDRA REDDY"
== "Indra Reddy Nomula"), with a space-insensitive ratio as a second opinion.

The score is based on the name ONLY. Rep and model agreement are reported next
to each match as independent evidence for manual review — they never inflate
the score or move a record between bands.
"""

from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz, process

# Match bands (score is 0-100)
CONFIDENT_THRESHOLD = 85   # >= 85: confident match
POSSIBLE_THRESHOLD = 70    # 70-84.9: possible match, needs manual review; < 70: unmatched

BAND_CONFIDENT = "Confident"
BAND_POSSIBLE = "Possible"
BAND_UNMATCHED = "Unmatched"

# Legal-entity suffixes, honorifics and filler words that differ between the
# two systems ("PVT LTD" vs "PRIVATE LIMITED", "M/S ...") but carry no identity.
_NOISE_WORDS = {
    "PVT", "PRIVATE", "PVTLTD", "LTD", "LIMITED", "LLP", "MS", "M/S", "MR", "MRS",
    "SMT", "DR", "THE", "AND", "CO", "COMPANY",
}

# Opportunity statuses consistent with a vehicle having been retailed — used
# only to break ties between equally scored candidates.
_STATUS_RANK = {"Delivered": 3, "Invoiced": 2, "Booking Confirmed": 1, "Booked": 1}


def normalize_name(name) -> str:
    """Upper-case, strip punctuation and legal suffixes, and join runs of single
    initials ('H.S.D. REALESTATES' -> 'HSD REALESTATES')."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    text = str(name).upper().replace("&", " AND ").replace("M/S", " ")
    text = re.sub(r"[^A-Z0-9 ]", " ", text)
    out: list[str] = []
    run = ""  # consecutive single-letter initials, e.g. H S D -> HSD
    for tok in text.split():
        if tok in _NOISE_WORDS:
            continue
        if len(tok) == 1 and tok.isalpha():
            run += tok
            continue
        if run:
            out.append(run)
            run = ""
        out.append(tok)
    if run:
        out.append(run)
    return " ".join(out)


def name_similarity(a: str, b: str, **_) -> float:
    """Score two normalized names, 0-100.

    token_sort_ratio ignores word order ("NOMULA INDRA REDDY" vs "INDRA REDDY
    NOMULA"). A second, space-insensitive ratio catches words run together or
    split ("REALESTATES" vs "REAL ESTATES"). The higher of the two is used."""
    if not a or not b:
        return 0.0
    return max(fuzz.token_sort_ratio(a, b), fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")))


def band_for(score: float) -> str:
    if score >= CONFIDENT_THRESHOLD:
        return BAND_CONFIDENT
    if score >= POSSIBLE_THRESHOLD:
        return BAND_POSSIBLE
    return BAND_UNMATCHED


def match_retail_to_opportunities(retail: pd.DataFrame, opps: pd.DataFrame, top_n: int = 5) -> pd.DataFrame:
    """For each retail row return its best Opportunity candidate.

    Output (one row per retail row, same index as `retail`):
      match_score       0-100 name_similarity on normalized names
      match_band        Confident / Possible / Unmatched
      opp_id, opp_customer, opp_rep, opp_status, opp_model, opp_created
      rep_agrees        retail SM == opportunity Assigned To (after name normalization)
      model_agrees      retail MODEL == opportunity Model Line
      review_reason     why the row is in the manual-review list (blank if it isn't)
    """
    opp_names = opps["Customer"].map(normalize_name).tolist()
    records = []
    for idx, row in retail.iterrows():
        query = normalize_name(row["customer_name_display"])
        best = None
        if query:
            candidates = process.extract(query, opp_names, scorer=name_similarity, limit=top_n)
            top_score = candidates[0][1] if candidates else 0
            # Among candidates tied on the top score, prefer rep agreement, then
            # model agreement, then a retail-consistent status.
            tied = [c for c in candidates if c[1] == top_score]

            def evidence(c):
                o = opps.iloc[c[2]]
                return (o["rep"] == row["rep"], o["model"] == row["model"], _STATUS_RANK.get(o["Status"], 0))

            best = max(tied, key=evidence) if tied else None

        if best is None:
            records.append({"match_score": 0.0, "match_band": BAND_UNMATCHED})
            continue
        o = opps.iloc[best[2]]
        records.append({
            "match_score": round(float(best[1]), 1),
            "match_band": band_for(best[1]),
            "opp_id": o["ID"],
            "opp_customer": o["Customer"],
            "opp_rep": o["rep"],
            "opp_status": o["Status"],
            "opp_model": o["model"],
            "opp_created": o["Created On"],
        })

    out = pd.DataFrame(records, index=retail.index)
    out["rep_agrees"] = out.get("opp_rep").eq(retail["rep"]) if "opp_rep" in out else False
    out["model_agrees"] = out.get("opp_model").eq(retail["model"]) if "opp_model" in out else False

    def reason(r) -> str:
        if r["match_band"] == BAND_UNMATCHED:
            return "No name match ≥ 70"
        if r["match_band"] == BAND_POSSIBLE:
            return "Possible match (70–84) — verify"
        if not r["rep_agrees"]:
            return "Confident name match, but rep differs"
        return ""

    out["review_reason"] = out.apply(reason, axis=1)
    return out


def summarize_matches(matches: pd.DataFrame) -> dict:
    """Counts and rates for the data-quality panel. Rates are of retail rows."""
    n = len(matches)
    counts = matches["match_band"].value_counts().to_dict() if n else {}
    confident = counts.get(BAND_CONFIDENT, 0)
    possible = counts.get(BAND_POSSIBLE, 0)
    unmatched = counts.get(BAND_UNMATCHED, 0)
    confident_rep_agrees = int(((matches["match_band"] == BAND_CONFIDENT) & matches["rep_agrees"]).sum()) if n else 0
    return {
        "total": n,
        "confident": confident,
        "possible": possible,
        "unmatched": unmatched,
        "confident_rate": confident / n if n else None,
        "possible_rate": possible / n if n else None,
        "unmatched_rate": unmatched / n if n else None,
        "confident_rep_agrees": confident_rep_agrees,
    }
