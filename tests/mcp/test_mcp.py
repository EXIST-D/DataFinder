import json

import pytest

from datafinder.mcp_server import build_server


async def test_mcp_exposes_expected_tools_and_prompt():
    mcp = build_server()
    names = {t.name for t in await mcp.list_tools()}
    assert {"plan_data_sources", "nbs_fetch", "nbs_find", "nbs_browse", "datasets_search", "portals_list",
            "describe_table", "datafinder_status", "nbs_indicators", "nbs_databases", "nbs_search_national"} <= names
    prompts = await mcp.list_prompts()
    assert [p.name for p in prompts] == ["research_data_plan"]
    msg = await mcp.get_prompt("research_data_plan", {"topic": "工业质量", "region": "河北省"})
    assert "工业质量" in str(msg) and "河北省" in str(msg)


async def test_mcp_tool_call_returns_plan(portals):
    mcp = build_server()
    result = await mcp.call_tool("plan_data_sources", {"topic": "河北省工业质量体系评价"})
    text = json.dumps(result.model_dump() if hasattr(result, "model_dump") else str(result), ensure_ascii=False, default=str)
    assert "河北省统计局" in text


async def test_mcp_tool_errors_carry_actionable_message():
    from mcp.server.mcpserver.exceptions import ToolError

    mcp = build_server()
    with pytest.raises(ToolError, match="datafinder sync"):  # 门户库为空：助手要能读到怎么办
        await mcp.call_tool("plan_data_sources", {"topic": "工业"})
