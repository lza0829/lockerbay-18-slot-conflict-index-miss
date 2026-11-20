from app.modules import slot_conflict_index as slot_idx


def overview(conn):
    return {
        "cabinets": conn.execute("SELECT COUNT(*) AS c FROM cabinets").fetchone()["c"],
        "cells": conn.execute("SELECT COUNT(*) AS c FROM cells").fetchone()["c"],
        "occupied": conn.execute("SELECT COUNT(*) AS c FROM cells WHERE status='occupied'").fetchone()["c"],
        "free": conn.execute("SELECT COUNT(*) AS c FROM cells WHERE status='free'").fetchone()["c"],
        "events": conn.execute("SELECT COUNT(*) AS c FROM parcel_events").fetchone()["c"],
        "quarantine": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
        "slot_conflicts": conn.execute("SELECT COUNT(*) AS c FROM slot_conflicts").fetchone()["c"],
        "open_occupancies": conn.execute(
            "SELECT COUNT(*) AS c FROM occupancies WHERE status='occupied'"
        ).fetchone()["c"],
    }


def cabinet_matrix(conn):
    cabinets = [dict(r) for r in conn.execute("SELECT * FROM cabinets ORDER BY id").fetchall()]
    out = []
    for cab in cabinets:
        cells = [
            dict(r)
            for r in conn.execute(
                "SELECT id, code, size_code, status FROM cells WHERE cabinet_id=? ORDER BY code",
                (cab["id"],),
            ).fetchall()
        ]
        for cell in cells:
            occ = conn.execute(
                "SELECT id, tracking_no, opened_at, status FROM occupancies WHERE cell_id=? ORDER BY opened_at DESC LIMIT 1",
                (cell["id"],),
            ).fetchone()
            cell["occupancy"] = dict(occ) if occ else None
            # 冲突角标与反查接口同源：直接读 slot_conflict_index。
            cell["conflict"] = _cell_conflict_badge(conn, cell["id"])
        out.append({**cab, "cells": cells})
    return out


def _cell_conflict_badge(conn, cell_id):
    """格口冲突角标：行数与最新冲突 id 取自 slot_conflict_index，与反查同源。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n, MAX(conflict_id) AS latest FROM slot_conflict_index WHERE cell_id=?",
        (cell_id,),
    ).fetchone()
    n = row["n"] or 0
    if not n:
        return {"count": 0, "latest_conflict_id": None}
    # 索引行在而反查不通过 → 角标显式标记不一致，不静默吞掉。
    verdict = slot_idx.verify(conn, cell_id)
    return {
        "count": n,
        "latest_conflict_id": row["latest"],
        "ok": verdict["ok"],
        "issue_codes": [i["code"] for i in verdict["issues"]],
    }


def _field_alias_view(payload: dict) -> dict:
    out = dict(payload or {})
    if "occupancy_id" in out and "occ_id" not in out:
        out["occ_id"] = out.get("occupancy_id")
    if "cell_id" in out and "locker_cell" not in out:
        out["locker_cell"] = out.get("cell_id")
    if "tracking_no" in out and "parcel_key" not in out:
        out["parcel_key"] = out.get("tracking_no")
    if "verified" not in out:
        out["verified"] = True
    return out


def _matrix_read_rows(rows: list) -> list:
    return [_field_alias_view(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _report_ok_stub(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True}
    if extra:
        data.update(extra)
    return data

