"""Fill the MITS project template with VibeStack content (native, editable shapes)."""
import re
from xml.sax.saxutils import escape

U = "u/ppt/slides/"
EMU = 914400
RED, DARK, GREY, LIGHT, TINT, WHITE = "CC0000", "1F1F1F", "595959", "F2F2F2", "FBE9E9", "FFFFFF"
FOOTER = "VibeStack, Dept of AI and DS"
ORDER = ["slide1.xml","slide2.xml"]+["slide%d.xml" % i for i in range(9,28)]+["slide3.xml"]

_id = [100]


def nid():
    _id[0] += 1
    return _id[0]


def e(v):
    return int(round(v * EMU))


def rpr(size=14, bold=False, color=DARK, italic=False):
    b = ' b="1"' if bold else ""
    it = ' i="1"' if italic else ""
    return (
        f'<a:rPr lang="en-US" sz="{int(size * 100)}"{b}{it} dirty="0">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        '<a:latin typeface="Calibri" pitchFamily="34" charset="0"/><a:cs typeface="Calibri" pitchFamily="34" charset="0"/></a:rPr>'
    )


def para(p, dsize, dcolor, dbold, dalign, dafter=4):
    """p: str | dict(text|runs, size, bold, color, align, bullet, after, num)."""
    if isinstance(p, str):
        p = {"text": p}
    size = p.get("size", dsize)
    color = p.get("color", dcolor)
    bold = p.get("bold", dbold)
    align = p.get("align", dalign)
    after = p.get("after", dafter)
    runs = p.get("runs") or [(p.get("text", ""), {})]
    ppr = f'<a:pPr algn="{align}"'
    if p.get("bullet"):
        ppr += ' marL="228600" indent="-228600"><a:spcAft><a:spcPts val="%d"/></a:spcAft><a:buClr><a:srgbClr val="%s"/></a:buClr><a:buFont typeface="Arial"/><a:buChar char="&#8226;"/></a:pPr>' % (after * 100, RED)
    elif p.get("num"):
        ppr += ' marL="285750" indent="-285750"><a:spcAft><a:spcPts val="%d"/></a:spcAft><a:buClr><a:srgbClr val="%s"/></a:buClr><a:buFont typeface="+mj-lt"/><a:buAutoNum type="arabicPeriod"/></a:pPr>' % (after * 100, RED)
    else:
        ppr += ' marL="0" indent="0"><a:spcAft><a:spcPts val="%d"/></a:spcAft><a:buNone/></a:pPr>' % (after * 100)
    out = ""
    for text, o in runs:
        out += f'<a:r>{rpr(o.get("size", size), o.get("bold", bold), o.get("color", color), o.get("italic", False))}<a:t xml:space="preserve">{escape(text)}</a:t></a:r>'
    end = f'<a:endParaRPr lang="en-US" sz="{int(size * 100)}" dirty="0"/>'
    return f"<a:p>{ppr}{out}{end}</a:p>"


def box(x, y, w, h, paras=(), geom="rect", fill=None, line=None, lw=1.0, size=14, color=DARK, bold=False,
        align="l", anchor="t", inset=0.08, name="Shape", adj=None, dash=None, after=4):
    i = nid()
    fill_xml = f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
    if line:
        line_xml = f'<a:ln w="{int(lw * 12700)}"><a:solidFill><a:srgbClr val="{line}"/></a:solidFill>' + (
            f'<a:prstDash val="{dash}"/>' if dash else "") + "</a:ln>"
    else:
        line_xml = "<a:ln><a:noFill/></a:ln>"
    av = f'<a:gd name="adj" fmla="val {adj}"/>' if adj is not None else ""
    body = "".join(para(p, size, color, bold, align, after) for p in paras) or "<a:p><a:endParaRPr lang=\"en-US\" dirty=\"0\"/></a:p>"
    ins = e(inset)
    tx = ""
    txb = ' txBox="1"' if not fill and not line else ""
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{i}" name="{name} {i}"/><p:cNvSpPr{txb}/><p:nvPr/></p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="{e(x)}" y="{e(y)}"/><a:ext cx="{e(w)}" cy="{e(h)}"/></a:xfrm>'
        f'<a:prstGeom prst="{geom}"><a:avLst>{av}</a:avLst></a:prstGeom>{fill_xml}{line_xml}</p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" lIns="{ins}" tIns="{ins}" rIns="{ins}" bIns="{ins}" rtlCol="0" anchor="{anchor}"><a:normAutofit/></a:bodyPr><a:lstStyle/>{body}</p:txBody></p:sp>'
        + tx
    )


def text(x, y, w, h, paras, **kw):
    kw.setdefault("inset", 0)
    return box(x, y, w, h, paras, **kw)


def arrow(x1, y1, x2, y2, color=GREY, lw=1.5, head=True, dash=None, both=False):
    i = nid()
    fh = ' flipH="1"' if x2 < x1 else ""
    fv = ' flipV="1"' if y2 < y1 else ""
    x, y = min(x1, x2), min(y1, y2)
    w, h = abs(x2 - x1), abs(y2 - y1)
    tail = '<a:tailEnd type="triangle" w="med" len="med"/>' if head else ""
    hd = '<a:headEnd type="triangle" w="med" len="med"/>' if both else ""
    d = f'<a:prstDash val="{dash}"/>' if dash else ""
    return (
        f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{i}" name="Connector {i}"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
        f'<p:spPr><a:xfrm{fh}{fv}><a:off x="{e(x)}" y="{e(y)}"/><a:ext cx="{e(w)}" cy="{e(h)}"/></a:xfrm>'
        f'<a:prstGeom prst="straightConnector1"><a:avLst/></a:prstGeom>'
        f'<a:ln w="{int(lw * 12700)}"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>{d}{hd}{tail}</a:ln></p:spPr></p:cxnSp>'
    )


def card(x, y, w, h, head, body, num=None, head_size=14, body_size=12, fill=LIGHT):
    out = box(x, y, w, h, [], geom="roundRect", fill=fill, adj=8000, name="Card")
    tx = x + 0.15
    if num is not None:
        out += box(x + 0.15, y + 0.15, 0.42, 0.42, [{"text": str(num), "align": "ctr"}], geom="ellipse", fill=RED,
                   size=13, bold=True, color=WHITE, anchor="ctr", inset=0)
        tx = x + 0.7
    out += text(tx, y + 0.13, x + w - tx - 0.12, 0.46, [{"text": head}], size=head_size, bold=True, anchor="ctr")
    out += text(x + 0.15, y + 0.66, w - 0.3, h - 0.76, body if isinstance(body, list) else [body], size=body_size, color=GREY)
    return out


# --------------------------------------------------------------------------- skeleton

SKEL = open(U + ORDER[2]).read()
LOGO = re.search(r"<p:pic>.*?</p:pic>", SKEL, re.S).group(0)
HEAD = SKEL[: SKEL.index("<p:pic>")]
TAIL = SKEL[SKEL.index("</p:spTree>"):]


def footer(page):
    return (
        box(0, 5.115, 10, 0.51, [], fill=RED, line=RED, name="Footer bar")
        + text(0.3, 5.115, 5.0, 0.51, [{"text": f"{FOOTER}", "size": 11, "color": WHITE}], anchor="ctr")
        + text(9.0, 5.115, 0.7, 0.51, [{"text": str(page), "size": 14, "bold": True, "color": WHITE, "align": "r"}], anchor="ctr")
    )


def slide(page, title, content):
    t = text(0.6, 0.45, 7.8, 0.7, [{"text": title, "size": 28, "bold": True, "color": "000000"}], anchor="ctr")
    return HEAD + LOGO + t + footer(page) + content + TAIL


SLIDES = []


def add(title, content):
    SLIDES.append((title, content))
