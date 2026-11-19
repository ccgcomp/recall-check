"""Append-only audit log, element E11 (FR-12, NFR-9)."""

from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(conn, actor, action, object_id, basis):
    """Append one entry. Every action that changes state calls this first.

    actor is a named person or an element with its version.
    basis says why the action happened, so a recall can be reconstructed
    from the log alone.
    """
    conn.execute(
        "INSERT INTO audit_log (ts, actor, action, object_id, basis) VALUES (?, ?, ?, ?, ?)",
        (now(), actor, action, object_id, basis),
    )


def entries(conn, object_id=None):
    if object_id:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE object_id = ? ORDER BY entry_id", (object_id,)
        )
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY entry_id")
    return [dict(r) for r in rows]
