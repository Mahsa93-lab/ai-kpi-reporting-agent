"""Generates the three n8n workflows (n8n/workflows/*.json) from this file and the prompts in prompts/.

  python n8n/build_workflows.py

Why generated instead of exported from the editor? The prompts live in prompts/*.md (reviewable in Git), the
workflows always contain exactly that text (a test checks it), and the JSON never contains credentials or
editor noise. After changing a workflow in the n8n editor, copy the change into this file.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "n8n" / "workflows"
MCP_URL = "http://mcp:8100/mcp"  # service name in docker-compose.yml
MODEL = "gpt-6-luna"
CRED_OPENAI = {"openAiApi": {"id": "openAiKpiAgent01", "name": "OpenAI"}}
CRED_SMTP = {"smtp": {"id": "smtpMailpit00001", "name": "Mailpit (lokal)"}}
IDS = {"report": "kpiWeeklyReport1", "chat": "kpiChatAssist001", "error": "kpiErrorNotify01"}
SENDER, APPROVER, MANAGEMENT, ANALYST = ("kpi-agent@example.com", "teamleitung@example.com",
                                         "management@example.com", "analyse@example.com")


def prompt(name: str) -> str:
    return (ROOT / "prompts" / name).read_text(encoding="utf-8").strip()


def node(name, type_, version, position, parameters, **extra):
    n = {"parameters": parameters, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"kpi-agent/{name}")), "name": name,
         "type": type_, "typeVersion": version, "position": position}
    n.update(extra)
    return n


def chat_model(position):
    return node("OpenAI " + MODEL, "@n8n/n8n-nodes-langchain.lmChatOpenAi", 1.3, position, {
        "model": {"__rl": True, "mode": "id", "value": MODEL},
        # Responses API: OpenAI accepts tools + reasoning effort for this model only there (not in chat completions)
        "responsesApiEnabled": True,
        "options": {"extraBody": json.dumps({"reasoning": {"effort": "low"}}), "maxRetries": 3, "timeout": 120000},
    }, credentials=CRED_OPENAI)


def mcp_tools(position, exclude=None):
    params = {"endpointUrl": MCP_URL, "serverTransport": "httpStreamable", "include": "all", "options": {}}
    if exclude:
        params.update({"include": "except", "excludeTools": exclude})
    return node("KPI-MCP-Server", "@n8n/n8n-nodes-langchain.mcpClientTool", 1.2, position, params)


def ai_link(source, target, kind):
    return source, {kind: [[{"node": target, "type": kind, "index": 0}]]}


def main_link(target, index=0):
    return {"node": target, "type": "main", "index": index}


# ------------------------------------------------------------------------------------------- 1 · weekly report
PARAM_CODE = """// Zeitplan → neueste Woche (as_of leer); Formular → gewählter Montag (leer = neueste Woche)
const j = $input.first().json;
const fromForm = 'submittedAt' in j;
const raw = j['Montag (as_of)'] || '';
const as_of = raw ? String(raw).slice(0, 10) : '';
return [{ json: { as_of, trigger: fromForm ? 'Formular' : 'Zeitplan', started_at: new Date().toISOString() } }];"""

COMBINE_CODE = """// Bericht des Agenten + Ergebnis der deterministischen Prüfung (validate_report) zusammenführen
const report = $('KI-Agent: Wochenbericht').first().json.output || '';
const content = $input.first().json.content || [];
const check = typeof content[0]?.text === 'object' ? content[0].text : JSON.parse(content[0]?.text || '{}');
// E-Mail-Fassungen: Text ohne Markdown-Sternchen, HTML mit fetten Überschriften (Inhalt wird escaped)
const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const report_text = report.replace(/\\*\\*/g, '');
const report_html = report.split('\\n').map((l) => esc(l).replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>')).join('<br>\\n');
return [{ json: {
  report, report_text, report_html, ok: check.ok === true,
  problems: check.problems || ['Keine Antwort von validate_report'],
  calendar_week: check.calendar_week, expected_status: check.expected_status,
  report_ready_at: new Date().toISOString(),
  ...$('Parameter').first().json,
} }];"""

LOG_CODE = """// Ein Protokolleintrag pro Lauf: Dauer (Agent vs. gesamt inkl. Warten auf Freigabe), Prüfung, Freigabe
const base = $('Bericht + Prüfung').first().json;
const approval = $('Freigabe per E-Mail').isExecuted ? $('Freigabe per E-Mail').first().json : null;
const answer = approval?.data?.approved;
const finished = new Date();
const secs = (a, b) => Math.round((new Date(b) - new Date(a)) / 100) / 10;
return [{ json: {
  as_of: base.as_of || null, trigger: base.trigger, calendar_week: base.calendar_week, status: base.expected_status,
  validation_ok: base.ok, problems: base.ok ? [] : base.problems,
  approval: !approval ? 'nicht angefragt' : answer === true ? 'freigegeben' : answer === false ? 'abgelehnt' : 'keine Antwort',
  sent_to_management: $('An Management senden').isExecuted,
  started_at: base.started_at, report_ready_at: base.report_ready_at, finished_at: finished.toISOString(),
  agent_duration_s: secs(base.started_at, base.report_ready_at),
  total_duration_s: secs(base.started_at, finished),
  report: base.report,
} }];"""


def weekly_report() -> dict:
    agent = "KI-Agent: Wochenbericht"
    nodes = [
        node("Jeden Montag 07:52", "n8n-nodes-base.scheduleTrigger", 1.2, [0, 0], {
            "rule": {"interval": [{"field": "weeks", "triggerAtDay": [1], "triggerAtHour": 7, "triggerAtMinute": 52}]},
        }),
        node("Formular: Bericht für einen Montag", "n8n-nodes-base.formTrigger", 2.2, [0, 200], {
            "formTitle": "KPI-Wochenbericht erzeugen",
            "formDescription": "Montag wählen, für den der Bericht erstellt wird (Olist-Daten: 2017-01-09 bis "
                               "2018-08-27). Leer lassen = neueste Woche.",
            "formFields": {"values": [{"fieldLabel": "Montag (as_of)", "fieldType": "date"}]},
            "options": {"respondWithOptions": {"values": {"formSubmittedText":
                        "Der Bericht wird erstellt. Die Freigabe-E-Mail erscheint in Mailpit: http://localhost:8025"}}},
        }, webhookId="5f0c7a52-8a64-4d8e-9a43-1b9d6bcd4e01"),
        node("Parameter", "n8n-nodes-base.code", 2, [240, 100], {"jsCode": PARAM_CODE}),
        node(agent, "@n8n/n8n-nodes-langchain.agent", 3.1, [480, 100], {
            "promptType": "define",
            "text": "=Erstelle die Management-Summary. as_of: {{ $json.as_of || 'leer (neueste Woche)' }}",
            "options": {"systemMessage": prompt("management_summary_de.md"), "maxIterations": 12},
        }),
        chat_model([400, 320]),
        mcp_tools([600, 320], exclude=["search_regulation"]),  # least privilege: the report needs no legal search
        node("Prüfen: validate_report", "@n8n/n8n-nodes-langchain.mcpClient", 1.1, [800, 100], {
            "endpointUrl": MCP_URL, "serverTransport": "httpStreamable",
            "tool": {"__rl": True, "mode": "id", "value": "validate_report"},
            "inputMode": "json",
            "jsonInput": "={{ JSON.stringify({ text: $json.output, as_of: $('Parameter').first().json.as_of || null }) }}",
            "options": {},
        }),
        node("Bericht + Prüfung", "n8n-nodes-base.code", 2, [1040, 100], {"jsCode": COMBINE_CODE}),
        node("Prüfung bestanden?", "n8n-nodes-base.if", 2.2, [1280, 100], {
            "conditions": {"options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                           "combinator": "and", "conditions": [{"id": "ok", "leftValue": "={{ $json.ok }}",
                                                                "rightValue": True,
                                                                "operator": {"type": "boolean", "operation": "true",
                                                                             "singleValue": True}}]},
            "options": {},
        }),
        node("Freigabe per E-Mail", "n8n-nodes-base.emailSend", 2.1, [1520, 0], {
            "operation": "sendAndWait", "fromEmail": SENDER, "toEmail": APPROVER,
            "subject": "=Freigabe: Management-Summary {{ $json.calendar_week }}",
            "message": "=Bitte den Entwurf prüfen. Die Zahlen wurden automatisch gegen die KPI-Daten geprüft "
                       "({{ $json.calendar_week }}).\n\n{{ $json.report_text }}\n\nNach Ihrem Klick erhalten Sie eine "
                       "Bestätigung per E-Mail. Es zählt nur der erste Klick.",
            "approvalOptions": {"values": {"approvalType": "double", "approveLabel": "Freigeben",
                                           "disapproveLabel": "Ablehnen"}},
            "options": {"limitWaitTime": {"values": {"limitType": "afterTimeInterval", "resumeAmount": 24,
                                                     "resumeUnit": "hours"}},
                        "appendAttribution": False},
        }, credentials=CRED_SMTP),
        node("Freigegeben?", "n8n-nodes-base.if", 2.2, [1760, 0], {
            "conditions": {"options": {"caseSensitive": True, "typeValidation": "loose", "version": 2},
                           "combinator": "and", "conditions": [{"id": "approved",
                                                                "leftValue": "={{ $json.data?.approved }}",
                                                                "rightValue": True,
                                                                "operator": {"type": "boolean", "operation": "true",
                                                                             "singleValue": True}}]},
            "options": {},
        }),
        node("An Management senden", "n8n-nodes-base.emailSend", 2.1, [2000, -80], {
            "fromEmail": SENDER, "toEmail": MANAGEMENT, "emailFormat": "both",
            "subject": "=Management-Summary {{ $('Bericht + Prüfung').first().json.calendar_week }}",
            "text": "={{ $('Bericht + Prüfung').first().json.report_text }}\n\n– Erstellt vom KPI-Agenten, "
                    "freigegeben durch die Teamleitung.",
            "html": "=<div style=\"font-family:Arial,sans-serif;font-size:14px;line-height:1.5\">"
                    "{{ $('Bericht + Prüfung').first().json.report_html }}<br><br>"
                    "<span style=\"color:#666\">– Erstellt vom KPI-Agenten, freigegeben durch die Teamleitung.</span></div>",
            "options": {"appendAttribution": False},
        }, credentials=CRED_SMTP),
        node("Hinweis an Analyse", "n8n-nodes-base.emailSend", 2.1, [1520, 240], {
            "fromEmail": SENDER, "toEmail": ANALYST, "emailFormat": "text",
            "subject": "=Bericht {{ $json.calendar_week }} nicht versendet – Prüfung fehlgeschlagen",
            "text": "=Der Entwurf des KPI-Agenten hat die Prüfung nicht bestanden und wurde nicht zur Freigabe "
                    "geschickt.\n\nProbleme:\n{{ $json.problems.join('\\n') }}\n\nEntwurf:\n{{ $json.report_text }}",
            "options": {"appendAttribution": False},
        }, credentials=CRED_SMTP),
        # the approval e-mail cannot change after sending – so the team lead gets a confirmation of the decision
        node("Bestätigung: freigegeben", "n8n-nodes-base.emailSend", 2.1, [2240, -80], {
            "fromEmail": SENDER, "toEmail": APPROVER, "emailFormat": "text",
            "subject": "=✅ Freigegeben: Management-Summary {{ $('Bericht + Prüfung').first().json.calendar_week }} "
                       "– an das Management gesendet",
            "text": "=Ihre Freigabe ist angekommen. Die Management-Summary "
                    "{{ $('Bericht + Prüfung').first().json.calendar_week }} wurde am {{ $now.toFormat('dd.MM.yyyy') }} "
                    "um {{ $now.toFormat('HH:mm') }} Uhr an {{ $('An Management senden').first().json.accepted.join(', ') }} "
                    "gesendet.\n\nWeitere Klicks in der Freigabe-E-Mail ändern nichts mehr.",
            "options": {"appendAttribution": False},
        }, credentials=CRED_SMTP),
        node("Bestätigung: nicht versendet", "n8n-nodes-base.emailSend", 2.1, [2000, 120], {
            "fromEmail": SENDER, "toEmail": APPROVER, "emailFormat": "text",
            "subject": "={{ $json.data?.approved === false ? '❌ Abgelehnt' : '⏰ Keine Antwort in 24 h' }}: "
                       "Management-Summary {{ $('Bericht + Prüfung').first().json.calendar_week }} – nicht versendet",
            "text": "={{ $json.data?.approved === false ? 'Sie haben den Entwurf abgelehnt.' : "
                    "'Die Freigabe wurde nicht innerhalb von 24 Stunden erteilt.' }} Die Management-Summary "
                    "{{ $('Bericht + Prüfung').first().json.calendar_week }} wurde nicht an das Management gesendet "
                    "({{ $now.toFormat('dd.MM.yyyy HH:mm') }} Uhr). Der Lauf ist im Protokoll vermerkt.",
            "options": {"appendAttribution": False},
        }, credentials=CRED_SMTP),
        node("Lauf protokollieren", "n8n-nodes-base.code", 2, [2480, 100], {"jsCode": LOG_CODE}),
        node("Als JSON", "n8n-nodes-base.convertToFile", 1.1, [2720, 100], {
            "operation": "toJson", "mode": "each", "options": {"format": True}}),
        node("In runs/ speichern", "n8n-nodes-base.readWriteFile", 1, [2960, 100], {
            "operation": "write",
            "fileName": "=/home/node/runs/{{ $now.toFormat('yyyy-MM-dd_HHmmss') }}_"
                        "{{ ($('Lauf protokollieren').first().json.calendar_week || 'KW').replace(/[^A-Za-z0-9]+/g, '-') }}.json",
            "options": {},
        }),
    ]
    connections = {
        "Jeden Montag 07:52": {"main": [[main_link("Parameter")]]},
        "Formular: Bericht für einen Montag": {"main": [[main_link("Parameter")]]},
        "Parameter": {"main": [[main_link(agent)]]},
        agent: {"main": [[main_link("Prüfen: validate_report")]]},
        "Prüfen: validate_report": {"main": [[main_link("Bericht + Prüfung")]]},
        "Bericht + Prüfung": {"main": [[main_link("Prüfung bestanden?")]]},
        "Prüfung bestanden?": {"main": [[main_link("Freigabe per E-Mail")], [main_link("Hinweis an Analyse")]]},
        "Freigabe per E-Mail": {"main": [[main_link("Freigegeben?")]]},
        "Freigegeben?": {"main": [[main_link("An Management senden")], [main_link("Bestätigung: nicht versendet")]]},
        "An Management senden": {"main": [[main_link("Bestätigung: freigegeben")]]},
        "Bestätigung: freigegeben": {"main": [[main_link("Lauf protokollieren")]]},
        "Bestätigung: nicht versendet": {"main": [[main_link("Lauf protokollieren")]]},
        "Hinweis an Analyse": {"main": [[main_link("Lauf protokollieren")]]},
        "Lauf protokollieren": {"main": [[main_link("Als JSON")]]},
        "Als JSON": {"main": [[main_link("In runs/ speichern")]]},
    }
    for src, conn in (ai_link("OpenAI " + MODEL, agent, "ai_languageModel"), ai_link("KPI-MCP-Server", agent, "ai_tool")):
        connections[src] = conn
    return {"id": IDS["report"], "name": "KPI-Wochenbericht (Agent + Freigabe)", "nodes": nodes,
            "connections": connections, "active": False,
            "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin", "errorWorkflow": IDS["error"],
                         "saveManualExecutions": True},
            "tags": []}


# ------------------------------------------------------------------------------------------- 2 · chat
def chat() -> dict:
    agent = "KI-Agent: Rückfragen"
    nodes = [
        node("Chat", "@n8n/n8n-nodes-langchain.chatTrigger", 1.3, [0, 0], {
            "public": True, "mode": "hostedChat",
            "initialMessages": "Hallo! Ich beantworte Rückfragen zum KPI-Wochenbericht – z. B. „Warum war KW 9/2018 "
                               "rot?“ oder „Was sagt die DSGVO zu automatisierten Entscheidungen?“",
            "options": {"title": "KPI-Assistent", "subtitle": "Rückfragen zur Lieferperformance",
                        "inputPlaceholder": "Frage eingeben …", "loadPreviousSession": "memory"}},
             webhookId="0b7e5d2c-3f4a-4b8e-9c1d-6a2f8e4b7c90"),
        node(agent, "@n8n/n8n-nodes-langchain.agent", 3.1, [260, 0], {
            "promptType": "auto", "text": "={{ $json.chatInput }}",
            "options": {"systemMessage": prompt("chat_assistant_de.md"), "maxIterations": 8},
        }),
        chat_model([160, 220]),
        node("Gesprächsverlauf", "@n8n/n8n-nodes-langchain.memoryBufferWindow", 1.3, [320, 220],
             {"contextWindowLength": 10}),
        mcp_tools([480, 220]),
    ]
    connections = {"Chat": {"main": [[main_link(agent)]]}}
    for src, kind in (("OpenAI " + MODEL, "ai_languageModel"), ("Gesprächsverlauf", "ai_memory"),
                      ("KPI-MCP-Server", "ai_tool")):
        connections[src] = ai_link(src, agent, kind)[1]
    return {"id": IDS["chat"], "name": "KPI-Chat (Rückfragen + DSGVO/KI-VO)", "nodes": nodes,
            "connections": connections, "active": False,
            "settings": {"executionOrder": "v1", "timezone": "Europe/Berlin", "errorWorkflow": IDS["error"]},
            "tags": []}


# ------------------------------------------------------------------------------------------- 3 · error handler
def error_handler() -> dict:
    nodes = [
        node("Fehler in einem Workflow", "n8n-nodes-base.errorTrigger", 1, [0, 0], {}),
        node("Fehler melden", "n8n-nodes-base.emailSend", 2.1, [260, 0], {
            "fromEmail": SENDER, "toEmail": ANALYST, "emailFormat": "text",
            "subject": "=Fehler im Workflow „{{ $json.workflow.name }}“",
            "text": "=Knoten: {{ $json.execution.lastNodeExecuted }}\nFehler: {{ $json.execution.error.message }}\n"
                    "{{ $json.execution.error.description || '' }}\nAusführung: {{ $json.execution.url }}",
            "options": {"appendAttribution": False},
        }, credentials=CRED_SMTP),
    ]
    return {"id": IDS["error"], "name": "Fehlermeldung per E-Mail", "nodes": nodes,
            "connections": {"Fehler in einem Workflow": {"main": [[main_link("Fehler melden")]]}},
            "active": False, "settings": {"executionOrder": "v1"}, "tags": []}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for file, wf in (("01_kpi_weekly_report.json", weekly_report()), ("02_kpi_chat.json", chat()),
                     ("03_error_notification.json", error_handler())):
        (OUT / file).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{file}: {len(wf['nodes'])} nodes")


if __name__ == "__main__":
    main()
