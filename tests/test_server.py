"""The MCP server in-process: tool list, read-only annotations, readable errors for invalid input."""
import asyncio
import json

from mcp import Client

from mcp_server.server import server

EXPECTED = {"get_weekly_kpis", "assess_week", "check_spc", "get_late_suppliers", "get_dq_status", "validate_report",
            "search_regulation"}


def run(coro):
    return asyncio.run(coro)


def test_tools_are_listed_and_read_only():
    async def main():
        async with Client(server) as client:
            return (await client.list_tools()).tools

    tools = {tool.name: tool for tool in run(main())}
    assert set(tools) == EXPECTED
    assert all(tool.annotations.read_only_hint for tool in tools.values())
    assert tools["search_regulation"].annotations.open_world_hint is True
    assert tools["check_spc"].input_schema["properties"]["kpi"]["enum"][0] == "supplier_otd"


def test_tool_call_returns_json():
    async def main():
        async with Client(server) as client:
            return await client.call_tool("assess_week", {"as_of": "2018-03-05"})

    result = run(main())
    assert not result.is_error
    assert json.loads(result.content[0].text)["status"] == "rot"


def test_invalid_input_comes_back_as_readable_error():
    async def main():
        async with Client(server) as client:
            return await client.call_tool("get_weekly_kpis", {"as_of": "2015-01-05"})

    result = run(main())
    assert result.is_error and "No complete week before 2015-01-05" in result.content[0].text
