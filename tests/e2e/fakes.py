"""Stand-ins for the end-to-end test of the n8n workflows (no API key, no Docker needed).

  python tests/e2e/fakes.py llm  9999   scripted OpenAI API (/v1/responses, and /v1/chat/completions with its tool rule)
  python tests/e2e/fakes.py smtp 1025   SMTP server that stores every mail as .eml in $E2E_DIR/mails
  python tests/e2e/fakes.py p3   8000   POST /search with the response shape of project 3

The fake LLM is not "smart": it calls the MCP tools in the order the system prompt asks for and writes the report
from the tool results with fixed sentences. That is enough to test everything around the model – tool wiring,
the deterministic check, the approval, the e-mails and the run log. If the file $E2E_DIR/bad_report exists, it adds
an invented number to the report (simulates an agent that ignores validate_report).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

E2E_DIR = Path(os.getenv("E2E_DIR", "/tmp/kpi-e2e"))
TOOL = re.compile(r"^.*?_(?=(get_|assess_|check_|validate_|search_))")  # n8n prefixes tool names: KPI_MCP_Server_


# ------------------------------------------------------------------------------------------------ fake LLM
def de(x, nd=1) -> str:
    s = f"{x:,.{nd}f}" if isinstance(x, float) else f"{x:,}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def tool_results(messages: list[dict]) -> dict:
    """{tool name: parsed result} from the conversation the agent sends back."""
    names, out = {}, {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            names[tc["id"]] = tc["function"]["name"]
        if m.get("role") != "tool":
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        try:
            data = json.loads(content)
            if isinstance(data, list) and data and "response" in data[0]:  # n8n wraps MCP results
                data = data[0]["response"]
            if isinstance(data, list) and data and "text" in data[0]:
                data = json.loads(data[0]["text"])
        except (TypeError, ValueError, KeyError):
            data = {"raw": content}  # e.g. a tool error message
        out[TOOL.sub("", names.get(m.get("tool_call_id"), "?"))] = data
    return out


def write_report(r: dict) -> str:
    w, a, dq = r["get_weekly_kpis"], r["assess_week"], r["get_dq_status"]
    k = w["kpis"]
    label = {"grün": "stabil", "gelb": "beobachten", "rot": "Handlungsbedarf"}[a["status"]]
    lines = [
        f"**{w['calendar_week']} – Lieferperformance**",
        f"**Status:** {a['status_emoji']} {label}",
        f"**Kennzahlen:** Lieferanten-Termintreue {de(k['supplier_otd']['value'])} % "
        f"({de(k['supplier_otd']['change'])} Pp), Kunden-Termintreue {de(k['customer_otd']['value'])} % "
        f"({de(k['customer_otd']['change'])} Pp), Reklamationsquote {de(k['complaint_rate']['value'])} %, "
        f"Ø Lieferzeit {de(k['avg_lead_time_days']['value'])} Tage, {de(k['orders']['value'])} Bestellungen",
        "**Auffälligkeiten (SPC):** " + a["signals_summary"],
    ]
    if r.get("get_late_suppliers", {}).get("suppliers"):
        lines.append("**Betroffene Lieferanten:** " + "; ".join(
            f"{s['supplier']} ({de(s['otd_pct'])} % Termintreue, {s['late_handovers']} verspätete Übergaben)"
            for s in r["get_late_suppliers"]["suppliers"][:2]))
    lines.append("**Empfehlung:** Ursachen mit den betroffenen Lieferanten klären und die Entwicklung in der "
                 "nächsten Woche prüfen.")
    lines.append(f"**Datenqualität:** {de(dq['dq_score_pct'], 2)} % ({de(dq['failed_rows'])} Befunde), {dq['trend']}")
    return "\n".join(lines)


def decide(body: dict) -> tuple[list | None, str | None]:
    """Next step of the agent: tool calls or the final text."""
    msgs = body["messages"]
    user = next((m["content"] for m in reversed(msgs) if m.get("role") == "user"), "")
    if isinstance(user, list):
        user = " ".join(p.get("text", "") for p in user if isinstance(p, dict))
    found = re.search(r"\d{4}-\d{2}-\d{2}", user)
    as_of = found.group(0) if found else None
    r = tool_results(msgs)
    if "Management-Summary" not in user:  # chat workflow
        if "search_regulation" not in r:
            return [("search_regulation", {"question": user[:300], "k": 3})], None
        arts = r["search_regulation"].get("articles", [])
        if not arts:
            return None, "Die Regelungs-API ist nicht erreichbar."
        return None, "Relevante Vorschriften: " + "; ".join(f"{a['label']} – {a['title']}" for a in arts)
    if "get_weekly_kpis" not in r:
        return [("get_dq_status", {}), ("get_weekly_kpis", {"as_of": as_of}), ("assess_week", {"as_of": as_of})], None
    if r["assess_week"]["status"] != "grün" and "get_late_suppliers" not in r:
        return [("get_late_suppliers", {"as_of": as_of, "top_n": 3})], None
    text = write_report(r)
    if "validate_report" not in r:
        return [("validate_report", {"text": text, "as_of": as_of})], None
    if (E2E_DIR / "bad_report").exists():
        text = text.replace("Lieferperformance**", "Lieferperformance**\nZiel verfehlt um 71,2 %", 1)
    return None, text


class LLM(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj: dict) -> None:
        out = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):  # model list
        self._json({"object": "list", "data": [{"id": "gpt-6-luna", "object": "model"}]})

    def _error(self, status: int, message: str) -> None:
        out = json.dumps({"error": {"message": message, "type": "invalid_request_error"}}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        responses_api = self.path.rstrip("/").endswith("/responses")
        tools = [t.get("name") or t.get("function", {}).get("name") for t in body.get("tools", [])]
        effort = (body.get("reasoning") or {}).get("effort") or body.get("reasoning_effort")
        with open(E2E_DIR / "llm_requests.jsonl", "a", encoding="utf-8") as log:  # checked by the scenarios
            log.write(json.dumps({"path": self.path, "keys": sorted(body), "model": body.get("model"), "tools": tools,
                                  "stream": body.get("stream"), "reasoning_effort": effort,
                                  "temperature": body.get("temperature")}) + "\n")
        if not responses_api and tools and body.get("reasoning_effort"):  # same rule as the real OpenAI API
            return self._error(400, f"Function tools with reasoning_effort are not supported for {body.get('model')} "
                                    "in /v1/chat/completions. To use function tools, use /v1/responses.")
        prefix = TOOL.match(tools[0]).group(0) if tools and TOOL.match(tools[0]) else ""
        calls, text = decide({"messages": as_chat_messages(body) if responses_api else body["messages"]})
        calls = [(prefix + name, json.dumps({k: v for k, v in a.items() if v})) for name, a in calls or []]
        if responses_api:
            stamp = time.time_ns()
            output = [{"type": "function_call", "id": f"fc_{stamp}_{i}", "call_id": f"call_{stamp}_{i}",
                       "name": name, "arguments": args, "status": "completed"} for i, (name, args) in enumerate(calls)]
            if not calls:
                output = [{"type": "message", "id": f"msg_{time.time_ns()}", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text, "annotations": []}]}]
            return self._json({"id": f"resp_{time.time_ns()}", "object": "response", "created_at": int(time.time()),
                               "status": "completed", "model": body["model"], "output": output,
                               "usage": {"input_tokens": 1200, "output_tokens": 150, "total_tokens": 1350,
                                         "input_tokens_details": {"cached_tokens": 0},
                                         "output_tokens_details": {"reasoning_tokens": 0}}})
        msg = {"role": "assistant", "content": text}
        if calls:
            msg = {"role": "assistant", "content": None, "tool_calls": [
                {"id": f"call_{time.time_ns()}_{i}", "type": "function", "function": {"name": name, "arguments": args}}
                for i, (name, args) in enumerate(calls)]}
        self._json({"id": "chatcmpl-e2e", "object": "chat.completion", "created": int(time.time()),
                    "model": body["model"], "usage": {"prompt_tokens": 1200, "completion_tokens": 150,
                                                      "total_tokens": 1350},
                    "choices": [{"index": 0, "message": msg, "finish_reason": "tool_calls" if calls else "stop"}]})


def as_chat_messages(body: dict) -> list[dict]:
    """Responses API input items → chat-completions messages (so decide() handles both APIs)."""
    def text_of(content):
        if isinstance(content, list):
            return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        return content or ""

    messages = [{"role": "system", "content": body["instructions"]}] if body.get("instructions") else []
    items = body.get("input") or []
    for item in [{"role": "user", "content": items}] if isinstance(items, str) else items:
        kind = item.get("type", "message")
        if kind == "message":
            messages.append({"role": item.get("role"), "content": text_of(item.get("content"))})
        elif kind == "function_call":
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": item["call_id"], "type": "function",
                 "function": {"name": item["name"], "arguments": item.get("arguments", "{}")}}]})
        elif kind == "function_call_output":
            output = item.get("output")
            messages.append({"role": "tool", "tool_call_id": item["call_id"],
                             "content": output if isinstance(output, str) else json.dumps(output)})
    return messages


# ------------------------------------------------------------------------------------------------ fake project 3
P3_SOURCES = [
    {"label": "DSGVO Art. 22", "title": "Automatisierte Entscheidungen im Einzelfall einschließlich Profiling",
     "url": "https://eur-lex.europa.eu/legal-content/DE/TXT/HTML/?uri=CELEX:32016R0679#art_22",
     "excerpt": "(1) Die betroffene Person hat das Recht, nicht einer ausschließlich auf einer automatisierten "
                "Verarbeitung beruhenden Entscheidung unterworfen zu werden ..."},
    {"label": "AI Act Art. 14", "title": "Menschliche Aufsicht",
     "url": "https://eur-lex.europa.eu/legal-content/DE/TXT/HTML/?uri=CELEX:32024R1689#art_14",
     "excerpt": "(1) Hochrisiko-KI-Systeme werden so konzipiert und entwickelt, dass sie ... von natürlichen "
                "Personen wirksam beaufsichtigt werden können."},
]


class P3(LLM):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self._json({"lang": "de", "mode": "vector", "sources": P3_SOURCES[: body.get("k", 3)]})


# ------------------------------------------------------------------------------------------------ fake SMTP
def run_smtp(port: int) -> None:
    from aiosmtpd.controller import Controller

    mails = E2E_DIR / "mails"
    mails.mkdir(parents=True, exist_ok=True)

    class Store:
        async def handle_DATA(self, server, session, envelope):
            (mails / f"{time.time_ns()}.eml").write_bytes(envelope.content)
            return "250 OK"

    Controller(Store(), hostname="0.0.0.0", port=port).start()
    asyncio.new_event_loop().run_forever()


if __name__ == "__main__":
    kind, port = sys.argv[1], int(sys.argv[2])
    E2E_DIR.mkdir(parents=True, exist_ok=True)
    if kind == "smtp":
        run_smtp(port)
    else:
        ThreadingHTTPServer(("0.0.0.0", port), {"llm": LLM, "p3": P3}[kind]).serve_forever()
