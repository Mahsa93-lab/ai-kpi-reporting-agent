"""MCP server: exposes the read-only KPI tools to the n8n AI agent (Model Context Protocol, Streamable HTTP).

  python -m mcp_server.server          → http://localhost:8100/mcp
  (in Docker Compose the n8n container reaches it as http://mcp:8100/mcp)

Security by design: the agent never gets database or file access – only these tools, all read-only
(annotated as such), with validated parameters. Port 8100, because project 3's API already uses 8000.
"""
from __future__ import annotations

import functools
import os

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from mcp_server import tools

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

server = MCPServer(
    name="kpi-server",
    instructions=(
        "Read-only access to the weekly supply-chain KPIs of the Olist lakehouse (project 2): KPIs, SPC control "
        "charts, traffic-light status, late suppliers, data-quality status, a report validator and – via project 3 – "
        "the EU AI Act and GDPR. Pass `as_of` (ISO date, e.g. 2018-03-05) to replay a past Monday."
    ),
)


def _explain_errors(fn):
    """Invalid input (unknown KPI, date before the data …) goes back to the agent as a readable message,
    so it can correct the call; every other exception stays a generic error (no internals leak)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


for fn in (tools.get_weekly_kpis, tools.assess_week, tools.check_spc, tools.get_late_suppliers,
           tools.get_dq_status, tools.validate_report):
    server.tool(annotations=READ_ONLY)(_explain_errors(fn))
OPEN_WORLD = ToolAnnotations(readOnlyHint=True, openWorldHint=True)  # calls the API of project 3
server.tool(annotations=OPEN_WORLD)(_explain_errors(tools.search_regulation))


def main() -> None:
    hosts = os.getenv("MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,host.docker.internal:*,mcp:*").split(",")
    server.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "0.0.0.0"),
        port=int(os.getenv("MCP_PORT", "8100")),
        stateless_http=True,  # every tool call stands alone – simple and robust behind n8n
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"http://{h}" for h in hosts],
        ),
    )


if __name__ == "__main__":
    main()
