"""Render Level 2 DFDs (Yourdon/DeMarco notation, monochrome, straight connectors)."""
import sys
from PIL import Image, ImageDraw, ImageFont

S = 2                      # supersample factor
W, H = 1920, 1080
LW = 3                     # line width
HEAD, HW = 18, 10
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
K = (0, 0, 0)


class Diagram:
    def __init__(self, title, subtitle):
        self.im = Image.new("RGB", (W * S, H * S), "white")
        self.d = ImageDraw.Draw(self.im)
        self.text(title, 80, 70, 52, bold=True, anchor="ls")
        self.text(subtitle, 80, 125, 28, anchor="ls")
        self.d.line([(80 * S, 150 * S), ((W - 80) * S, 150 * S)], fill=K, width=2 * S)

    def font(self, size, bold):
        return ImageFont.truetype(BOLD if bold else FONT, size * S)

    def text(self, s, x, y, size=22, bold=False, anchor="mm"):
        align = {"l": "left", "r": "right", "m": "center"}[anchor[0]]
        self.d.multiline_text((x * S, y * S), s, font=self.font(size, bold), fill=K,
                              anchor=anchor, align=align, spacing=4 * S)

    def circle(self, cx, cy, r, label):
        """Process symbol, drawn as a rhombus with half-diagonal r."""
        pts = [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)]
        self.d.polygon([(x * S, y * S) for x, y in pts], outline=K, width=LW * S, fill="white")
        self.text(label, cx, cy, 22, bold=True)

    def rect(self, x, y, w, h, label, size=26):
        self.d.rectangle([x * S, y * S, (x + w) * S, (y + h) * S], outline=K, width=LW * S, fill="white")
        self.text(label, x + w / 2, y + h / 2, size, bold=True)

    def store(self, x, y, w, h, sid, name, idw=70):
        pts = [((x + w) * S, y * S), (x * S, y * S), (x * S, (y + h) * S), ((x + w) * S, (y + h) * S)]
        self.d.line(pts, fill=K, width=LW * S, joint="curve")
        self.d.line([((x + idw) * S, y * S), ((x + idw) * S, (y + h) * S)], fill=K, width=LW * S)
        self.text(sid, x + idw / 2, y + h / 2, 24, bold=True)
        self.text(name, x + idw + (w - idw) / 2, y + h / 2, 24)

    def line(self, x1, y1, x2, y2):
        assert x1 == x2 or y1 == y2
        self.d.line([(x1 * S, y1 * S), (x2 * S, y2 * S)], fill=K, width=LW * S)

    def head(self, x1, y1, x2, y2):
        if y1 == y2:
            s = 1 if x2 > x1 else -1
            pts = [(x2, y2), (x2 - s * HEAD, y2 - HW), (x2 - s * HEAD, y2 + HW)]
        else:
            s = 1 if y2 > y1 else -1
            pts = [(x2, y2), (x2 - HW, y2 - s * HEAD), (x2 + HW, y2 - s * HEAD)]
        self.d.polygon([(px * S, py * S) for px, py in pts], fill=K)

    def arrow(self, x1, y1, x2, y2, both=False):
        self.line(x1, y1, x2, y2)
        self.head(x1, y1, x2, y2)
        if both:
            self.head(x2, y2, x1, y1)

    def save(self, path):
        self.im.resize((W, H), Image.LANCZOS).save(path, optimize=True)
        print("wrote", path)


R, Y = 125, 560


def edge(cx, dx, r=R, cy=Y, top=False):
    """y on the rhombus edge (cx, cy) at horizontal offset dx (bottom unless top)."""
    dy = r - abs(dx)
    return round(cy - dy) if top else round(cy + dy)


def dfd_20(out):
    d = Diagram("Level 2 DFD: Process 2.0 Plan & Generate Code",
                "Decomposition of process 2.0 from the Level 1 DFD")
    xs = [560, 900, 1240, 1580]
    labels = ["2.1\nOrder Files by\nDependency", "2.2\nRender Verified\nTemplates",
              "2.3\nGenerate Entity\nCode (LLM)", "2.4\nAssemble\nProject"]
    for x, l in zip(xs, labels):
        d.circle(x, Y, R, l)
    d.store(80, 530, 300, 60, "D1", "Project Spec")
    d.arrow(380, Y, xs[0] - R, Y); d.text("spec", 417, Y - 22)
    flows = ["file plan", "baseline\nfiles", "entity\ncode"]
    for a, b, f in zip(xs, xs[1:], flows):
        d.arrow(a + R, Y, b - R, Y); d.text(f, (a + b) / 2, Y - 40 if "\n" in f else Y - 22, 22, anchor="mm")
    # template library above 2.2, LLM above 2.3
    d.store(770, 250, 260, 60, "T1", "Template Library")
    d.arrow(900, 310, 900, Y - R); d.text("verified\ntemplates", 915, 380, anchor="lm")
    d.rect(1110, 240, 260, 80, "LLM Provider")
    d.arrow(1240, 320, 1240, Y - R, both=True); d.text("entity prompt /\ngenerated code", 1255, 380, anchor="lm")
    # outputs of 2.4: store D2 and process 3.0
    d.store(1290, 830, 280, 60, "D2", "Generated Files")
    d.arrow(1530, edge(1580, -50), 1530, 830); d.text("project\nfiles", 1515, 745, anchor="rm")
    d.rect(1610, 820, 260, 80, "3.0 Validate\nin Sandbox", 24)
    d.arrow(1630, edge(1580, 50), 1630, 820); d.text("source files", 1645, 745, anchor="lm")
    d.text("2.3 runs one LLM call per entity in parallel; on invalid output the template baseline from 2.2 is kept.",
           80, 1010, 22, anchor="lm")
    d.save(out)


def dfd_30(out):
    d = Diagram("Level 2 DFD: Process 3.0 Validate in Sandbox",
                "Decomposition of process 3.0: three gates run in an isolated, unprivileged subprocess")
    xs = [470, 790, 1110, 1430]
    labels = ["3.1\nImport\nCheck", "3.2\nBoot Server\n+ /health", "3.3\nRun API\nContract Tests", "3.4\nReport\nResult"]
    for x, l in zip(xs, labels):
        d.circle(x, Y, R, l)
    d.store(40, 530, 260, 60, "D2", "Generated Files")
    d.arrow(300, Y, xs[0] - R, Y); d.text("source\nfiles", 332, Y - 40)
    for a, b, f in zip(xs, xs[1:], ["importable\napp", "running\nserver", "test\nresults"]):
        d.arrow(a + R, Y, b - R, Y); d.text(f, (a + b) / 2, Y - 40)
    # tests derived from the spec
    d.store(980, 260, 260, 60, "D1", "Project Spec")
    d.arrow(1110, 320, 1110, Y - R); d.text("contract tests\nfrom spec", 1125, 390, anchor="lm")
    # pass -> 5.0 (above 3.4), fail -> 4.0 (right of 3.4)
    d.rect(1300, 250, 260, 80, "5.0 Package\n& Preview", 24)
    d.arrow(1430, Y - R, 1430, 330); d.text("validated files\n(all gates pass)", 1445, 400, anchor="lm")
    d.rect(1640, 520, 230, 80, "4.0 Classify\n& Repair", 24)
    d.arrow(1430 + R, Y, 1640, Y); d.text("failure\nlog", 1587, Y - 40)
    # failure bus: every gate can fail straight down into a shared line that feeds 3.4
    bus = 800
    for x, f in zip(xs[:3], ["import error", "boot / health\nerror", "failed tests"]):
        d.line(x, edge(x, 0), x, bus); d.text(f, x + 15, 730, anchor="lm")
    d.line(xs[0], bus, xs[3], bus)
    d.arrow(xs[3], bus, xs[3], edge(xs[3], 0)); d.text("gate failure + log", 950, bus + 28)
    d.text("Each gate has a hard timeout and a scrubbed environment; the first failing gate stops validation.",
           80, 1010, 22, anchor="lm")
    d.save(out)


def dfd_40(out):
    d = Diagram("Level 2 DFD: Process 4.0 Classify & Repair",
                "Decomposition of process 4.0: the self-healing loop (max 3 attempts, then circuit breaker)")
    xs = [470, 790, 1110, 1430]
    labels = ["4.1\nClassify\nError", "4.2\nLocalise\nFault", "4.3\nSelect Model\nTier", "4.4\nGenerate\nPatch"]
    for x, l in zip(xs, labels):
        d.circle(x, Y, R, l)
    d.rect(60, 520, 220, 80, "3.0 Validate\nin Sandbox", 24)
    d.arrow(280, Y, xs[0] - R, Y); d.text("failure\nlog", 322, Y - 40)
    for a, b, f in zip(xs, xs[1:], ["error\ncategory", "faulty file\n+ siblings", "repair\nrequest"]):
        d.arrow(a + R, Y, b - R, Y); d.text(f, (a + b) / 2, Y - 40)
    # inputs from above
    d.store(660, 250, 260, 60, "D2", "Generated Files")
    d.arrow(790, 310, 790, Y - R); d.text("source files", 805, 380, anchor="lm")
    d.rect(990, 240, 240, 80, "User")
    d.arrow(1110, Y - R, 1110, 320); d.text("diagnostic report\n(after 3 attempts)", 1125, 390, anchor="lm")
    d.rect(1310, 240, 240, 80, "LLM Provider")
    d.arrow(1430, 320, 1430, Y - R, both=True); d.text("cheap → strong\nmodel / patch", 1445, 390, anchor="lm")
    # 4.5 below 4.4
    c5 = 860
    d.circle(1430, c5, R, "4.5\nApply Patch\n& Log")
    d.arrow(1430, Y + R, 1430, c5 - R); d.text("patched\nfile", 1445, 705, anchor="lm")
    d.store(1610, 820, 260, 60, "D3", "Change Ledger")
    d.arrow(1430 + R, c5, 1610, c5); d.text("fix\nrecord", 1572, c5 - 40)
    d.rect(60, 810, 220, 80, "3.0 Validate\nin Sandbox", 24)
    d.arrow(1430 - R, c5, 280, c5); d.text("patched file (re-validate)", 800, c5 - 22)
    d.text("4.3 escalates per attempt: Qwen3 Coder → Claude Sonnet 4.6 → verified template restore.",
           80, 1010, 22, anchor="lm")
    d.save(out)


if __name__ == "__main__":
    out = sys.argv[1]
    dfd_20(f"{out}/dfd-level2-process-2.0.png")
    dfd_30(f"{out}/dfd-level2-process-3.0.png")
    dfd_40(f"{out}/dfd-level2-process-4.0.png")
