# Expected results per step

Values marked ✔ are fixed (data, rules, code) and must match exactly. Values marked ≈ depend on the model or the PC.
The wording of the report changes from run to run – the **numbers, the traffic light and the calendar week must not**.

## 1 · Tests (no network, no API key)
```
pip install -r requirements-dev.txt
ruff check .   → All checks passed!
pytest -q      → 46 passed ✔
```

## 2 · Start – `docker compose up -d --build`
`docker compose ps` shows three running containers: `kpi-agent-n8n-1`, `kpi-agent-mcp-1`, `kpi-agent-mailpit-1`.
If it stops with `set N8N_ENCRYPTION_KEY in .env`, the `.env` file is missing or empty.

## 3 · Setup – `docker compose exec n8n sh /home/node/setup/setup.sh`
```
Successfully imported 2 credentials.
Importing 3 workflows...
Successfully imported 3 workflows.
Publishing workflow with ID: kpiWeeklyReport1 (current version)
Publishing workflow with ID: kpiChatAssist001 (current version)
Publishing workflow with ID: kpiErrorNotify01 (current version)
Done. Restart n8n so the published workflows are active:  docker compose restart n8n
```
(plus a few "Note: … restart n8n" lines). After `docker compose restart n8n` and creating the owner account,
http://localhost:5678 lists three workflows, the first two marked as published/active.

## 4 · Report for Monday 2018-03-05 (form)
Approval e-mail in Mailpit (http://localhost:8025) to `teamleitung@example.com`, within ≈ 10–40 s:
- Subject ✔ `Freigabe: Management-Summary KW 9/2018`
- Status ✔ `🔴 Handlungsbedarf`
- Numbers ✔ in German format (if the model mentions them): Lieferanten-Termintreue **88,6 %** (−0,7 Pp), Kunden-Termintreue **73,7 %**
  (−7,3 Pp), Reklamationsquote **28,5 %**, Ø Lieferzeit **19,0 Tage**, **1.903** Bestellungen
- SPC line ✔ exactly (written by code, copied by the model): `Lieferanten-Termintreue: KW 6/2018 (Regel 1);
  Kunden-Termintreue: KW 7/2018 (Regel 1), KW 8/2018 (Regeln 1, 2), KW 9/2018 (Regeln 1, 2, 3); Reklamationsquote:
  KW 8/2018 (Regeln 1, 2, 3), KW 9/2018 (Regeln 1, 2, 3)`
- Suppliers ✔ (month 2018-02): `8e6d7754` (13,0 % Termintreue), `88460e8e` (15,1 %), `710e3548` (52,9 %)
- Data quality ✔ 99,62 % (4.542 Befunde), unverändert
- Two buttons **Ablehnen** / **Freigeben**, no "n8n" footer

Click **Freigeben** (in the browser) → "Got it, thanks"-page, then a second e-mail to `management@example.com`
`Management-Summary KW 9/2018` with bold headings, and a confirmation to `teamleitung@example.com`:
`✅ Freigegeben: Management-Summary KW 9/2018 – an das Management gesendet`. Clicking again shows "No action required". In `runs/` a new file `…_KW-9-2018.json`:
`"status": "rot"`, `"validation_ok": true`, `"approval": "freigegeben"`, `"sent_to_management": true`,
`agent_duration_s` ≈ 10–40.

Same form again, click **Ablehnen** → confirmation `❌ Abgelehnt: … – nicht versendet`, no management e-mail,
run log `"approval": "abgelehnt"`.

## 5 · Other weeks (form)
| Monday | Week ✔ | Traffic light ✔ |
|---|---|---|
| empty (latest) | KW 34/2018 | 🟡 beobachten |
| 2018-06-04 | KW 22/2018 | 🟢 stabil (no supplier section) |
| 2017-11-27 | KW 47/2017 | 🔴 Handlungsbedarf (Black Friday week) |

## 6 · Chat – http://localhost:5678/webhook/0b7e5d2c-3f4a-4b8e-9c1d-6a2f8e4b7c90/chat
- "Warum war KW 9/2018 rot?" → answer names the SPC signals of customer on-time delivery (73,7 %, rules 1–3)
- "Was sagt die DSGVO zu automatisierten Entscheidungen?"
  - project 3 running → answer cites **DSGVO Art. 22** with EUR-Lex link and ends with
    "Hinweis: Dies ist keine Rechtsberatung."
  - project 3 not running → the assistant says the legal search is not available (no invented article)

## 7 · Error workflow
`docker compose stop mcp`, then submit the form → e-mail to `analyse@example.com`
`Fehler im Workflow „KPI-Wochenbericht (Agent + Freigabe)“` with node, message and link to the execution.
`docker compose start mcp` afterwards.

## 8 · Agent evaluation – `python scripts/evaluate_agent.py`
12 lines (one per Monday) and `docs/agent_eval_results.md`, `docs/agent_eval_reports.md`, `docs/agent_eval_per_week.csv`.
Weeks and traffic lights ✔: 2017-04-24 KW 16/2017 rot · 2017-11-27 KW 47/2017 rot · 2018-03-05 KW 9/2018 rot ·
2018-08-13 KW 32/2018 rot · 2017-05-15 KW 19/2017 gelb · 2017-10-02 KW 39/2017 gelb · 2018-04-16 KW 15/2018 gelb ·
2018-08-27 KW 34/2018 gelb · 2017-02-13 KW 6/2017 grün · 2017-07-10 KW 27/2017 grün · 2018-06-04 KW 22/2018 grün ·
2018-07-16 KW 28/2018 grün.
Check passed, words, seconds and cost ≈ – these are the results of the evaluation.
