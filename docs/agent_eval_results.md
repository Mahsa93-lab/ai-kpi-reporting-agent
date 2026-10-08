# Agent evaluation – 12 weekly reports

Model `gpt-6-luna`, reasoning effort `low`, prompt `prompts/management_summary_de.md`, MCP tools as in the n8n workflow (without `search_regulation`). Run: 2026-10-08 11:57.

| Metric | Result |
|---|---|
| Reports passing the deterministic check (`validate_report`) | **100%** (12 weeks) |
| … already with the first draft | 100% |
| Agent checked its own draft (`validate_report` called) | 100% |
| All required sections present | 100% |
| Within 180 words | 100% (median 78) |
| Tool calls per report | median 5 |
| Duration per report | median 13.4 s |
| Cost per report | $0.0013 (≈ $0.07 per year) |

| Monday | Week | Status | Check | 1st draft | Words | Tools | Tokens in/out | s | Problems |
|---|---|---|---|---|---|---|---|---|---|
| 2017-04-24 | KW 16/2017 | rot | ✅ | ✅ | 74 | 5 | 11148/672 | 13.6 |  |
| 2017-11-27 | KW 47/2017 | rot | ✅ | ✅ | 125 | 5 | 12334/952 | 17.3 |  |
| 2018-03-05 | KW 9/2018 | rot | ✅ | ✅ | 117 | 5 | 11731/889 | 12.4 |  |
| 2018-08-13 | KW 32/2018 | rot | ✅ | ✅ | 84 | 5 | 11721/715 | 15.6 |  |
| 2017-05-15 | KW 19/2017 | gelb | ✅ | ✅ | 77 | 5 | 11139/653 | 13.8 |  |
| 2017-10-02 | KW 39/2017 | gelb | ✅ | ✅ | 80 | 5 | 10880/720 | 11.1 |  |
| 2018-04-16 | KW 15/2018 | gelb | ✅ | ✅ | 78 | 5 | 11200/707 | 15.4 |  |
| 2018-08-27 | KW 34/2018 | gelb | ✅ | ✅ | 80 | 5 | 11078/672 | 12.9 |  |
| 2017-02-13 | KW 6/2017 | grün | ✅ | ✅ | 53 | 4 | 6797/448 | 13.4 |  |
| 2017-07-10 | KW 27/2017 | grün | ✅ | ✅ | 54 | 4 | 7724/413 | 7.3 |  |
| 2018-06-04 | KW 22/2018 | grün | ✅ | ✅ | 54 | 4 | 8138/455 | 9.6 |  |
| 2018-07-16 | KW 28/2018 | grün | ✅ | ✅ | 52 | 4 | 8068/462 | 9.2 |  |
