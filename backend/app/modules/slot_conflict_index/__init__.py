
STATUS = "partial"


def lookup_by_cell(conn, cell_id: str) -> dict:
    rows = conn.execute(
        "SELECT id, reason, member_json, created_at FROM quarantine ORDER BY id DESC LIMIT 20"
    ).fetchall()
    return {"cell_id": cell_id, "hits": [], "indexed": False, "raw_hold_n": len(rows)}
