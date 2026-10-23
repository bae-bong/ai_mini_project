import os, re, zlib, zipfile, struct, glob
import xml.etree.ElementTree as ET
import olefile
from xml.sax.saxutils import escape
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader

FONTS = [
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
]
font_path = next((p for p in FONTS if os.path.exists(p)), None)
if not font_path:
    raise SystemExit("한글 폰트를 못 찾았어요")
pdfmetrics.registerFont(TTFont("Kor", font_path))

def hwpx_lines(path):
    lines = []
    with zipfile.ZipFile(path) as z:
        secs = sorted(n for n in z.namelist() if re.match(r"Contents/section\d+\.xml$", n))
        for n in secs:
            root = ET.fromstring(z.read(n))
            for p in root.iter():
                if not p.tag.endswith("}p"):
                    continue
                if any(d is not p and d.tag.endswith("}p") for d in p.iter()):
                    continue  # 안쪽에 문단이 또 있으면 바깥은 건너뜀(중복 방지)
                s = "".join(t.text or "" for t in p.iter() if t.tag.endswith("}t")).strip()
                if s:
                    lines.append(s)
    return lines

CHAR_CTRL = {0, 24, 25, 26, 27, 28, 29, 30, 31}

def decode_para(b):
    out, i, n = [], 0, len(b) // 2 * 2
    while i < n:
        c = b[i] | (b[i + 1] << 8)
        i += 2
        if c >= 32:
            out.append(chr(c))
        elif c in (10, 13):
            out.append("\n")
        elif c in CHAR_CTRL:
            continue
        else:
            if c == 9:
                out.append(" ")
            i += 14  # 인라인·확장 제어문자는 8글자 분량
    return "".join(out).split("\n")

def hwp_lines(path):
    ole = olefile.OleFileIO(path)
    header = ole.openstream("FileHeader").read()
    if header[36] & 2:
        raise RuntimeError("암호가 걸린 문서")
    compressed = bool(header[36] & 1)
    secs = sorted((e for e in ole.listdir() if e[0] == "BodyText" and e[1].startswith("Section")),
                  key=lambda e: int(e[1][7:]))
    lines = []
    for e in secs:
        data = ole.openstream(e).read()
        if compressed:
            data = zlib.decompress(data, -15)
        i = 0
        while i + 4 <= len(data):
            h = struct.unpack_from("<I", data, i)[0]
            i += 4
            tag, size = h & 0x3FF, (h >> 20) & 0xFFF
            if size == 0xFFF:
                size = struct.unpack_from("<I", data, i)[0]
                i += 4
            payload = data[i:i + size]
            i += size
            if tag == 67:  # HWPTAG_PARA_TEXT
                lines.extend(s.strip() for s in decode_para(payload) if s.strip())
    return lines

body = ParagraphStyle("b", fontName="Kor", fontSize=10, leading=15, wordWrap="CJK")

for path in sorted(glob.glob("data/bizinfo/hwp/*")):
    pid, ext = os.path.basename(path).rsplit(".", 1)
    try:
        lines = hwpx_lines(path) if ext == "hwpx" else hwp_lines(path)
    except Exception as ex:
        print("실패", pid[-6:], ext, ex)
        continue
    out = f"data/bizinfo/pdf/{pid}.pdf"
    SimpleDocTemplate(out, pagesize=A4).build([Paragraph(escape(l), body) for l in lines])
    r = PdfReader(out)
    n = sum(len((p.extract_text() or "").strip()) for p in r.pages)
    print(("OK " if n > 500 else "글자 부족 "), pid[-6:], ext, f"{len(r.pages)}쪽, 글자 {n}자")
    print("   미리보기:", " / ".join(lines[:3])[:150])
