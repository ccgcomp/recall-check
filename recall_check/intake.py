"""Notice intake, element E1, and the notice store, element E2 (FR-1).

A notice is registered with its source, channel, receipt time, recalling firm,
classification, status, initiation date and complete original text before
anything else reads it. A notice with no text is refused.
"""

import json
import os
import urllib.parse
import urllib.request

from . import audit

OPENFDA_URL = "https://api.fda.gov/food/enforcement.json"
NOT_STATED = "not stated"
ACTOR = "E1 notice intake"


class IntakeError(ValueError):
    """Raised when a notice cannot be registered."""


def build_original_text(product_description, code_info, reason=""):
    """Combine the notice fields into the one text that every source span points into."""
    parts = ["PRODUCT: " + (product_description or "").strip(),
             "CODES: " + (code_info or "").strip()]
    if reason:
        parts.append("REASON: " + reason.strip())
    return "\n".join(parts)


def register_openfda(conn, record):
    """Register one record from the openFDA food enforcement endpoint."""
    recall_number = record.get("recall_number")
    if not recall_number:
        raise IntakeError("openFDA record has no recall_number")
    return _register(
        conn,
        notice_id=recall_number,
        source="openFDA food enforcement",
        channel="HTTPS API",
        recall_number=recall_number,
        recalling_firm=record.get("recalling_firm") or NOT_STATED,
        classification=record.get("classification") or NOT_STATED,
        status=record.get("status") or NOT_STATED,
        initiation_date=record.get("recall_initiation_date") or NOT_STATED,
        product_description=record.get("product_description", ""),
        code_info=record.get("code_info", ""),
        reason=record.get("reason_for_recall", ""),
    )


def register_upload(conn, notice_id, text, uploaded_by, recalling_firm=NOT_STATED,
                    classification=NOT_STATED, status=NOT_STATED,
                    initiation_date=NOT_STATED, expands_notice_id=None):
    """Register a notice the manager uploads, for example one a distributor emailed."""
    if not uploaded_by:
        raise IntakeError("an uploaded notice must name the person who uploaded it")
    return _register(
        conn,
        notice_id=notice_id,
        source="uploaded by " + uploaded_by,
        channel="manual upload",
        recall_number=None,
        recalling_firm=recalling_firm,
        classification=classification,
        status=status,
        initiation_date=initiation_date,
        product_description="",
        code_info=text,
        reason="",
        expands_notice_id=expands_notice_id,
        raw_text=text,
    )


def _register(conn, notice_id, source, channel, recall_number, recalling_firm,
              classification, status, initiation_date, product_description,
              code_info, reason, expands_notice_id=None, raw_text=None):
    if not (code_info or "").strip() and not (product_description or "").strip():
        raise IntakeError(f"notice {notice_id} has no text and was not registered")
    if conn.execute("SELECT 1 FROM notices WHERE notice_id = ?", (notice_id,)).fetchone():
        return notice_id  # already registered; registering twice changes nothing
    original = raw_text if raw_text is not None else build_original_text(
        product_description, code_info, reason)
    conn.execute(
        """INSERT INTO notices (notice_id, source, channel, received_at, recall_number,
               recalling_firm, classification, status, initiation_date,
               product_description, code_info, original_text, expands_notice_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (notice_id, source, channel, audit.now(), recall_number, recalling_firm,
         classification, status, initiation_date, product_description, code_info,
         original, expands_notice_id),
    )
    audit.log(conn, ACTOR, "register_notice", notice_id, f"{channel} from {source}")
    conn.commit()
    return notice_id


def fetch_openfda(search, limit=10, timeout=30):
    """Fetch records from the public openFDA food enforcement endpoint. No key is needed."""
    query = urllib.parse.urlencode({"search": search, "limit": limit})
    with urllib.request.urlopen(f"{OPENFDA_URL}?{query}", timeout=timeout) as response:
        payload = json.load(response)
    return payload.get("results", [])


def save_records(records, folder):
    """Save each record as its own JSON file, named by recall number."""
    os.makedirs(folder, exist_ok=True)
    saved = []
    for record in records:
        number = record.get("recall_number")
        if not number:
            continue
        path = os.path.join(folder, number + ".json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, ensure_ascii=False)
        saved.append(path)
    return saved


def load_records(folder):
    records = []
    for name in sorted(os.listdir(folder)):
        if name.endswith(".json"):
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                records.append(json.load(handle))
    return records
