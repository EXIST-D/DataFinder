import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from datafinder.mcp_server import build_server

from ..catalog.conftest import build

TOOLS = {
    "facets",
    "sources",
    "status",
    "search",
    "plan",
    "inspect",
    "fetch",
    "download",
    "describe",
}


@pytest.fixture
def ready():
    from datafinder.catalog import Catalog

    build(Catalog.open()).close()


def text_of(result) -> str:
    return json.dumps(
        result.model_dump() if hasattr(result, "model_dump") else str(result),
        ensure_ascii=False,
        default=str,
    )


async def test_exposes_exactly_the_nine_capability_tools_and_the_prompt():
    mcp = build_server()
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == TOOLS
    assert not any(
        n.startswith(("nbs_", "yearbook_", "portals_", "datasets_")) for n in tools
    )  # 工具名里没有领域词
    assert [p.name for p in await mcp.list_prompts()] == ["research_data_plan"]
    msg = await mcp.get_prompt("research_data_plan", {"topic": "工业质量", "region": "河北省"})
    assert "工业质量" in str(msg) and "河北省" in str(msg) and "confirm" in str(msg)


async def test_every_tool_documents_its_layer_and_get_tools_default_to_preview():
    tools = {t.name: t for t in await build_server().list_tools()}
    for name, layer in {
        "facets": "L0",
        "search": "L1",
        "plan": "L1",
        "inspect": "L2",
        "fetch": "L3",
        "download": "L3",
        "describe": "L4",
    }.items():
        assert layer in tools[name].description, name
    for name in ("fetch", "download"):
        assert tools[name].input_schema["properties"]["confirm"]["default"] is False  # 默认只预览
    assert (
        "confirm" in tools["fetch"].description and "不带 confirm" in tools["download"].description
    )


async def test_search_and_plan_work_through_the_protocol(ready, portals):
    mcp = build_server()
    r = await mcp.call_tool(
        "search", {"query": "工业增加值", "region_ids": ["河北"], "kinds": ["indicator"]}
    )
    assert "nbs:indicator:6/c1/i1" in text_of(r)
    p = await mcp.call_tool("plan", {"topic": "河北省工业增加值"})
    assert "河北省统计局" in text_of(p)


async def test_errors_carry_code_reason_and_next_step_in_text(tmp_path, monkeypatch):
    monkeypatch.setenv("DATAFINDER_HOME", str(tmp_path / "empty"))
    mcp = build_server()
    with pytest.raises(ToolError, match=r"\[INDEX_NOT_READY\].*index build"):
        await mcp.call_tool("search", {"query": "工业"})


async def test_invalid_facet_and_not_found_errors_are_coded(ready):
    mcp = build_server()
    with pytest.raises(ToolError, match=r"\[INVALID_FACET_ID\].*facets"):
        await mcp.call_tool("search", {"query": "工业", "topic_ids": ["cnstat.nope"]})
    with pytest.raises(ToolError, match=r"\[NOT_FOUND\]"):
        await mcp.call_tool("inspect", {"resource_id": "nbs:indicator:nope"})


async def test_fetch_and_download_preview_without_side_effects(ready):
    from datafinder.tables import exports_dir

    mcp = build_server()
    f = await mcp.call_tool("fetch", {"resource_id": "nbs:indicator:6/c1/i1", "regions": ["河北"]})
    d = await mcp.call_tool("download", {"resource_id": "yb.sd:table:2025/14-08"})
    assert f.structured_content["status"] == "plan" and d.structured_content["status"] == "plan"
    assert f.structured_content["data_stage"] == d.structured_content["data_stage"] == "previewed"
    assert f.structured_content["presentation"]["operation_status"] == "needs_confirmation"
    assert "confirm=true" in f.content[0].text and "已预览" in f.content[0].text
    assert not list(exports_dir().glob("*"))


async def test_download_cannot_take_an_arbitrary_url_or_path(ready):
    tools = {t.name: t for t in await build_server().list_tools()}
    assert set(tools["download"].input_schema["properties"]) == {
        "resource_id",
        "distribution_index",
        "confirm",
        "detail",  # 汇报详细程度，不是路径或网址
    }
    assert "path" not in tools["fetch"].input_schema["properties"]  # MCP 不允许指定写入位置


async def test_content_is_concise_text_while_structured_content_keeps_full_data(ready, portals):
    mcp = build_server()
    args = {"query": "工业增加值", "region_ids": ["河北"], "kinds": ["indicator"]}
    r = await mcp.call_tool("search", args)
    text = r.content[0].text
    assert not text.lstrip().startswith("{")  # 给助手的是简明文字，不是 JSON
    assert "nbs:indicator:6/c1/i1" in text  # 但后续调用需要的 id 在里面
    sc = r.structured_content
    assert sc["results"] and sc["total"] >= 1  # 完整数据不变
    pres = sc["presentation"]
    assert set(pres) == {"operation_status", "summary", "evidence", "gaps", "next_actions"}
    assert pres["operation_status"] == "complete"


async def test_detail_json_gives_compact_json_and_bad_detail_is_coded(ready, portals):
    mcp = build_server()
    r = await mcp.call_tool("search", {"query": "工业增加值", "detail": "json"})
    data = json.loads(r.content[0].text)
    assert data["results"] and "presentation" in data and "\n" not in r.content[0].text
    with pytest.raises(ToolError, match=r"\[INVALID_ARGUMENT\]"):
        await mcp.call_tool("search", {"query": "工业", "detail": "huge"})


async def test_concise_text_is_much_smaller_than_the_full_json(ready, portals):
    mcp = build_server()
    for tool, args in (("plan", {"topic": "河北省工业增加值"}), ("search", {"query": "工业"})):
        r = await mcp.call_tool(tool, args)
        full = len(json.dumps(r.structured_content, ensure_ascii=False))
        assert len(r.content[0].text) < full  # 简明版不比完整数据大
