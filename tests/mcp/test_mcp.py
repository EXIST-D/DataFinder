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
    assert '"status": "plan"' in text_of(f) and '"status": "plan"' in text_of(d)
    assert not list(exports_dir().glob("*"))


async def test_download_cannot_take_an_arbitrary_url_or_path(ready):
    tools = {t.name: t for t in await build_server().list_tools()}
    assert set(tools["download"].input_schema["properties"]) == {
        "resource_id",
        "distribution_index",
        "confirm",
    }
    assert "path" not in tools["fetch"].input_schema["properties"]  # MCP 不允许指定写入位置
