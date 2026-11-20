"""同格二次投柜冲突：本地夹具 + SQLite 端到端核对。

对照夹具：E6 同格二次 drop（CAB-A-02，先占 TN1002）应整组搁置；
E5 邻格正常 drop（CAB-A-03）、E7 空闲格 drop（CAB-A-04）仍按基座物化。

运行：python3 backend/tests/test_dual_drop_conflict.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

TMP = Path(tempfile.mkdtemp(prefix="lockerbay-ddc-"))
os.environ["DATA_DIR"] = str(TMP / "data")
os.environ["FIXTURES_DIR"] = str(BACKEND.parent / "fixtures")

from app import seed  # noqa: E402
from app.db import connect  # noqa: E402
from app.modules import dual_drop_conflict as ddc  # noqa: E402
from app.services import ingest  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def main():
    seed.init_db()
    conn = connect()
    before = conn.execute(
        "SELECT * FROM occupancies WHERE cell_id='CAB-A-02' AND status='occupied'"
    ).fetchone()
    assert before, "夹具前置：CAB-A-02 应有先占占用"
    before = dict(before)
    conn.close()

    report = ingest.run_batch()
    print("批次报告:", report)

    conn = connect()
    try:
        # 1) 槽位互斥：先占占用保持冲突前态，未开第二条占用
        occs = conn.execute(
            "SELECT * FROM occupancies WHERE cell_id='CAB-A-02'"
        ).fetchall()
        check("先占占用仍唯一（未开第二条）", len(occs) == 1, f"{len(occs)} 条")
        after = dict(occs[0])
        check(
            "先占占用未被改写（tracking_no/opened_at/last_event_id）",
            after["tracking_no"] == before["tracking_no"]
            and after["opened_at"] == before["opened_at"]
            and after["last_event_id"] == before["last_event_id"]
            and after["status"] == "occupied",
            f"{after['tracking_no']} @ {after['opened_at']}",
        )
        check(
            "冲突 drop 未落主事件账（装载入口分流）",
            conn.execute("SELECT 1 FROM parcel_events WHERE id='E6'").fetchone() is None,
        )

        # 2) 冲突落库：冲突表挂双事件 id+格口+先占占用 id，整组载荷可回看
        rows = ddc.list_conflicts(conn)
        check("冲突表恰一条", len(rows) == 1)
        c = rows[0]
        check(
            "冲突行挂双事件 id+格口+先占占用 id",
            c["cell_id"] == "CAB-A-02"
            and c["occupancy_id"] == before["id"]
            and c["open_event_id"] == "E2"
            and c["drop_event_id"] == "E6",
            json.dumps(c, ensure_ascii=False),
        )
        detail = ddc.get_conflict(conn, c["id"])
        evs = {e["role"]: e for e in detail["member"]["events"]}
        check(
            "整组载荷可回看（先占 E2 + 冲突 E6）",
            evs["open"]["id"] == "E2"
            and evs["drop"]["id"] == "E6"
            and evs["drop"]["payload"].get("note") == "同格二次投柜-应整组搁置"
            and evs["drop"]["tracking_no"] == "TN1009",
        )
        q = conn.execute(
            "SELECT * FROM quarantine WHERE reason=?", (ddc.REASON,)
        ).fetchall()
        check("搁置表同组落一条（reason=dual_drop_conflict）", len(q) == 1)
        check(
            "搁置组与冲突行同组",
            json.loads(q[0]["member_json"])["drop_event_id"] == "E6",
        )
        check("核证只读：缺失 id 返回 None", ddc.get_conflict(conn, 9999) is None)

        # 3) 对照：邻格/空闲格 drop 仍按基座物化
        check(
            "邻格 E5 正常物化（CAB-A-03）",
            conn.execute(
                "SELECT 1 FROM occupancies WHERE id='OCC-CAB-A-03-E5' AND status='occupied' AND tracking_no='TN1003'"
            ).fetchone()
            is not None,
        )
        check(
            "空闲格 E7 正常物化（CAB-A-04）",
            conn.execute(
                "SELECT 1 FROM occupancies WHERE id='OCC-CAB-A-04-E7' AND status='occupied'"
            ).fetchone()
            is not None,
        )
        check(
            "批次报告 accepted=2 conflicts=1 skipped=0",
            report["accepted"] == 2 and report["conflicts"] == 1 and report["skipped"] == 0,
            json.dumps(report, ensure_ascii=False),
        )
    finally:
        conn.close()

    # 4) 失败闭 + 幂等：重跑不重复搁置、不重复物化
    report2 = ingest.run_batch()
    conn = connect()
    try:
        check(
            "重跑整组跳过（skipped=3，无新冲突）",
            report2["skipped"] == 3 and report2["conflicts"] == 0 and report2["accepted"] == 0,
            json.dumps(report2, ensure_ascii=False),
        )
        check("重跑后冲突表仍恰一条", len(ddc.list_conflicts(conn)) == 1)
        check(
            "重跑后 CAB-A-02 占用仍唯一",
            conn.execute(
                "SELECT COUNT(*) AS c FROM occupancies WHERE cell_id='CAB-A-02'"
            ).fetchone()["c"]
            == 1,
        )
    finally:
        conn.close()

    failed = [n for n, ok, _ in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} 通过")
    shutil.rmtree(TMP, ignore_errors=True)
    if failed:
        print("未过:", failed)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
