"""The n8n workflow files are generated (n8n/build_workflows.py) – these tests keep them, the prompts, the
credentials template and the MCP server consistent, without starting n8n (that is tests/e2e)."""
import asyncio
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("build_workflows", ROOT / "n8n" / "build_workflows.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

FILES = {"01_kpi_weekly_report.json": build.weekly_report, "02_kpi_chat.json": build.chat,
         "03_error_notification.json": build.error_handler}


def load(name: str) -> dict:
    return json.loads((ROOT / "n8n" / "workflows" / name).read_text(encoding="utf-8"))


def nodes_by_type(wf: dict, suffix: str) -> list[dict]:
    return [n for n in wf["nodes"] if n["type"].endswith(suffix)]


@pytest.mark.parametrize("name", FILES)
def test_committed_json_matches_generator(name):
    assert load(name) == json.loads(json.dumps(FILES[name]())), "run: python n8n/build_workflows.py"


@pytest.mark.parametrize("name", FILES)
def test_connections_point_to_existing_nodes(name):
    wf = load(name)
    names = {n["name"] for n in wf["nodes"]}
    assert len(names) == len(wf["nodes"])
    for source, outputs in wf["connections"].items():
        assert source in names
        for branches in outputs.values():
            assert all(link["node"] in names for branch in branches for link in branch)


@pytest.mark.parametrize("name", FILES)
def test_no_secrets_and_only_known_credentials(name):
    text = (ROOT / "n8n" / "workflows" / name).read_text(encoding="utf-8")
    assert not re.search(r"sk-[A-Za-z0-9]{10,}|apiKey|password", text)
    template_ids = {c["id"] for c in json.loads((ROOT / "n8n" / "credentials.template.json").read_text())}
    used = {c["id"] for n in load(name)["nodes"] for c in n.get("credentials", {}).values()}
    assert used <= template_ids


def test_agents_use_the_prompt_files_verbatim():
    report_agent = nodes_by_type(load("01_kpi_weekly_report.json"), ".agent")[0]
    chat_agent = nodes_by_type(load("02_kpi_chat.json"), ".agent")[0]
    for agent, file in ((report_agent, "management_summary_de.md"), (chat_agent, "chat_assistant_de.md")):
        assert agent["parameters"]["options"]["systemMessage"] == (ROOT / "prompts" / file).read_text("utf-8").strip()


def test_report_agent_has_least_privilege_and_known_tools():
    from mcp import Client

    from mcp_server.server import server

    async def tool_names():
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    names = asyncio.run(tool_names())
    report_tools = nodes_by_type(load("01_kpi_weekly_report.json"), ".mcpClientTool")[0]["parameters"]
    assert report_tools["include"] == "except" and report_tools["excludeTools"] == ["search_regulation"]
    assert set(report_tools["excludeTools"]) <= names
    check = nodes_by_type(load("01_kpi_weekly_report.json"), ".mcpClient")[0]["parameters"]
    assert check["tool"]["value"] == "validate_report" in names


def test_setup_publishes_every_workflow():
    setup = (ROOT / "n8n" / "setup.sh").read_text(encoding="utf-8")
    for wf in FILES:
        assert load(wf)["id"] in setup
    assert load("01_kpi_weekly_report.json")["settings"]["errorWorkflow"] == load("03_error_notification.json")["id"]


def test_approval_waits_and_is_not_bypassed():
    wf = load("01_kpi_weekly_report.json")
    approval = next(n for n in wf["nodes"] if n["parameters"].get("operation") == "sendAndWait")
    assert approval["parameters"]["options"]["appendAttribution"] is False
    # the only way to the management e-mail is: check passed → approval → approved
    to_management = [src for src, out in wf["connections"].items()
                     for branch in out.get("main", []) for link in branch if link["node"] == "An Management senden"]
    assert to_management == ["Freigegeben?"]
    assert wf["connections"]["Freigegeben?"]["main"][0][0]["node"] == "An Management senden"
    assert wf["connections"]["Prüfung bestanden?"]["main"][0][0]["node"] == approval["name"]
