"""End-to-end scenarios against a running n8n with the imported workflows (started by tests/e2e/run_e2e.sh).

Every scenario goes through the real n8n engine, the real MCP server and the real deterministic check –
only the LLM, the mail server and project 3 are replaced by tests/e2e/fakes.py.
"""
from __future__ import annotations

import email
import json
import os
import re
import signal
import sys
import time
import urllib.request
import uuid
from email import policy
from pathlib import Path

E2E_DIR = Path(os.getenv("E2E_DIR", "/tmp/kpi-e2e"))
RUNS = Path(os.getenv("RUNS_DIR", "/home/node/runs"))
BASE = os.getenv("N8N_URL", "http://localhost:5678")
FORM = f"{BASE}/form/5f0c7a52-8a64-4d8e-9a43-1b9d6bcd4e01"
CHAT = f"{BASE}/webhook/0b7e5d2c-3f4a-4b8e-9c1d-6a2f8e4b7c90/chat"
# n8n ignores approval clicks from bots and link scanners – the e-mail link must be opened like a browser does
BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"


# ------------------------------------------------------------------------------------------------ helpers
def http(url: str, data: bytes | None = None, headers: dict | None = None) -> str:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", "replace")


def submit_form(monday: str) -> None:
    boundary = uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="field-0"\r\n\r\n{monday}\r\n'
            f"--{boundary}--\r\n").encode()
    http(FORM, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def wait_for(what: str, check, timeout: float = 60):
    end = time.time() + timeout
    while time.time() < end:
        result = check()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError(f"timeout: {what}")


def mails() -> list:
    out = []
    for f in sorted((E2E_DIR / "mails").glob("*.eml")):
        m = email.message_from_bytes(f.read_bytes(), policy=policy.default)
        html = m.get_body(preferencelist=("html",))
        plain = m.get_body(preferencelist=("plain",))
        out.append({"to": str(m["To"]), "subject": str(m["Subject"]),
                    "html": html.get_content() if html else "", "text": plain.get_content() if plain else ""})
    return out


def mail_to(address: str, seen: int):
    return lambda: next((m for m in mails()[seen:] if address in m["to"]), None)


def subject_to(address: str, subject: str, seen: int):
    return lambda: next((m for m in mails()[seen:] if address in m["to"] and subject in m["subject"]), None)


def new_run(before: set):
    def check():
        files = set(RUNS.glob("*.json")) - before
        return json.loads(max(files).read_text(encoding="utf-8")) if files else None
    return check


def click(mail: dict, approved: bool) -> None:
    link = next(h for h in re.findall(r'href="([^"]+)"', mail["html"]) if f"approved={str(approved).lower()}" in h)
    http(link.replace("&amp;", "&"), headers={"User-Agent": BROWSER})


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


# ------------------------------------------------------------------------------------------------ scenarios
def approved_report():
    seen, runs = len(mails()), set(RUNS.glob("*.json"))
    submit_form("2018-03-05")
    approval = wait_for("approval e-mail", mail_to("teamleitung@", seen))
    expect("KW 9/2018" in approval["subject"], approval["subject"])
    expect("🔴 Handlungsbedarf" in approval["html"] and "**" not in approval["html"], "approval body")
    expect("automatically with n8n" not in approval["html"].lower(), "n8n attribution in approval mail")
    click(approval, approved=True)
    final = wait_for("management e-mail", mail_to("management@", seen))
    confirmation = wait_for("confirmation", subject_to("teamleitung@", "✅ Freigegeben", seen))
    expect("management@example.com" in confirmation["text"], confirmation["text"])
    expect("<strong>Status:</strong> 🔴 Handlungsbedarf" in final["html"], "management html")
    expect("Kunden-Termintreue 73,7 % (-7,3 Pp)" in final["text"] and "**" not in final["text"], "management text")
    run = wait_for("run log", new_run(runs))
    expect(run["status"] == "rot" and run["validation_ok"] and run["approval"] == "freigegeben"
           and run["sent_to_management"] and run["trigger"] == "Formular", str(run))
    expect(run["agent_duration_s"] <= run["total_duration_s"], "durations")


def rejected_report():
    seen, runs = len(mails()), set(RUNS.glob("*.json"))
    submit_form("2018-03-05")
    approval = wait_for("approval e-mail", mail_to("teamleitung@", seen))
    expect("Es zählt nur der erste Klick" in approval["html"], "hint about the confirmation")
    click(approval, approved=False)
    wait_for("confirmation", subject_to("teamleitung@", "❌ Abgelehnt", seen))
    click(approval, approved=True)  # a second click must not change the decision
    run = wait_for("run log", new_run(runs))
    expect(run["approval"] == "abgelehnt" and not run["sent_to_management"], str(run))
    expect(mail_to("management@", seen)() is None, "rejected report reached management")


def invented_number_is_blocked():
    seen, runs = len(mails()), set(RUNS.glob("*.json"))
    (E2E_DIR / "bad_report").touch()
    try:
        submit_form("2018-06-04")
        notice = wait_for("analyst e-mail", mail_to("analyse@", seen))
    finally:
        (E2E_DIR / "bad_report").unlink()
    expect("KW 22/2018" in notice["subject"] and "71,2" in notice["text"], notice["text"][:300])
    run = wait_for("run log", new_run(runs))
    expect(not run["validation_ok"] and run["approval"] == "nicht angefragt" and not run["sent_to_management"],
           str(run))
    expect(mail_to("teamleitung@", seen)() is None, "unchecked report was sent for approval")


def latest_week_by_default():
    seen = len(mails())
    submit_form("")
    approval = wait_for("approval e-mail", mail_to("teamleitung@", seen))
    expect("KW 34/2018" in approval["subject"] and "🟡" in approval["html"], approval["subject"])


def chat_with_regulation_lookup():
    answer = json.loads(http(CHAT, json.dumps({"action": "sendMessage", "sessionId": "e2e",
                                               "chatInput": "Was sagt die DSGVO zu automatisierten Entscheidungen?"}
                                              ).encode(), {"Content-Type": "application/json"}))
    expect("DSGVO Art. 22" in answer["output"], answer["output"])


def model_settings():
    requests = [json.loads(line) for line in (E2E_DIR / "llm_requests.jsonl").read_text().splitlines()]
    expect(all(r["path"].endswith("/responses") for r in requests), "all calls must use the Responses API")
    expect(all(r["model"] == "gpt-6-luna" and r["reasoning_effort"] == "low" and r["temperature"] is None
               for r in requests), "model, reasoning effort or temperature")
    report_calls = [r for r in requests if not any(t.endswith("search_regulation") for t in r["tools"])]
    expect(report_calls and len(report_calls[0]["tools"]) == 6, "report agent should see 6 tools (least privilege)")


def error_notification():
    seen = len(mails())
    os.kill(int(Path(os.environ["MCP_PID_FILE"]).read_text()), signal.SIGTERM)  # MCP server down
    time.sleep(1)
    submit_form("2018-03-05")
    alert = wait_for("error e-mail", mail_to("analyse@", seen))
    expect("Fehler im Workflow" in alert["subject"] and "KPI-MCP-Server" in alert["text"], alert["text"])


SCENARIOS = [approved_report, rejected_report, invented_number_is_blocked, latest_week_by_default,
             chat_with_regulation_lookup, model_settings, error_notification]  # error last: it stops the MCP server

if __name__ == "__main__":
    failed = 0
    for scenario in SCENARIOS:
        try:
            scenario()
            print(f"PASS  {scenario.__name__}")
        except Exception as exc:  # report all scenarios, then fail
            failed += 1
            print(f"FAIL  {scenario.__name__}: {exc}")
    print(f"\n{len(SCENARIOS) - failed}/{len(SCENARIOS)} scenarios passed")
    sys.exit(1 if failed else 0)
