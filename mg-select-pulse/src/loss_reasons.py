"""
Loss-reason classification for Lost opportunities.

C4C's own "Cancellation Status" column is empty in the export, so the reason is
read from the closing note ("Post Activity Notes of the Last Completed Activity")
that the closing team writes on every Lost opportunity, e.g.
    "Sejal // Tried connecting the customer 4 times but customer is not contactable."

Rules are checked in order; the first match wins. Anything that matches no rule
is reported as "Other / unclear" so nothing is silently forced into a bucket.
Edit LOSS_RULES to add phrases or reasons.
"""

from __future__ import annotations

import re

import pandas as pd

NOTES_COLUMN = "Post Activity Notes of the Last Completed Activity"
LOST_STATUS = "Lost"
UNCLEAR = "Other / unclear"

# (reason, regex) — first match wins, so the more specific reasons come first.
LOSS_RULES: list[tuple[str, str]] = [
    ("Chose another MG model",
     r"(purchas\w*|purcahs\w*|bought|booked|going for|switched to)\s+(the\s+|an?\s+)?mg\s+"
     r"(windsor|majestor|hector|gloster|astor|zs ?ev|comet)\b"),
    ("Bought another brand",
     r"v-?class|\b(mercedes|benz|bmw|audi|toyota|vellfire|innova|hycross|kia|carnival|byd|volvo|lexus|hyundai|"
     r"tata|mahindra|porsche)\b|land rover|range rover|other brand|another brand|competitor"),
    ("Bought from MG (duplicate record)",
     r"already (booked|purchased|purcahsed|bought|paid)|has booked|booked the vehicle|booked in the name|"
     r"booking with another number|from (a )?different number|from another number|delivered to him|"
     r"paid the booking amount"),
    ("Not contactable / wrong number",
     r"not contactable|not connected|no response|not reachable|not answering|not responding|switch(ed)? off|"
     r"not picking|unreachable|invalid number|wrong number|does not belong|\bnc\b"),
    ("Budget / price", r"budget|price|expensive|costly|afford|financ|loan|\bemi\b|cibil"),
    ("Still in touch with showroom", r"in touch with (the )?showroom|will be visiting the showroom"),
    ("Postponed / deciding later",
     r"postpon|later|next (few )?months?|diwali|time to decide|will (let us know|connect|get back|decide)|"
     r"still interested|not now|future|november|december|january|investment plan|get back"),
    ("Dropped plan / not interested",
     r"dropped|drop the plan|not interested|no (plan|requirement)|cancel|down ?sized|personal reason|"
     r"casually enquired|not looking"),
    ("Product / fit",
     r"build quality|feature|\brange\b|seat|ground clearance|colou?r|design|charging|didn.?t like|did not like"),
]
_COMPILED = [(name, re.compile(rx, re.I)) for name, rx in LOSS_RULES]
REASON_ORDER = [name for name, _ in LOSS_RULES] + [UNCLEAR]


def classify(note) -> str:
    text = str(note or "")
    for name, rx in _COMPILED:
        if rx.search(text):
            return name
    return UNCLEAR


def closer(note) -> str | None:
    """The closing agent's name, written before '//' or '||' at the start of the note."""
    text = re.sub(r"\*+[^*]*\*+\s*/*", "", str(note or ""))  # drop "****nearest sub category selected***//"
    m = re.match(r"^\W*([A-Za-z][A-Za-z .]{1,24}?)\s*(?:/{2,}|\|\|)", text)
    return m.group(1).strip().title() if m else None


def lost_view(opps: pd.DataFrame) -> pd.DataFrame:
    """Lost opportunities (already filtered) with reason and closing agent."""
    lost = opps[opps["Status"].eq(LOST_STATUS)].copy()
    notes = lost[NOTES_COLUMN] if NOTES_COLUMN in lost else pd.Series("", index=lost.index)
    lost["loss_reason"] = notes.map(classify)
    lost["closed_by"] = notes.map(closer)
    return lost


def reason_summary(lost: pd.DataFrame) -> pd.DataFrame:
    counts = lost["loss_reason"].value_counts()
    out = pd.DataFrame({"Reason": REASON_ORDER})
    out["Lost"] = out["Reason"].map(counts).fillna(0).astype(int)
    out["Share %"] = out["Lost"] / len(lost) * 100 if len(lost) else 0.0
    out["Test drive done"] = out["Reason"].map(lost.groupby("loss_reason")["test_drive_done"].sum()).fillna(0).astype(int)
    return out[out["Lost"] > 0].sort_values("Lost", ascending=False).reset_index(drop=True)
