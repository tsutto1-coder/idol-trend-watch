#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IDOL TREND WATCH - ステージ系ランキング動画生成(クール/コンサート演出テイスト)
outputs/<日付>/ranking_all.json を読み、黒×メタリックのスタイリッシュな
ランキング動画を生成する。

出力(outputs/<日付>/):
  reel.mp4    1080x1920 (9:16)  Instagramリール / ストーリーズ / YouTubeショート用
  tiktok.mp4  1080x1920 (9:16)  TikTok用(reelと同内容。UI被り回避のセーフゾーン設計)
  feed.mp4    1080x1350 (4:5)   Instagramフィード投稿用

デザイン:
  暗転したステージにスポットライトときらめきが舞う、コンサート風の演出。
  金・銀・銅のメタリックグラデーション文字。季節は「差し色」で表現され、
  実行日から自動判定(SEASON_OVERRIDE で固定も可)。

使用素材: 自前で生成したテキスト・図形のみ(権利物は一切使わない)
必要環境: ffmpeg, fonts-noto-cjk, fonts-mplus, pillow
使い方:   python make_video.py            # 今日のフォルダを対象
          python make_video.py 2026-10-05 # 日付指定
"""

import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ===== メディア設定 =====
BRAND = "IDOL TREND WATCH"
TAGLINE = "いま伸びているアイドルコンテンツを毎週届ける速報"
CATEGORIES = [("all", "アイドル")]

SIZES = {"reel": (1080, 1920), "feed": (1080, 1350)}
TIKTOK_FROM = "reel"

SLIDE_SEC = 4.6
FADE = 0.4
FPS = 30

BASE_DIR = Path(__file__).resolve().parent
OUT_ROOT = BASE_DIR / "outputs"
JST = timezone(timedelta(hours=9))

# ============================================================
# 季節テーマ(ステージの「差し色」。自由に編集OK)
# ============================================================
SEASONS = {
    "spring": {"ja": "春", "en": "SPRING",
               "bg_top": (12, 8, 18), "bg_bottom": (44, 22, 48),
               "accent": (255, 158, 196), "glow": (255, 130, 180)},
    "summer": {"ja": "夏", "en": "SUMMER",
               "bg_top": (6, 10, 22), "bg_bottom": (16, 42, 72),
               "accent": (110, 200, 255), "glow": (80, 170, 255)},
    "autumn": {"ja": "秋", "en": "AUTUMN",
               "bg_top": (14, 9, 8), "bg_bottom": (52, 30, 18),
               "accent": (255, 196, 110), "glow": (255, 170, 80)},
    "winter": {"ja": "冬", "en": "WINTER",
               "bg_top": (8, 10, 20), "bg_bottom": (26, 36, 58),
               "accent": (205, 222, 245), "glow": (170, 200, 240)},
}
SEASON_OVERRIDE = None  # "spring"/"summer"/"autumn"/"winter" で固定。Noneなら月から自動

WHITE = (242, 242, 248)
MUTED = (158, 156, 176)
INK_DARK = (22, 16, 38)          # メタリック面の上に載せる濃紺
GOLD_FLAT = (226, 186, 110)

# メタリックグラデーション(上→下の色стопс)
METAL_GOLD = [(255, 240, 190), (230, 186, 100), (140, 95, 40), (255, 230, 150)]
METAL_SILVER = [(248, 250, 255), (185, 196, 214), (105, 118, 140), (238, 243, 252)]
METAL_BRONZE = [(255, 224, 196), (210, 140, 88), (125, 75, 45), (252, 214, 184)]


def season_of(month: int) -> str:
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def rank_label(i: int) -> str:
    return f"第{i + 1}位" if i < 3 else f"{i + 1}位"


# ============================================================
# フォント
# ============================================================
FONT_HEAVY_CANDIDATES = [
    "/usr/share/fonts/opentype/mplus/Mplus2-Black.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
FONT_BOLD_CANDIDATES = [
    "/usr/share/fonts/opentype/mplus/Mplus2-Bold.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]


def pick_font(cands: list[str]) -> str:
    for p in cands:
        if Path(p).exists():
            return p
    out = subprocess.run(["fc-list", ":lang=ja", "file"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        f = line.split(":")[0].strip()
        if f.endswith((".ttc", ".ttf", ".otf")):
            return f
    sys.exit("日本語フォントが見つかりません。'sudo apt-get install fonts-noto-cjk fonts-mplus' を実行してください")


FONT_HEAVY = pick_font(FONT_HEAVY_CANDIDATES)
FONT_BOLD = pick_font(FONT_BOLD_CANDIDATES)
FONT_SYMBOL = pick_font(["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                         FONT_BOLD_CANDIDATES[-1]])  # ★☆用


def stars_text(v) -> str:
    try:
        v = int(v)
        return "★" * v + "☆" * (5 - v)
    except (TypeError, ValueError):
        return ""


def metal_for_rank(i: int):
    return [METAL_GOLD, METAL_SILVER, METAL_BRONZE][i] if i < 3 else None


def grad_column(h: int, stops: list[tuple]) -> list[tuple]:
    """高さhぶんの縦グラデーション色列を作る"""
    out = []
    seg = max(len(stops) - 1, 1)
    for yy in range(h):
        t = yy / max(h - 1, 1) * seg
        i = min(int(t), seg - 1)
        f = t - i
        a, b = stops[i], stops[i + 1]
        out.append(tuple(int(x + (y - x) * f) for x, y in zip(a, b)))
    return out


# ============================================================
# レンダラー(縦1920px基準で設計し、比例縮尺)
# ============================================================
class Renderer:
    def __init__(self, w: int, h: int, season: dict):
        self.w, self.h = w, h
        self.k = h / 1920
        self.s = season

    def f(self, size: int, heavy=True) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(FONT_HEAVY if heavy else FONT_BOLD, max(int(size * self.k), 12))

    def y(self, v: float) -> int:
        return int(v * self.k)

    # --- 背景(暗転ステージ+スポットライト+きらめき) ---
    def bg(self) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        top, bottom = self.s["bg_top"], self.s["bg_bottom"]
        for yy in range(self.h):
            t = yy / self.h
            c = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
            img.paste(c, (0, yy, self.w, yy + 1))

        ov = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        gw = self.s["glow"]

        # スポットライト(上から差す斜めの光)
        beams = [(-0.18, 0.26, 16), (0.52, 0.95, 13), (0.18, 0.62, 9)]
        for x1f, x2f, alpha in beams:
            x1, x2 = int(self.w * x1f), int(self.w * x2f)
            spread = self.y(330)
            d.polygon([(x1, -10), (x2, -10),
                       (x2 + spread, self.h), (x1 + spread, self.h)],
                      fill=(255, 255, 255, alpha))

        rnd = random.Random(11)
        # ボケ光(柔らかい丸)
        for _ in range(16):
            x, yy = rnd.randint(0, self.w), rnd.randint(0, self.h)
            r = self.y(rnd.randint(10, 34))
            a = rnd.randint(14, 46)
            d.ellipse([x - r, yy - r, x + r, yy + r], fill=gw + (a,))
        # きらめき(4方向スターダスト)
        for _ in range(22):
            x, yy = rnd.randint(0, self.w), rnd.randint(0, self.h)
            r = self.y(rnd.randint(8, 26))
            a = rnd.randint(70, 160)
            col = (255, 255, 255, a) if rnd.random() < 0.6 else gw + (a,)
            rw = max(self.y(4), 2)
            d.polygon([(x, yy - r), (x + rw, yy), (x, yy + r), (x - rw, yy)], fill=col)
            d.polygon([(x - r, yy), (x, yy + rw), (x + r, yy), (x, yy - rw)], fill=col)

        return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

    # --- ガラス風パネル(半透明+ゴールド枠) ---
    def panel(self, img: Image.Image, x1, y1, x2, y2) -> Image.Image:
        ov = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        rad = self.y(40)
        d.rounded_rectangle([x1, y1, x2, y2], radius=rad, fill=(255, 255, 255, 14))
        d.rounded_rectangle([x1, y1, x2, y2], radius=rad,
                            outline=GOLD_FLAT + (210,), width=max(self.y(4), 2))
        # 上辺のハイライト
        d.line([x1 + rad, y1 + self.y(3), x2 - rad, y1 + self.y(3)],
               fill=(255, 255, 255, 60), width=max(self.y(2), 1))
        return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

    # --- メタリックグラデ文字 ---
    def metal_text(self, img: Image.Image, yy: int, text: str, fnt, stops) -> int:
        mask = Image.new("L", (self.w, self.h), 0)
        md = ImageDraw.Draw(mask)
        tw = md.textlength(text, font=fnt)
        md.text(((self.w - tw) // 2, yy), text, font=fnt, fill=255)
        grad = Image.new("RGB", (self.w, self.h))
        col = grad_column(int(fnt.size * 1.35), stops)
        for i, c in enumerate(col):
            gy = yy + i
            if 0 <= gy < self.h:
                grad.paste(c, (0, gy, self.w, gy + 1))
        img.paste(grad, (0, 0), mask)
        return yy + fnt.size

    # --- メタリック塗りつぶし円(順位バッジ) ---
    def metal_circle(self, img: Image.Image, cx, cy, r, stops):
        mask = Image.new("L", (self.w, self.h), 0)
        ImageDraw.Draw(mask).ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
        grad = Image.new("RGB", (self.w, self.h))
        col = grad_column(r * 2, stops)
        for i, c in enumerate(col):
            gy = cy - r + i
            if 0 <= gy < self.h:
                grad.paste(c, (0, gy, self.w, gy + 1))
        img.paste(grad, (0, 0), mask)

    # --- テキスト ---
    def wrap(self, d, text, fnt, max_w):
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur); cur = ""
                continue
            if d.textlength(cur + ch, font=fnt) > max_w:
                lines.append(cur); cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines

    def center_wrapped(self, d, yy, text, fnt, fill, max_w, gap=14):
        for line in self.wrap(d, text, fnt, max_w):
            d.text(((self.w - d.textlength(line, font=fnt)) // 2, yy), line, font=fnt, fill=fill)
            yy += fnt.size + self.y(gap)
        return yy

    def center_line(self, d, yy, text, fnt, fill):
        d.text(((self.w - d.textlength(text, font=fnt)) // 2, yy), text, font=fnt, fill=fill)
        return yy + fnt.size

    def tracked_line(self, d, yy, text, fnt, fill, tracking=0.45):
        """字間を広げた英字見出し(コンサートロゴ風)"""
        tr = int(fnt.size * tracking)
        widths = [d.textlength(ch, font=fnt) for ch in text]
        total = sum(widths) + tr * (len(text) - 1)
        x = (self.w - total) // 2
        for ch, cw in zip(text, widths):
            d.text((x, yy), ch, font=fnt, fill=fill)
            x += cw + tr
        return yy + fnt.size

    def star_line(self, d, yy, label, v, fill):
        f1 = self.f(50)
        f2 = ImageFont.truetype(FONT_SYMBOL, max(int(50 * self.k), 12))
        seg1, seg2 = f"{label} ", stars_text(v)
        w1, w2 = d.textlength(seg1, font=f1), d.textlength(seg2, font=f2)
        x = (self.w - (w1 + w2)) // 2
        d.text((x, yy), seg1, font=f1, fill=fill)
        d.text((x + w1, yy + self.y(3)), seg2, font=f2, fill=fill)
        return yy + f1.size

    def outline_pill(self, d, cy, text, fnt, color):
        tw = d.textlength(text, font=fnt)
        pw, ph = tw + self.y(80), fnt.size + self.y(36)
        x1 = (self.w - pw) // 2
        d.rounded_rectangle([x1, cy, x1 + pw, cy + ph], radius=ph // 2,
                            outline=color, width=max(self.y(4), 2))
        d.text(((self.w - tw) // 2, cy + self.y(16)), text, font=fnt, fill=color)
        return cy + ph

    def footer(self, d):
        self.tracked_line(d, self.h - self.y(190), BRAND, self.f(30), MUTED, tracking=0.35)

    # --- 区切り(ゴールドの細線+ダイヤ) ---
    def divider(self, d, yy):
        cx = self.w // 2
        half = self.y(190)
        d.line([cx - half, yy, cx - self.y(30), yy], fill=GOLD_FLAT, width=max(self.y(3), 1))
        d.line([cx + self.y(30), yy, cx + half, yy], fill=GOLD_FLAT, width=max(self.y(3), 1))
        r = self.y(13)
        d.polygon([(cx, yy - r), (cx + r, yy), (cx, yy + r), (cx - r, yy)], outline=GOLD_FLAT,
                  width=max(self.y(3), 1))
        return yy + self.y(40)

    # --- スライド ---
    def cover(self, date_s: str, n: int, label: str) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)
        acc = self.s["accent"]

        yy = self.y(300)
        yy = self.tracked_line(d, yy, BRAND, self.f(36), MUTED) + self.y(60)
        yy = self.outline_pill(d, yy, f"{self.s['en']}・{self.s['ja']}", self.f(40), acc) + self.y(80)

        img = self.panel(img, self.y(70), yy, self.w - self.y(70), yy + self.y(800))
        d = ImageDraw.Draw(img)
        cy = yy + self.y(95)
        cy = self.center_line(d, cy, "今週の", self.f(80), WHITE) + self.y(40)
        cy = self.center_line(d, cy, label, self.f(118), acc) + self.y(60)
        cy = self.metal_text(img, cy, f"TOP{n}", self.f(185), METAL_GOLD) + self.y(75)
        cy = self.divider(d, cy)
        self.center_line(d, cy + self.y(10), date_s, self.f(50), MUTED)

        self.center_wrapped(d, yy + self.y(890), TAGLINE, self.f(34, heavy=False),
                            MUTED, self.w - self.y(130))
        self.footer(d)
        return img

    def cm_slide(self, rank: int, c: dict) -> Image.Image:
        img = self.bg()
        acc = self.s["accent"]
        metal = metal_for_rank(rank) or METAL_GOLD

        # メタリック順位バッジ
        cx, cy, r = self.w // 2, self.y(300), self.y(118)
        d = ImageDraw.Draw(img)
        d.ellipse([cx - r - self.y(14), cy - r - self.y(14),
                   cx + r + self.y(14), cy + r + self.y(14)],
                  outline=GOLD_FLAT, width=max(self.y(3), 1))
        self.metal_circle(img, cx, cy, r, metal)
        d = ImageDraw.Draw(img)
        fnt = self.f(70)
        label = rank_label(rank)
        d.text((cx - d.textlength(label, font=fnt) // 2, cy - self.y(46)),
               label, font=fnt, fill=INK_DARK)

        # コンテンツパネル
        top = self.y(490)
        img = self.panel(img, self.y(70), top, self.w - self.y(70), self.h - self.y(370))
        d = ImageDraw.Draw(img)
        yy = top + self.y(75)
        yy = self.center_wrapped(d, yy, c.get("company", ""), self.f(78), WHITE,
                                 self.w - self.y(260))
        if c.get("product"):
            yy = self.center_wrapped(d, yy + self.y(6), f"「{c['product']}」",
                                     self.f(56), acc, self.w - self.y(260))
        yy += self.y(46)
        yy = self.divider(d, yy) + self.y(26)

        yy = self.center_wrapped(d, yy, c.get("hitokoto", ""), self.f(54, heavy=False),
                                 WHITE, self.w - self.y(300), gap=20)
        kw = c.get("keywords") or []
        if kw:
            yy += self.y(26)
            yy = self.center_wrapped(d, yy, "  ".join(f"#{k}" for k in kw[:4]),
                                     self.f(42), MUTED, self.w - self.y(280))
        r_ = (c.get("ratings") or {}).get("話題性")
        yy += self.y(40)
        if stars_text(r_):
            yy = self.star_line(d, yy, "話題性", r_, GOLD_FLAT) + self.y(34)
        self.center_line(d, yy, f"{c.get('published_at', '')} 公開", self.f(38, heavy=False), MUTED)
        self.footer(d)
        return img

    def list_slide(self, rest: list[dict], total: int) -> Image.Image:
        img = self.bg()
        d = ImageDraw.Draw(img)

        yy = self.y(210)
        yy = self.outline_pill(d, yy, f"4位 〜 {total}位", self.f(60), self.s["accent"]) + self.y(75)
        img = self.panel(img, self.y(70), yy, self.w - self.y(70), self.h - self.y(340))
        d = ImageDraw.Draw(img)

        row_y = yy + self.y(75)
        num_f, name_f = self.f(44), self.f(46)
        left = self.y(150)
        for i, c in enumerate(rest, start=4):
            cr = self.y(42)
            ccx, ccy = left + cr, row_y + self.y(30)
            d.ellipse([ccx - cr, ccy - cr, ccx + cr, ccy + cr],
                      outline=GOLD_FLAT, width=max(self.y(4), 2))
            num_s = str(i)
            d.text((ccx - d.textlength(num_s, font=num_f) // 2, ccy - self.y(31)),
                   num_s, font=num_f, fill=GOLD_FLAT)
            prod = f"「{c['product']}」" if c.get("product") else ""
            text = f"{c.get('company', '')}{prod}"
            full = text
            max_w = self.w - left - self.y(260)
            while text and d.textlength(text, font=name_f) > max_w:
                text = text[:-1]
            if text != full:
                text = text[:-1] + "…"
            d.text((left + self.y(115), row_y), text, font=name_f, fill=WHITE)
            row_y += self.y(142)
        self.footer(d)
        return img

    def outro(self) -> Image.Image:
        img = self.bg()
        top = self.y(620)
        img = self.panel(img, self.y(90), top, self.w - self.y(90), top + self.y(560))
        d = ImageDraw.Draw(img)
        yy = top + self.y(105)
        yy = self.center_line(d, yy, "リンクはすべて", self.f(64), WHITE) + self.y(40)
        yy = self.center_line(d, yy, "投稿本文からチェック", self.f(64), WHITE) + self.y(85)
        self.outline_pill(d, yy, "フォローして最新回をチェック", self.f(44), GOLD_FLAT)
        self.footer(d)
        return img


# ============================================================
# 動画化
# ============================================================
def build_video(slides, out_path: Path):
    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        clips = []
        for i, img in enumerate(slides):
            png = tdir / f"s{i}.png"
            img.save(png)
            clip = tdir / f"c{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-t", str(SLIDE_SEC), "-i", str(png),
                "-vf", (f"fade=t=in:st=0:d={FADE},"
                        f"fade=t=out:st={SLIDE_SEC - FADE}:d={FADE},format=yuv420p"),
                "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "26",
                str(clip),
            ], check=True)
            clips.append(clip)
        lst = tdir / "list.txt"
        lst.write_text("".join(f"file '{c}'\n" for c in clips), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                        "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(out_path)], check=True)


def main():
    date_s = sys.argv[1] if len(sys.argv) > 1 else datetime.now(JST).strftime("%Y-%m-%d")
    day_dir = OUT_ROOT / date_s

    month = int(date_s.split("-")[1])
    season_key = SEASON_OVERRIDE or season_of(month)
    season = SEASONS[season_key]
    print(f"[info] 季節テーマ: {season_key}({season['ja']}/差し色)")
    disp_date = date_s.replace("-", "/")

    made = 0
    for key, label in CATEGORIES:
        data_file = day_dir / f"ranking_{key}.json"
        if not data_file.exists():
            print(f"[warn] {data_file} がないためスキップ")
            continue
        top = json.loads(data_file.read_text(encoding="utf-8"))
        if not top:
            print(f"[warn] {label} のランキングが空のためスキップ")
            continue
        n = len(top)
        outputs = {}
        for size_name, (w, h) in SIZES.items():
            r = Renderer(w, h, season)
            slides = [r.cover(disp_date, n, label)]
            slides += [r.cm_slide(i, top[i]) for i in (0, 1, 2) if i < n]
            if n > 3:
                slides.append(r.list_slide(top[3:], n))
            slides.append(r.outro())
            fname = f"{size_name}.mp4" if key == "all" else f"{key}_{size_name}.mp4"
            out_path = day_dir / fname
            build_video(slides, out_path)
            outputs[size_name] = out_path
            print(f"[info] 動画生成完了: {out_path} ({w}x{h} / "
                  f"{out_path.stat().st_size / 1048576:.1f}MB / 約{SLIDE_SEC * len(slides):.0f}秒)")
            made += 1
        if TIKTOK_FROM in outputs:
            tk = outputs[TIKTOK_FROM].with_name(
                "tiktok.mp4" if key == "all" else f"{key}_tiktok.mp4")
            shutil.copyfile(outputs[TIKTOK_FROM], tk)
            print(f"[info] 動画生成完了: {tk} (TikTok用 / {TIKTOK_FROM}と同内容)")
            made += 1
    if made == 0:
        sys.exit("生成できた動画がありません。先に収集スクリプトを実行してください")


if __name__ == "__main__":
    main()
