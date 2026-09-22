"""A synthetic contract corpus, for evaluating retrieval on something bigger than a demo.

Two properties matter, and they pull against each other:

  * every document shares a domain, vocabulary and register, so distractors are genuinely
    hard — "thirty (30) days" appears everywhere;
  * every specific fact lives in exactly one document, so removing that document makes a
    question about it provably unanswerable. That is what the abstention eval needs, and
    it is only true if the corpus is built for it.

    uv run python -m app.evals seed --space "Vendor Corpus" --documents 12
"""

import textwrap
from dataclasses import dataclass

import pymupdf

VENDORS = [
    ("Northwind Systems", "NWS", "Delaware"), ("Ardent Logistics", "ARL", "New York"),
    ("Kestrel Analytics", "KST", "California"), ("Brightmoor Health", "BMH", "Illinois"),
    ("Calder Freight", "CDF", "Texas"), ("Dunmore Energy", "DME", "Alberta"),
    ("Everly Robotics", "EVR", "Ontario"), ("Fenwick Media", "FNW", "England"),
    ("Granite Payments", "GRP", "Singapore"), ("Halcyon Biotech", "HLB", "Massachusetts"),
    ("Ironvale Mining", "IVM", "Western Australia"), ("Juniper Telecom", "JNT", "Ireland"),
    ("Karst Insurance", "KRI", "Bermuda"), ("Lowell Aerospace", "LWA", "Washington"),
]

# Each clause carries a fact unique to its document — the notice period, the cap, the
# uptime figure. Those uniqueness points are what the eval questions target.
CLAUSES = [
    ("Definitions", "\"Customer Data\" means any data submitted by Customer to the {vendor} "
     "Platform. \"Authorised User\" means an employee or contractor of Customer registered "
     "under Customer's {code} account identifier. \"Order Form\" means an ordering document "
     "executed under this Agreement and numbered in the {code}-series."),
    ("Fees and Payment", "Customer shall pay the fees set out in each Order Form. {vendor} "
     "invoices monthly in arrears, due net {net} days. Amounts unpaid after that date accrue "
     "interest at {interest}% per month. Fees may be increased once per renewal term on "
     "{feenotice} days written notice."),
    ("Service Levels", "{vendor} shall make the Platform available {uptime}% of the time each "
     "calendar month, excluding maintenance notified {maint} business days in advance. Below "
     "that figure Customer earns a service credit of {credit}% of the monthly fee, which is "
     "Customer's sole remedy for unavailability."),
    ("Support and Response", "{vendor} shall acknowledge a Severity One incident within "
     "{sev1} minutes and a Severity Two incident within {sev2} hours, in each case during the "
     "support window of {window}. Severity is determined by {vendor} acting reasonably."),
    ("Confidentiality", "Each party shall protect the other's Confidential Information with no "
     "less than reasonable care, and these obligations survive for {conf} years after "
     "termination. Disclosure to professional advisers is permitted where they are bound by "
     "equivalent obligations."),
    ("Intellectual Property", "{vendor} retains all right, title and interest in the Platform. "
     "Customer grants {vendor} a licence to use Customer Data solely to provide the Services. "
     "Feedback provided by Customer may be used by {vendor} without restriction or attribution."),
    ("Warranties", "{vendor} warrants the Platform will perform materially in accordance with "
     "its documentation for {warranty} days following the Effective Date. Customer's exclusive "
     "remedy for breach of this warranty is repair, replacement, or refund of fees paid for "
     "the affected period."),
    ("Indemnity", "{vendor} shall indemnify Customer against third party claims that the "
     "Platform infringes a patent or copyright, provided Customer notifies {vendor} within "
     "{indemnity} days of becoming aware of the claim and grants sole control of the defence."),
    ("Limitation of Liability", "Neither party's aggregate liability shall exceed {cap} times "
     "the fees paid in the {capmonths} months preceding the claim. Neither party is liable for "
     "loss of profit, revenue, or anticipated savings, whether in contract or tort."),
    ("Insurance", "{vendor} shall maintain commercial general liability cover of not less than "
     "USD {insurance} million and professional indemnity cover of not less than USD "
     "{proindemnity} million throughout the Term, and shall furnish certificates on request."),
    ("Audit Rights", "Customer may audit {vendor}'s compliance with this Agreement once per "
     "{audit} month period, on {auditnotice} days written notice, during normal business hours, "
     "at Customer's expense unless the audit reveals a material breach."),
    ("Force Majeure", "Neither party is liable for failure caused by an event beyond its "
     "reasonable control. If the event continues for more than {fm} consecutive days, either "
     "party may terminate the affected Order Form on written notice."),
    ("Assignment", "Neither party may assign this Agreement without the other's written "
     "consent, save that {vendor} may assign to an affiliate or successor in connection with a "
     "merger or sale of substantially all its assets on {assign} days notice."),
    ("Term", "This Agreement runs for an initial term of {term} months from the Effective Date "
     "and renews automatically for successive {renewal} month periods unless either party "
     "gives notice of non-renewal {nonrenew} days before the end of the then-current term."),
    ("Termination for Convenience", "Either party may terminate this Agreement for convenience "
     "on {convenience} days prior written notice. Prepaid fees covering the period after "
     "termination are refunded pro rata within {refund} days."),
    ("Termination for Cause", "Either party may terminate with immediate effect on a material "
     "breach uncured {cure} days after written notice, or immediately on the other party's "
     "insolvency or the appointment of a receiver over a material part of its assets."),
    ("Data Deletion", "On termination {vendor} shall, within {deletion} days, return or "
     "securely destroy all Customer Data and certify destruction in writing, save where "
     "retention is required by law for no longer than {retention} years."),
    ("Sub-processors", "{vendor} may engage sub-processors on {subproc} days prior notice to "
     "Customer, during which Customer may object on reasonable data protection grounds. "
     "{vendor} remains liable for the acts and omissions of each sub-processor."),
    ("Security Incidents", "{vendor} shall notify Customer of a Personal Data Breach without "
     "undue delay and in any event within {breach} hours of becoming aware of it, describing "
     "the categories of data subject affected and the remedial steps taken."),
    ("Governing Law", "This Agreement is governed by the laws of {state}, and the parties "
     "submit to the exclusive jurisdiction of its courts. The parties shall first attempt "
     "resolution by senior executives over a period of {escalation} days."),
]

FILLER = (
    "Each party acknowledges it has taken independent advice on this Section and that the "
    "allocation of risk here is reflected in the fees. Nothing in this Section limits liability "
    "for fraud or for death or personal injury caused by negligence. Notices under this Section "
    "are in writing to the addresses in the Order Form and are deemed received on the second "
    "business day after dispatch. The provisions of this Section survive termination to the "
    "extent necessary to give them effect. "
)


@dataclass
class Document:
    filename: str
    pdf: bytes
    pages: int


def _values(seed: int) -> dict[str, object]:
    """Each document's numbers differ, so every fact is unique to one file."""
    n = seed + 1
    return {
        "net": 15 + n * 5, "interest": round(0.5 + n * 0.25, 2),
        "feenotice": 30 + n * 10, "uptime": round(99.0 + n * 0.07, 2),
        "maint": 2 + n, "credit": 5 + n * 3, "sev1": 15 + n * 10, "sev2": 2 + n,
        "window": f"{6 + n}am to {6 + n}pm local time", "conf": 2 + n,
        "warranty": 30 + n * 15, "indemnity": 5 + n * 3, "cap": n,
        "capmonths": 6 + n * 2, "insurance": n * 2, "proindemnity": n + 1,
        "audit": 6 + n * 3, "auditnotice": 10 + n * 5, "fm": 20 + n * 7,
        "assign": 10 + n * 4, "term": 12 + n * 6, "renewal": 6 + n * 3,
        "nonrenew": 30 + n * 15, "convenience": 15 + n * 10, "refund": 20 + n * 8,
        "cure": 5 + n * 4, "deletion": 15 + n * 7, "retention": n + 2,
        "subproc": 10 + n * 6, "breach": 24 + n * 12, "escalation": 10 + n * 5,
    }


def build(count: int) -> list[Document]:
    docs: list[Document] = []
    for seed in range(min(count, len(VENDORS))):
        vendor, code, state = VENDORS[seed]
        values = _values(seed) | {"vendor": vendor, "code": code, "state": state}
        pdf = pymupdf.open()
        for i, (heading, body) in enumerate(CLAUSES, 1):
            page = pdf.new_page()
            page.insert_textbox(pymupdf.Rect(56, 46, 540, 74),
                                f"MASTER SERVICES AGREEMENT - {vendor} ({code})", fontsize=8)
            page.insert_textbox(pymupdf.Rect(56, 88, 540, 118), f"{i}. {heading}", fontsize=13)
            text = body.format(**values) + " " + FILLER * 2
            page.insert_textbox(pymupdf.Rect(56, 132, 540, 780),
                                "\n".join(textwrap.wrap(text, 92)), fontsize=10)
        docs.append(Document(f"MSA-{code}-{vendor.split()[0]}.pdf", pdf.tobytes(), len(CLAUSES)))
        pdf.close()
    return docs
