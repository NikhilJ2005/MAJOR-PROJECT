# Slide content for the LangGraph seminar (exec'd by build_seminar.py).
# Slides 12-15 hold base-paper content; refine against the uploaded PDF.

# ---------------------------------------------------------------- 1 Title
s = prs.slides.add_slide(BLANK)
logo(s)
rect(s, 0, 0, 0.35, 7.5, fill=ACCENT, shape=MSO_SHAPE.RECTANGLE)
tb(s, 1.1, 1.3, 11, 0.4, ["SEMINAR PRESENTATION"], size=13, color=ACCENT, bold=True)
tb(s, 1.1, 1.85, 11.2, 1.0, ["LangGraph"], size=54, font=HEAD, bold=True)
tb(s, 1.1, 2.95, 11.2, 1.2, ["Graph-Based Orchestration for Stateful LLM Applications"], size=28, font=HEAD, color=INK)
tb(s, 1.1, 3.95, 11, 0.5, ["and its Applications"], size=20, color=MUTED)
s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1.1), Inches(4.9), Inches(5.1), Inches(4.9)).line.color.rgb = rgb(ACCENT)
tb(s, 1.1, 5.15, 6, 1.4, [{"text": "Nikhil Jimmy Thomas", "bold": True, "size": 20}, "MUT23CA055",
                          "Department of Artificial Intelligence and Data Science"], size=16, color=INK, after=4)
tb(s, 7.6, 5.15, 5, 1.4, [{"text": "Seminar Guide", "bold": True, "size": 16},
                          {"text": "Nimmi M K", "color": INK},
                          {"text": "Muthoot Institute of Technology and Science, Kochi", "color": MUTED}], size=16, after=4)

# ---------------------------------------------------------------- 2 Outline
s = base("Outline", 2)
items = ["Introduction", "What is LangGraph?", "Core concepts & features", "Literature review (6 papers)",
         "Base paper", "Methodology", "Results", "Advantages & disadvantages", "Applications", "Conclusion & references"]
for k, t in enumerate(items):
    col, row = divmod(k, 5)
    x, y = 0.85 + col * 6.0, 1.75 + row * 0.95
    c = rect(s, x, y, 0.62, 0.62, fill=SOFT, shape=MSO_SHAPE.OVAL)
    c.text_frame.margin_left = c.text_frame.margin_right = 0
    c.text_frame.word_wrap = False
    fill_paras(c.text_frame, [{"text": f"{k + 1:02d}"}], 13, ACCENT, HEAD, True, "c")
    c.text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    tb(s, x + 0.85, y, 4.8, 0.62, [t], size=20, anchor="m")

# ---------------------------------------------------------------- 3 Introduction
s = base("Introduction", 3, "Why we need LangGraph")
tb(s, 0.85, 1.65, 6.4, 5, bullets([
    ("LLMs ", "can reason, write and use tools, but real tasks need many steps."),
    ("Real workflows ", "branch (if / else), loop (retry until correct) and remember earlier results."),
    ("Simple chains ", "run a fixed, one-way sequence: no loops, no shared state."),
    ("Free-running agents ", "are flexible but hard to control, test and debug."),
    ("LangGraph ", "sits in between: the developer draws the flow as a graph, the LLM works inside each step."),
], size=18))
for k, (t, sub) in enumerate([("Chain", "fixed steps"), ("Agent", "LLM decides everything"), ("Graph", "controlled steps + loops")]):
    y = 1.75 + k * 1.6
    node(s, 8.3, y, 3.6, 1.05, f"{t}\n", fill=ACCENT if k == 2 else WHITE, line=ACCENT, color=WHITE if k == 2 else INK, size=20)
    tb(s, 8.3, y + 0.58, 3.6, 0.4, [sub], size=13, color=WHITE if k == 2 else MUTED, align="c")
    if k < 2:
        arrow(s, 10.1, y + 1.05, 10.1, y + 1.6, color=ACCENT)

# ---------------------------------------------------------------- 4 What is LangGraph
s = base("What is LangGraph?", 4, "Definition")
rect(s, 0.85, 1.7, 11.6, 1.25, fill=SOFT)
tb(s, 1.15, 1.7, 11.0, 1.25, [{"runs": [("LangGraph ", {"bold": True, "color": ACCENT}),
    ("is an open-source library from LangChain Inc. for building stateful, multi-step LLM applications "
     "as a graph of nodes (steps) and edges (transitions) that share one state object.", {})]}], size=20, anchor="m")
facts = [("Released", "2024 by LangChain Inc.; open source (MIT licence)"),
         ("Languages", "Python and JavaScript / TypeScript"),
         ("Inspired by", "Google's Pregel and Apache Beam graph-processing models"),
         ("Works with", "any LLM: OpenAI, Anthropic, Google, open-source models"),
         ("Used for", "agents, multi-agent teams, RAG pipelines, human-approval workflows")]
for k, (h, b) in enumerate(facts):
    y = 3.3 + k * 0.68
    tb(s, 0.85, y, 2.6, 0.5, [h], size=17, bold=True, color=ACCENT, font=HEAD)
    tb(s, 3.45, y, 9, 0.5, [b], size=17)

# ---------------------------------------------------------------- 5 Core concepts
s = base("Core Concepts", 5, "State · Nodes · Edges")
concepts = [("State", "Shared data (a typed dictionary) that every node reads and updates."),
            ("Node", "A Python function: one step of work, e.g. call the LLM or a tool."),
            ("Edge", "A fixed transition: after node A, run node B."),
            ("Conditional edge", "A router function picks the next node from the current state."),
            ("START / END", "Special entry and exit points of the graph.")]
for k, (h, b) in enumerate(concepts):
    y = 1.7 + k * 0.98
    tb(s, 0.85, y, 5.6, 0.95, [{"text": h, "bold": True, "font": HEAD, "size": 18, "after": 2}, {"text": b, "color": MUTED, "size": 14}])
# small example graph: START -> agent -> (tool | END), tool -> agent
node(s, 8.55, 1.7, 2.2, 0.62, "START", fill=SOFT, line=ACCENT, shape=MSO_SHAPE.OVAL)
node(s, 8.35, 2.95, 2.6, 0.85, "Agent node\n(call LLM)")
node(s, 8.35, 4.6, 2.6, 0.85, "Router\n(conditional edge)", shape=MSO_SHAPE.DIAMOND, size=12)
node(s, 11.05, 4.68, 1.6, 0.7, "Tool node")
node(s, 8.55, 6.0, 2.2, 0.62, "END", fill=SOFT, line=ACCENT, shape=MSO_SHAPE.OVAL)
arrow(s, 9.65, 2.32, 9.65, 2.95, color=ACCENT)
arrow(s, 9.65, 3.8, 9.65, 4.6, color=ACCENT)
arrow(s, 10.95, 5.03, 11.05, 5.03, color=ACCENT)
arrow(s, 9.65, 5.45, 9.65, 6.0, color=ACCENT)
arrow(s, 11.85, 4.68, 11.85, 3.375, color=ACCENT)
arrow(s, 11.85, 3.375, 10.95, 3.375, color=ACCENT)
tb(s, 11.95, 3.9, 1.3, 0.5, ["loop back"], size=11, color=MUTED)
tb(s, 9.75, 5.5, 1.5, 0.4, ["done"], size=11, color=MUTED)

# ---------------------------------------------------------------- 6 Key features
s = base("Key Features", 6, "What LangGraph adds")
feats = [("Cycles & loops", "Retry or refine until a condition is met, which plain chains cannot do."),
         ("Persistence", "Checkpointers save state after every step; resume after a crash."),
         ("Human-in-the-loop", "Pause the graph, let a person review or edit, then continue."),
         ("Streaming", "Show tokens and intermediate steps to the user in real time."),
         ("Multi-agent", "Each agent is a node or sub-graph; a supervisor routes work."),
         ("Controllability", "The developer fixes the allowed paths; the LLM fills in each step.")]
for k, (h, b) in enumerate(feats):
    card(s, 0.85 + (k % 3) * 3.95, 1.7 + (k // 3) * 2.55, 3.7, 2.3, h, b, num=k + 1, size=15)

# ---------------------------------------------------------------- 7 How to build
s = base("Building a LangGraph Application", 7, "Five steps")
steps = ["Define the State", "Add nodes (functions)", "Connect edges", "Compile the graph", "Invoke with input"]
for k, t in enumerate(steps):
    y = 1.7 + k * 1.0
    node(s, 0.85, y, 4.2, 0.7, f"{k + 1}.  {t}", fill=ACCENT if k == 4 else WHITE, color=WHITE if k == 4 else INK, size=16)
    if k < 4:
        arrow(s, 2.95, y + 0.7, 2.95, y + 1.0, color=ACCENT)
rect(s, 5.7, 1.7, 6.75, 4.85, fill="111827")
code = ["from langgraph.graph import StateGraph, START, END", "",
        "class State(TypedDict):", "    question: str", "    answer: str", "",
        "def answer(state):", "    return {\"answer\": llm.invoke(state[\"question\"])}", "",
        "g = StateGraph(State)", "g.add_node(\"answer\", answer)", "g.add_edge(START, \"answer\")",
        "g.add_edge(\"answer\", END)", "app = g.compile()", "app.invoke({\"question\": \"What is LangGraph?\"})"]
tb(s, 6.0, 1.9, 6.3, 4.5, [{"text": c, "font": "Consolas", "color": "E5E7EB", "after": 0} for c in code], size=13, spacing=1.05)

# ---------------------------------------------------------------- 8 Literature review overview
s = base("Literature Review: Overview", 8, "Six arXiv papers")
rows = [("Paper (arXiv)", "Method", "Main disadvantage"),
        ("ReAct, Yao et al. (2210.03629)", "Interleaves reasoning steps with tool actions", "One linear loop; no saved state; errors cascade"),
        ("Reflexion, Shinn et al. (2303.11366)", "Agent reflects on failures and retries", "Needs good feedback; extra LLM calls"),
        ("AutoGen, Wu et al. (2308.08155)", "Agents solve tasks by conversing", "Conversation-driven flow is hard to control"),
        ("MetaGPT, Hong et al. (2308.00352)", "Role-based agents follow SOPs", "Rigid pipeline built for software tasks"),
        ("LLM Agent Survey, Wang et al. (2308.11432)", "Unified agent framework: profile, memory, planning, action", "No concrete orchestration tool"),
        ("LangGraph MT, Wang & Duan (2412.03801)", "LangGraph routes text to translation agents", "Few languages; small evaluation")]
table(s, 0.85, 1.65, [4.1, 4.0, 3.55], rows, rh=0.72, size=13)

# ---------------------------------------------------------------- 9-11 Literature review detail
def lr_pair(title, num, papers):
    s = base(title, num, "Literature review")
    for k, (name, ref, method, dis) in enumerate(papers):
        x = 0.85 + k * 6.0
        rect(s, x, 1.65, 5.6, 5.0, fill=CARD)
        tb(s, x + 0.3, 1.85, 5.0, 0.5, [name], size=22, font=HEAD, bold=True)
        tb(s, x + 0.3, 2.4, 5.0, 0.4, [ref], size=12, color=ACCENT, bold=True)
        tb(s, x + 0.3, 2.95, 5.0, 2.2, [{"text": "Method", "bold": True, "size": 14, "color": INK, "after": 3}] +
           [{"text": m, "bullet": True, "size": 14, "color": MUTED, "after": 4} for m in method])
        tb(s, x + 0.3, 5.15, 5.0, 1.5, [{"text": "Disadvantages", "bold": True, "size": 14, "color": INK, "after": 3}] +
           [{"text": d, "bullet": True, "size": 14, "color": MUTED, "after": 4} for d in dis])


lr_pair("ReAct and Reflexion", 9, [
    ("ReAct", "Yao et al., ICLR 2023 · arXiv:2210.03629",
     ["The LLM alternates Thought → Action → Observation.", "Actions call tools such as Wikipedia search.", "Beat baselines on HotpotQA, ALFWorld, WebShop."],
     ["Single linear loop; no persistent state.", "A wrong step early can derail the whole run."]),
    ("Reflexion", "Shinn et al., NeurIPS 2023 · arXiv:2303.11366",
     ["After a failure, the agent writes a verbal self-reflection.", "Reflections are stored in memory and used on the next try.", "No model fine-tuning needed."],
     ["Depends on a reliable success / failure signal.", "Each retry costs more LLM calls and time."]),
])
lr_pair("AutoGen and MetaGPT", 10, [
    ("AutoGen", "Wu et al., 2023 · arXiv:2308.08155",
     ["Multiple agents solve a task by chatting with each other.", "Agents can combine LLMs, tools and human input.", "Open-source framework from Microsoft."],
     ["Flow emerges from conversation, so it is hard to predict.", "Agents can loop or drift without strict control."]),
    ("MetaGPT", "Hong et al., ICLR 2024 · arXiv:2308.00352",
     ["Encodes Standard Operating Procedures (SOPs) in prompts.", "Roles (PM, architect, engineer) work like an assembly line.", "Agents cross-check outputs to reduce errors."],
     ["Fixed pipeline designed mainly for software projects.", "Many agents means high token cost."]),
])
lr_pair("Agent Survey and LangGraph for Translation", 11, [
    ("LLM Agent Survey", "Wang et al., 2023 · arXiv:2308.11432",
     ["Reviews LLM-based autonomous agents.", "Proposes a framework: profile, memory, planning, action.", "Covers applications and evaluation methods."],
     ["A survey: no implementation or orchestration tool.", "Field changes faster than surveys can track."]),
    ("LangGraph for Translation", "Wang & Duan, 2024 · arXiv:2412.03801",
     ["LangGraph analyses the input and routes it to an agent.", "Separate agents for English, French and Japanese.", "Agents run on LLMs such as GPT-4o."],
     ["Evaluated on only a few languages.", "Relies on a paid proprietary model."]),
])

# ---------------------------------------------------------------- 12 Base paper
s = base("Base Paper", 12, "arXiv:2411.18241")
rect(s, 0.85, 1.65, 11.6, 1.55, fill=SOFT)
tb(s, 1.15, 1.75, 11.0, 1.4, [
    {"text": "Exploration of LLM Multi-Agent Application Implementation Based on LangGraph+CrewAI", "bold": True, "font": HEAD, "size": 21, "after": 4},
    {"text": "Zhihua Duan and Jialin Wang  ·  arXiv:2411.18241 [cs]  ·  November 2024", "color": MUTED, "size": 15}], anchor="m")
tb(s, 0.85, 3.5, 5.6, 3.2, [{"text": "Problem addressed", "bold": True, "font": HEAD, "size": 18, "after": 6}] + [
    {"text": t, "bullet": True, "size": 15, "color": MUTED, "after": 6} for t in [
        "Complex tasks are too much for a single LLM agent.",
        "Multi-agent systems need precise control of the flow and good teamwork."]])
tb(s, 6.85, 3.5, 5.6, 3.2, [{"text": "Key idea", "bold": True, "font": HEAD, "size": 18, "after": 6}] + [
    {"text": t, "bullet": True, "size": 15, "color": MUTED, "after": 6} for t in [
        "LangGraph: graph architecture gives precise control and efficient information flow between agents.",
        "CrewAI: role-based crews give intelligent task allocation and resource management.",
        "Together they divide work and collaborate on tasks a single agent struggles with."]])

# ---------------------------------------------------------------- 13 Methodology: architecture
s = base("Methodology: System Architecture", 13, "Base paper")
node(s, 0.85, 3.3, 1.9, 0.9, "User task", fill=SOFT)
rect(s, 3.25, 1.7, 5.3, 4.9, fill=WHITE, line=ACCENT, lw=1.5)
tb(s, 3.45, 1.8, 4.9, 0.4, ["LangGraph: control layer"], size=14, color=ACCENT, bold=True)
node(s, 4.05, 2.45, 3.7, 0.75, "State (shared data)", fill=SOFT, size=14)
node(s, 4.05, 3.55, 3.7, 0.75, "Nodes: agent steps", size=14)
node(s, 4.05, 4.65, 3.7, 0.75, "Edges & conditional routing", size=14)
tb(s, 3.45, 5.65, 4.9, 0.8, ["Decides which step runs next and passes state between steps."], size=12, color=MUTED, align="c")
arrow(s, 2.75, 3.75, 3.25, 3.75, color=ACCENT)
rect(s, 9.05, 1.7, 3.4, 4.9, fill=WHITE, line=ACCENT, lw=1.5)
tb(s, 9.25, 1.8, 3.0, 0.4, ["CrewAI: team layer"], size=14, color=ACCENT, bold=True)
for k, t in enumerate(["Agent: role + goal", "Task assignment", "Collaboration"]):
    node(s, 9.35, 2.45 + k * 1.1, 2.8, 0.75, t, size=14)
arrow(s, 8.55, 3.92, 9.05, 3.92, color=ACCENT, both=True)
tb(s, 9.25, 5.65, 3.0, 0.8, ["Agents with roles carry out the work inside each node."], size=12, color=MUTED, align="c")

# ---------------------------------------------------------------- 14 Methodology: workflow
s = base("Methodology: Workflow", 14, "Base paper")
flow = ["Receive task", "LangGraph routes to next node", "CrewAI crew performs the task", "Update shared state", "Check: done?"]
for k, t in enumerate(flow):
    x = 0.85 + k * 2.42
    node(s, x, 2.0, 2.05, 1.2, t, fill=ACCENT if k == 4 else WHITE, color=WHITE if k == 4 else INK, size=14,
         shape=MSO_SHAPE.DIAMOND if k == 4 else MSO_SHAPE.ROUNDED_RECTANGLE)
    if k < 4:
        arrow(s, x + 2.05, 2.6, x + 2.42, 2.6, color=ACCENT)
arrow(s, 10.53, 3.2, 10.53, 3.75, color=ACCENT)
arrow(s, 10.53, 3.75, 4.28, 3.75, color=ACCENT)
arrow(s, 4.28, 3.75, 4.28, 3.2, color=ACCENT)
tb(s, 6.0, 3.82, 3.0, 0.4, ["no: loop to the next step"], size=12, color=MUTED, align="c")
tb(s, 0.85, 4.6, 11.6, 2.1, [{"text": "How the two frameworks share the work", "bold": True, "font": HEAD, "size": 18, "after": 6}] + bullets([
    ("LangGraph ", "owns the control flow: order of steps, branching, loops and state."),
    ("CrewAI ", "owns the teamwork: which role-based agent does which task, and how agents collaborate."),
], size=15))

# ---------------------------------------------------------------- 15 Results
s = base("Key Findings", 15, "Base paper (as reported by the authors)")
obs = [("Efficient information flow", "LangGraph's graph architecture improves how efficiently information passes between agents."),
       ("Better collaboration", "CrewAI's intelligent task allocation and resource management improve teamwork and system performance."),
       ("Precise control", "Designing agents on LangGraph gives precise control over the multi-agent workflow."),
       ("Complex tasks", "Agents divide the work and collaborate on tasks a single agent struggles with.")]
for k, (h, b) in enumerate(obs):
    card(s, 0.85 + (k % 2) * 5.95, 1.7 + (k // 2) * 2.5, 5.65, 2.25, h, b, num=k + 1, size=15)

# ---------------------------------------------------------------- 16 Advantages
s = base("Advantages", 16)
adv = [("Explicit control: ", "the developer decides the allowed paths."),
       ("Shared state: ", "every step sees the same up-to-date data."),
       ("Loops and retries: ", "refine an answer until it is correct."),
       ("Persistence: ", "pause, resume and recover from failures."),
       ("Human-in-the-loop: ", "add approval steps easily."),
       ("Easy to debug: ", "each node can be tested and traced on its own."),
       ("Model-agnostic: ", "works with any LLM provider.")]
tb(s, 0.85, 1.7, 11.6, 5.1, bullets(adv, size=20))

# ---------------------------------------------------------------- 17 Disadvantages
s = base("Disadvantages", 17)
dis = [("Learning curve: ", "graphs, state and reducers take time to learn."),
       ("More code: ", "simple tasks need more setup than a basic chain."),
       ("Large graphs get complex: ", "many nodes and routes are hard to visualise."),
       ("Cost and latency: ", "loops and multiple agents mean more LLM calls."),
       ("Still depends on the LLM: ", "a wrong model output can still pick a wrong route."),
       ("Fast-changing ecosystem: ", "APIs change between versions.")]
tb(s, 0.85, 1.7, 11.6, 5.1, bullets(dis, size=20))

# ---------------------------------------------------------------- 18 Applications
s = base("Applications", 18)
apps = [("Customer support", "Route queries to billing, technical or human agents."),
        ("RAG question answering", "Retrieve, grade documents, re-search if they are poor."),
        ("Code generation & bug fixing", "Write, test, fix in a loop (arXiv:2502.18465)."),
        ("Machine translation", "Route text to language agents (arXiv:2412.03801)."),
        ("Data analysis", "Spark agents for ML pipelines (arXiv:2412.01490)."),
        ("Multi-agent teams", "Research, writing and review crews (arXiv:2411.18241).")]
for k, (h, b) in enumerate(apps):
    card(s, 0.85 + (k % 3) * 3.95, 1.7 + (k // 3) * 2.55, 3.7, 2.3, h, b, num=k + 1, size=15)

# ---------------------------------------------------------------- 19 Conclusion
s = base("Conclusion", 19)
tb(s, 0.85, 1.75, 7.2, 5, bullets([
    ("LangGraph ", "turns an LLM application into a clear graph of steps with shared state."),
    ("It adds ", "what chains lack: branching, loops, memory and human control."),
    ("The base paper ", "shows LangGraph + CrewAI coordinating multiple agents for complex tasks."),
    ("Graph-based orchestration ", "is becoming a standard way to build reliable agentic AI."),
], size=19))
rect(s, 8.6, 1.75, 3.85, 4.6, fill=ACCENT)
tb(s, 8.9, 1.75, 3.25, 4.6, [{"text": "Key takeaway", "size": 14, "color": "C7D2FE", "bold": True, "after": 10},
                             {"text": "The LLM does the thinking; the graph keeps it on track.", "size": 24, "font": HEAD, "bold": True,
                              "color": WHITE}], anchor="m")

# ---------------------------------------------------------------- 20 References
s = base("References", 20)
refs = [
    "Z. Duan, J. Wang, \"Exploration of LLM Multi-Agent Application Implementation Based on LangGraph+CrewAI,\" arXiv:2411.18241, 2024. (Base paper)",
    "S. Yao et al., \"ReAct: Synergizing Reasoning and Acting in Language Models,\" arXiv:2210.03629, 2022.",
    "N. Shinn et al., \"Reflexion: Language Agents with Verbal Reinforcement Learning,\" arXiv:2303.11366, 2023.",
    "Q. Wu et al., \"AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation,\" arXiv:2308.08155, 2023.",
    "S. Hong et al., \"MetaGPT: Meta Programming for a Multi-Agent Collaborative Framework,\" arXiv:2308.00352, 2023.",
    "L. Wang et al., \"A Survey on Large Language Model based Autonomous Agents,\" arXiv:2308.11432, 2023.",
    "J. Wang, Z. Duan, \"Agent AI with LangGraph: A Modular Framework for Enhancing Machine Translation Using LLMs,\" arXiv:2412.03801, 2024.",
    "J. Wang, Z. Duan, \"Intelligent Spark Agents: A Modular LangGraph Framework for Big Data ML Workflows,\" arXiv:2412.01490, 2024.",
    "J. Wang, Z. Duan, \"Empirical Research on Utilizing LLM-based Agents for Automated Bug Fixing via LangGraph,\" arXiv:2502.18465, 2025.",
    "LangChain Inc., \"LangGraph Documentation,\" langchain-ai.github.io/langgraph, 2025.",
]
tb(s, 0.85, 1.6, 11.6, 4.7, [{"runs": [(f"[{k + 1}]  ", {"bold": True, "color": ACCENT}), (r, {})], "after": 5} for k, r in enumerate(refs)],
   size=13, spacing=1.05)
tb(s, 0.85, 6.3, 11.6, 0.5, [{"text": "Thank you  ·  Questions?", "bold": True, "font": HEAD, "color": ACCENT}], size=18, align="c")
