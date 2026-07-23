#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IDOL TREND WATCH
「いま伸びているアイドルコンテンツを毎朝届ける速報メディア」

※人物の人気を序列化するものではなく、公式コンテンツ(MV・新曲・パフォーマンス
　映像等)の話題度をデータで集計するランキングです。

毎朝実行すると:
  1. YouTube全体から「新CM」関連の新着動画を検索(特定チャンネル指定不要)
  2. 再生数の伸び・高評価率から「話題性スコア」を算出
  3. Claude APIがCM判定・企業名/商品名の抽出・一言分析・5軸評価を実施
  4. TOP5を選出し、X / Threads / Instagram / note 用のコピペ投稿文を生成
  5. outputs/日付/ に保存(Discord Webhook設定時はそこにも通知)
  6. 6か月(183日)を過ぎたデータはアーカイブから自動削除

使い方:
  環境変数 YOUTUBE_API_KEY, ANTHROPIC_API_KEY を設定して
    python cm_trend_watch.py
  APIキーなしで出力フォーマットを確認したい場合:
    python cm_trend_watch.py --demo
"""

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================================================
# 設定(必要に応じてここを編集)
# ============================================================

CONFIG = {
    # YouTube検索クエリ(この語で新着動画を横断検索する)
    "search_queries": [
        "アイドル MV", "アイドル 新曲", "MV 公開", "Music Video 公開",
        "ダンスプラクティス", "Dance Practice", "アイドル パフォーマンス",
        "新曲 Performance Video",
    ],
    # 何日以内に公開された動画を対象にするか
    "lookback_days": 7,
    # 掲載保持期間(3か月)
    "retention_days": 92,
    # AI分析に回す最大候補数(話題性スコア上位から)
    "max_candidates": 35,
    # 男女それぞれの最終ランキング件数
    "top_n": 10,
    # ランキングの掲載順
    "categories": [("female", "女性アイドル"), ("male", "男性アイドル")],
    # K-POPを含めるか(False なら日本のアイドルのみ)
    "include_kpop": True,
    # 動画の長さ制限(秒)。MV・ダンス動画は30秒〜8分程度
    "min_duration_sec": 30,
    "max_duration_sec": 480,
    # Claudeモデル(コスト重視なら claude-haiku-4-5-20251001)
    "claude_model": "claude-sonnet-4-6",
    # ハッシュタグ
    "hashtags": "#アイドル #新曲 #MV #推し活 #ランキング",
    # メディア名
    "brand": "IDOL TREND WATCH",
    "tagline": "いま伸びているアイドルコンテンツを毎朝届ける速報",
}

JST = timezone(timedelta(hours=9))
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR / "outputs"
ARCHIVE_FILE = DATA_DIR / "archive.json"   # 掲載済みCMの蓄積(週間企画・半年ルールに使用)
SEEN_FILE = DATA_DIR / "seen.json"         # 取り上げ済み動画ID(重複掲載防止)

MEDAL = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# ============================================================
# ユーティリティ
# ============================================================

def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "cm-trend-watch/1.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def http_post_json(url: str, payload: dict, headers: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=120) as res:
        return json.loads(res.read().decode("utf-8"))


def parse_iso_duration(s: str) -> int:
    """ISO8601 (PT1M30S) → 秒"""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m:
        return 0
    h, mi, sec = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + sec


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def jst_now() -> datetime:
    return datetime.now(JST)

# ============================================================
# 1. YouTube収集
# ============================================================

def yt_search(api_key: str, query: str, published_after: str) -> list[str]:
    params = urllib.parse.urlencode({
        "part": "id",
        "q": query,
        "type": "video",
        "regionCode": "JP",
        "relevanceLanguage": "ja",
        "publishedAfter": published_after,
        "order": "viewCount",
        "maxResults": 25,
        "key": api_key,
    })
    data = http_get_json(f"https://www.googleapis.com/youtube/v3/search?{params}")
    return [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]


def yt_videos(api_key: str, ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        params = urllib.parse.urlencode({
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(chunk),
            "key": api_key,
        })
        data = http_get_json(f"https://www.googleapis.com/youtube/v3/videos?{params}")
        out.extend(data.get("items", []))
    return out


CM_TITLE_HINTS = re.compile(r"(MV|Music Video|ミュージックビデオ|新曲|Dance Practice|ダンス|Performance|パフォーマンス|アイドル|IDOL)", re.IGNORECASE)


def collect_candidates(api_key: str, seen_ids: set[str]) -> list[dict]:
    published_after = (jst_now() - timedelta(days=CONFIG["lookback_days"])) \
        .astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ids: list[str] = []
    for q in CONFIG["search_queries"]:
        try:
            ids += yt_search(api_key, q, published_after)
        except Exception as e:
            print(f"[warn] search failed for '{q}': {e}", file=sys.stderr)
    ids = [i for i in dict.fromkeys(ids) if i not in seen_ids]  # 重複除去+既出除外
    if not ids:
        return []

    videos = yt_videos(api_key, ids)
    now = jst_now()
    candidates = []
    for v in videos:
        sn, st = v.get("snippet", {}), v.get("statistics", {})
        dur = parse_iso_duration(v.get("contentDetails", {}).get("duration", ""))
        title = sn.get("title", "")
        if not (CONFIG["min_duration_sec"] <= dur <= CONFIG["max_duration_sec"]):
            continue
        if not CM_TITLE_HINTS.search(title + " " + sn.get("description", "")[:200]):
            continue

        published = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).astimezone(JST)
        days = max((now - published).total_seconds() / 86400, 0.25)
        views = int(st.get("viewCount", 0))
        likes = int(st.get("likeCount", 0) or 0)
        comments = int(st.get("commentCount", 0) or 0)

        # 話題性スコア: 1日あたり再生数 × エンゲージメント補正(対数で暴れを抑制)
        vpd = views / days
        engagement = (likes + comments * 2) / max(views, 1)
        buzz = math.log10(vpd + 1) * (1 + min(engagement * 20, 1.0))

        candidates.append({
            "video_id": v["id"],
            "title": title,
            "channel": sn.get("channelTitle", ""),
            "description": sn.get("description", "")[:600],
            "published_at": published.strftime("%Y-%m-%d"),
            "duration_sec": dur,
            "views": views,
            "likes": likes,
            "comments": comments,
            "views_per_day": round(vpd),
            "buzz_score": round(buzz, 3),
            "url": f"https://www.youtube.com/watch?v={v['id']}",
        })

    candidates.sort(key=lambda c: c["buzz_score"], reverse=True)
    return candidates[:CONFIG["max_candidates"]]

# ============================================================
# 2. Claude分析(CM判定・情報抽出・一言分析・5軸評価)
# ============================================================

ANALYSIS_SYSTEM = """あなたはアイドルコンテンツ専門メディア「IDOL TREND WATCH」の編集AIです。
YouTube動画のメタデータ(タイトル・チャンネル名・説明文・統計)から、アイドルの
公式コンテンツかどうかを判定し、掲載情報を抽出します。

判定基準(is_official_idol=true の条件):
- アイドルグループ/アイドルソロの公式チャンネル(レーベル・事務所公式含む)の投稿
- MV、新曲音源、ダンスプラクティス、パフォーマンス映像、公式企画動画など
- K-POPアイドルも対象。
- ファンによる切り抜き・ファンカム・リアクション・考察動画は false
- アイドルではない一般アーティスト・バンド・VTuberは false

編集ポリシー(重要):
- コメントは楽曲・映像・パフォーマンス・企画内容についてのみ書く
- メンバー個人の外見・容姿・私生活への言及は一切しない
- 人物の優劣ではなく、コンテンツの話題性を伝える表現にする

各動画について以下のJSONを返してください。確認できないことは推測せず null に。

出力は必ず次の形式のJSON配列のみ。前置き・後書き・コードブロック記号は一切不要:
[
  {
    "video_id": "...",
    "is_official_idol": true/false,
    "company": "グループ名(ソロならアーティスト名)",
    "product": "曲名・コンテンツ名 or null",
    "category": "female" / "male" / "mixed" / "other",
    "cast": null,
    "media": "MV" / "ダンス" / "ライブ" / "企画" など or null,
    "keywords": ["ダンス", "夏曲"] (2〜4語),
    "hitokoto": "15〜40文字の一言分析(楽曲・映像・企画の見どころを編集者目線で)",
    "ratings": {"話題性": 1-5, "SNS拡散性": 1-5, "ファン熱量": 1-5,
                 "映像インパクト": 1-5 or null}
  }
]"""


def parse_json_array_lenient(text: str) -> list[dict]:
    """Claudeの返答からJSON配列を取り出す。
    途中で切れていても、完成しているオブジェクトだけ救出する。"""
    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass  # 下の救出処理へ
    # 完全なJSONオブジェクト({...})を先頭から順に拾えるだけ拾う
    dec = json.JSONDecoder()
    objs, i = [], text.find("{")
    while i != -1:
        try:
            obj, consumed = dec.raw_decode(text[i:])
            if isinstance(obj, dict) and obj.get("video_id"):
                objs.append(obj)
            i = text.find("{", i + consumed)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
    if not objs:
        raise RuntimeError("Claudeの返答からJSONを取り出せませんでした:\n" + text[:500])
    print(f"[warn] JSONが不完全だったため {len(objs)} 件を救出して続行します", file=sys.stderr)
    return objs


def analyze_with_claude(candidates: list[dict]) -> list[dict]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")

    payload_videos = [
        {k: c[k] for k in ("video_id", "title", "channel", "description",
                           "published_at", "duration_sec", "views", "views_per_day", "likes")}
        for c in candidates
    ]
    resp = http_post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": CONFIG["claude_model"],
            "max_tokens": 8000,
            "system": ANALYSIS_SYSTEM,
            "messages": [{
                "role": "user",
                "content": "以下の動画リストを分析してください:\n"
                           + json.dumps(payload_videos, ensure_ascii=False),
            }],
        },
        {"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )
    text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
    analyses = {a["video_id"]: a for a in parse_json_array_lenient(text)}

    results = []
    for c in candidates:
        a = analyses.get(c["video_id"])
        if a and a.get("is_official_idol"):
            results.append({**c, **a})
    return results

# ============================================================
# 3. 投稿文生成
# ============================================================

def fmt_views(n: int) -> str:
    return f"{n/10000:.1f}万" if n >= 10000 else f"{n:,}"


def stars(v) -> str:
    return "★" * int(v) + "☆" * (5 - int(v)) if v else "—"


def playlist_url(top: list[dict]) -> str:
    """TOP全件をYouTube上で連続自動再生するURL"""
    ids = ",".join(c["video_id"] for c in top)
    return f"https://www.youtube.com/watch_videos?video_ids={ids}"


def rank_label(i: int) -> str:
    medals = {0: "🥇第1位", 1: "🥈第2位", 2: "🥉第3位"}
    return medals.get(i, f"{i + 1}位")


def podium_order(top: list[dict]) -> list[int]:
    """発表順: 3位→2位→1位(存在するものだけ)"""
    return [i for i in (2, 1, 0) if i < len(top)]


def x_section(top: list[dict], label: str) -> list[str]:
    n = len(top)
    lines = [f"◤ 本日の{label} TOP{n} ◢",
             "",
             "3位からのカウントダウンで発表🧵",
             "",
             CONFIG["hashtags"],
             "\n--- ↓スレッドに続ける ---\n"]
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        lines.append(f"{rank_label(i)} {c['company']}{prod}")
        lines.append(f"📅 {c['published_at']}公開 / ▶️ {fmt_views(c['views'])}回再生")
        lines.append(f"💬 {c['hitokoto']}")
        lines.append(c["url"])
        lines.append("\n---\n")
    rest = top[3:]
    if rest:
        lines.append(f"続いて4位〜{n}位はこちら👇")
        lines.append("")
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"{i}位 {c['company']}{prod}")
            lines.append(c["url"])
        lines.append("\n(長い場合は2ツイートに分割してください)")
    return lines


def render_x(rankings: dict, date_s: str) -> str:
    blocks = [f"【{date_s}】"]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        blocks += x_section(top, label)
        blocks.append("\n========【ここから別スレッド】========\n")
    return "\n".join(blocks)


def threads_section(top: list[dict], label: str) -> list[str]:
    n = len(top)
    lines = [f"🎤 本日の{label} TOP{n}",
             "3位からカウントダウン!", ""]
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        lines.append(f"{rank_label(i)} {c['company']}{prod}")
        lines.append(f"　{c['hitokoto']}")
        lines.append(f"　{c['url']}")
        lines.append("")
    rest = top[3:]
    if rest:
        lines.append(f"— 4位〜{n}位 —")
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"{i}位 {c['company']}{prod} {c['url']}")
        lines.append("")
    lines.append(f"🎬 全部まとめて見る→ {playlist_url(top)}")
    lines.append("")
    return lines


def render_threads(rankings: dict, date_s: str) -> str:
    blocks = [f"({date_s})"]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        blocks += threads_section(top, label)
        blocks.append("========【ここから別投稿】========\n")
    blocks.append(f"※{CONFIG['tagline']}")
    return "\n".join(blocks)


def ig_section(top: list[dict], label: str, date_s: str) -> list[str]:
    n = len(top)
    out = [f"■ {label}用カルーセル原稿",
           "",
           "── スライド1(表紙) ──",
           f"本日の{label} TOP{n}",
           date_s,
           CONFIG["brand"], ""]
    slide_no = 2
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        kw = " / ".join(c.get("keywords") or [])
        r = c.get("ratings") or {}
        out += [f"── スライド{slide_no}({rank_label(i)}) ──",
                f"{rank_label(i)} {c['company']}{prod}",
                f"公開日: {c['published_at']}",
                f"キーワード: {kw}",
                f"話題性 {stars(r.get('話題性'))}  拡散性 {stars(r.get('SNS拡散性'))}",
                f"見どころ: {c['hitokoto']}", ""]
        slide_no += 1
    rest = top[3:]
    if rest:
        out.append(f"── スライド{slide_no}(4位〜{n}位一覧) ──")
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            out.append(f"{i}位 {c['company']}{prod}")
        out.append("")
        slide_no += 1
    out += [f"── スライド{slide_no}(最終) ──",
            "リンクはプロフィール・キャプションから",
            "毎朝7:00更新", "",
            "── キャプション ──",
            f"本日の{label} TOP{n}({date_s})",
            "3位からカウントダウンで発表!", ""]
    for i, c in enumerate(top):
        out.append(f"{i + 1}位 {c['company']} → {c['url']}")
    out += ["", CONFIG["hashtags"] + " #アイドル好きと繋がりたい", ""]
    return out


def render_instagram(rankings: dict, date_s: str) -> str:
    blocks = []
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        blocks += ig_section(top, label, date_s)
        blocks.append("=" * 40)
        blocks.append("")
    return "\n".join(blocks)


def note_section(top: list[dict], label: str) -> list[str]:
    n = len(top)
    lines = [f"# {label} TOP{n}", ""]
    for i in podium_order(top):
        c = top[i]
        prod = f"「{c['product']}」" if c.get("product") else ""
        r = c.get("ratings") or {}
        lines += [f"## {rank_label(i)} {c['company']}{prod}", "",
                  f"- 公開日: {c['published_at']}",
                  f"- 再生数: {fmt_views(c['views'])}回(1日あたり約{fmt_views(c['views_per_day'])}回)"]
        if c.get("media"):
            lines.append(f"- 種別: {c['media']}")
        if c.get("keywords"):
            lines.append(f"- キーワード: {' / '.join(c['keywords'])}")
        lines += ["",
                  f"**一言分析**: {c['hitokoto']}", "",
                  "|話題性|SNS拡散性|ファン熱量|映像インパクト|",
                  "|---|---|---|---|",
                  f"|{stars(r.get('話題性'))}|{stars(r.get('SNS拡散性'))}"
                  f"|{stars(r.get('ファン熱量'))}|{stars(r.get('映像インパクト'))}|", "",
                  f"▶️ [公式チャンネルで見る]({c['url']})", ""]
    rest = top[3:]
    if rest:
        lines += [f"## 4位〜{n}位", ""]
        for i, c in enumerate(rest, start=4):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"**{i}位 {c['company']}{prod}** — {c.get('hitokoto', '')}")
            lines.append(f"　▶️ [公式チャンネルで見る]({c['url']})")
            lines.append("")
    lines.append(f"🎬 [{label} TOP{n}を連続再生でまとめて見る]({playlist_url(top)})")
    lines.append("")
    return lines


def render_note(rankings: dict, date_s: str) -> str:
    lines = [f"# 【{date_s}】本日のアイドルコンテンツランキング|{CONFIG['brand']}", "",
             f"{CONFIG['tagline']}。",
             "YouTube上の新着MV・パフォーマンス映像などを再生数の伸び・エンゲージメントで"
             "自動集計し、AIが分析したデイリーランキングです。人気投票ではなく、"
             "コンテンツの話題度をデータで測る趣旨のランキングです。"
             "映像は各公式チャンネルでご覧ください。", ""]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        if not top:
            continue
        lines += note_section(top, label)
        lines.append("---")
        lines.append("")
    lines += ["※本メディアは画像・動画を保持せず、公式チャンネルへのリンクのみ掲載しています。",
              "※評価はタイトル・説明文・公開統計に基づくAI分析であり、映像自体の視聴評価ではありません。",
              "※コンテンツの話題度を対象としたランキングであり、人物の優劣を示すものではありません。"]
    return "\n".join(lines)


def render_digest(rankings: dict, date_s: str) -> str:
    lines = [f"📋 {CONFIG['brand']} {date_s} 投稿文生成完了", ""]
    for key, label in CONFIG["categories"]:
        top = rankings.get(key) or []
        lines.append(f"◆ {label} TOP{len(top)}")
        for i, c in enumerate(top):
            prod = f"「{c['product']}」" if c.get("product") else ""
            lines.append(f"{i + 1}位 {c['company']}{prod} ({fmt_views(c['views'])}回) {c['url']}")
        if top:
            lines.append(f"▶️ 連続再生チェック: {playlist_url(top)}")
        lines.append("")
    lines += ["outputs/ フォルダの各ファイルを確認してコピペ投稿してください:",
              "x.txt / threads.txt / instagram.txt / note.md / 各動画"]
    return "\n".join(lines)


# ============================================================
# 4. 週間ランキング(月曜のみ)
# ============================================================

def render_weekly(archive: list[dict], today: datetime) -> str | None:
    if today.weekday() != 0:  # 月曜以外はスキップ
        return None
    week_ago = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    pool = [c for c in archive if c.get("ranked_on", "") >= week_ago]
    pool.sort(key=lambda c: c.get("buzz_score", 0), reverse=True)
    top = pool[:5]
    if not top:
        return None
    lines = [f"🏆 今週のバズ・アイドルコンテンツ BEST{len(top)}({week_ago}〜)", ""]
    for i, c in enumerate(top):
        prod = f"「{c.get('product')}」" if c.get("product") else ""
        lines += [f"{MEDAL[i]} {c['company']}{prod}",
                  f"　{c.get('hitokoto','')}",
                  f"　{c['url']}", ""]
    lines.append(CONFIG["hashtags"] + " #今週のベスト")
    return "\n".join(lines)

# ============================================================
# 5. デモデータ(--demo用。実在企業ではなく架空データ)
# ============================================================

DEMO_DATA = [
    {"video_id": "f1", "company": "ソラシド彗星団", "product": "流星前夜",
     "category": "female", "title": "ソラシド彗星団『流星前夜』MV", "channel": "ソラシド彗星団公式",
     "published_at": "2026-07-11", "views": 1240000, "likes": 98000, "comments": 12400,
     "views_per_day": 620000, "buzz_score": 6.9, "cast": None,
     "keywords": ["夏曲", "青春", "疾走感"], "media": "MV",
     "hitokoto": "夜の校舎を一筆書きで駆けるワンカットMVが圧巻",
     "ratings": {"話題性": 5, "SNS拡散性": 5, "ファン熱量": 5, "映像インパクト": 5},
     "url": "https://www.youtube.com/watch?v=f1"},
    {"video_id": "f2", "company": "ミルクティー同盟", "product": "はちみつシンドローム",
     "category": "female", "title": "ミルクティー同盟『はちみつシンドローム』MV", "channel": "ミルクティー同盟公式",
     "published_at": "2026-07-12", "views": 680000, "likes": 54000, "comments": 8100,
     "views_per_day": 680000, "buzz_score": 6.5, "cast": None,
     "keywords": ["王道", "かわいい", "振りコピ"], "media": "MV",
     "hitokoto": "サビ振りの中毒性が高く振りコピ動画が急増中",
     "ratings": {"話題性": 4, "SNS拡散性": 5, "ファン熱量": 4, "映像インパクト": 3},
     "url": "https://www.youtube.com/watch?v=f2"},
    {"video_id": "f3", "company": "週末シネマガールズ", "product": "ラストシーンのその後",
     "category": "female", "title": "週末シネマガールズ『ラストシーンのその後』ダンス", "channel": "週末シネマガールズ公式",
     "published_at": "2026-07-10", "views": 410000, "likes": 31000, "comments": 4200,
     "views_per_day": 137000, "buzz_score": 5.9, "cast": None,
     "keywords": ["ダンス", "エモ", "映画的"], "media": "ダンス",
     "hitokoto": "照明1灯のダンスプラクティスが逆に映画的と話題",
     "ratings": {"話題性": 4, "SNS拡散性": 4, "ファン熱量": 4, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=f3"},
    {"video_id": "f4", "company": "ペパーミント白書", "product": "青とマリン",
     "category": "female", "title": "ペパーミント白書『青とマリン』MV", "channel": "ペパーミント白書公式",
     "published_at": "2026-07-09", "views": 250000, "likes": 19000, "comments": 2600,
     "views_per_day": 62000, "buzz_score": 5.4, "cast": None,
     "keywords": ["爽やか", "海"], "media": "MV",
     "hitokoto": "ドローン海撮×制服の対比が夏の定番狙い",
     "ratings": {"話題性": 3, "SNS拡散性": 3, "ファン熱量": 4, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=f4"},
    {"video_id": "f5", "company": "カナリア放課後", "product": "ないしょのシグナル",
     "category": "female", "title": "カナリア放課後『ないしょのシグナル』企画", "channel": "カナリア放課後公式",
     "published_at": "2026-07-12", "views": 180000, "likes": 22000, "comments": 3900,
     "views_per_day": 180000, "buzz_score": 5.3, "cast": None,
     "keywords": ["企画", "歌ってみた"], "media": "企画",
     "hitokoto": "メンバー同士のパート交換企画がファン考察を誘発",
     "ratings": {"話題性": 3, "SNS拡散性": 4, "ファン熱量": 5, "映像インパクト": 2},
     "url": "https://www.youtube.com/watch?v=f5"},
    {"video_id": "m1", "company": "蒼天ブレイカーズ", "product": "覚醒アラート",
     "category": "male", "title": "蒼天ブレイカーズ『覚醒アラート』MV", "channel": "蒼天ブレイカーズ OFFICIAL",
     "published_at": "2026-07-11", "views": 1580000, "likes": 142000, "comments": 18800,
     "views_per_day": 790000, "buzz_score": 7.1, "cast": None,
     "keywords": ["ダンス", "群舞", "衣装"], "media": "MV",
     "hitokoto": "70人群舞のドローン俯瞰カットが海外でも拡散中",
     "ratings": {"話題性": 5, "SNS拡散性": 5, "ファン熱量": 5, "映像インパクト": 5},
     "url": "https://www.youtube.com/watch?v=m1"},
    {"video_id": "m2", "company": "月曜日のギャラクシー", "product": "重力なんていらない",
     "category": "male", "title": "月曜日のギャラクシー『重力なんていらない』ダンス", "channel": "月曜日のギャラクシー OFFICIAL",
     "published_at": "2026-07-12", "views": 520000, "likes": 47000, "comments": 6300,
     "views_per_day": 520000, "buzz_score": 6.3, "cast": None,
     "keywords": ["アクロバット", "縦動画"], "media": "ダンス",
     "hitokoto": "縦型フルサイズのアクロ構成、ショート転載を先回り",
     "ratings": {"話題性": 4, "SNS拡散性": 5, "ファン熱量": 4, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=m2"},
    {"video_id": "m3", "company": "ノースゲート", "product": "水平線ドライブ",
     "category": "male", "title": "ノースゲート『水平線ドライブ』MV", "channel": "ノースゲート OFFICIAL",
     "published_at": "2026-07-10", "views": 380000, "likes": 29000, "comments": 3800,
     "views_per_day": 127000, "buzz_score": 5.8, "cast": None,
     "keywords": ["夏曲", "ロードムービー"], "media": "MV",
     "hitokoto": "車内定点カメラだけで進むロードムービー型MV",
     "ratings": {"話題性": 4, "SNS拡散性": 3, "ファン熱量": 4, "映像インパクト": 4},
     "url": "https://www.youtube.com/watch?v=m3"},
    {"video_id": "m4", "company": "純情マグネタイト", "product": "N極とS極",
     "category": "male", "title": "純情マグネタイト『N極とS極』ライブ", "channel": "純情マグネタイト OFFICIAL",
     "published_at": "2026-07-09", "views": 210000, "likes": 18000, "comments": 2900,
     "views_per_day": 52000, "buzz_score": 5.2, "cast": None,
     "keywords": ["ライブ", "多幸感"], "media": "ライブ",
     "hitokoto": "ツアー千秋楽映像、客席の合唱まで含めて完成する曲",
     "ratings": {"話題性": 3, "SNS拡散性": 3, "ファン熱量": 5, "映像インパクト": 3},
     "url": "https://www.youtube.com/watch?v=m4"},
    {"video_id": "m5", "company": "真夜中スケッチ", "product": "ネオンの落書き",
     "category": "male", "title": "真夜中スケッチ『ネオンの落書き』MV", "channel": "真夜中スケッチ OFFICIAL",
     "published_at": "2026-07-12", "views": 150000, "likes": 14000, "comments": 2100,
     "views_per_day": 150000, "buzz_score": 5.0, "cast": None,
     "keywords": ["シティポップ", "手描き"], "media": "MV",
     "hitokoto": "実写×手描きアニメの合成が新境地、深夜帯に伸長",
     "ratings": {"話題性": 3, "SNS拡散性": 4, "ファン熱量": 3, "映像インパクト": 5},
     "url": "https://www.youtube.com/watch?v=m5"},
]

# ============================================================
# メイン
# ============================================================

def notify_discord(text: str):
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        return
    try:
        http_post_json(url, {"content": text[:1900]}, {})
        print("[info] Discordに通知しました")
    except Exception as e:
        print(f"[warn] Discord通知失敗: {e}", file=sys.stderr)


def split_by_category(items: list[dict]) -> dict:
    """分析済みリストを男女ランキングに振り分け(mixedは両方に掲載)"""
    rankings = {key: [] for key, _ in CONFIG["categories"]}
    for c in sorted(items, key=lambda x: x.get("buzz_score", 0), reverse=True):
        cat = c.get("category")
        if cat in rankings:
            rankings[cat].append(c)
        elif cat == "mixed":
            for key in rankings:
                rankings[key].append(c)
    return {k: v[:CONFIG["top_n"]] for k, v in rankings.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="APIキーなしで架空データを使い出力を確認")
    args = ap.parse_args()

    today = jst_now()
    date_s = today.strftime("%Y/%m/%d")
    out_dir = OUT_DIR / today.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    seen = set(load_json(SEEN_FILE, []))
    archive = load_json(ARCHIVE_FILE, [])

    if args.demo:
        print("[info] デモモード: 架空データで出力を生成します")
        rankings = split_by_category(DEMO_DATA)
    else:
        yt_key = os.environ.get("YOUTUBE_API_KEY")
        if not yt_key:
            sys.exit("YOUTUBE_API_KEY が設定されていません(動作確認だけなら --demo を付けてください)")
        print("[info] YouTubeから候補を収集中...")
        candidates = collect_candidates(yt_key, seen)
        print(f"[info] 候補 {len(candidates)} 件 → Claudeで分析中...")
        if not candidates:
            sys.exit("本日の新着候補が見つかりませんでした")
        analyzed = analyze_with_claude(candidates)
        rankings = split_by_category(analyzed)
        if not any(rankings.values()):
            sys.exit("公式アイドルコンテンツと判定された動画がありませんでした")

    # 出力生成
    files = {
        "x.txt": render_x(rankings, date_s),
        "threads.txt": render_threads(rankings, date_s),
        "instagram.txt": render_instagram(rankings, date_s),
        "note.md": render_note(rankings, date_s),
        "digest.txt": render_digest(rankings, date_s),
    }
    weekly = render_weekly(archive, today)
    if weekly:
        files["weekly.txt"] = weekly

    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8")
        print(f"[info] 出力: {out_dir / name}")

    # 動画生成(make_video.py)用の構造化データ(男女別)
    for key, _ in CONFIG["categories"]:
        save_json(out_dir / f"ranking_{key}.json", rankings.get(key) or [])
        print(f"[info] 出力: {out_dir / f'ranking_{key}.json'}")

    # 状態更新(デモ時は保存しない)
    if not args.demo:
        for top in rankings.values():
            for c in top:
                seen.add(c["video_id"])
                archive.append({**c, "ranked_on": today.strftime("%Y-%m-%d")})
        cutoff = (today - timedelta(days=CONFIG["retention_days"])).strftime("%Y-%m-%d")
        archive = [c for c in archive if c.get("published_at", "") >= cutoff]
        save_json(SEEN_FILE, sorted(seen))
        save_json(ARCHIVE_FILE, archive)

    notify_discord(files["digest.txt"])
    print("\n" + files["digest.txt"])


if __name__ == "__main__":
    main()
