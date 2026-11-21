import json

import pytest

from datafinder.service import ServiceError
from datafinder.service import presentation as P


def _item(i: int) -> dict:
    return {
        "resource_id": f"s:indicator:{i}",
        "kind": "indicator",
        "title": f"指标{i}",
        "delivery": "generate",
        "usability": "tabular",
        "regions": [],
        "period": {},
        "why_matched": ["标题含“指标”"],
        "missing_requirements": [],
    }


def test_present_adds_a_four_part_block_and_keeps_the_data_unchanged():
    data = {
        "query": "x",
        "mode": "discovery",
        "total": 2,
        "returned": 2,
        "offset": 0,
        "results": [_item(1), _item(2)],
    }
    out, text = P.present("search", data)
    assert {k: v for k, v in out.items() if k != "presentation"} == data
    assert set(out["presentation"]) == {
        "operation_status",
        "summary",
        "evidence",
        "gaps",
        "next_actions",
    }
    assert "s:indicator:1" in text and "指标2" in text


def test_text_is_capped_by_dropping_items_not_cutting_them(monkeypatch):
    monkeypatch.setattr(P, "TEXT_LIMIT", 600)
    data = {
        "query": "x",
        "total": 50,
        "returned": 50,
        "offset": 0,
        "results": [_item(i) for i in range(50)],
    }
    # 条目都在同一个块里，所以整块保留；分块过长时才省略后面的块
    _, text = P.present(
        "search", {**data, "portals": [{"name": "门户" * 30, "url": "http://x/" + "a" * 500}]}
    )
    assert "输出过长" in text


def test_detail_modes():
    data = {"query": "x", "total": 1, "returned": 1, "offset": 0, "results": [_item(1)]}
    _, js = P.present("search", data, detail="json")
    assert json.loads(js)["presentation"]["summary"]
    with pytest.raises(ServiceError, match="detail"):
        P.present("search", data, detail="huge")


def test_fetch_preview_and_done_report_stages():
    plan = {
        "status": "plan",
        "data_stage": "previewed",
        "title": "T",
        "params": {"regions": ["河北"]},
        "save_to": "/x",
        "license": "?",
        "next_action": "加 confirm=true",
    }
    pres, text = P.present("fetch", plan)[0]["presentation"], P.present("fetch", plan)[1]
    assert pres["operation_status"] == "needs_confirmation" and "已预览" in text
    done = {
        "status": "done",
        "data_stage": "acquired",
        "title": "T",
        "saved_to": "/x/a.csv",
        "row_count": 3,
        "coverage": {
            "missing_cells": 2,
            "expected_cells": 6,
            "observed_cells": 4,
            "status": "partial",
        },
    }
    out, text = P.present("fetch", done)
    assert out["presentation"]["operation_status"] == "partial"
    assert "覆盖缺口 2/6" in text
