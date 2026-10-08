"""Evaluates the report agent on 12 Mondays (4 red, 4 yellow, 4 green) with the real model.

Same system prompt, same model settings and the same MCP tools as the n8n workflow – but run directly, so all
12 weeks run in one go and tokens, cost and duration can be counted per report. The MCP server must be running
(docker compose up -d → http://localhost:8100/mcp).

  python scripts/evaluate_agent.py                 # → docs/agent_eval_results.md, docs/agent_eval_reports.md
  python scripts/evaluate_agent.py 2018-03-05      # single Monday, prints the report

Settings (environment or .env): OPENAI_API_KEY, OPENAI_BASE_URL (optional), CHAT_MODEL (default gpt-6-luna),
REASONING_EFFORT (default low; empty for models without reasoning), PRICE_INPUT_PER_M / PRICE_OUTPUT_PER_M
(USD per 1M tokens, from the pricing page), MCP_URL (default http://localhost:8100/mcp).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
MONDAYS = {  # Monday of the report → the week before it is reported
    "rot": ["2017-04-24", "2017-11-27", "2018-03-05", "2018-08-13"],
    "gelb": ["2017-05-15", "2017-10-02", "2018-04-16", "2018-08-27"],
    "grün": ["2017-02-13", "2017-07-10", "2018-06-04", "2018-07-16"],
}
SECTIONS = ["Status:", "Kennzahlen:", "Auffälligkeiten (SPC):", "Empfehlung:", "Datenqualität:"]
MAX_ITERATIONS = 12  # as in the n8n agent node


def load_env(path: Path = ROOT / ".env") -> None:
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


class MCP:
    """Minimal MCP client over Streamable HTTP (the server runs stateless with JSON responses)."""

    def __init__(self, url: str):
        self.url, self._id = url, 0
        self.http = httpx.Client(timeout=120, headers={"Accept": "application/json, text/event-stream"})

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        resp = self.http.post(self.url, json={"jsonrpc": "2.0", "id": self._id, "method": method,
                                              "params": params or {}})
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        return body["result"]

    def tools(self, exclude: tuple[str, ...]) -> list[dict]:
        return [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                 "parameters": t["inputSchema"]}
                for t in self.request("tools/list")["tools"] if t["name"] not in exclude]

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        return "\n".join(c.get("text", "") for c in result["content"]), bool(result.get("isError"))


class LLM:
    def __init__(self):
        self.model = os.getenv("CHAT_MODEL") or "gpt-6-luna"
        self.effort = os.getenv("REASONING_EFFORT", "low")
        self.price_in = float(os.getenv("PRICE_INPUT_PER_M") or 0)
        self.price_out = float(os.getenv("PRICE_OUTPUT_PER_M") or 0)
        key = os.environ.get("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY is missing (.env)")
        base = (os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.http = httpx.Client(base_url=base, timeout=180, headers={"Authorization": f"Bearer {key}"})

    def respond(self, instructions: str, items: list[dict], tools: list[dict]) -> dict:
        """One call to the Responses API – like n8n (OpenAI accepts tools + reasoning effort only there)."""
        body = {"model": self.model, "instructions": instructions, "input": items, "tools": tools}
        if self.effort:
            body["reasoning"] = {"effort": self.effort}  # no temperature: reasoning models only accept the default
        for attempt in range(4):
            resp = self.http.post("/responses", json=body)
            if resp.status_code not in (429, 500, 502, 503):
                break
            time.sleep(2 ** attempt * 3)
        if resp.status_code >= 400:
            raise RuntimeError(f"OpenAI API {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def run_agent(monday: str, llm: LLM, mcp: MCP, system: str, tools: list[dict]) -> dict:
    items: list[dict] = [{"role": "user", "content": f"Erstelle die Management-Summary. as_of: {monday}"}]
    used = {"in": 0, "out": 0}
    sequence, own_checks, text = [], [], ""
    start = time.perf_counter()
    for _ in range(MAX_ITERATIONS):
        reply = llm.respond(system, items, tools)
        used["in"] += reply.get("usage", {}).get("input_tokens", 0)
        used["out"] += reply.get("usage", {}).get("output_tokens", 0)
        output = reply.get("output", [])
        items += output  # model output (incl. reasoning items) goes back as input of the next call
        calls = [o for o in output if o.get("type") == "function_call"]
        if not calls:
            text = "".join(c.get("text", "") for o in output if o.get("type") == "message"
                           for c in o.get("content", []) if c.get("type") == "output_text").strip()
            break
        for call in calls:
            name, args = call["name"], json.loads(call.get("arguments") or "{}")
            sequence.append(name)
            result, is_error = mcp.call(name, args)
            if name == "validate_report" and not is_error:
                own_checks.append(json.loads(result).get("ok"))
            items.append({"type": "function_call_output", "call_id": call["call_id"], "output": result})
    seconds = time.perf_counter() - start

    check = json.loads(mcp.call("validate_report", {"text": text or "-", "as_of": monday})[0])  # independent
    expected_suppliers = check.get("expected_status") != "grün"
    sections = SECTIONS + (["Betroffene Lieferanten"] if expected_suppliers else [])
    words = len(re.findall(r"\S+", text.replace("**", "")))
    return {
        "monday": monday, "calendar_week": check.get("calendar_week"), "expected_status": check.get("expected_status"),
        "ok": check.get("ok"), "problems": "; ".join(check.get("problems", [])),
        "numbers_checked": check.get("numbers_checked"),
        "own_checks": len(own_checks), "first_draft_ok": own_checks[0] if own_checks else None,
        "sections_ok": all(s in text for s in sections), "words": words, "within_180_words": words <= 180,
        "tool_calls": len(sequence), "tools": " → ".join(sequence),
        "tokens_in": used["in"], "tokens_out": used["out"],
        "cost_usd": round((used["in"] * llm.price_in + used["out"] * llm.price_out) / 1e6, 5),
        "seconds": round(seconds, 1), "report": text,
    }


def pct(rows: list[dict], key: str) -> str:
    return f"{sum(1 for r in rows if r[key]) / len(rows):.0%}"


def write_results(rows: list[dict], llm: LLM) -> None:
    docs = ROOT / "docs"
    fields = [k for k in rows[0] if k != "report"]
    with open(docs / "agent_eval_per_week.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    n = len(rows)
    cost = sum(r["cost_usd"] for r in rows) / n
    first = [r for r in rows if r["first_draft_ok"] is not None]
    lines = [
        "# Agent evaluation – 12 weekly reports",
        "",
        f"Model `{llm.model}`, reasoning effort `{llm.effort or '–'}`, prompt `prompts/management_summary_de.md`, "
        f"MCP tools as in the n8n workflow (without `search_regulation`). Run: {time.strftime('%Y-%m-%d %H:%M')}.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Reports passing the deterministic check (`validate_report`) | **{pct(rows, 'ok')}** ({n} weeks) |",
        f"| … already with the first draft | {pct(first, 'first_draft_ok') if first else '–'} |",
        f"| Agent checked its own draft (`validate_report` called) | {pct(rows, 'own_checks')} |",
        f"| All required sections present | {pct(rows, 'sections_ok')} |",
        f"| Within 180 words | {pct(rows, 'within_180_words')} (median {sorted(r['words'] for r in rows)[n // 2]}) |",
        f"| Tool calls per report | median {sorted(r['tool_calls'] for r in rows)[n // 2]} |",
        f"| Duration per report | median {sorted(r['seconds'] for r in rows)[n // 2]} s |",
        f"| Cost per report | ${cost:.4f} (≈ ${cost * 52:.2f} per year) |",
        "",
        "| Monday | Week | Status | Check | 1st draft | Words | Tools | Tokens in/out | s | Problems |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {r['monday']} | {r['calendar_week']} | {r['expected_status']} | {'✅' if r['ok'] else '❌'} | "
              f"{'–' if r['first_draft_ok'] is None else '✅' if r['first_draft_ok'] else '❌'} | {r['words']} | "
              f"{r['tool_calls']} | {r['tokens_in']}/{r['tokens_out']} | {r['seconds']} | {r['problems'] or ''} |"
              for r in rows]
    (docs / "agent_eval_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    reports = ["# All 12 generated reports (for manual review)", ""]
    for r in rows:
        reports += [f"## {r['monday']} · {r['calendar_week']} · {r['expected_status']}", "", f"Tools: {r['tools']}",
                    "", r["report"].replace("\n", "  \n"), ""]
    (docs / "agent_eval_reports.md").write_text("\n".join(reports), encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    load_env()
    llm = LLM()
    mcp = MCP(os.getenv("MCP_URL", "http://localhost:8100/mcp"))
    system = (ROOT / "prompts" / "management_summary_de.md").read_text(encoding="utf-8").strip()
    tools = mcp.tools(exclude=("search_regulation",))
    mondays = sys.argv[1:] or [m for group in MONDAYS.values() for m in group]
    rows = []
    for monday in mondays:
        row = run_agent(monday, llm, mcp, system, tools)
        rows.append(row)
        print(f"{monday} {row['calendar_week']:<10} {row['expected_status']:<5} check={'ok' if row['ok'] else 'FAIL'} "
              f"words={row['words']} {row['seconds']} s ${row['cost_usd']}")
    if len(sys.argv) > 1:
        print("\n" + rows[0]["report"])
    else:
        write_results(rows, llm)


if __name__ == "__main__":
    main()
