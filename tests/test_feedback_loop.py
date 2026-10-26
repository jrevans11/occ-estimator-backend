"""Tests for the restored feedback-loop baseline capture (Oct 2026).

A fake jobtread_query stands in for the JobTread API and answers the
job.costItems read-back and the createDailyLog write the same shape the
real API does.
"""
import json

import feedback_loop as fl


def _item(item_id, name, group_id, group_name, qty, cost, price, type_id="22P9ppJUAHYN"):
    return {
        "id": item_id, "name": name, "quantity": qty, "unitCost": cost,
        "unitPrice": price, "createdAt": "2026-10-26T18:00:00.000Z",
        "costType": {"id": type_id, "name": "Labor"},
        "costCode": {"name": "Siding & Trim"},
        "costGroup": {"id": group_id, "name": group_name},
    }


class FakeJobTread:
    def __init__(self, pages, fail_first_write=False):
        self.pages = pages            # list of node lists, one per page
        self.calls = []
        self.logs = []
        self.fail_first_write = fail_first_write

    def __call__(self, query):
        self.calls.append(query)
        if "createDailyLog" in query:
            if self.fail_first_write and not self.logs and not getattr(self, "_failed", False):
                self._failed = True
                raise RuntimeError("notes too long")
            self.logs.append(query["createDailyLog"]["$"]["notes"])
            return {"createDailyLog": {"createdDailyLog": {"id": "log1"}}}
        args = query["job"]["costItems"]["$"]
        index = int(args.get("page") or 0)
        next_page = str(index + 1) if index + 1 < len(self.pages) else None
        return {"job": {"costItems": {"nodes": self.pages[index], "nextPage": next_page}}}


def test_capture_writes_baseline_in_original_format_and_skips_notes():
    pages = [[
        _item("i1", "Carpentry labor", "g1", "Exterior Repairs", 3, 55, 89),
        _item("i2", "NOTES (internal - not shown on client documents)", "g1", "Exterior Repairs", 1, 0, 0),
        _item("i3", "Exterior caulk", "g1", "Exterior Repairs", 2, 8, 13.2, "22P9ppJUAHYP"),
    ], [
        _item("i4", "Electrical sub", "g2", "Electrical Repairs", 1, 600, 870, "22P9ppJUAHYQ"),
    ]]
    jt = FakeJobTread(pages)
    assert fl.capture_baseline_from_job(jt, "JOB1", "2026-10-26T17:59:00.000Z") is True

    # read-back is filtered to budget lines created since the run started, and paginates
    first = jt.calls[0]["job"]["costItems"]["$"]
    assert first["where"] == {"and": [[["document", "id"], None],
                                      ["createdAt", ">=", "2026-10-26T17:59:00.000Z"]]}
    assert jt.calls[1]["job"]["costItems"]["$"]["page"] == "1"

    assert len(jt.logs) == 1
    note = jt.logs[0]
    assert note.startswith(fl.BASELINE_MARKER)
    record = json.loads(note[len(fl.BASELINE_MARKER):])
    assert record["job_id"] == "JOB1"
    groups = {g["group_id"]: g for g in record["groups"]}
    assert set(groups) == {"g1", "g2"}
    assert set(groups["g1"]["items"]) == {"i1", "i3"}          # NOTES line skipped
    assert groups["g1"]["items"]["i1"] == {
        "name": "Carpentry labor", "cost_type_id": "22P9ppJUAHYN",
        "qty": 3, "unit_cost": 55, "unit_price": 89,
    }

    # the record parses back with the existing reader
    baseline, swept = fl.parse_baseline_and_swept([{"notes": note}])
    assert baseline == record and swept is False


def test_capture_then_diff_round_trip():
    jt = FakeJobTread([[_item("i1", "Carpentry labor", "g1", "Exterior", 3, 55, 89)]])
    fl.capture_baseline_from_job(jt, "JOB1", "2026-10-26T17:59:00.000Z")
    baseline = json.loads(jt.logs[0][len(fl.BASELINE_MARKER):])

    # later: hours raised from 3 to 5 and a line added by hand
    later = FakeJobTread([[
        _item("i1", "Carpentry labor", "g1", "Exterior", 5, 55, 89),
        _item("i9", "Miscellaneous", "g1", "Exterior", 1, 20, 33),
    ]])
    rows = fl.diff_baseline_vs_current(baseline, fl.fetch_current_cost_items(later, "JOB1"))
    kinds = {(r["change_type"], r["field"]) for r in rows}
    assert ("changed", "Quantity") in kinds
    assert ("added", "") in kinds
    # current read-back excludes estimate-document copies
    assert later.calls[0]["job"]["costItems"]["$"]["where"] == [["document", "id"], None]


def test_capture_never_raises_and_writes_nothing_when_readback_fails():
    def broken(query):
        raise RuntimeError("JobTread down")
    assert fl.capture_baseline_from_job(broken, "JOB1", "2026-10-26T17:59:00.000Z") is False


def test_capture_with_no_new_lines_writes_nothing():
    jt = FakeJobTread([[]])
    assert fl.capture_baseline_from_job(jt, "JOB1", "2026-10-26T17:59:00.000Z") is False
    assert jt.logs == []


def test_oversized_baseline_retries_without_item_names():
    jt = FakeJobTread([[_item("i1", "Carpentry labor", "g1", "Exterior", 3, 55, 89)]],
                      fail_first_write=True)
    assert fl.capture_baseline_from_job(jt, "JOB1", "2026-10-26T17:59:00.000Z") is True
    record = json.loads(jt.logs[0][len(fl.BASELINE_MARKER):])
    assert record["slim"] is True
    assert "name" not in record["groups"][0]["items"]["i1"]
    assert record["groups"][0]["items"]["i1"]["qty"] == 3


def test_run_started_marker_is_utc_iso():
    marker = fl.run_started_marker()
    assert marker.endswith(".000Z") and marker[10] == "T"
