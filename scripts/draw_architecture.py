"""Redraw docs/architecture.png (and the copy used by the /about/ page).

Needs Pillow (not an app dependency): uv pip install pillow
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1340, 900
INK, SLATE, LINE, PANEL, BOX, ACCENT, ACCENT_BG = "#10202B", "#566873", "#BDC4BE", "#ECEEEB", "#F4F5F3", "#2E73D2", "#D5E0EA"


def font(size, bold=False):
    for path in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size, index=1 if bold and path.endswith(".ttc") else 0)
            except Exception:
                continue
    return ImageFont.load_default()


img = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(img)
d.text((44, 38), "Answers stream sentence by sentence and play one voice at a time", font=font(27, True), fill=INK)

COLS = {"Browser (static page)": (42, 382), "FastAPI (one container)": (482, 892), "Managed services": (962, 1302)}
for title, (x0, x1) in COLS.items():
    d.rounded_rectangle((x0, 92, x1, 842), 14, fill=PANEL, outline=LINE, width=2)
    d.text((x0 + 28, 110), title, font=font(23, True), fill=SLATE)


def box(x0, y0, x1, y1, title, sub, highlight=False):
    d.rounded_rectangle((x0, y0, x1, y1), 14, fill=ACCENT_BG if highlight else BOX, outline=ACCENT if highlight else LINE, width=3 if highlight else 2)
    cx = (x0 + x1) / 2
    d.text((cx, y0 + 32), title, font=font(23, True), fill=INK, anchor="mm")
    d.text((cx, y0 + 66), sub, font=font(20), fill=SLATE, anchor="mm")
    return (x0, y0, x1, y1)


def arrow(p0, p1, both=False, label=None, label_at=None):
    d.line((p0, p1), fill="#9AA29C", width=3)
    for (a, b) in ([(p0, p1), (p1, p0)] if both else [(p0, p1)]):
        # arrow head at b, pointing from a
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / n, dy / n
        px, py = -uy, ux
        d.polygon([b, (b[0] - 12 * ux + 6 * px, b[1] - 12 * uy + 6 * py), (b[0] - 12 * ux - 6 * px, b[1] - 12 * uy - 6 * py)], fill="#9AA29C")
    if label:
        d.text(label_at, label, font=font(20), fill=SLATE)


# Browser
bx0, bx1 = 70, 354
rec = box(bx0, 148, bx1, 248, "Recorder", "tap to talk")
sse = box(bx0, 290, bx1, 390, "SSE client", "streams text + audio")
q = box(bx0, 432, bx1, 532, "Audio queue", "one voice at a time", highlight=True)
ui = box(bx0, 574, bx1, 674, "Voice line + status", "shows \"thinking\" while waiting")
# Server
sx0, sx1 = 510, 864
tr = box(sx0, 148, sx1, 248, "/api/transcribe", "Whisper proxy + filters")
llm = box(sx0, 290, sx1, 390, "LLM stream", "profile in prompt, streamed")
ch = box(sx0, 432, sx1, 532, "Sentence chunker", "first sentence out fast")
tts = box(sx0, 574, sx1, 674, "TTS workers", "parallel, semaphore of 3")
em = box(sx0, 716, sx1, 816, "Ordered emitter", "audio events in seq order")
# Services
vx0, vx1 = 990, 1274
gq = box(vx0, 148, vx1, 248, "Groq Whisper", "speech to text")
bd = box(vx0, 290, vx1, 390, "AWS Bedrock", "Claude Haiku")
pl = box(vx0, 574, vx1, 674, "Amazon Polly", "generative voice, mp3")

arrow((bx1, 198), (sx0, 198), both=True, label="audio to text", label_at=(386, 160))
arrow((bx1, 340), (sx0, 340), label="question", label_at=(398, 304))
arrow((sx1, 198), (vx0, 198), both=True)
arrow((sx1, 340), (vx0, 340), both=True)
arrow((sx1, 624), (vx0, 624), both=True)
arrow(((sx0 + sx1) // 2, 390), ((sx0 + sx1) // 2, 432))
arrow(((sx0 + sx1) // 2, 532), ((sx0 + sx1) // 2, 574))
arrow(((sx0 + sx1) // 2, 674), ((sx0 + sx1) // 2, 716))
# events back to the browser: emitter -> left, up to SSE client
d.line(((sx0, 766), (446, 766), (446, 360), (bx1, 360)), fill="#9AA29C", width=3, joint="curve")
arrow((372, 360), (bx1, 360))
d.text((456, 400), "events", font=font(20), fill=SLATE)
arrow(((bx0 + bx1) // 2, 390), ((bx0 + bx1) // 2, 432))
arrow(((bx0 + bx1) // 2, 532), ((bx0 + bx1) // 2, 574))

for out in (ROOT / "docs" / "architecture.png", ROOT / "static" / "about" / "architecture.png"):
    img.save(out, optimize=True)
    print("wrote", out)
