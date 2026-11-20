"""格口冲突索引反查：同格二次 drop 成功搁置时反查必须同成败接通。

夹具：E6 同格二次 drop（CAB-A-02，先占 TN1002）→ 整组搁置 + 反查索引；
E5 邻格正常 drop（CAB-A-03）不受冲突路径影响。

反证：删索引行 / 串格 / 清空后到事件 id / 索引写失败，均须判失败或整组回滚。

运行：python3 backend/tests/test_slot_conflict_index.py
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="lockerbay-idx-"))
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["FIXTURES_DIR"] = str(BACKEND.parent / "fixtures")

from app import seed  # noqa: E402
from app.db import connect  # noqa: E402
from app.modules import dual_drop_conflict as ddc  # noqa: E402
from app.modules import slot_conflict_index as slot_idx  # noqa: E402
from app.services import bay, ingest  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def issue_codes(verdict):
    return {i["code"] for i in verdict["issues"]}


def cell_badges(conn):
    matrix = bay.cabinet_matrix(conn)
    return {c["id"]: c["conflict"] for cab in matrix for c in cab["cells"]}


def main():
    seed.init_db()
    conn = connect()
    before_pre = dict(
        conn.execute(
            "SELECT * FROM occupancies WHERE cell_id='CAB-A-02' AND status='occupied'"
        ).fetchone()
    )
    before_neighbor = conn.execute(
        "SELECT * FROM occupancies WHERE cell_id='CAB-A-03'"
    ).fetchone()
    before_neighbor = dict(before_neighbor) if before_neighbor else None
    conn.close()

    report = ingest.run_batch()
    print("批次报告:", report)

    conn = connect()
    try:
        # 1) 反查接通：按格口取回冲突组，五要素齐备
        idx = slot_idx.lookup_by_cell(conn, "CAB-A-02")
        check("反查 indexed=True 且命中一行", idx["indexed"] and len(idx["hits"]) == 1)
        hit = idx["hits"][0] if idx["hits"] else {}
        check(
            "反查钉住 cell_id / 先占占用 id / 双方事件 id / 搁置键",
            hit.get("cell_id") == "CAB-A-02"
            and hit.get("occupancy_id") == before_pre["id"]
            and hit.get("open_event_id") == "E2"
            and hit.get("drop_event_id") == "E6"
            and hit.get("hold_key", "").startswith("Q"),
            json.dumps(
                {k: hit.get(k) for k in (
                    "cell_id", "occupancy_id", "open_event_id", "drop_event_id", "hold_key"
                )},
                ensure_ascii=False,
            ),
        )
        check("反查自带整组载荷（双方事件可回看）", (hit.get("member") or {}).get("drop_event_id") == "E6")
        check("反查核证通过", idx["ok"] is True and idx["issues"] == [])

        # 2) 正查与反查互逆
        conflict = ddc.list_conflicts(conn)[0]
        fwd = ddc.conflict_index_verdict(conn, conflict["id"])
        check(
            "正查（冲突→索引）与反查（格口→冲突组）互逆",
            fwd["ok"]
            and fwd["cell_id"] == idx["cell_id"]
            and fwd["index"]["drop_event_id"] == hit["drop_event_id"]
            and fwd["index"]["hold_key"] == hit["hold_key"],
        )
        full = slot_idx.verify(conn)
        check("全库互逆核证通过", full["ok"] and full["checked"]["index_rows"] == 1)

        # 3) 邻格不含冲突、占用未被误动
        nb = slot_idx.lookup_by_cell(conn, "CAB-A-03")
        check("邻格反查为空（未串格）", nb["indexed"] is False and nb["hits"] == [])
        after_neighbor = dict(
            conn.execute("SELECT * FROM occupancies WHERE cell_id='CAB-A-03'").fetchone()
        )
        check(
            "邻格占用未被冲突路径改动",
            after_neighbor["status"] == "occupied"
            and after_neighbor["tracking_no"] == "TN1003"
            and after_neighbor["opened_at"] == "2026-11-19T02:00:00Z"
            and (before_neighbor is None or after_neighbor["id"] == before_neighbor["id"]),
            json.dumps(after_neighbor, ensure_ascii=False),
        )
        after_pre = dict(
            conn.execute("SELECT * FROM occupancies WHERE id=?", (before_pre["id"],)).fetchone()
        )
        check(
            "先占 tracking 未被改",
            after_pre["tracking_no"] == before_pre["tracking_no"]
            and after_pre["opened_at"] == before_pre["opened_at"]
            and after_pre["last_event_id"] == before_pre["last_event_id"],
        )

        # 4) 格阵角标与反查同源
        badges = cell_badges(conn)
        check(
            "格阵冲突角标与反查同源",
            badges["CAB-A-02"]["count"] == len(idx["hits"])
            and badges["CAB-A-02"]["ok"] is True
            and badges["CAB-A-02"]["latest_conflict_id"] == conflict["id"]
            and badges["CAB-A-03"]["count"] == 0,
        )
    finally:
        conn.close()

    _case_half_failure()
    _case_rerun_idempotent()
    _case_blank_drop()
    _case_cross_cell()
    _case_delete_index()

    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} 通过")
    shutil.rmtree(TMP, ignore_errors=True)
    if failed:
        print("未过:", failed)
        sys.exit(1)
    print("全部通过")


def _case_half_failure():
    """索引写入半截失败 → 冲突行/搁置行/索引/占用整组回滚。"""
    orig = slot_idx.record

    def boom(*a, **kw):
        raise RuntimeError("注入：索引写失败")

    conn = connect()
    before = {
        "conflicts": conn.execute("SELECT COUNT(*) AS c FROM slot_conflicts").fetchone()["c"],
        "holds": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
        "index": conn.execute("SELECT COUNT(*) AS c FROM slot_conflict_index").fetchone()["c"],
        "occs": conn.execute("SELECT COUNT(*) AS c FROM occupancies").fetchone()["c"],
    }
    occ = ddc.find_open_occupancy(conn, "CAB-A-02")
    conn.close()

    ev = {
        "id": "E99",
        "event_type": "drop",
        "event_ts": "2026-11-19T03:00:00Z",
        "tracking_no": "TN1099",
        "payload": {"note": "半截失败注入"},
    }
    slot_idx.record = boom
    try:
        conn = connect()
        try:
            try:
                ingest._shelve_atomic(conn, ev, occ, "2026-11-20T00:00:00Z")
                raised = False
            except RuntimeError:
                raised = True
        finally:
            conn.close()
    finally:
        slot_idx.record = orig

    check("索引写失败时分流报错（不静默）", raised)

    conn = connect()
    try:
        after = {
            "conflicts": conn.execute("SELECT COUNT(*) AS c FROM slot_conflicts").fetchone()["c"],
            "holds": conn.execute("SELECT COUNT(*) AS c FROM quarantine").fetchone()["c"],
            "index": conn.execute("SELECT COUNT(*) AS c FROM slot_conflict_index").fetchone()["c"],
            "occs": conn.execute("SELECT COUNT(*) AS c FROM occupancies").fetchone()["c"],
        }
        check("半截失败整组回滚（无新增冲突/搁置/索引/占用）", after == before, f"{before} -> {after}")
        check("回滚后核证仍通过", slot_idx.verify(conn)["ok"] is True)
        check(
            "回滚后冲突 drop 未落主事件账",
            conn.execute("SELECT 1 FROM parcel_events WHERE id='E99'").fetchone() is None,
        )
    finally:
        conn.close()


def _case_rerun_idempotent():
    """重跑不重复建索引。"""
    report = ingest.run_batch()
    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM slot_conflict_index").fetchone()["c"]
        check(
            "重跑不重复搁置/建索引",
            report["conflicts"] == 0 and report["skipped"] == 3 and n == 1,
            json.dumps(report, ensure_ascii=False),
        )
    finally:
        conn.close()


def _case_blank_drop():
    """后到事件 id 落库即空 → 索引 CHECK 拒收；拒收后库内仍一致。"""
    conn = connect()
    try:
        try:
            conn.execute("UPDATE slot_conflict_index SET drop_event_id=''")
            conn.commit()
            check("后到事件 id 为空时落库被拒", False, "UPDATE 竟然成功")
        except sqlite3.IntegrityError:
            conn.rollback()
            check("后到事件 id 为空时落库被拒", True)
        check("拒收后库内仍一致", slot_idx.verify(conn)["ok"] is True)
    finally:
        conn.close()


def _case_cross_cell():
    """索引行改挂邻格 → 串格，核证失败；恢复后重新通过。"""
    conn = connect()
    try:
        conn.execute("UPDATE slot_conflict_index SET cell_id='CAB-A-03'")
        conn.commit()
        verdict = slot_idx.verify(conn)
        check(
            "串格后全库核证失败",
            verdict["ok"] is False and "index_misrouted" in issue_codes(verdict),
            json.dumps(verdict["issues"], ensure_ascii=False),
        )
        wrong = slot_idx.lookup_by_cell(conn, "CAB-A-03")
        check(
            "串格后邻格反查显式报不一致（不静默）",
            wrong["indexed"] is True and wrong["ok"] is False,
        )
        check("串格后本格反查报缺行", slot_idx.lookup_by_cell(conn, "CAB-A-02")["ok"] is False)
        badges = cell_badges(conn)
        check("格阵角标随反查显式标坏", badges["CAB-A-03"]["ok"] is False)

        conn.execute("UPDATE slot_conflict_index SET cell_id='CAB-A-02'")
        conn.commit()
        check("格口恢复后核证重新通过", slot_idx.verify(conn)["ok"] is True)
    finally:
        conn.close()


def _case_delete_index():
    """人为删索引行 → 反查缺行，核证失败且格阵角标归零。"""
    conn = connect()
    try:
        conn.execute("DELETE FROM slot_conflict_index")
        conn.commit()
        verdict = slot_idx.verify(conn)
        check(
            "删索引后全库核证失败且报缺行",
            verdict["ok"] is False
            and "missing_index" in issue_codes(verdict)
            and any(i.get("drop_event_id") == "E6" for i in verdict["issues"]),
            json.dumps(verdict["issues"], ensure_ascii=False),
        )
        by_cell = slot_idx.lookup_by_cell(conn, "CAB-A-02")
        check(
            "删索引后按格口反查失败（不再静默空命中）",
            by_cell["indexed"] is False and by_cell["ok"] is False,
        )
        check(
            "删索引后正查反打也报缺行",
            ddc.conflict_index_verdict(conn, 1)["ok"] is False,
        )
        check("删索引后格阵角标归零", cell_badges(conn)["CAB-A-02"]["count"] == 0)
    finally:
        conn.close()

    # 启动迁移补建：老库升级后不留「搁置成功而反查缺行」。
    seed.init_db()
    conn = connect()
    try:
        check(
            "启动补偿为既有冲突补建反查（恢复互逆）",
            slot_idx.verify(conn)["ok"] is True
            and cell_badges(conn)["CAB-A-02"]["count"] == 1,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
