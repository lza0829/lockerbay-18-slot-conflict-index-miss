"""同格二次投柜冲突：格口槽位互斥 + 冲突整组搁置 + 只读核证。

形态择一：冲突表（slot_conflicts）挂双事件 id + 格口 + 先占占用 id，
整组载荷随冲突行落库可回看；不采用事件行 conflict_of 列，禁止后写覆盖先占。

失败闭择一：装载入口见 occupied 即分流搁置（services/ingest 调用本模块），
空闲格上的 drop 仍按基座物化。
"""

import json

STATUS = "implemented"
REASON = "dual_drop_conflict"

SCHEMA = """
CREATE TABLE IF NOT EXISTS slot_conflicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cell_id TEXT NOT NULL,
  occupancy_id TEXT NOT NULL,
  open_event_id TEXT NOT NULL,
  drop_event_id TEXT NOT NULL,
  member_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


def find_open_occupancy(conn, cell_id):
    """目标格现行 status=occupied 的占用（槽位互斥的裁决依据）。"""
    return conn.execute(
        "SELECT * FROM occupancies WHERE cell_id=? AND status='occupied' "
        "ORDER BY opened_at DESC LIMIT 1",
        (cell_id,),
    ).fetchone()


def event_known(conn, event_id):
    """事件已落主账或已进冲突搁置则为真（重跑幂等，不重复搁置）。"""
    if conn.execute("SELECT 1 FROM parcel_events WHERE id=?", (event_id,)).fetchone():
        return True
    return conn.execute(
        "SELECT 1 FROM slot_conflicts WHERE drop_event_id=?", (event_id,)
    ).fetchone() is not None


def shelve_conflict(conn, ev, occupancy, now):
    """冲突双方事件整组进搁置：冲突行挂双事件 id+格口+先占占用 id，载荷可回看。

    既有占用保持冲突前态——本函数只新增冲突/搁置行，绝不改写 occupancies。
    """
    open_event_id = occupancy["last_event_id"]
    open_event = conn.execute(
        "SELECT * FROM parcel_events WHERE id=?", (open_event_id,)
    ).fetchone()
    group = {
        "cell_id": occupancy["cell_id"],
        "occupancy_id": occupancy["id"],
        "open_event_id": open_event_id,
        "drop_event_id": ev["id"],
        "events": [
            _row_event_view(open_event, open_event_id, "open"),
            _incoming_event_view(ev),
        ],
    }
    member_json = json.dumps(group, ensure_ascii=False)
    cur = conn.execute(
        "INSERT INTO slot_conflicts(cell_id, occupancy_id, open_event_id, drop_event_id, member_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (occupancy["cell_id"], occupancy["id"], open_event_id, ev["id"], member_json, now),
    )
    conn.execute(
        "INSERT INTO quarantine(reason, member_json, created_at) VALUES (?,?,?)",
        (REASON, member_json, now),
    )
    return cur.lastrowid


def _row_event_view(row, fallback_id, role):
    if row is None:
        return {"role": role, "id": fallback_id, "missing": True}
    return {
        "role": role,
        "id": row["id"],
        "event_type": row["event_type"],
        "event_ts": row["event_ts"],
        "tracking_no": row["tracking_no"],
        "payload": json.loads(row["payload_json"]),
    }


def _incoming_event_view(ev):
    return {
        "role": "drop",
        "id": ev["id"],
        "event_type": ev["event_type"],
        "event_ts": ev["event_ts"],
        "tracking_no": ev.get("tracking_no"),
        "payload": ev.get("payload", {}),
    }


def list_conflicts(conn):
    """只读核证：冲突列表（不含整组载荷，载荷走详情）。"""
    rows = conn.execute(
        "SELECT id, cell_id, occupancy_id, open_event_id, drop_event_id, created_at "
        "FROM slot_conflicts ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_conflict(conn, conflict_id):
    """只读核证：单条冲突含整组载荷（先占事件 + 冲突 drop 双方可回看）。"""
    row = conn.execute(
        "SELECT * FROM slot_conflicts WHERE id=?", (conflict_id,)
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["member"] = json.loads(row["member_json"])
    return out
