"""Build the offline HTML study guide. Optional authoring dependency: markdown."""

from html import escape
from pathlib import Path
import re
import textwrap

import markdown


ROOT = Path(__file__).resolve().parent
DIAGRAMS = [
    ("The search pipeline", ["User goal", "Resolve intent", "Search hypotheses", "Merge and deduplicate", "Inspect sources", "Evaluate requirements", "Explain decision"]),
    ("Resolve intent before retrieval", ["Ambiguous request", "Use surrounding context", "Identify the intended role", "Ask only if material", "Search the chosen function"]),
    ("Follow connections to close a gap", ["Promising result", "Read primary evidence", "Identify a specific gap", "Follow a useful connection", "Inspect the alternative", "Update the comparison"]),
    ("Eligibility before ranking", ["Candidate and criteria", "Inspect must-have evidence", "Failure: reject", "Missing or conflict: provisional", "Supported: compare"]),
    ("The adaptive investigation loop", ["Search brief", "Retrieve candidates", "Inspect evidence", "Update criterion matrix", "Could a gap change the choice?", "Yes: targeted follow-up", "No: explain the decision"]),
    ("Two independently usable skills", ["Host agent and its tools", "Search With Evidence", "Intent and investigation", "Optional local ranking", "Find Before Build", "GitHub search and inspection"]),
]


def flow_svg(number: int, title: str, labels: list[str]) -> str:
    columns = 3
    width, cell_w, cell_h, gap_x, gap_y = 800, 236, 80, 24, 43
    rows = (len(labels) + columns - 1) // columns
    height = 60 + rows * (cell_h + gap_y)
    positions = []
    for index in range(len(labels)):
        row, column = divmod(index, columns)
        if row % 2:
            column = columns - 1 - column
        positions.append((22 + column * (cell_w + gap_x), 46 + row * (cell_h + gap_y)))
    content = [f'<svg class="diagram" role="img" aria-label="{escape(title)}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">',
               f'<title>{escape(title)}</title><defs><marker id="arrow-{number}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#64748b"/></marker></defs>',
               f'<text x="22" y="24" font-family="Arial,sans-serif" font-size="17" fill="#334155">{escape(title)}</text>']
    for index, ((x1, y1), (x2, y2)) in enumerate(zip(positions, positions[1:])):
        if y1 == y2:
            start = x1 + cell_w if x2 > x1 else x1
            end = x2 - 5 if x2 > x1 else x2 + cell_w + 5
            coords = f'x1="{start}" y1="{y1 + cell_h / 2}" x2="{end}" y2="{y2 + cell_h / 2}"'
        else:
            coords = f'x1="{x1 + cell_w / 2}" y1="{y1 + cell_h}" x2="{x2 + cell_w / 2}" y2="{y2 - 5}"'
        content.append(f'<line {coords} stroke="#64748b" stroke-width="2" marker-end="url(#arrow-{number})"/>')
    for index, (label, (x, y)) in enumerate(zip(labels, positions)):
        fill = "#e0f2fe" if index == 0 else "#f0fdfa" if index == len(labels) - 1 else "#f8fafc"
        content.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" rx="12" fill="{fill}" stroke="#94a3b8"/>')
        lines = textwrap.wrap(label, 24)
        first_y = y + cell_h / 2 - (len(lines) - 1) * 11 + 5
        for line_index, line in enumerate(lines):
            content.append(f'<text x="{x + cell_w / 2}" y="{first_y + line_index * 22}" text-anchor="middle" font-family="Arial,sans-serif" font-size="17" fill="#0f172a">{escape(line)}</text>')
    content.append('</svg>')
    return "\n".join(content)


def build() -> Path:
    text = (ROOT / "SEARCH_STUDY_GUIDE.md").read_text(encoding="utf-8")
    index = 0

    def replace_diagram(match):
        nonlocal index
        title, labels = DIAGRAMS[index]
        rendered = flow_svg(index, title, labels)
        # Branching diagrams use a branching layout, not a misleading chain.
        if index in {1, 3, 4, 5}:
            rendered = branch_svg(index, title)
        index += 1
        return "\n" + rendered + "\n"

    text = re.sub(r"```mermaid\n[\s\S]*?```", replace_diagram, text)
    if index != len(DIAGRAMS):
        raise ValueError("The guide's diagrams and renderer must stay synchronized")
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    toc = re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', body)
    navigation = "".join(f'<a href="#{anchor}">{label}</a>' for anchor, label in toc)
    html = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>How an agent can search better</title><style>
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:#172033;background:#edf2f7;font:17px/1.72 system-ui,Segoe UI,Arial,sans-serif}}.layout{{max-width:1450px;margin:auto;display:grid;grid-template-columns:270px minmax(0,1fr);gap:34px;padding:34px}}nav{{position:sticky;top:25px;align-self:start;max-height:92vh;overflow:auto;padding:20px;background:#fff;border-radius:14px}}nav strong{{font-size:13px;letter-spacing:.12em;text-transform:uppercase}}nav a{{display:block;font-size:13px;line-height:1.45;margin-top:11px;color:#334155;text-decoration:none}}nav a:hover{{color:#0369a1}}main{{padding:44px 52px;background:#fff;border-radius:18px;box-shadow:0 8px 34px #16324f0b;min-width:0}}h1{{font-size:43px;line-height:1.17;letter-spacing:-.04em;margin-top:0;color:#0f172a}}h2{{font-size:27px;line-height:1.3;letter-spacing:-.02em;margin-top:58px;padding-top:15px;border-top:2px solid #cbd5e1;scroll-margin-top:20px}}h3{{font-size:21px;line-height:1.4;margin-top:32px}}p{{margin:15px 0}}a{{color:#0369a1;text-underline-offset:3px;overflow-wrap:anywhere}}table{{width:100%;border-collapse:collapse;font-size:14px;line-height:1.55;margin:23px 0}}th,td{{padding:12px 13px;border:1px solid #d7e0ea;text-align:left;vertical-align:top}}th{{background:#eef6fc;color:#163b56}}tr:nth-child(even){{background:#f8fafc}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:20px;border-radius:10px;font-size:13px;line-height:1.6}}code{{font-family:Consolas,monospace;font-size:.88em}}:not(pre)>code{{background:#edf2f7;padding:2px 4px;border-radius:4px}}.diagram{{display:block;width:100%;height:auto;background:#fff;margin:26px 0;border:1px solid #d7e0ea;border-radius:12px}}li{{margin:9px 0}}.tools{{display:flex;gap:12px;margin-bottom:24px;font-size:13px}}.tools a{{padding:7px 11px;background:#edf5fc;border-radius:6px}}
@media(max-width:1000px){{.layout{{grid-template-columns:1fr;padding:15px;gap:15px}}nav{{position:static;max-height:240px}}main{{padding:28px}}h1{{font-size:35px}}table{{font-size:12px}}}}
@media print{{@page{{size:A4;margin:17mm 15mm}}body{{background:white;font:10.5pt/1.5 Arial,sans-serif}}.layout{{display:block;padding:0}}nav,.tools{{display:none}}main{{padding:0;box-shadow:none;border-radius:0}}h1{{font-size:28pt}}h2{{font-size:17pt;margin-top:25pt;break-after:avoid}}h3{{font-size:12.5pt;break-after:avoid}}p,li{{orphans:3;widows:3}}table{{font-size:8.5pt}}th,td{{padding:7px}}thead{{display:table-header-group}}tr,pre,.diagram{{break-inside:avoid}}a{{color:#075985;text-decoration:none}}}}
</style></head><body><div class="layout"><nav><strong>Study guide</strong>{navigation}</nav><main><div class="tools"><a href="SEARCH_STUDY_GUIDE.md">Markdown source</a><a href="../search-with-evidence/SKILL.md">General skill</a><a href="../find-before-build/SKILL.md">GitHub skill</a></div>{body}</main></div></body></html>'''
    output = ROOT / "SEARCH_STUDY_GUIDE.html"
    output.write_text(html, encoding="utf-8")
    return output


def branch_svg(number: int, title: str) -> str:
    if number == 1:
        nodes = [(290, 45, "Ambiguous request"), (290, 160, "Context identifies role?"), (35, 275, "Yes: state interpretation"), (545, 275, "No: ask one question"), (290, 390, "Search the chosen function")]
        edges = [(0, 1), (1, 2), (1, 3), (2, 4), (3, 4)]
    elif number == 3:
        nodes = [(290, 45, "Candidate and criteria"), (290, 160, "Inspect must-haves"), (35, 305, "Failure: rejected"), (290, 305, "Gap/conflict: provisional"), (545, 305, "Supported: compare")]
        edges = [(0, 1), (1, 2), (1, 3), (1, 4)]
    elif number == 4:
        nodes = [(35, 45, "Search brief"), (290, 45, "Retrieve candidates"), (545, 45, "Inspect evidence"), (545, 200, "Update criteria"), (290, 200, "Could a gap change it?"), (35, 200, "Yes: targeted follow-up"), (290, 365, "No: explain decision")]
        edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 1), (4, 6)]
    else:
        nodes = [(290, 45, "Host agent and tools"), (35, 180, "Search With Evidence"), (545, 180, "Find Before Build"), (35, 315, "Intent and investigation"), (545, 315, "GitHub search/inspection"), (35, 450, "Optional local ranking")]
        edges = [(0, 1), (0, 2), (1, 3), (3, 5), (2, 4)]
    height = max(y for _, y, _ in nodes) + 115
    content = [f'<svg class="diagram" role="img" aria-label="{escape(title)}" viewBox="0 0 800 {height}" xmlns="http://www.w3.org/2000/svg"><title>{escape(title)}</title>', f'<defs><marker id="branch-{number}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#64748b"/></marker></defs>']
    for a, b in edges:
        ax, ay, _ = nodes[a]
        bx, by, _ = nodes[b]
        if ay == by:
            start = ax + 220 if bx > ax else ax
            end = bx - 6 if bx > ax else bx + 226
            path = f"M {start},{ay + 35} L {end},{by + 35}"
        elif by < ay:
            middle = ay - 30
            path = f"M {ax + 110},{ay} L {ax + 110},{middle} L {bx + 110},{middle} L {bx + 110},{by + 76}"
        else:
            path = f"M {ax + 110},{ay + 70} L {bx + 110},{by - 6}"
        content.append(f'<path d="{path}" stroke="#64748b" stroke-width="2" fill="none" marker-end="url(#branch-{number})"/>')
    for x, y, label in nodes:
        content.append(f'<rect x="{x}" y="{y}" width="220" height="70" rx="12" fill="#eef6fc" stroke="#94a3b8"/>')
        lines = textwrap.wrap(label, 23)
        for index, line in enumerate(lines):
            content.append(f'<text x="{x + 110}" y="{y + 40 - (len(lines) - 1) * 10 + index * 21}" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#0f172a">{escape(line)}</text>')
    content.append('</svg>')
    return "\n".join(content)


if __name__ == "__main__":
    print(build())
