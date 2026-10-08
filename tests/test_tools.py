"""Tool tests on the real gold tables of project 2 (data/*.parquet) – no MCP, no LLM, no network."""
import pytest

from mcp_server import data
from mcp_server import tools as t

KW9 = "2018-03-05"  # Monday after the worst week for customer OTD (KW 9/2018)


def test_weekly_kpis_are_display_ready():
    w = t.get_weekly_kpis(KW9)
    assert (w["calendar_week"], w["week_start"], w["week_end"]) == ("KW 9/2018", "2018-02-26", "2018-03-04")
    otd = w["kpis"]["customer_otd"]
    assert (otd["value"], otd["previous_week"], otd["change"], otd["change_unit"]) == (73.7, 80.9, -7.3, "Pp")
    assert w["kpis"]["orders"]["value"] == 1903 and w["kpis"]["orders"]["change_unit"] == "%"
    assert w["kpis"]["avg_lead_time_days"]["change_unit"] == "Tage"


def test_report_week_skips_cut_off_weeks_at_the_end():
    # the last Olist week has only a few orders and must not be reported (or trigger alarms)
    assert t.get_weekly_kpis()["calendar_week"] == "KW 34/2018"
    assert data.weekly()["week_start"].iloc[-1].isoformat() == "2018-08-27"


def test_as_of_before_the_data_is_rejected():
    with pytest.raises(ValueError, match="No complete week"):
        t.get_weekly_kpis("2016-12-01")
    with pytest.raises(ValueError):
        t.get_weekly_kpis("not-a-date")


@pytest.mark.parametrize("as_of, status", [(KW9, "rot"), ("2018-06-04", "grün"), (None, "gelb")])
def test_traffic_light_comes_from_spc_rules(as_of, status):
    assert t.assess_week(as_of)["status"] == status


def test_assess_week_explains_the_signal():
    checks = t.assess_week(KW9)["checks"]
    otd = checks["customer_otd"]
    assert otd["status"] == "rot" and otd["value"] == 73.7
    assert otd["limits"] == {"cl": 93.9, "ucl": 99.6, "lcl": 88.1}
    assert {"calendar_week": "KW 9/2018", "value": 73.7, "direction": "ungünstig", "rules": [1, 2, 3]} in otd["signals"]


def test_check_spc_validates_its_input():
    with pytest.raises(ValueError, match="Unknown KPI"):
        t.check_spc("drop table", KW9)
    with pytest.raises(ValueError):
        t.check_spc("supplier_otd", KW9, weeks=60)


def test_late_suppliers_use_last_complete_month():
    late = t.get_late_suppliers(KW9)
    assert late["month"] == "2018-02" and late["suppliers_considered"] == 81
    first = late["suppliers"][0]
    assert (first["supplier"], first["otd_pct"], first["late_handovers"]) == ("8e6d7754", 13.0, 47)
    assert len(first["supplier"]) == 8  # shortened id, as in the Power BI report


def test_dq_status_matches_project_2():
    dq = t.get_dq_status()
    assert (dq["checks"], dq["failed_rows"], dq["dq_score_pct"], dq["trend"]) == (13, 4542, 99.62, "unverändert")


SPC_KW9 = ("Lieferanten-Termintreue: KW 6/2018 (Regel 1); Kunden-Termintreue: KW 7/2018 (Regel 1), KW 8/2018 "
           "(Regeln 1, 2), KW 9/2018 (Regeln 1, 2, 3); Reklamationsquote: KW 8/2018 (Regeln 1, 2, 3), KW 9/2018 "
           "(Regeln 1, 2, 3)")


def test_assess_week_writes_the_spc_line():
    assert t.assess_week(KW9)["signals_summary"] == SPC_KW9
    assert t.assess_week("2018-06-04")["signals_summary"] == "keine"


def good_report() -> str:
    return (
        "**KW 9/2018 – Lieferperformance**\n"
        "**Status:** 🔴 Handlungsbedarf\n"
        "Kunden-Termintreue 73,7 % (−7,3 Pp), Lieferanten-Termintreue 88,6 %, Reklamationsquote 28,5 %, "
        "1.903 Bestellungen, Ø Lieferzeit 19,0 Tage.\n"
        "Auffälligkeiten (SPC): " + SPC_KW9 + "\n"
        "Regel 1, 2 und 3 in KW 9/2018 (untere Grenze 88,1 %).\n"
        "Lieferant 8e6d7754: 13,0 % Termintreue, 47 verspätete Übergaben.\n"
        "Datenqualität: 99,62 %, 4.542 Befunde, unverändert."
    )


def test_validate_report_accepts_numbers_from_the_tools():
    result = t.validate_report(good_report(), KW9)
    assert result["ok"], result["problems"]
    assert result["numbers_checked"] >= 10


def test_validate_report_rejects_invented_numbers():
    result = t.validate_report(good_report().replace("73,7 %", "71,2 %"), KW9)
    assert not result["ok"] and "71,2" in result["problems"][0]


@pytest.mark.parametrize("change", ["−7,3", "-7,3", "7,3"])
def test_validate_report_accepts_correct_or_missing_sign(change):
    result = t.validate_report(good_report().replace("−7,3", change).replace("Regel 1, 2 und 3", "Regeln 1–3"), KW9)
    assert result["ok"], result["problems"]  # "1–3" is a range, not a negative number


def test_validate_report_rejects_flipped_sign():
    result = t.validate_report(good_report().replace("(−7,3 Pp)", "(+7,3 Pp)"), KW9)
    assert not result["ok"] and result["problems"] == ["Falsches Vorzeichen (Richtung der Veränderung): +7,3"]


def test_validate_report_rejects_changed_spc_line():
    # found in the first real run: the model gave KW 8/2018 the rules of another KPI (1, 2, 3 instead of 1, 2)
    wrong = good_report().replace("KW 8/2018 (Regeln 1, 2), KW 9", "KW 8/2018 (Regeln 1, 2, 3), KW 9")
    result = t.validate_report(wrong, KW9)
    assert not result["ok"] and result["problems"][0].startswith("SPC-Zeile muss wörtlich lauten:")


def test_validate_report_rejects_value_of_another_kpi():
    swapped = good_report().replace("Kunden-Termintreue 73,7 %", "Kunden-Termintreue 88,6 %")
    result = t.validate_report(swapped, KW9)
    assert result["problems"] == ["Wert gehört nicht zu dieser Kennzahl: Kunden-Termintreue 88,6 (richtig: 73.7)"]


def test_validate_report_rejects_wrong_traffic_light_and_missing_week():
    result = t.validate_report(good_report().replace("🔴", "🟢").replace("KW 9/2018", "KW 9"), KW9)
    assert not result["ok"]
    assert any("Ampel muss 🔴" in p for p in result["problems"])
    assert any("Kalenderwoche KW 9/2018 fehlt" in p for p in result["problems"])


def test_search_regulation_uses_project_3_api(monkeypatch):
    import httpx

    def fake_post(url, json, timeout):
        assert url.endswith("/search") and json == {"question": "Automatisierte Sperre von Lieferanten", "k": 2}
        body = {"lang": "de", "sources": [{"label": "DSGVO Art. 22", "title": "Automatisierte Entscheidungen",
                                           "url": "https://eur-lex.europa.eu/x#art_22", "excerpt": "x" * 900}]}
        return httpx.Response(200, json=body, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    r = t.search_regulation("Automatisierte Sperre von Lieferanten", k=2)
    assert r["available"] and r["articles"][0]["label"] == "DSGVO Art. 22"
    assert len(r["articles"][0]["excerpt"]) == 400


def test_search_regulation_reports_when_project_3_is_down(monkeypatch):
    monkeypatch.setenv("REGULATION_API_URL", "http://127.0.0.1:9")  # nothing listens there
    r = t.search_regulation("Was regelt Artikel 22 DSGVO?")
    assert r["available"] is False and "not reachable" in r["message"]


def test_rounding_matches_power_bi_dashboard_of_project_1():
    # found in the manual cross-check: the gold table stores 0.1505 → Power BI shows 15,1 %, Python's round() gave 15.0
    assert t._round(0.1505, 1, 100) == 15.1 and t._round(-0.0725, 1, 100) == -7.3
    suppliers = t.get_late_suppliers(KW9, top_n=3)["suppliers"]
    assert [(s["supplier"], s["otd_pct"]) for s in suppliers] == [("8e6d7754", 13.0), ("88460e8e", 15.1),
                                                                  ("710e3548", 52.9)]
