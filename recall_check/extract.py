"""Code extraction, element E3, the only part of the system that uses AI (FR-2, FR-3, NFR-2, NFR-5).

The AI only reads codes out of a notice. Every value it returns must quote the
exact text it came from; a value whose source text cannot be found in the
notice is thrown out. Matching against purchase records happens elsewhere,
with fixed rules.

Two extractors share one interface:
  ClaudeExtractor  calls Claude through the Claude Code command line, using
                   the developer's own Claude login. No API key.
  ReplayExtractor  replays responses Claude gave earlier for the sample
                   notices, saved in data/extractions. This lets anyone run
                   the demo without a Claude account.
If neither can produce a result, the notice stays registered and is routed to
the manager with its original text (NFR-12).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

from . import audit

PROMPT_VERSION = "p1"
VALUE_TYPES = {"upc", "lot", "date", "date_range", "pack_size", "brand", "product_name", "other_code"}
CODE_TYPES = {"upc", "lot", "date", "date_range", "other_code"}

# NFR-5: a notice is "largely uninterpreted" when more than this share of the
# letters and digits in its code text are not covered by any extracted value.
LARGELY_UNINTERPRETED = 0.50

PROMPT = """You read FDA food recall notices for a store. From the notice below, list every
product identifier: UPC or GTIN codes, lot or batch codes, dates, date ranges, pack sizes,
brand and product name, and any other product code.

Rules:
- Copy source_text EXACTLY as it appears in the notice, character for character.
- Write single dates as YYYY-MM-DD. Write a date range as YYYY-MM-DD/YYYY-MM-DD. Never shorten a range to one date.
- If part of the code text cannot be read as an identifier, put that exact text in "uninterpreted".
- Do not guess values that are not in the text.

Return only a JSON object, with no other text, in this form:
{{"items": [{{"type": "upc|lot|date|date_range|pack_size|brand|product_name|other_code", "value": "...", "source_text": "..."}}],
 "uninterpreted": ["..."]}}

NOTICE:
{notice}
"""


class ExtractionUnavailable(RuntimeError):
    """No extractor could produce a result for this notice."""


class ClaudeExtractor:
    """Calls Claude through the Claude Code command line (`claude -p`)."""

    def __init__(self, timeout=180):
        self.timeout = timeout
        self.version = f"claude-code-cli/{PROMPT_VERSION}"

    def run(self, notice_id, original_text):
        exe = shutil.which("claude")
        if not exe:
            raise ExtractionUnavailable("Claude Code is not installed or not on PATH")
        prompt = PROMPT.format(notice=original_text)
        # Run from an empty folder so Claude Code loads no project instructions
        # and the extraction is shaped only by this prompt.
        with tempfile.TemporaryDirectory() as neutral_dir:
            try:
                result = subprocess.run(
                    [exe, "-p", prompt], cwd=neutral_dir, capture_output=True,
                    text=True, encoding="utf-8", timeout=self.timeout,
                )
            except subprocess.TimeoutExpired:
                raise ExtractionUnavailable(f"Claude did not answer within {self.timeout} seconds")
        if result.returncode != 0 or not result.stdout.strip():
            raise ExtractionUnavailable("Claude returned an error: " + (result.stderr or "").strip()[:200])
        return result.stdout


class ReplayExtractor:
    """Replays a response Claude gave earlier, saved by `extract --record`."""

    def __init__(self, folder):
        self.folder = folder
        self.version = f"replay/{PROMPT_VERSION}"

    def run(self, notice_id, original_text):
        path = os.path.join(self.folder, notice_id + ".json")
        if not os.path.exists(path):
            raise ExtractionUnavailable(f"no saved Claude response for {notice_id}")
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.version = "replay of " + saved.get("extractor_version", "unknown")
        return saved["raw_response"]


def parse_response(raw):
    """Read Claude's answer as JSON, removing a ```json code fence if it added one."""
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in response")
    data = json.loads(text[start:end + 1])
    if not isinstance(data.get("items", []), list):
        raise ValueError("items is not a list")
    return data


def _find(text, source_text, offset=0):
    index = text.find(source_text)
    if index != -1:
        return offset + index, offset + index + len(source_text)
    pattern = r"\s+".join(re.escape(part) for part in source_text.split())
    match = re.search(pattern, text)
    return (offset + match.start(), offset + match.end()) if match else None


def locate(original, source_text, region=None):
    """Find where source_text appears in the notice. Spacing may differ; nothing else may.

    If a region is given, an occurrence inside it is preferred, so a lot code
    that appears in both the product line and the code line points at the
    code line.
    """
    if not source_text or not source_text.strip():
        return None
    if region:
        start, end = region
        found = _find(original[start:end], source_text, start)
        if found:
            return found
    return _find(original, source_text)


def code_section(original):
    """Return the start and end of the code text that the uninterpreted share is measured on."""
    marker = original.find("CODES: ")
    if marker == -1:
        return 0, len(original)
    start = marker + len("CODES: ")
    end = original.find("\n", start)
    return start, (len(original) if end == -1 else end)


# Words that label a code rather than being one, such as "Lot No." or "Best By".
# They are left out when measuring how much of the code text was read.
LABEL_WORDS = {
    "lot", "lots", "no", "number", "upc", "code", "codes", "case", "exp", "expiration",
    "date", "dates", "best", "by", "before", "use", "sell", "from", "to", "thru", "through",
    "up", "and", "including", "product", "description", "all", "recalled", "item", "size",
}


def uninterpreted_share(original, kept):
    """Share of letters and digits in the code text not covered by any extracted value.

    A value covers every place its source text appears in the code text, not
    only the first, so a product name repeated on every line counts as read.
    Label words do not count either way.
    """
    start, end = code_section(original)
    section = original[start:end]
    covered = set()
    for value in kept:
        if start <= value["span_start"] < end:
            covered.update(range(value["span_start"] - start, value["span_end"] - start))
        pattern = r"\s+".join(re.escape(part) for part in value["source_text"].split())
        if pattern:
            for match in re.finditer(pattern, section, re.IGNORECASE):
                covered.update(range(match.start(), match.end()))
    label = set()
    for match in re.finditer(r"[A-Za-z]+", section):
        if match.group().lower() in LABEL_WORDS:
            label.update(range(match.start(), match.end()))
    positions = [i for i, ch in enumerate(section) if ch.isalnum() and i not in label]
    if not positions:
        return 0.0
    read = sum(1 for i in positions if i in covered)
    return round(1 - read / len(positions), 3)


def validate(original, data):
    """Keep only values of a known type whose source text is found in the notice."""
    kept, dropped = [], []
    for item in data.get("items", []):
        kind = str(item.get("type", "")).strip().lower()
        value = str(item.get("value", "")).strip()
        region = code_section(original) if kind in CODE_TYPES else None
        span = locate(original, str(item.get("source_text", "")), region)
        if kind not in VALUE_TYPES or not value or span is None:
            dropped.append(item)
            continue
        kept.append({"type": kind, "value": value,
                     "source_text": original[span[0]:span[1]],
                     "span_start": span[0], "span_end": span[1]})
    unread = []
    for text in data.get("uninterpreted", []) or []:
        span = locate(original, str(text), code_section(original))
        unread.append({"source_text": str(text),
                       "span_start": span[0] if span else None,
                       "span_end": span[1] if span else None})
    return kept, dropped, unread


def extract_notice(conn, notice_id, extractor, record_folder=None):
    """Run extraction on one registered notice and store the result.

    Returns a summary dict. Never raises for an extraction problem: a notice
    that cannot be read is routed to the manager instead (NFR-12).
    """
    row = conn.execute("SELECT * FROM notices WHERE notice_id = ?", (notice_id,)).fetchone()
    if row is None:
        raise KeyError(f"notice {notice_id} is not registered")
    if conn.execute("SELECT 1 FROM extracted_values WHERE notice_id = ?", (notice_id,)).fetchone():
        return {"notice_id": notice_id, "status": row["case_status"], "skipped": True}
    original = row["original_text"]

    try:
        raw = extractor.run(notice_id, original)
        data = parse_response(raw)
    except (ExtractionUnavailable, ValueError) as problem:
        return _route_to_manager(conn, notice_id, f"extraction unavailable: {problem}")

    kept, dropped, unread = validate(original, data)
    certainty = "lower" if not any(v["type"] in ("upc", "other_code") for v in kept) else "standard"
    for value in kept:
        conn.execute(
            """INSERT INTO extracted_values (notice_id, type, value, source_text,
                   span_start, span_end, certainty, extractor_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (notice_id, value["type"], value["value"], value["source_text"],
             value["span_start"], value["span_end"], certainty, extractor.version),
        )
    for text in unread:
        conn.execute(
            "INSERT INTO uninterpreted_text (notice_id, source_text, span_start, span_end) VALUES (?, ?, ?, ?)",
            (notice_id, text["source_text"], text["span_start"], text["span_end"]),
        )
    share = uninterpreted_share(original, kept)
    actor = "E3 code extraction, " + extractor.version
    audit.log(conn, actor, "extract", notice_id,
              f"{len(kept)} values kept, {len(dropped)} dropped for no source text, "
              f"{share:.0%} of code text uninterpreted")

    if record_folder and isinstance(extractor, ClaudeExtractor):
        os.makedirs(record_folder, exist_ok=True)
        with open(os.path.join(record_folder, notice_id + ".json"), "w", encoding="utf-8") as handle:
            json.dump({"notice_id": notice_id, "extractor_version": extractor.version,
                       "recorded_at": audit.now(), "raw_response": raw}, handle, indent=2, ensure_ascii=False)

    if share > LARGELY_UNINTERPRETED:
        result = _route_to_manager(conn, notice_id,
                                   f"largely uninterpreted: {share:.0%} of code text unread, "
                                   f"threshold {LARGELY_UNINTERPRETED:.0%}", commit=False)
    else:
        _set_status(conn, notice_id, "Extracted")
        result = {"notice_id": notice_id, "status": "Extracted"}
    conn.commit()
    result.update({"kept": len(kept), "dropped": len(dropped), "uninterpreted_share": share})
    return result


def _set_status(conn, notice_id, status):
    conn.execute("UPDATE notices SET case_status = ? WHERE notice_id = ?", (status, notice_id))


def _route_to_manager(conn, notice_id, reason, commit=True):
    _set_status(conn, notice_id, "Routed to manager")
    audit.log(conn, "E7 coordinator", "route_to_manager", notice_id, reason)
    if commit:
        conn.commit()
    return {"notice_id": notice_id, "status": "Routed to manager", "reason": reason}


def values_for(conn, notice_id):
    rows = conn.execute(
        "SELECT * FROM extracted_values WHERE notice_id = ? ORDER BY span_start", (notice_id,))
    return [dict(r) for r in rows]
