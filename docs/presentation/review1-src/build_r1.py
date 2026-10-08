"""Review 1 (Interim Evaluation) deck: 12 guideline slides + Thank You, on the MITS template."""
import os
import re
from xml.sax.saxutils import escape

exec(open("helpers.py").read())  # box, text, arrow, card, slide, footer, colours, ORDER, U

BLK, MID, RULE = "000000", "7F7F7F", "D9D9D9"
B = lambda t, **k: {"text": t, "bullet": True, **k}  # noqa: E731


def dbox(x, y, w, h, label, size=10, bold=False, geom="rect", fill=WHITE, lw=1.0, dash=None):
    """Monochrome diagram node: black outline, white fill, centred label."""
    return box(x, y, w, h, [{"text": t, "align": "ctr"} for t in label.split("\n")], geom=geom, fill=fill, line=BLK,
               lw=lw, size=size, bold=bold, color=BLK if fill == WHITE else WHITE, anchor="ctr", inset=0.03, dash=dash,
               after=0)


def ar(x1, y1, x2, y2, both=False, head=True, dash=None):
    assert x1 == x2 or y1 == y2, "connectors must be straight"
    return arrow(x1, y1, x2, y2, color=BLK, lw=1.0, both=both, head=head, dash=dash)


def lbl(x, y, w, h, t, size=8, align="l", italic=False):
    return text(x, y, w, h, [{"text": t, "align": align, "italic": italic}], size=size, color=DARK, anchor="ctr")


def grid(x, y, colw, rows, rh, size=9.5, hdr_size=10.5, first_bold=True):
    out = ""
    for r, row in enumerate(rows):
        xx = x
        hdr = r == 0
        for k, cell in enumerate(row):
            out += box(xx, y + r * rh, colw[k] - 0.03, rh - 0.03, [{"text": cell}],
                       fill=RED if hdr else (LIGHT if r % 2 else WHITE), line=None if hdr else RULE, lw=0.75,
                       size=hdr_size if hdr else size, bold=hdr or (first_bold and k == 0),
                       color=WHITE if hdr else DARK, anchor="ctr", inset=0.07, after=0)
            xx += colw[k]
    return out


SLIDES = []


def add(title, content):
    SLIDES.append((title, content))


# ============================================================ 2 Problem statement
c = box(0.6, 1.25, 8.8, 1.05, [{"runs": [
    ("Problem: ", {"bold": True, "color": RED}),
    ("Developers increasingly ship AI-generated code they do not fully understand. Existing tools generate code but do not "
     "design a coherent architecture, prove that the result runs, fix their own errors, or explain what they changed.", {}),
]}], fill=TINT, size=15, anchor="ctr", inset=0.15, geom="roundRect", adj=8000)
pains = [
    ("Unverified output", "Generated code is handed over without being executed or tested."),
    ("Design drift", "Models, routes and auth are generated independently and fall out of sync."),
    ("No self-correction", "When code breaks, the developer has to debug the AI's mistakes by hand."),
    ("No traceability", "No record of what changed, where, or why; hard to review or maintain."),
]
for k, (h, b) in enumerate(pains):
    c += card(0.6 + k * 2.25, 2.55, 2.1, 2.3, f"P{k + 1}  {h}", b, head_size=13, body_size=12)
add("Problem Statement", c)

# ============================================================ 3 Objectives
objs = [
    ("Understand", "Convert a natural-language description into a validated architecture (entities, relations, API, auth).", "P2"),
    ("Generate", "Produce a complete, runnable FastAPI + SQLAlchemy backend with JWT auth, Docker and tests.", "P2"),
    ("Verify", "Execute every generated app in an isolated sandbox: import, boot and API contract tests.", "P1"),
    ("Self-heal", "Classify failures, patch only the faulty file, re-validate; stop after 3 attempts (circuit breaker).", "P3"),
    ("Explain & deliver", "Record every agent action in a change ledger, show code diffs, run a live preview of the app.", "P4"),
]
c = ""
for k, (h, b, p) in enumerate(objs):
    y = 1.25 + k * 0.72
    c += box(0.6, y, 0.52, 0.52, [{"text": f"O{k + 1}", "align": "ctr"}], geom="ellipse", fill=RED, size=13, bold=True,
             color=WHITE, anchor="ctr", inset=0)
    c += text(1.3, y - 0.04, 6.9, 0.62, [{"runs": [(h + ": ", {"bold": True}), (b, {"color": GREY})]}], size=14, anchor="ctr")
    c += box(8.35, y + 0.08, 1.05, 0.36, [{"text": "solves " + p, "align": "ctr"}], fill=WHITE, line=BLK, lw=0.75,
             size=10, anchor="ctr", inset=0.02, after=0)
c += text(0.6, 4.82, 8.8, 0.25, [{"text": "Together O1–O5 cover all four problems P1–P4 on the previous slide.", "italic": True}],
          size=11, color=GREY, anchor="ctr")
add("Objectives", c)

# ============================================================ 4 Project overview (+ single SDG)
ov = [
    ("Engineering Problem", "AI code generators produce unverified, inconsistent multi-file backends."),
    ("Motivation", "Debugging AI-written code often costs more time than it saves."),
    ("Need", "A harness that designs, executes, tests and repairs code before delivery."),
    ("Target Users", "Students, hackathon teams, start-ups and developers prototyping REST backends."),
    ("Scope", "FastAPI + SQLAlchemy backends with JWT auth, tests and Docker; web UI with live preview."),
    ("Expected Impact", "A working, tested backend in about a minute for $0.01–0.20, with every change explained."),
]
c = ""
for k, (h, b) in enumerate(ov):
    x = 0.6 + (k % 3) * 2.15
    y = 1.25 + (k // 3) * 1.85
    c += card(x, y, 2.05, 1.72, h, b, head_size=12.5, body_size=11)
SDG = "FD6925"
c += box(7.15, 1.25, 2.25, 1.15, [{"text": "SDG 9", "align": "ctr", "size": 26, "bold": True, "color": WHITE, "after": 0},
                                   {"text": "Industry, Innovation and Infrastructure", "align": "ctr", "size": 11,
                                    "color": WHITE, "after": 0}], fill=SDG, anchor="ctr", inset=0.06)
c += box(7.15, 2.5, 2.25, 2.32, [
    {"text": "Target 9.5", "bold": True, "size": 12, "after": 3},
    {"text": "Enhance research and upgrade the technological capabilities of industry.", "size": 10.5, "color": GREY, "after": 5},
    {"text": "VibeStack makes reliable, verified software engineering accessible to small teams and students.",
     "size": 10.5, "color": GREY, "after": 0},
], fill=WHITE, line=SDG, lw=1.25, inset=0.1)
add("Project Overview", c)

# ============================================================ 5 Progress since zeroth review
rows = [
    ("Parameter", "Zeroth Review (Aug 2026)", "Current Status (Oct 2026)"),
    ("Literature Survey", "Survey of AI coding tools", "15 sources (Reflexion, Self-Debug, SWE-bench…); gap identified"),
    ("Problem Definition", "Broad: \"AI that builds software\"", "Narrowed: verified, self-healing FastAPI backend generation"),
    ("Architecture", "Proposed multi-agent block diagram", "4-layer architecture implemented with LangGraph"),
    ("Algorithms", "Generate-then-test idea", "Generate → 3 gates → classify → repair; circuit breaker"),
    ("Hardware", "Not finalised", "No GPU needed; dev PC 8 GB; Railway 1 vCPU container"),
    ("Software", "Stack under evaluation", "Python, FastAPI, LangGraph, Next.js; live on Railway"),
]
c = grid(0.6, 1.2, [1.6, 2.6, 4.6], rows, 0.335, size=9.5, hdr_size=10.5)
rows2 = [
    ("Committee Suggestion", "Action Taken"),
    ("1. Map the project to a single, appropriate SDG", "Mapped to SDG 9 only (target 9.5), shown on Project Overview (slide 4)"),
    ("2. Make activities and deliverables more precise", "Deliverable + % complete per activity (slide 10); dated Gantt (slide 11)"),
]
c += grid(0.6, 3.75, [3.3, 5.5], rows2, 0.4, size=9.5, hdr_size=10.5, first_bold=False)
add("Progress since Zeroth Review", c)

# ============================================================ 6 System requirements and design
c = ""
X0, LW, W = 0.5, 0.95, 5.5
layers = [(1.22, 0.5, "Presentation"), (1.9, 0.5, "Application\n(FastAPI)"), (2.58, 1.1, "Orchestration\n(LangGraph)"),
          (3.86, 0.5, "Infrastructure\n& Data")]
for y, h, name in layers:
    c += box(X0, y, W, h, [], line=BLK, lw=1.0)
    c += ar(X0 + LW, y, X0 + LW, y + h, head=False)
    c += text(X0 + 0.03, y, LW - 0.06, h, [{"text": t, "align": "ctr"} for t in name.split("\n")], size=8, bold=True,
              anchor="ctr", after=0)
c += dbox(1.65, 1.29, 1.6, 0.36, "User (browser)", 9) + dbox(3.9, 1.29, 1.8, 0.36, "Web UI (Next.js)", 9)
c += ar(3.25, 1.47, 3.9, 1.47, both=True)
for x, t in [(1.6, "Run Manager"), (3.05, "SSE Event Stream"), (4.5, "Preview Proxy")]:
    c += dbox(x, 1.98, 1.35, 0.34, t, 9)
c += ar(4.0, 1.65, 4.0, 1.98, both=True)
pipe = ["Architect", "Planner", "Code Gen", "Validator", "Packager"]
for k, t in enumerate(pipe):
    c += dbox(1.55 + k * 0.9, 2.66, 0.8, 0.36, t, 8)
    if k < 4:
        c += ar(1.55 + k * 0.9 + 0.8, 2.84, 1.55 + (k + 1) * 0.9, 2.84)
c += ar(1.95, 2.32, 1.95, 2.66)
c += dbox(3.4, 3.22, 0.95, 0.34, "Reflector", 8) + dbox(4.45, 3.22, 0.9, 0.34, "Classifier", 8)
c += ar(4.95, 3.02, 4.95, 3.22) + ar(4.45, 3.39, 4.35, 3.39) + ar(4.3, 3.22, 4.3, 3.02)
c += lbl(5.0, 3.04, 0.4, 0.17, "fail", 7) + lbl(3.95, 3.04, 0.33, 0.17, "patch", 7, align="r")
for x, w, t in [(1.55, 1.0, "OpenRouter LLM API"), (2.7, 1.0, "Isolated Sandbox"), (3.85, 1.0, "SQLite Checkpoints"),
                (5.0, 0.95, "Run Artifacts")]:
    c += dbox(x, 3.93, w, 0.36, t, 7.5)
    c += ar(x + w / 2, 3.68, x + w / 2, 3.93)
c += lbl(0.5, 4.33, 5.5, 0.2, "(a) Overall system architecture", 9, align="ctr", italic=True)
# module block diagram
mods = ["Web UI & API", "Architect", "Planner & Code Generator", "Sandbox Validator", "Packager & Live Preview"]
for k, t in enumerate(mods):
    y = 1.25 + k * 0.6
    c += dbox(6.25, y, 1.95, 0.38, t, 9, bold=True)
    if k < 4:
        c += ar(7.22, y + 0.38, 7.22, y + 0.6)
c += dbox(8.5, 3.0, 1.0, 0.5, "Self-Healing\nEngine", 8.5, bold=True)
c += ar(8.2, 3.15, 8.5, 3.15) + ar(8.5, 3.35, 8.2, 3.35)
c += lbl(6.25, 4.1, 3.25, 0.2, "(b) Module block diagram", 9, align="ctr", italic=True)
c += box(0.5, 4.6, 9.0, 0.42, [{"runs": [
    ("Software: ", {"bold": True}), ("Python 3.11, Node.js 22, FastAPI, LangGraph, OpenRouter API, Docker.   ", {}),
    ("Hardware: ", {"bold": True}), ("4-core CPU, 8 GB RAM, 10 GB disk; Railway 1 vCPU / 1 GB container; no GPU.", {})]}],
    fill=LIGHT, size=10, anchor="ctr", inset=0.1, after=0)
add("System Requirements and Design", c)

# ============================================================ 7 Methodology
c = ""
CX, MW = 1.6, 1.8          # main column centre / width
RX, RW = 3.65, 1.5         # right column
rows_y = {"start": 1.2, "a": 1.6, "b": 1.98, "c": 2.36, "d": 2.76, "e": 3.5, "end": 3.92}
c += dbox(CX - 0.65, 1.2, 1.3, 0.3, "Start", 9.5, bold=True, geom="flowChartTerminator")
c += dbox(CX - MW / 2, 1.6, MW, 0.3, "Design architecture", 9.5)
c += dbox(CX - MW / 2, 1.98, MW, 0.3, "Plan & generate code", 9.5)
c += dbox(CX - MW / 2, 2.36, MW, 0.3, "Validate in sandbox", 9.5)
c += dbox(CX - MW / 2, 2.76, MW, 0.55, "All gates pass?", 9, geom="flowChartDecision")
c += dbox(CX - MW / 2, 3.5, MW, 0.3, "Package & live preview", 9.5)
c += dbox(CX - 0.65, 3.92, 1.3, 0.3, "End", 9.5, bold=True, geom="flowChartTerminator")
for y1, y2 in [(1.5, 1.6), (1.9, 1.98), (2.28, 2.36), (2.66, 2.76), (3.31, 3.5), (3.8, 3.92)]:
    c += ar(CX, y1, CX, y2)
c += lbl(CX + 0.06, 3.33, 0.4, 0.16, "Yes", 8)
c += dbox(RX - RW / 2, 2.76, RW, 0.55, "Attempts\n< 3?", 8.5, geom="flowChartDecision")
c += ar(CX + MW / 2, 3.035, RX - RW / 2, 3.035) + lbl(2.55, 2.86, 0.3, 0.16, "No", 8)
c += dbox(RX - RW / 2, 2.36, RW, 0.3, "Classify & repair", 9.5)
c += ar(RX, 2.76, RX, 2.66) + lbl(RX + 0.06, 2.67, 0.35, 0.1, "Yes", 8)
c += ar(RX - RW / 2, 2.51, CX + MW / 2, 2.51) + lbl(2.52, 2.33, 0.4, 0.16, "patch", 8)
c += dbox(RX - RW / 2, 3.5, RW, 0.3, "Circuit breaker report", 9.5)
c += ar(RX, 3.31, RX, 3.5) + lbl(RX + 0.06, 3.33, 0.3, 0.16, "No", 8)
c += ar(RX, 3.8, RX, 4.07, head=False) + ar(RX, 4.07, CX + 0.65, 4.07)
c += lbl(0.6, 4.4, 4.0, 0.2, "Flowchart of the Generate → Validate → Heal algorithm", 9, align="ctr", italic=True)
steps = [
    ("Requirement capture: ", "user describes the app in plain English in the web UI."),
    ("Architecture: ", "Architect LLM → ProjectSpec (entities, fields, FKs, auth), schema-validated."),
    ("Planning: ", "dependency-ordered file plan; verified templates act as the contract."),
    ("Generation: ", "per-entity models, schemas and routers written in parallel."),
    ("Validation: ", "sandbox gates import → boot (/health) → API contract tests."),
    ("Self-healing: ", "classify → localise → patch (Qwen3 → Sonnet → template), max 3 attempts."),
    ("Delivery: ", "zip + Dockerfile + change ledger; app started as a live preview."),
]
paras = [{"text": "Processing steps & workflow", "bold": True, "size": 12.5, "after": 3}]
paras += [{"runs": [(f"{k + 1}. {h}", {"bold": True}), (b, {"color": GREY})], "after": 2} for k, (h, b) in enumerate(steps)]
paras += [{"text": "AI-project items", "bold": True, "size": 12.5, "after": 3}]
ai = [("Dataset: ", "10 benchmark prompts (backend/evals)."),
      ("Pre-processing: ", "prompt → schema-validated spec; repair round-trip on invalid JSON."),
      ("Training: ", "none; pre-trained LLMs via API (no fine-tuning)."),
      ("Testing: ", "36 automated tests with a deterministic fake LLM + benchmark runner."),
      ("Deployment: ", "Docker image on Railway; same image via docker compose.")]
paras += [{"runs": [(h, {"bold": True}), (b, {"color": GREY})], "bullet": True, "after": 1} for h, b in ai]
c += text(4.75, 1.2, 4.75, 3.8, paras, size=10)
add("Methodology", c)

# ============================================================ 8 Technology used
rows = [
    ("Category", "Choice", "Why it was selected"),
    ("Programming languages", "Python 3.11, TypeScript", "Python: AI ecosystem and our output language; TS: type-safe UI"),
    ("Backend framework", "FastAPI, SQLAlchemy 2.0, Pydantic v2", "Async, auto API docs, typed models; same stack we generate"),
    ("Agent orchestration", "LangGraph", "State machine with loops and checkpoints for the heal cycle"),
    ("LLMs / cloud AI", "OpenRouter (Sonnet 4.6, Qwen3 Coder)", "One API: tiered cost routing and fallback"),
    ("Frontend", "Next.js 16, React 19, Tailwind CSS", "Static export served by FastAPI; fast, projector-friendly UI"),
    ("Database", "SQLite", "Zero-setup storage for checkpoints and apps; Postgres planned"),
    ("Protocols", "HTTP/REST, Server-Sent Events, JWT", "SSE streams agent progress live; JWT secures generated apps"),
    ("Libraries", "Jinja2, httpx, uvicorn, pytest", "Verified templates, LLM calls, app server, contract tests"),
    ("Cloud platform", "Railway + Docker", "$5/month, Git deploys, volume; Docker to self-host"),
    ("IDE & tools", "VS Code, Git/GitHub, ruff", "Shared team tooling, linting and version control"),
    ("Hardware", "4-core CPU, 8 GB RAM; no GPU", "Models are API-hosted, so no local GPU is required"),
]
c = grid(0.6, 1.2, [1.75, 2.85, 4.2], rows, 0.315, size=9, hdr_size=10)
add("Technology Used", c)

# ============================================================ 9 Preliminary results
tiles = [("36", "automated tests passing"), ("10 / 10", "benchmark projects healed\n(planted bug, harness eval)"),
         ("1", "repair iteration to fix\na planted bug"), ("~51 s", "end-to-end real run on\nRailway (blog + JWT auth)"),
         ("$0.009", "API cost of that run\n(~9.6k tokens)"), ("3 / 3", "gates passed: import,\nboot, API tests")]
c = ""
for k, (n, t) in enumerate(tiles):
    x = 0.6 + (k % 3) * 1.85
    y = 1.2 + (k // 3) * 1.02
    c += box(x, y, 1.75, 0.92, [{"text": n, "align": "ctr", "size": 20, "bold": True, "color": RED, "after": 0}] +
             [{"text": s, "align": "ctr", "size": 8.5, "color": GREY, "after": 0} for s in t.split("\n")],
             fill=WHITE, line=BLK, lw=0.75, anchor="ctr", inset=0.04)
c += box(6.25, 1.2, 3.15, 1.94, [{"text": "Screenshot", "align": "ctr", "bold": True, "size": 13, "color": GREY},
                                  {"text": "Insert: VibeStack UI with the self-healing diff and the live app preview",
                                   "align": "ctr", "size": 10, "color": GREY}],
         fill=LIGHT, line=MID, lw=1.0, dash="dash", anchor="ctr", inset=0.15)
rows = [
    ("Performance indicator", "Expected", "Achieved (prototype)"),
    ("Architecture produced from a prompt", "Valid, schema-checked spec", "Yes, every run"),
    ("Generated app passes all 3 gates", "≥ 80% of benchmark prompts", "10/10 after healing (harness evaluation)"),
    ("Repair attempts per failure", "≤ 3 (circuit breaker)", "1 on the planted-bug runs"),
    ("API cost per generated app", "< $0.25", "$0.009 (budget models) to ~$0.20 (Sonnet)"),
]
c += grid(0.6, 3.3, [3.0, 2.6, 3.2], rows, 0.31, size=9, hdr_size=10)
add("Preliminary / Interim Results", c)

# ============================================================ 10 Remaining work
work = [
    ("Integration", "ChromaDB memory and MCP tool servers wired into the graph", 0),
    ("Testing", "Benchmark report: 10 prompts, real models (pass rate, cost)", 40),
    ("Optimisation", "Lower token cost and latency (prompt caching)", 20),
    ("Security", "Trivy / Checkov scan gate added to the sandbox", 0),
    ("Documentation", "Project report, user guide and API documentation", 50),
    ("Publication", "Conference paper draft on the self-healing harness", 10),
    ("Copyright", "Software copyright registration for VibeStack", 0),
]
c = grid(0.6, 1.2, [1.5, 4.6, 0.75, 1.95], [("Activity", "Deliverable", "Done", "Progress")] +
         [(a, d, f"{p}%", "") for a, d, p in work], 0.42, size=10, hdr_size=10.5)
for k, (_, _, p) in enumerate(work):
    y = 1.2 + (k + 1) * 0.42 + 0.13
    c += box(7.5, y, 1.8, 0.14, [], fill=WHITE, line=BLK, lw=0.5)
    if p:
        c += box(7.5, y, 1.8 * p / 100, 0.14, [], fill=BLK)
c += box(0.6, 4.6, 8.8, 0.42, [{"runs": [("Overall completion ≈ 65% of planned scope", {"bold": True, "color": WHITE}),
                                         ("   (6 of 8 milestones done)",
                                          {"color": WHITE, "size": 10})]}], fill=RED, size=12, anchor="ctr", inset=0.12, after=0)
add("Remaining Work", c)

# ============================================================ 11 Updated timelines (Gantt)
months = ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr"]
GX, GW, MWD = 2.55, 6.85, 6.85 / 9
c = box(0.6, 1.2, 1.9, 0.3, [{"text": "Activity"}], fill=RED, size=10, bold=True, color=WHITE, anchor="ctr", inset=0.07, after=0)
for k, m in enumerate(months):
    c += box(GX + k * MWD, 1.2, MWD - 0.02, 0.3, [{"text": m + (" '26" if k < 5 else " '27"), "align": "ctr"}], fill=RED,
             size=9, bold=True, color=WHITE, anchor="ctr", inset=0, after=0)
tasks = [  # name, start month, end month (fractional), status
    ("Literature & zeroth review", 0, 1.0, "done"),
    ("Design (architecture, DFDs)", 0.5, 1.8, "done"),
    ("Implementation (harness, UI)", 1.0, 4.5, "ongoing"),
    ("Review 1 (8 Oct)", 2.2, 2.32, "done"),
    ("Testing & benchmarks", 2.0, 5.5, "ongoing"),
    ("Validation with real models", 4.0, 6.5, "pending"),
    ("Documentation & report", 2.5, 8.0, "ongoing"),
    ("Review 2", 5.4, 5.6, "pending"),
    ("Final project & viva", 7.5, 9.0, "pending"),
]
for k, (n, s, e_, st) in enumerate(tasks):
    y = 1.55 + k * 0.33
    c += box(0.6, y, 1.9, 0.3, [{"text": n}], fill=LIGHT if k % 2 else WHITE, line=RULE, lw=0.5, size=9, anchor="ctr",
             inset=0.06, after=0)
    c += box(GX, y, GW, 0.3, [], fill=LIGHT if k % 2 else WHITE, line=RULE, lw=0.5)
    fill = {"done": BLK, "ongoing": MID, "pending": WHITE}[st]
    c += box(GX + s * MWD, y + 0.06, max((e_ - s) * MWD, 0.09), 0.18, [], fill=fill, line=BLK, lw=0.75,
             geom="diamond" if e_ - s < 0.3 else "rect")
for k in range(1, 9):
    c += ar(GX + k * MWD, 1.55, GX + k * MWD, 1.55 + 9 * 0.33, head=False)
TX = GX + (2 + 7 / 31) * MWD
c += arrow(TX, 1.5, TX, 1.55 + 9 * 0.33, color=RED, lw=1.5, head=False, dash="dash")
c += lbl(TX + 0.05, 4.55, 1.2, 0.18, "Today: 8 Oct 2026", 8)
lx = 0.6
for nm, f in [("Completed", BLK), ("Ongoing", MID), ("Pending", WHITE)]:
    c += box(lx, 4.79, 0.35, 0.16, [], fill=f, line=BLK, lw=0.75) + lbl(lx + 0.42, 4.76, 1.0, 0.22, nm, 9)
    lx += 1.45
c += lbl(5.0, 4.76, 4.4, 0.22, "◆ milestone (review)", 9, align="r")
add("Updated Timelines", c)

# ============================================================ 12 References
refs = [
    "N. Shinn et al., \"Reflexion: Language agents with verbal reinforcement learning,\" NeurIPS, 2023.",
    "X. Chen, M. Lin, N. Schärli and D. Zhou, \"Teaching large language models to self-debug,\" ICLR, 2024.",
    "Z. Gou et al., \"CRITIC: Large language models can self-correct with tool-interactive critiquing,\" ICLR, 2024.",
    "S. Yao et al., \"ReAct: Synergizing reasoning and acting in language models,\" ICLR, 2023.",
    "C. E. Jimenez et al., \"SWE-bench: Can language models resolve real-world GitHub issues?,\" ICLR, 2024.",
    "J. Yang et al., \"SWE-agent: Agent-computer interfaces enable automated software engineering,\" NeurIPS, 2024.",
    "D. Huang et al., \"AgentCoder: Multi-agent code generation with iterative testing and optimisation,\" arXiv:2312.13010, 2023.",
    "M. Chen et al., \"Evaluating large language models trained on code,\" arXiv:2107.03374, 2021.",
    "LangChain Inc., \"LangGraph documentation,\" langchain-ai.github.io/langgraph, 2025.",
    "S. Ramírez, \"FastAPI documentation,\" fastapi.tiangolo.com, 2025.",
    "SQLAlchemy Project, \"SQLAlchemy 2.0 documentation,\" docs.sqlalchemy.org, 2025.",
    "Pydantic, \"Pydantic v2 documentation,\" docs.pydantic.dev, 2025.",
    "OpenRouter, \"OpenRouter API reference,\" openrouter.ai/docs, 2025.",
    "Vercel, \"Next.js documentation,\" nextjs.org/docs, 2025.",
    "pytest-dev, \"pytest documentation,\" docs.pytest.org, 2025.",
]
col = lambda rs, start: [{"runs": [(f"[{start + i}] ", {"bold": True}), (r, {})], "after": 4} for i, r in enumerate(rs)]  # noqa: E731
c = text(0.6, 1.2, 4.3, 3.8, col(refs[:8], 1), size=9.5) + text(5.1, 1.2, 4.3, 3.8, col(refs[8:], 9), size=9.5)
add("References", c)

# ============================================================ assemble
KEEP = ORDER[2:2 + len(SLIDES)]
for page, ((title, content), fname) in enumerate(zip(SLIDES, KEEP), start=2):
    open(U + fname, "w").write(slide(page, title, content))

s = open(U + ORDER[0]).read()
for a, b in [
    ("[PROJECT NAME]", "VibeStack"),
    ("[One-line description of the project]", "Architecture-Aware AI Software Engineering Harness"),
    ("[Review Name – e.g., Second Interim Review]", "Review 1 (Interim Evaluation)"),
    ("[Date]", "Academic Year 2026–27  |  8 October 2026"),
    ("[Guide Name]", "Nimmi M K"),
    ("[Designation]", "Assistant Professor"),
    ("[Department]", "Dept of AI and DS"),
    ("Team Members (Group: [X])", "Team Members"),
    ("[Member 1] ([Roll No.])", "Reuben Sabu V (MUT23CA060)"),
    ("[Member 2] ([Roll No.])", "Sidharth Ravi (MUT23CA063)"),
    ("[Member 3] ([Roll No.])", "Jino Jenz (MUT23CA035)"),
    ("[Member 4] ([Roll No.])", "Nikhil Jimmy (MUT23CA055)"),
]:
    assert a in s, a
    s = s.replace(a, escape(b))
open(U + ORDER[0], "w").write(s)

total = 2 + len(SLIDES)
s = open(U + ORDER[-1]).read()
s = s.replace("[PROJECT NAME], Dept of AI and DS", FOOTER).replace("[Page No]", str(total))
ty = (text(0.6, 1.75, 8.8, 1.0, [{"text": "Thank You", "align": "ctr", "size": 44, "bold": True, "color": RED}], anchor="ctr")
      + text(0.6, 2.75, 8.8, 0.6, [{"text": "VibeStack: prompt → architecture → working, self-healed app", "align": "ctr"}], size=18, color=GREY, anchor="ctr")
      + text(0.6, 3.4, 8.8, 0.5, [{"text": "Questions?", "align": "ctr", "italic": True}], size=16, color=DARK, anchor="ctr"))
s = s.replace("</p:spTree>", ty + "</p:spTree>", 1)
open(U + ORDER[-1], "w").write(s)

# ============================================================ drop unused template slides (contents + spare content slides)
drop = [ORDER[1]] + ORDER[2 + len(SLIDES):-1]
pres = open("u/ppt/presentation.xml").read()
prels = open("u/ppt/_rels/presentation.xml.rels").read()
ct = open("u/[Content_Types].xml").read()
for f in drop:
    m = re.search(r'<Relationship Id="(rId\d+)"[^>]*Target="slides/%s"/>' % re.escape(f), prels)
    rid = m.group(1)
    prels = prels.replace(m.group(0), "")
    pres = re.sub(r'<p:sldId id="\d+" r:id="%s"/>' % rid, "", pres)
    ct = re.sub(r'<Override PartName="/ppt/slides/%s"[^>]*/>' % re.escape(f), "", ct)
    rel = f"u/ppt/slides/_rels/{f}.rels"
    for n in re.findall(r'Target="../notesSlides/(notesSlide\d+\.xml)"', open(rel).read()):
        os.remove(f"u/ppt/notesSlides/{n}")
        os.remove(f"u/ppt/notesSlides/_rels/{n}.rels")
        ct = re.sub(r'<Override PartName="/ppt/notesSlides/%s"[^>]*/>' % re.escape(n), "", ct)
    os.remove(U + f)
    os.remove(rel)
open("u/ppt/presentation.xml", "w").write(pres)
open("u/ppt/_rels/presentation.xml.rels", "w").write(prels)
open("u/[Content_Types].xml", "w").write(ct)
print("slides:", total, "dropped:", len(drop))
