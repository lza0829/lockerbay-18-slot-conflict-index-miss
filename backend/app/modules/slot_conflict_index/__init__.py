"""格口 → 冲突组反查索引（独立表形态）。

落库形态择一：本模块采用独立 `slot_conflict_index` 表，不把反查列塞进搁置行、
事件行或占用行。提交形态择一：索引与 `slot_conflicts` / `quarantine` 在同一
savepoint 内写入（见 `dual_drop_conflict.shelve_conflict`），分流成功则三行同
成败落库，任一行缺失整组回滚。

每行钉住：格口 id、冲突行 id、先占占用 id、先到（先占）事件 id、后到 drop
事件 id、搁置键。正查（冲突 → 索引）与反查（格口 → 冲突组）必须互逆；反查
核证同时校验搁置组，任何缺行、缺后到事件 id、串格都会判失败。
"""

import json

STATUS = "implemented"

# 与 dual_drop_conflict.REASON 同值；此处复写常量以避免循环导入。
REASON = "dual_drop_conflict"

SCHEMA = """
CREATE TABLE IF NOT EXISTS slot_conflict_index (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cell_id TEXT NOT NULL,
  conflict_id INTEGER NOT NULL UNIQUE,
  occupancy_id TEXT NOT NULL,
  open_event_id TEXT NOT NULL,
  drop_event_id TEXT NOT NULL CHECK (length(trim(drop_event_id)) > 0),
  hold_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  UNIQUE(cell_id, drop_event_id)
);
CREATE INDEX IF NOT EXISTS idx_slot_conflict_index_cell
  ON slot_conflict_index(cell_id);
"""


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _quarantine_by_key(conn, hold_key):
    if _blank(hold_key) or not str(hold_key).startswith("Q"):
        return None
    try:
        hold_id = int(str(hold_key)[1:])
    except (TypeError, ValueError):
        return None
    return conn.execute("SELECT * FROM quarantine WHERE id=?", (hold_id,)).fetchone()


def _member(hold):
    if hold is None:
        return {}
    try:
        return json.loads(hold["member_json"]) or {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def record(
    conn,
    *,
    cell_id,
    conflict_id,
    occupancy_id,
    open_event_id,
    drop_event_id,
    hold_key,
    created_at,
) -> int:
    """写入一行反查索引。

    入参先与冲突行、先占占用、搁置组逐项对撞：格口不一致、后到事件 id 缺失、
    搁置键对不上都直接拒写，保证索引不会挂到邻格或空着后到事件 id。
    写入异常（含唯一约束冲突）由调用方 savepoint 整组回滚。
    """
    for name, value in (
        ("cell_id", cell_id),
        ("conflict_id", conflict_id),
        ("occupancy_id", occupancy_id),
        ("open_event_id", open_event_id),
        ("drop_event_id", drop_event_id),
        ("hold_key", hold_key),
    ):
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError(f"反查索引缺 {name}")

    conflict = conn.execute(
        "SELECT * FROM slot_conflicts WHERE id=?", (conflict_id,)
    ).fetchone()
    if conflict is None:
        raise ValueError("反查索引无对应冲突行")
    if conflict["cell_id"] != cell_id:
        raise ValueError("反查索引格口与冲突行不一致")
    if conflict["occupancy_id"] != occupancy_id:
        raise ValueError("反查索引先占占用与冲突行不一致")
    if conflict["open_event_id"] != open_event_id:
        raise ValueError("反查索引先到事件 id 与冲突行不一致")
    if conflict["drop_event_id"] != drop_event_id:
        raise ValueError("反查索引后到事件 id 与冲突行不一致")

    occupancy = conn.execute(
        "SELECT id, cell_id FROM occupancies WHERE id=?", (occupancy_id,)
    ).fetchone()
    if occupancy is None:
        raise ValueError("反查索引无对应先占占用")
    if occupancy["cell_id"] != cell_id:
        raise ValueError("反查索引格口与先占占用不一致")

    hold = _quarantine_by_key(conn, hold_key)
    if hold is None:
        raise ValueError("反查索引无对应搁置行")
    member = _member(hold)
    if member.get("cell_id") != cell_id or member.get("drop_event_id") != drop_event_id:
        raise ValueError("反查索引与搁置组不一致")

    cur = conn.execute(
        "INSERT INTO slot_conflict_index("
        "cell_id, conflict_id, occupancy_id, open_event_id, drop_event_id, hold_key, created_at"
        ") VALUES (?,?,?,?,?,?,?)",
        (
            cell_id,
            conflict_id,
            occupancy_id,
            open_event_id,
            drop_event_id,
            hold_key,
            created_at,
        ),
    )
    return cur.lastrowid


def _add_issue(issues, seen, code, detail, **fields):
    key = (code, fields.get("cell_id"), fields.get("conflict_id"), fields.get("index_id"))
    if key in seen:
        return
    seen.add(key)
    issues.append({"code": code, "detail": detail, **fields})


def _check_row(conn, row, issues, seen):
    """校验一行索引：与冲突行、搁置组双向对齐。"""
    rid = row["id"]
    cell_id = row["cell_id"]
    conflict_id = row["conflict_id"]
    if _blank(row["drop_event_id"]):
        _add_issue(
            issues, seen, "missing_drop_event_id",
            "反查索引缺后到 drop 事件 id", cell_id=cell_id, conflict_id=conflict_id, index_id=rid,
        )
    conflict = conn.execute(
        "SELECT * FROM slot_conflicts WHERE id=?", (conflict_id,)
    ).fetchone()
    if conflict is None:
        _add_issue(
            issues, seen, "index_without_conflict",
            "反查索引指向不存在的冲突行", cell_id=cell_id, conflict_id=conflict_id, index_id=rid,
        )
    else:
        if conflict["cell_id"] != cell_id:
            _add_issue(
                issues, seen, "index_misrouted",
                "反查索引格口与冲突行不一致（串格）", cell_id=cell_id,
                conflict_id=conflict_id, index_id=rid,
                conflict_cell_id=conflict["cell_id"],
            )
        if conflict["occupancy_id"] != row["occupancy_id"]:
            _add_issue(
                issues, seen, "index_field_mismatch",
                "反查索引先占占用与冲突行不一致", cell_id=cell_id,
                conflict_id=conflict_id, index_id=rid,
            )
        if conflict["open_event_id"] != row["open_event_id"]:
            _add_issue(
                issues, seen, "index_field_mismatch",
                "反查索引先到事件 id 与冲突行不一致", cell_id=cell_id,
                conflict_id=conflict_id, index_id=rid,
            )
        if conflict["drop_event_id"] != row["drop_event_id"]:
            _add_issue(
                issues, seen, "index_field_mismatch",
                "反查索引后到事件 id 与冲突行不一致", cell_id=cell_id,
                conflict_id=conflict_id, index_id=rid,
            )
    hold = _quarantine_by_key(conn, row["hold_key"])
    if hold is None:
        _add_issue(
            issues, seen, "missing_hold",
            "反查索引搁置键无对应搁置行", cell_id=cell_id,
            conflict_id=conflict_id, index_id=rid,
        )
    else:
        member = _member(hold)
        if member.get("cell_id") != cell_id or member.get("drop_event_id") != row["drop_event_id"]:
            _add_issue(
                issues, seen, "index_hold_mismatch",
                "反查索引与搁置组不一致", cell_id=cell_id,
                conflict_id=conflict_id, index_id=rid,
            )


def verify(conn, cell_id=None) -> dict:
    """只读核证：正查搁置与反查索引互逆、无缺行、无缺后到事件 id、无串格。

    cell_id 为空时核全库；否则只核该格口（含挂到该格口的错格索引）。
    """
    if cell_id is None:
        conflicts = conn.execute("SELECT * FROM slot_conflicts ORDER BY id").fetchall()
        index_rows = conn.execute("SELECT * FROM slot_conflict_index ORDER BY id").fetchall()
        holds = conn.execute(
            "SELECT * FROM quarantine WHERE reason=? ORDER BY id", (REASON,)
        ).fetchall()
    else:
        conflicts = conn.execute(
            "SELECT * FROM slot_conflicts WHERE cell_id=? ORDER BY id", (cell_id,)
        ).fetchall()
        index_rows = conn.execute(
            "SELECT * FROM slot_conflict_index WHERE cell_id=? ORDER BY id", (cell_id,)
        ).fetchall()
        holds = [
            h
            for h in conn.execute(
                "SELECT * FROM quarantine WHERE reason=? ORDER BY id", (REASON,)
            ).fetchall()
            if _member(h).get("cell_id") == cell_id
        ]

    issues = []
    seen = set()

    # 正查 → 反查：每条冲突都必须恰有一行同格口、同双方事件、同搁置键的索引。
    for conflict in conflicts:
        rows = conn.execute(
            "SELECT * FROM slot_conflict_index WHERE conflict_id=? ORDER BY id",
            (conflict["id"],),
        ).fetchall()
        if not rows:
            _add_issue(
                issues, seen, "missing_index",
                "冲突已搁置而格口反查缺行", cell_id=conflict["cell_id"],
                conflict_id=conflict["id"], drop_event_id=conflict["drop_event_id"],
            )
            continue
        if len(rows) > 1:
            _add_issue(
                issues, seen, "duplicate_index",
                "同一冲突行挂多行反查索引", cell_id=conflict["cell_id"],
                conflict_id=conflict["id"],
            )
        for row in rows:
            _check_row(conn, row, issues, seen)

    # 反查 → 正查：每行索引都必须回指存在的冲突行（按冲突 id 全量取，抓错格）。
    for row in index_rows:
        _check_row(conn, row, issues, seen)

    # 搁置组 → 反查：搁置键必须能被同格口、同后到事件 id 的索引接住。
    for hold in holds:
        member = _member(hold)
        hold_key = f"Q{hold['id']}"
        rows = conn.execute(
            "SELECT * FROM slot_conflict_index WHERE hold_key=?", (hold_key,)
        ).fetchall()
        wanted_cell = member.get("cell_id")
        wanted_drop = member.get("drop_event_id")
        if not rows:
            _add_issue(
                issues, seen, "missing_index",
                "搁置成功而反查索引缺行", cell_id=wanted_cell,
                hold_key=hold_key, drop_event_id=wanted_drop,
            )
            continue
        for row in rows:
            if row["cell_id"] != wanted_cell or row["drop_event_id"] != wanted_drop:
                _add_issue(
                    issues, seen, "index_hold_mismatch",
                    "搁置组与反查索引格口/后到事件不一致", cell_id=row["cell_id"],
                    conflict_id=row["conflict_id"], index_id=row["id"],
                )

    return {
        "ok": not issues,
        "cell_id": cell_id,
        "scope": "cell" if cell_id is not None else "all",
        "checked": {
            "conflicts": len(conflicts),
            "index_rows": len(index_rows),
            "holds": len(holds),
        },
        "issues": issues,
    }


def _hit(conn, row):
    conflict = conn.execute(
        "SELECT * FROM slot_conflicts WHERE id=?", (row["conflict_id"],)
    ).fetchone()
    hold = _quarantine_by_key(conn, row["hold_key"])
    member = None
    if conflict is not None:
        try:
            member = json.loads(conflict["member_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            member = None
    out = dict(row)
    out["hold_id"] = hold["id"] if hold is not None else None
    out["member"] = member
    return out


def backfill(conn) -> int:
    """迁移补偿：为既有冲突行补建反查索引（老库升级后不留「搁置成功而反查缺行」）。

    搁置键取同组搁置行（reason=dual_drop_conflict 且 member_json 后到事件 id 相同）。
    补建失败的行不吞掉：由核证接口报 missing_index。
    """
    conflicts = conn.execute(
        "SELECT c.* FROM slot_conflicts c "
        "LEFT JOIN slot_conflict_index i ON i.conflict_id = c.id "
        "WHERE i.id IS NULL ORDER BY c.id"
    ).fetchall()
    if not conflicts:
        return 0
    holds = conn.execute(
        "SELECT * FROM quarantine WHERE reason=? ORDER BY id", (REASON,)
    ).fetchall()
    made = 0
    for conflict in conflicts:
        hold_key = None
        for hold in holds:
            member = _member(hold)
            if (
                member.get("cell_id") == conflict["cell_id"]
                and member.get("drop_event_id") == conflict["drop_event_id"]
                and member.get("occupancy_id") == conflict["occupancy_id"]
            ):
                hold_key = f"Q{hold['id']}"
                break
        if hold_key is None:
            # 无同组搁置行 → 该冲突本就缺搁置键，留给核证报 missing_index。
            continue
        try:
            record(
                conn,
                cell_id=conflict["cell_id"],
                conflict_id=conflict["id"],
                occupancy_id=conflict["occupancy_id"],
                open_event_id=conflict["open_event_id"],
                drop_event_id=conflict["drop_event_id"],
                hold_key=hold_key,
                created_at=conflict["created_at"],
            )
            made += 1
        except ValueError:
            # 冲突行自身对不上（如格口/事件 id 缺失），不强行补建；核证会报出。
            continue
    return made


def lookup_by_cell(conn, cell_id: str) -> dict:
    """反查：按格口取冲突组（含整组载荷与搁置键），并带同源核证结论。"""
    rows = conn.execute(
        "SELECT * FROM slot_conflict_index WHERE cell_id=? ORDER BY id DESC",
        (cell_id,),
    ).fetchall()
    verdict = verify(conn, cell_id)
    return {
        "cell_id": cell_id,
        "indexed": bool(rows),
        "hits": [_hit(conn, row) for row in rows],
        "ok": verdict["ok"],
        "issues": verdict["issues"],
        "checked": verdict["checked"],
    }


def lookup_by_conflict(conn, conflict_id: int):
    """正查反打：冲突行 → 反查索引，证明正查与反查互逆。"""
    conflict = conn.execute(
        "SELECT * FROM slot_conflicts WHERE id=?", (conflict_id,)
    ).fetchone()
    if conflict is None:
        return None
    rows = conn.execute(
        "SELECT * FROM slot_conflict_index WHERE conflict_id=?", (conflict_id,)
    ).fetchall()
    issues = []
    seen = set()
    if not rows:
        _add_issue(
            issues, seen, "missing_index",
            "冲突已搁置而格口反查缺行", cell_id=conflict["cell_id"],
            conflict_id=conflict_id, drop_event_id=conflict["drop_event_id"],
        )
    for row in rows:
        _check_row(conn, row, issues, seen)
    return {
        "conflict_id": conflict_id,
        "cell_id": conflict["cell_id"],
        "indexed": bool(rows),
        "ok": not issues,
        "issues": issues,
        "index": dict(rows[0]) if rows else None,
        "index_rows": [dict(r) for r in rows],
    }
