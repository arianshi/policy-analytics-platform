"""Export an analysis result as Markdown, CSV (facts) or standalone HTML."""

from __future__ import annotations

import csv
import html
import io
import re
from typing import Any


def to_markdown(result: dict[str, Any]) -> str:
    r = result["report"]
    cites = {c["id"]: c for c in r["citations"]}
    facts = {f["id"]: f for f in result["facts"]}

    def refs(ids: list[str]) -> str:
        sids = sorted({s for i in ids for s in facts.get(i, {}).get("citations", [])}, key=lambda s: int(s[1:]))
        return f" [{', '.join(sids)}]" if sids else ""

    lines = [f"# {r['title']}", "", f"*Query:* {r['query']}", f"*Generated:* {r['generated_at']} - narrative: {r['narrative_source']}", ""]
    lines += ["## Executive summary", "", r["executive_summary"]["text"] + refs(r["executive_summary"]["fact_ids"]), ""]
    if r["findings"]:
        lines += ["## Key findings", ""]
        for f in r["findings"]:
            lines += [f"### {f['title']}", "", f["text"] + refs(f["fact_ids"]), ""]
    if r["implications"]:
        lines += ["## Implications", ""] + [f"- {i['text']}{refs(i['fact_ids'])}" for i in r["implications"]] + [""]
    if r["recommendations"]:
        lines += ["## Recommendations", ""] + [f"- {i['text']}{refs(i['fact_ids'])}" for i in r["recommendations"]] + [""]
    if r["assumptions"]:
        lines += ["## Assumptions", ""] + [f"- {a}" for a in r["assumptions"]] + [""]
    lines += ["## Limitations", ""] + [f"- {x}" for x in r["limitations"]] + [""]
    lines += ["## Data quality", ""]
    for d in result["data_quality"]:
        lines.append(f"- **{d['dataset_key']}** - score {d['score']}/100, completeness {d['completeness'] * 100:.1f}%, {d['rows_in']} -> {d['rows_out']} rows"
                     + (f"; {len([i for i in d['issues'] if i['severity'] != 'info'])} warnings" if any(i['severity'] != 'info' for i in d['issues']) else ""))
    v = r["verification"]
    lines += ["", "## Verification", "", f"Hallucination check: {'passed' if v.get('passed') else 'FAILED'} - {v.get('numbers_checked', 0)} numbers checked, "
              f"grounding score {v.get('grounding_score', 0)}; {v.get('error_count', 0)} errors, {v.get('warning_count', 0)} warnings.", ""]
    lines += ["## Methodology", ""] + [f"- {m}" for m in r["methodology"]] + ["", "## Sources", ""]
    for c in cites.values():
        flag = " **[MOCK]**" if c["is_mock"] else ""
        deg = " (fallback snapshot)" if c["degraded"] else ""
        lines.append(f"- **{c['id']}** {c['source']} - *{c['title']}*{flag}. {c['url']} (retrieved {c['retrieved_at'][:10]}, {c['mode']}{deg}; {c['licence']})")
    return "\n".join(lines) + "\n"


def facts_to_csv(result: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["fact_id", "kind", "sector", "metric", "period", "statement", "sources", "mock"])
    for f in result["facts"]:
        w.writerow([f["id"], f["kind"], f["sector"] or "", f["metric"] or "", "-".join(map(str, f["period"])) if f["period"] else "", f["statement"],
                    ";".join(f["sources"]), f["mock"]])
    return buf.getvalue()


def to_html(result: dict[str, Any]) -> str:
    md = to_markdown(result)
    body = []
    for line in md.splitlines():
        e = html.escape(line)
        e = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e)
        e = re.sub(r"\*(.+?)\*", r"<em>\1</em>", e)
        if line.startswith("### "):
            body.append(f"<h3>{e[4:]}</h3>")
        elif line.startswith("## "):
            body.append(f"<h2>{e[3:]}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{e[2:]}</h1>")
        elif line.startswith("- "):
            body.append(f"<li>{e[2:]}</li>")
        elif line.strip():
            body.append(f"<p>{e}</p>")
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Analysis report</title>"
            "<style>body{font:16px/1.55 system-ui;max-width:780px;margin:2rem auto;padding:0 1rem;color:#1b1f24}h1,h2,h3{line-height:1.2}"
            "li{margin:.25rem 0}</style><body>" + "\n".join(body) + "</body></html>")
