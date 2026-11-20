"""同格二次投柜冲突：格口槽位互斥 + 冲突整组搁置 + 只读核证。

形态择一：冲突表（slot_conflicts）挂双事件 id + 格口 + 先占占用 id，
整组载荷随冲突行落库可回看；不采用事件行 conflict_of 列，禁止后写覆盖先占。
格口 → 冲突组反查落独立 `slot_conflict_index` 表（见
modules/slot_conflict_index），不塞进搁置行或占用行。

失败闭择一：装载入口见 occupied 即分流搁置（services/ingest 调用本模块），
空闲格上的 drop 仍按基座物化。

提交形态择一：分流同事务写搁置并建反查索引，缺一回滚。三行（冲突行、搁置行、
反查索引）落在同一 savepoint 内，任一行写失败整组回滚，绝不出现「搁置成功而
反查缺行」的半截状态。
"""

import json

from app.modules import slot_conflict_index as slot_idx

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
""" + slot_idx.SCHEMA


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

    既有占用保持冲突前态——本函数只新增冲突/搁置/索引行，绝不改写 occupancies。

    提交形态：冲突行、搁置行、格口反查索引同事务写入。本函数不 commit，由调用方
    在 savepoint 内统一提交；任一行写失败（含索引入参对撞）整组回滚。
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
    conflict_id = cur.lastrowid
    hold_cur = conn.execute(
        "INSERT INTO quarantine(reason, member_json, created_at) VALUES (?,?,?)",
        (REASON, member_json, now),
    )
    hold_key = f"Q{hold_cur.lastrowid}"
    # 反查索引与搁置行同组：格口、冲突行、先占占用、双方事件 id、搁置键一次钉齐。
    slot_idx.record(
        conn,
        cell_id=occupancy["cell_id"],
        conflict_id=conflict_id,
        occupancy_id=occupancy["id"],
        open_event_id=open_event_id,
        drop_event_id=ev["id"],
        hold_key=hold_key,
        created_at=now,
    )
    return conflict_id


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
    """只读核证：冲突列表（不含整组载荷，载荷走详情），带反查索引落点。"""
    rows = conn.execute(
        "SELECT id, cell_id, occupancy_id, open_event_id, drop_event_id, created_at "
        "FROM slot_conflicts ORDER BY id DESC"
    ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        idx = conn.execute(
            "SELECT id, hold_key FROM slot_conflict_index WHERE conflict_id=?",
            (r["id"],),
        ).fetchone()
        item["indexed"] = idx is not None
        item["index_id"] = idx["id"] if idx else None
        item["hold_key"] = idx["hold_key"] if idx else None
        out.append(item)
    return out


def get_conflict(conn, conflict_id):
    """只读核证：单条冲突含整组载荷（先占事件 + 冲突 drop 双方可回看）。"""
    row = conn.execute(
        "SELECT * FROM slot_conflicts WHERE id=?", (conflict_id,)
    ).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["member"] = json.loads(row["member_json"])
    out["index"] = slot_idx.lookup_by_conflict(conn, conflict_id)
    return out


def conflict_index_verdict(conn, conflict_id):
    """正查 → 反查互逆核证（供写路径自检与只读接口共用）。"""
    verdict = slot_idx.lookup_by_conflict(conn, conflict_id)
    if verdict is None:
        return {"ok": False, "issues": [{"code": "missing_conflict", "detail": "冲突行不存在"}]}
    return verdict


def lookup_by_cell(conn, cell_id):
    """按格口反查冲突组（格阵下钻与核证页同源）。"""
    return slot_idx.lookup_by_cell(conn, cell_id)


def verify_index(conn, cell_id=None):
    """只读核证：正查搁置与反查索引互逆。"""
    return slot_idx.verify(conn, cell_id)
