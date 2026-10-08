"""The tools of the KPI agent as plain Python functions (tested without any MCP or LLM).

Design rules
  * read-only: no tool can change data; parameters are validated (no free SQL, no file paths)
  * display-ready numbers: percentages, percentage points and days are already rounded the way the report shows
    them, so the LLM never has to calculate – and `validate_report` can compare the report number by number
  * rules decide, the LLM writes: the traffic-light status comes from control-chart rules (SPC), not from the model
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import Field

from mcp_server import data
from mcp_server.spc_rules import assess

# name: (German label, unit, which direction is bad)
KPIS = {
    "supplier_otd": ("Lieferanten-Termintreue", "%", "low"),
    "customer_otd": ("Kunden-Termintreue", "%", "low"),
    "complaint_rate": ("Reklamationsquote", "%", "high"),
    "cancellation_rate": ("Stornoquote", "%", "high"),
    "avg_lead_time_days": ("Ø Lieferzeit", "Tage", "high"),
    "orders": ("Bestellungen", "Anzahl", "none"),
    "revenue_eur": ("Umsatz", "EUR", "none"),
}
KpiName = Literal["supplier_otd", "customer_otd", "complaint_rate", "cancellation_rate", "avg_lead_time_days",
                  "orders", "revenue_eur"]
AsOf = Annotated[str | None, Field(description="ISO date of the Monday to replay, e.g. 2018-03-05. "
                                               "Empty = latest complete week in the data.")]
STATUS_KPIS = ("supplier_otd", "customer_otd", "complaint_rate")  # these decide the traffic light
STATUS_ORDER = {"grün": 0, "gelb": 1, "rot": 2}
STATUS_EMOJI = {"grün": "🟢", "gelb": "🟡", "rot": "🔴"}


def _check_kpi(kpi: str) -> None:
    if kpi not in KPIS:
        raise ValueError(f"Unknown KPI '{kpi}'. Allowed: {', '.join(KPIS)}")


def _round(value: float, digits: int = 1, factor: int = 1) -> float:
    """Round half up like Excel / Power BI (Python's round() rounds 15.05 to 15.0). Decimal avoids float noise:
    0.1505 * 100 is 15.049999… as float, but exactly 15.05 here → 15.1, as in the project 1 dashboard."""
    d = Decimal(repr(float(value))) * factor
    return float(d.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)) + 0.0


def _display(kpi: str, raw: float | None) -> float | int | None:
    if raw is None or raw != raw:  # NaN
        return None
    unit = KPIS[kpi][1]
    if unit == "%":
        return _round(raw, 1, 100)
    if unit == "Tage":
        return _round(raw, 1)
    return int(_round(raw, 0))


def _change(kpi: str, cur: float | None, prev: float | None) -> tuple[float | None, str]:
    if cur is None or prev is None or cur != cur or prev != prev:
        return None, ""
    unit = KPIS[kpi][1]
    if unit == "%":
        return _round(Decimal(repr(float(cur))) - Decimal(repr(float(prev))), 1, 100), "Pp"  # + 0.0 turns -0.0 into 0.0
    if unit == "Tage":
        return _round(float(Decimal(repr(float(cur))) - Decimal(repr(float(prev)))), 1), "Tage"
    return (_round(cur / prev - 1, 1, 100), "%") if prev else (None, "")


def get_weekly_kpis(as_of: AsOf = None) -> dict:
    """KPIs of the last complete week before `as_of` and the change against the week before."""
    df = data.weekly()
    i = data.report_index(as_of)
    cur, prev = df.iloc[i], (df.iloc[i - 1] if i > 0 else None)
    kpis = {}
    for kpi, (label, unit, _) in KPIS.items():
        value = _display(kpi, cur[kpi])
        previous = _display(kpi, prev[kpi]) if prev is not None else None
        change, change_unit = _change(kpi, cur[kpi], prev[kpi] if prev is not None else None)
        kpis[kpi] = {"label": label, "value": value, "unit": unit, "previous_week": previous,
                     "change": change, "change_unit": change_unit}
    week_start = cur["week_start"]
    return {
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "calendar_week": data.calendar_week(week_start),
        "late_orders": int(cur["late_orders"]),
        "late_handovers": int(cur["late_handovers"]),
        "kpis": kpis,
    }


def check_spc(kpi: KpiName = "supplier_otd", as_of: AsOf = None, weeks: int = 26, baseline_weeks: int = 20) -> dict:
    """Control chart (I-MR, Western Electric rules) for one KPI over the last `weeks` weeks up to the report week.
    Limits come from the first `baseline_weeks` of the window; the weeks after are checked against them."""
    _check_kpi(kpi)
    if not 8 <= baseline_weeks < weeks <= 52:
        raise ValueError("Use 8 <= baseline_weeks < weeks <= 52")
    df = data.weekly()
    i = data.report_index(as_of)
    window = df.iloc[max(0, i - weeks + 1) : i + 1]
    if len(window) <= 8:
        return {"kpi": kpi, "available": False, "reason": "not enough history for control limits"}
    baseline = min(baseline_weeks, len(window) - 1)
    values = [_display(kpi, v) for v in window[kpi].astype(float)]
    result = assess(values, baseline=baseline)
    bad = KPIS[kpi][2]
    cl = result["limits"]["cl"]
    violations = []
    for v in result["violations"]:
        side = "oben" if v["value"] > cl else "unten"
        direction = ("ungünstig" if (bad == "low" and side == "unten") or (bad == "high" and side == "oben")
                     else "neutral" if bad == "none" else "günstig")
        ws = window["week_start"].iloc[v["index"]]
        violations.append({"calendar_week": data.calendar_week(ws), "week_start": ws.isoformat(),
                           "value": v["value"], "rule": v["rule"], "description": v["description"],
                           "side": side, "direction": direction, "weeks_ago": len(window) - 1 - v["index"]})
    limits = {k: _round(val, 1) for k, val in result["limits"].items() if k != "sigma"}
    return {"kpi": kpi, "label": KPIS[kpi][0], "unit": KPIS[kpi][1], "available": True,
            "baseline_weeks": baseline, "limits": limits, "latest_value": values[-1], "violations": violations}


def _kpi_status(spc: dict) -> str:
    if not spc.get("available"):
        return "grün"
    bad = [v for v in spc["violations"] if v["direction"] == "ungünstig"]
    if any(v["weeks_ago"] == 0 for v in bad):
        return "rot"
    if any(v["weeks_ago"] <= 3 for v in bad):
        return "gelb"
    return "grün"


def assess_week(as_of: AsOf = None) -> dict:
    """Traffic light for the report week, decided by SPC rules on supplier OTD, customer OTD and complaint rate:
    rot = unfavourable signal in the report week, gelb = in the 3 weeks before, grün = none."""
    week = get_weekly_kpis(as_of)
    checks = {}
    for kpi in STATUS_KPIS:
        spc = check_spc(kpi, as_of)
        signals: dict[str, dict] = {}  # one entry per week, all rules that fired in that week
        for v in spc.get("violations", []):
            if v["weeks_ago"] <= 3:
                s = signals.setdefault(v["calendar_week"], {"calendar_week": v["calendar_week"], "value": v["value"],
                                                            "direction": v["direction"], "rules": []})
                s["rules"].append(v["rule"])
        checks[kpi] = {"label": KPIS[kpi][0], "value": spc.get("latest_value"), "status": _kpi_status(spc),
                       "limits": spc.get("limits"), "signals": list(signals.values())}
    overall = max((c["status"] for c in checks.values()), key=STATUS_ORDER.get)
    return {"calendar_week": week["calendar_week"], "status": overall, "status_emoji": STATUS_EMOJI[overall],
            "signals_summary": _signals_summary(checks), "checks": checks}


def _signals_summary(checks: dict) -> str:
    """The SPC line of the report, written by code: rule numbers 1–4 cannot be checked like other numbers, so the
    agent copies this text verbatim and validate_report looks for it."""
    parts = []
    for check in checks.values():
        weeks = [f"{s['calendar_week']} (Regel{'n' if len(s['rules']) > 1 else ''} {', '.join(map(str, s['rules']))})"
                 for s in check["signals"] if s["direction"] == "ungünstig"]
        if weeks:
            parts.append(f"{check['label']}: {', '.join(weeks)}")
    return "; ".join(parts) or "keine"


def get_late_suppliers(as_of: AsOf = None, top_n: int = 5, min_items: int = 20) -> dict:
    """Suppliers with the lowest on-time handover rate in the last complete month before the report week ends."""
    if not 1 <= top_n <= 20 or not 1 <= min_items <= 500:
        raise ValueError("Use 1 <= top_n <= 20 and 1 <= min_items <= 500")
    week_end = date.fromisoformat(get_weekly_kpis(as_of)["week_end"])
    df = data.scorecard()
    month_end = lambda m: (m.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)  # noqa: E731
    complete = sorted({m for m in df["month"] if month_end(m) <= week_end})
    if not complete:
        return {"month": None, "suppliers": []}
    month = complete[-1]
    rows = df[(df["month"] == month) & (df["order_items"] >= min_items)]
    rows = rows.sort_values(["supplier_otd", "late_handovers"], ascending=[True, False]).head(top_n)
    return {
        "month": month.strftime("%Y-%m"),
        "suppliers_considered": int(((df["month"] == month) & (df["order_items"] >= min_items)).sum()),
        "suppliers": [
            {"supplier": r.seller_id[:8], "items": int(r.order_items), "otd_pct": _round(r.supplier_otd, 1, 100),
             "late_handovers": int(r.late_handovers), "complaint_pct": _round(r.complaint_rate, 1, 100),
             "revenue_eur": int(_round(r.revenue_eur, 0))}
            for r in rows.itertuples()
        ],
    }


def get_dq_status() -> dict:
    """Latest data-quality run of the pipeline (project 2) and the change against the run before."""
    df = data.dq_results()
    if df is None or df.empty:
        return {"available": False, "reason": "dq_results.parquet not found"}
    runs = sorted(df["run_ts"].unique())
    last = df[df["run_ts"] == runs[-1]].sort_values("check_id")
    prev = df[df["run_ts"] == runs[-2]].set_index("check_id") if len(runs) > 1 else None
    failed, total = int(last["failed_rows"].sum()), int(last["total_rows"].sum())
    checks = []
    for r in last.itertuples():
        change = int(r.failed_rows - prev.loc[r.check_id, "failed_rows"]) if prev is not None else None
        checks.append({"check": r.check_name, "table": r.table_name, "failed_rows": int(r.failed_rows),
                       "change_vs_previous_run": change})
    worse = [c for c in checks if (c["change_vs_previous_run"] or 0) > 0]
    return {
        "available": True,
        "run": str(runs[-1])[:19],
        "runs_in_history": len(runs),
        "checks": len(checks),
        "failed_rows": failed,
        "dq_score_pct": _round(1 - failed / total, 2, 100),
        "trend": "verschlechtert" if worse else ("unverändert" if prev is not None else "erster Lauf"),
        "worse_checks": worse,
        "details": checks,
    }


# ---------------------------------------------------------------- report validation (deterministic guardrail)
DATE_PATTERNS = [r"\b\d{4}-\d{2}-\d{2}\b", r"\b\d{1,2}\.\d{1,2}\.\d{2,4}\b"]
# "+" / "-" / "−" (U+2212) directly before a number, after a space, "(" or line start. Not "–": "Regeln 1–3" is a range.
SIGN = re.compile(r"(?:^|(?<=[\s(]))([+\-\u2212])$")
NUMBER = re.compile(r"(?<![\w.,])\d{1,3}(?:[.\s ]\d{3})+(?:,\d+)?(?![\d])|(?<![\w.,])\d+(?:[.,]\d+)?(?![\d])")


def _number_candidates(token: str) -> set[float]:
    """'106.138' may be German thousands or an English decimal – accept both readings."""
    t = token.replace(" ", " ")
    out = set()
    german = t.replace(" ", "").replace(".", "").replace(",", ".")
    english = t.replace(" ", "").replace(",", "")
    for s in (german, english, t.replace(",", ".")):
        try:
            out.add(round(float(s), 2))
        except ValueError:
            pass
    return out


def _collect(obj, out: set[float], signed: set[float] | None = None) -> None:
    """All numbers of the tool results: `out` without sign, `signed` with sign (for "+1,3" / "−7,3" in the text)."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, int | float):
        out.add(round(abs(float(obj)), 2))
        if signed is not None:
            signed.add(round(float(obj), 2))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect(v, out, signed)
    elif isinstance(obj, list):
        for v in obj:
            _collect(v, out, signed)
    elif isinstance(obj, str):
        for n in re.findall(r"\d+", obj):
            out.add(float(n))


def validate_report(text: str, as_of: AsOf = None) -> dict:
    """Checks a drafted report before anyone sees it: every number must come from a tool result for this week,
    and the traffic light must match the SPC decision. Returns ok=False with the reasons otherwise."""
    status = assess_week(as_of)
    sources = [get_weekly_kpis(as_of), status, get_late_suppliers(as_of), get_dq_status()]
    allowed: set[float] = set(range(0, 11))  # small counts: "2 Schritte", "Regel 1", "8 Punkte"
    signed: set[float] = set()
    _collect(sources, allowed, signed)
    clean = text
    for pattern in DATE_PATTERNS:
        clean = re.sub(pattern, " ", clean)
    unsupported, wrong_sign, checked = [], [], 0
    for match in NUMBER.finditer(clean):
        token = match.group(0)
        checked += 1
        if not any(abs(c - a) < 0.051 for c in _number_candidates(token) for a in allowed):
            unsupported.append(token)
            continue
        sign = SIGN.search(clean[: match.start()])  # "(−7,3 Pp)": a sign right before the number …
        if sign:  # … must match the direction in the data – "+7,3" for a drop of 7.3 is wrong
            factor = -1 if sign.group(1) in "-−" else 1
            if not any(abs(factor * c - a) < 0.051 for c in _number_candidates(token) for a in signed):
                wrong_sign.append(sign.group(1) + token)
    mixed_up = []  # "Kunden-Termintreue 88,6 %": the number right after a KPI name must be that KPI's value
    for kpi in sources[0]["kpis"].values():
        for found_value in re.findall(re.escape(kpi["label"]) + r":?\s+(\d[\d.,]*\d|\d)(?![\d/])", clean):
            if kpi["value"] is not None and not any(abs(c - abs(kpi["value"])) < 0.051
                                                    for c in _number_candidates(found_value)):
                mixed_up.append(f"{kpi['label']} {found_value} (richtig: {kpi['value']})")
    expected = status["status"]
    found = [s for s, e in STATUS_EMOJI.items() if e in text]
    problems = []
    if mixed_up:
        problems.append(f"Wert gehört nicht zu dieser Kennzahl: {'; '.join(mixed_up)}")
    if " ".join(status["signals_summary"].split()) not in " ".join(text.split()):
        problems.append(f"SPC-Zeile muss wörtlich lauten: {status['signals_summary']}")
    if unsupported:
        problems.append(f"Zahlen ohne Beleg aus den Werkzeugen: {', '.join(unsupported)}")
    if wrong_sign:
        problems.append(f"Falsches Vorzeichen (Richtung der Veränderung): {', '.join(wrong_sign)}")
    if found != [expected]:
        problems.append(f"Ampel muss {STATUS_EMOJI[expected]} ({expected}) sein, gefunden: "
                        f"{' '.join(STATUS_EMOJI[s] for s in found) or 'keine'}")
    if status["calendar_week"] not in text:
        problems.append(f"Kalenderwoche {status['calendar_week']} fehlt")
    return {"ok": not problems, "problems": problems, "numbers_checked": checked,
            "expected_status": expected, "calendar_week": status["calendar_week"]}


# ---------------------------------------------------------------- link to project 3 (EU AI Act & GDPR assistant)
def search_regulation(question: str, k: int = 3) -> dict:
    """Finds the relevant articles of the EU AI Act / GDPR via the search API of project 3 (no LLM involved).
    Returns article label, title, EUR-Lex link and an excerpt – or available=False if that API is not running."""
    import httpx

    if not 3 <= len(question) <= 500 or not 1 <= k <= 5:
        raise ValueError("question: 3–500 characters, k: 1–5")
    url = os.getenv("REGULATION_API_URL", "http://localhost:8000").rstrip("/") + "/search"
    try:
        resp = httpx.post(url, json={"question": question, "k": k}, timeout=60)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"available": False, "message": f"Regulation API not reachable ({exc.__class__.__name__}). "
                "Start project 3: uvicorn src.api:app"}
    body = resp.json()
    return {"available": True, "lang": body.get("lang"), "articles": [
        {"label": s["label"], "title": s["title"], "url": s["url"], "excerpt": s["excerpt"][:400]}
        for s in body.get("sources", [])
    ]}
