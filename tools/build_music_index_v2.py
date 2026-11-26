# -*- coding: utf-8 -*-
"""
music index v2 建置管線：yt-dlp 元數據 → wiki 權威映射 → Gemini embedding → index_v2.json

用法（在 repo 根目錄）：
  python tools/build_music_index_v2.py enrich   # yt-dlp 抓 description/tags/留言（可斷點續傳，慢）
  python tools/build_music_index_v2.py wiki     # Limbus wiki 權威場景映射
  python tools/build_music_index_v2.py embed    # Gemini embedding 666 首
  python tools/build_music_index_v2.py merge    # 合併產出 data/music_index/index_v2.json
  python tools/build_music_index_v2.py all      # 依序執行

資料來源：
  - 基礎索引 data/music_index/index.json（666 首、4 歌單）
  - cache/<video_id>.json（yt-dlp 原始輸出，斷點續傳）
  - AstrBot config 的 gemini_embedding provider（金鑰只讀入記憶體，不落盤）

輸出 index_v2.json 結構：
  embedding_model / embedding_dim
  playlists: 原樣
  enrich: { vid: {description, tags, wiki_labels, scenes, moods, comments_digest, duration, view_count} }
  vectors: { vid: [floats] }
"""
import json
import unicodedata
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
INDEX_V1 = REPO / "data" / "music_index" / "index.json"
INDEX_V2 = REPO / "data" / "music_index" / "index_v2.json"
CACHE = REPO / "data" / "music_index" / "cache"
VECTORS_FILE = CACHE.parent / "index_v2_vectors.json"
ASTRBOT_CFG = REPO / "data" / "config" / "abconf_27eb8d48-0c05-431d-b5d2-3cc97651647d.json"

LIMBUS_WIKI = "https://limbuscompany.wiki.gg/api.php"
UA = {"User-Agent": "Mozilla/5.0 (music-index-builder; contact: local)"}

# 規則式場景/情緒標籤：從標題+描述+留言抓（無 LLM 依賴）
SCENE_PATTERNS = [
    # 腦葉公司平常時期的設施 BGM（Neutral01~04，使用者確認）
    (r"\bneutral\d*\b", "Neutral"), (r"平常", "Neutral"), (r"日常", "Neutral"),
    # 警報（腦葉／廢墟圖書館皆有 First/Second/Third Warning；中文作「警報」）
    (r"\b(first|second|third)\s+warning\b", "Warning"),
    (r"\bwarning\b", "Warning"), (r"警報", "Warning"), (r"警报", "Warning"),
    (r"\blobby\b", "Lobby"), (r"大廳", "Lobby"),
    (r"\bmain\s*menu\b|\bmenu\s*theme\b", "Main Menu"),
    (r"\bbattle\b(?!.*boss)", "Battle"), (r"戰鬥", "Battle"),
    (r"\bboss\b", "Boss Battle"),
    (r"\bending\b", "Ending"), (r"\bcredits?\b", "Ending"),
    (r"\bcutscene\b|\bstory\b", "Cutscene"),
    (r"\btitle\s*theme\b", "Title"),
    (r"refraction\s*railway", "Refraction Railway"),
    (r"walpurgis", "Walpurgis Night"),
    (r"mirror\s*dungeon", "Mirror Dungeon"),
]
MOOD_PATTERNS = [
    (r"\bsad\b|\bmelanchol|\bsorrow|\bgrief", "Sad"),
    (r"\bcalm\b|\brelax|\bpeaceful", "Calm"),
    (r"\bintense\b|\btense\b|\baggressive", "Intense"),
    (r"\bepic\b|\bgrand\b", "Epic"),
    (r"\bominous\b|\bdark\b|\bhaunting", "Dark"),
    (r"\bhopeful\b|\buplifting\b", "Hopeful"),
    (r"\bemotional\b|\btouching\b|\bmoving", "Emotional"),
    (r"治癒|療癒|療癒", "Healing"),
    (r"悲傷|難過|感動", "Emotional"),
    (r"燃|熱血", "Intense"),
]


def load_v1() -> list[dict]:
    with open(INDEX_V1, "r", encoding="utf-8") as f:
        return json.load(f).get("playlists", [])


def iter_tracks(playlists: list[dict]):
    seen = set()
    for pl in playlists:
        for t in pl.get("tracks", []):
            vid = t.get("video_id", "")
            if vid and vid not in seen:
                seen.add(vid)
                yield vid, t.get("title", ""), pl.get("name", "")


# --------------------------------------------------------------------------- #
# Stage A: yt-dlp enrich
# --------------------------------------------------------------------------- #
def cmd_enrich() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    playlists = load_v1()
    tracks = list(iter_tracks(playlists))
    total = len(tracks)
    print(f"[enrich] 共 {total} 首，cache 目錄 {CACHE}")

    done = fail = 0
    for i, (vid, title, plname) in enumerate(tracks, 1):
        cache_file = CACHE / f"{vid}.json"
        if cache_file.exists() and cache_file.stat().st_size > 100:
            done += 1
            continue
        url = f"https://www.youtube.com/watch?v={vid}"
        cmd = [
            "yt-dlp", "-J", "--skip-download", "--no-warnings",
            "--sleep-requests", "0.3",
            "--extractor-args", "youtube:max_comments=20,0;comment_sort=top",
            url,
        ]
        try:
            raw = subprocess.run(cmd, capture_output=True, timeout=90)
            data = json.loads(raw.stdout.decode("utf-8", errors="replace"))
            slim = {
                "video_id": vid,
                "title": data.get("title", title),
                "description": (data.get("description") or "")[:2000],
                "tags": (data.get("tags") or [])[:30],
                "duration": data.get("duration"),
                "view_count": data.get("view_count"),
                "channel": data.get("channel") or data.get("uploader"),
                "comments": [
                    {
                        "text": (c.get("text") or "")[:300],
                        "likes": c.get("like_count", 0),
                    }
                    for c in (data.get("comments") or [])[:20]
                ],
            }
            cache_file.write_text(json.dumps(slim, ensure_ascii=False), encoding="utf-8")
            done += 1
        except Exception as e:
            fail += 1
            print(f"[enrich] {i}/{total} {vid} FAIL: {type(e).__name__}: {str(e)[:100]}")
        if i % 25 == 0 or i == total:
            print(f"[enrich] {i}/{total} done={done} fail={fail}", flush=True)
        time.sleep(0.2)
    print(f"[enrich] 完成 done={done} fail={fail}")


# --------------------------------------------------------------------------- #
# Stage B: Limbus wiki 權威映射
# --------------------------------------------------------------------------- #
def cmd_wiki() -> None:
    url = (f"{LIMBUS_WIKI}?action=parse&page=Soundtrack"
           f"&prop=wikitext&format=json&formatversion=2")
    req = urllib.request.Request(url, headers=UA)
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
    text = data["parse"]["wikitext"]

    # video_id -> 同行場景文字（章節/活動名）
    mapping = {}
    for m in re.finditer(
        r"https?://(?:youtu\.be/|www\.youtube\.com/watch\?v=)([A-Za-z0-9_-]{11})([^\n]*)", text
    ):
        vid, rest = m.group(1), m.group(2).strip()
        # 去除 wiki 尾碼符號，保留場景描述
        label = rest.strip("|] \t")
        if label:
            mapping.setdefault(vid, []).append(label)
    print(f"[wiki] 解析出 {len(mapping)} 個影片的權威場景標籤")

    out = CACHE / "_wiki_labels.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(mapping, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[wiki] 已寫入 {out}")


# --------------------------------------------------------------------------- #
# Stage C: Gemini embedding（金鑰從 AstrBot config 讀，不落盤）
# --------------------------------------------------------------------------- #
def _embed_cfg() -> tuple[str, str, str]:
    """回傳 (backend, key_or_None, model)。

    優先序：環境變數 EMBED_BASE_URL/EMBED_API_KEY/EMBED_MODEL（OpenAI 相容 /v1/embeddings）
    > LM Studio（本機，config 有 source）> AstrBot gemini_embedding。
    """
    import os
    base = os.environ.get("EMBED_BASE_URL", "")
    if base:
        return ("openai", os.environ.get("EMBED_API_KEY", ""), os.environ.get("EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5"))
    # NVIDIA NIM（nvapi- 金鑰，序列呼叫）；環境變數或 HKCU\Environment registry
    nv_key = os.environ.get("NVIDIA_API_KEY", "")
    if not nv_key:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                nv_key, _ = winreg.QueryValueEx(k, "NVIDIA_API_KEY")
        except Exception:
            pass
    if nv_key:
        return ("nvidia", nv_key, os.environ.get("EMBED_MODEL", "nvidia/nemotron-3.5-embed-8b"))
    # LM Studio 本機（config 的 source 有 base）
    try:
        with open(ASTRBOT_CFG, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        for src in cfg.get("provider_sources", []):
            if "lm_studio" in str(src.get("id", "")):
                base = (src.get("api_base") or "").rstrip("/")
                if base:
                    # 探測伺服器是否在跑 + 有 embedding 模型
                    try:
                        req = urllib.request.Request(f"{base}/models")
                        with urllib.request.urlopen(req, timeout=3) as r:
                            models = [m.get("id", "") for m in json.loads(r.read().decode()).get("data", [])]
                        emb = next((m for m in models if "embed" in m.lower()), None)
                        if emb:
                            key = (src.get("key") or [""])[0]
                            return ("openai", key, emb)
                    except Exception:
                        pass
        for pr in cfg.get("provider", []):
            if pr.get("type") == "gemini_embedding" and pr.get("enable"):
                if pr.get("embedding_api_key"):
                    return ("gemini", pr["embedding_api_key"], pr.get("embedding_model", "gemini-embedding-001"))
    except Exception:
        pass
    raise RuntimeError(
        "無可用 embedding 後端。選項：\n"
        "  1. 啟動 LM Studio 並載入 embedding 模型（自動偵測）\n"
        "  2. 設環境變數 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL（OpenAI 相容 /v1/embeddings）\n"
        "  3. 修正 AstrBot config 的 gemini_embedding 金鑰"
    )


def _embed_batch(backend: str, key: str, model: str, texts: list[str]) -> list[list[float]]:
    if backend == "gemini":
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
               f"?key={key}")
        body = {
            "requests": [
                {"model": f"models/{model}", "content": {"parts": [{"text": t}]}}
                for t in texts
            ]
        }
        headers = {**UA, "Content-Type": "application/json"}
    elif backend == "nvidia":
        # NVIDIA NIM：OpenAI 相容 /v1/embeddings，input_type=query，序列呼叫
        url = "https://integrate.api.nvidia.com/v1/embeddings"
        body = {"model": model, "input": texts, "input_type": "query",
                "encoding_format": "float", "truncate": "END"}
        headers = {**UA, "Content-Type": "application/json", "Accept": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
    else:  # openai 相容 /v1/embeddings（LM Studio 等）
        base = __import__("os").environ.get("EMBED_BASE_URL", "http://localhost:1234/v1").rstrip("/")
        url = f"{base}/embeddings"
        body = {"model": model, "input": texts}
        headers = {**UA, "Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read().decode("utf-8"))
    if backend == "gemini":
        return [e["values"] for e in resp["embeddings"]]
    return [d["embedding"] for d in resp["data"]]


def _track_text(vid: str, title: str, plname: str, enrich: dict, wiki: dict) -> str:
    e = enrich.get(vid, {})
    wl = wiki.get(vid, [])
    parts = [
        title,
        f"playlist: {plname}",
        f"tags: {', '.join(e.get('tags', [])[:10])}",
        f"scenes: {', '.join(e.get('scenes', []))}",
    ]
    if wl:
        parts.append(f"in-game: {', '.join(wl[:3])}")
    ig = e.get("ingame", [])
    if ig:
        parts.append(f"internal track names: {', '.join(ig)}")
    fac = e.get("factions", [])
    if fac:
        parts.append(f"factions: {', '.join(fac)}")
    ch = e.get("chapters", [])
    if ch:
        parts.append(f"chapters: {', '.join(ch)}")
    pl = e.get("played", [])
    if pl:
        parts.append(f"used for: {', '.join(pl)}")
    desc = (e.get("description") or "")[:300].replace("\n", " ")
    if desc:
        parts.append(f"desc: {desc}")
    cd = (e.get("comments_digest") or "")[:200].replace("\n", " ")
    if cd:
        parts.append(f"listeners: {cd}")
    return " | ".join(parts)


def cmd_embed() -> None:
    backend, key, model = _embed_cfg()
    print(f"[embed] backend={backend} model={model}")
    playlists = load_v1()
    enrich, wiki = _load_enrich(), _load_wiki()

    vids, texts = [], []
    for vid, title, plname in iter_tracks(playlists):
        vids.append(vid)
        texts.append(_track_text(vid, title, plname, enrich, wiki))

    vectors = {}
    B = 16 if backend == "nvidia" else 50  # NVIDIA 序列呼叫 + 4096 維，小批次
    for i in range(0, len(texts), B):
        batch_t, batch_v = texts[i:i + B], vids[i:i + B]
        for attempt in range(3):
            try:
                embs = _embed_batch(backend, key, model, batch_t)
                break
            except Exception as e:
                if attempt == 2:
                    print(f"[embed] 批次 {i} 三次失敗，跳過: {e}")
                    embs = None
                else:
                    time.sleep(2 * (attempt + 1))
        if backend == "nvidia":
            time.sleep(0.3)  # 序列約束：批次間隔
        if embs:
            for v, e in zip(batch_v, embs):
                vectors[v] = [round(x, 6) for x in e]
        print(f"[embed] {min(i + B, len(vids))}/{len(vids)}", flush=True)
        time.sleep(0.5)

    out = CACHE / "_vectors.json"
    out.write_text(json.dumps(vectors), encoding="utf-8")
    (CACHE / "_vectors_meta.json").write_text(
        json.dumps({"model": model, "dim": len(next(iter(vectors.values()))) if vectors else 0}),
        encoding="utf-8")
    print(f"[embed] 完成 {len(vectors)} 條向量 → {out}")


# --------------------------------------------------------------------------- #
# Stage D: merge → index_v2.json
# --------------------------------------------------------------------------- #
# 遊戲內 OST 曲目名（廢墟圖書館 80 首；YouTube 上傳名 ↔ 遊戲內部名映射）
# 例：YouTube "Keter Battle Theme 1" ↔ 遊戲內 KeterBattle01
#     YouTube "Roland Realization Theme 2" ↔ 遊戲內 Roland02
# 五指勢力提示詞：留言/描述提及即自動掛標籤（Canto IX Boss 曲 ↔ 五指戰）
# ⚠️ 標題與留言分級：標題可用弱詞（曲名可信）；留言用強詞（"index" 在 AI 樂評泛用會誤掛）
_FINGER_HINTS_TITLE = {
    "Thumb": ["thumb", "valencina", "瓦倫希娜", "瓦伦希娜", "拇指"],
    "Index": ["index", "rien", "里恩", "食指"],
    "Middle": ["middle", "matthias", "馬蒂亞斯", "马蒂亚斯", "中指"],
    "Ring": ["ring", "callisto", "卡利斯托", "環指", "环指", "無名指"],
    "Pinky": ["pinky", "araya", "阿賴耶", "阿赖耶", "yoru", "小指"],
}
_FINGER_HINTS_TEXT = {
    "Thumb": ["thumb", "valencina", "瓦倫希娜", "瓦伦希娜", "拇指"],
    # ⚠️ Rien 不入 hints：Mili 歌詞/留言大量法語 "rien"（=nothing）會誤掛；
    #    改用大小寫敏感 regex \bRien\b（見 _faction_labels）
    "Index": ["the index", "index nursefather", "call new index", "里恩", "食指"],
    "Middle": ["middle", "matthias", "馬蒂亞斯", "马蒂亚斯", "中指"],
    "Ring": ["ring nursefather", "callisto", "卡利斯托", "環指", "环指", "無名指"],
    "Pinky": ["pinky", "araya", "阿賴耶", "阿赖耶", "yoru", "小指"],
}


# 權威五指/父輩 → 曲目映射（video_id；覆寫自動挖掘結果）
_FINGER_TRACK_MAP = {
    # Araya（小指現任父輩）
    "oyz_gYE4hOU": ["Pinky", "Araya"],   # Canto IX Boss 5 Battle Theme
    "xzF14a_YQvY": ["Pinky", "Araya"],   # Canto IX Boss 9 Battle Theme
    "b8fKDXWH-kI": ["Pinky", "Araya"],   # Canto IX Boss 10 Battle Theme
    "IAEMa4pdtVo": ["Pinky", "Araya"],   # Yumenomaichiru
    # Index（食指）
    "CbUlPeCDBSs": ["Index", "Rien"],    # Call New Index
    "GKje6sxXszQ": ["Index", "Rien"],    # Canto IX Boss 4 Battle Theme
    # Ring（環指）
    "cAodjNjtYQs": ["Ring"],             # Cut Your Rings
    "BuzAgy_88HM": ["Ring"],             # Canto IX Boss 3 Battle Theme 2
    "7u74j6KOa3c": ["Ring"],             # Canto IX Boss 7 Battle Theme
    # Thumb（拇指）
    "W-BobfNM1tg": ["Thumb"],            # Canto IX Boss 1 Battle Theme 2
    # Rien（食指父輩）：Boss 4 兩個變體 + Call New Index Cut
    "JPfBZsjiPrE": ["Index", "Rien"],    # Canto IX Boss 4 Battle Theme 1
    "CCU43wbwq5g": ["Index", "Rien"],    # Canto IX Boss 4 Battle Theme 2
    "bhvRFPD8KtY": ["Index", "Rien"],    # Call New Index Cut
}


# 用途映射（wiki.gg "Played During" / Studio EIM 官方表）：遊戲內名 → Boss 名/用途標籤
_PLAYED_MAP = {
    # Canto IX 五指戰（Studio EIM 官方 Boss 名）
    "canto ix boss 1 battle theme 2": ["Valencina", "Lucio", "Thumb"],
    "canto ix boss 6 battle theme": ["Matthias", "Kira", "Middle"],
    "canto ix boss 7 battle theme": ["Sinclair"],
    "canto ix boss 10 battle theme": ["Ryoshu", "Muga", "Pinky"],
    # 歷章 Boss
    "canto 5 ricardo battle theme": ["Ricardo"],
    "canto 5 ahab battle theme": ["Ahab"],
    "canto 5.5 bamboo-hatted kim battle theme": ["Bamboo-hatted Kim"],
    "canto 6.5 the time ripper battle theme": ["Time Ripper"],
    "canto 6 boss battle theme": ["Heathcliff"],
    "canto 7 boss battle 1 theme": ["The Barber"],
    "canto 7 boss battle 2 theme": ["Dulcinea"],
    "canto 7 boss battle 3 theme": ["Bloodfiend Trio"],
    "canto 7 boss battle 4 theme": ["Sancho"],
    "s.e.a boss battle theme": ["S.E.A."],
    "intervallo vi-ex boss 1 battle theme": ["Fabius"],
    "intervallo vi-ex boss 3 battle theme": ["Mayors"],
    "intervallo vii-1 boss battle theme": ["Rufo"],
    "intervallo vii-2 boss battle theme": ["Im Gyeong-eop"],
    "canto x boss 1 battle theme": ["Crimson God"],
    "canto x boss 2 battle theme": ["Le Noir"],
    "canto x boss 3 battle theme": ["The Warden"],
    "canto x boss 4 battle theme": ["The Chaplain"],
    "canto x boss 5 battle theme": ["Crimson God"],
    # Mili 主題曲用途（wiki.gg Major Songs）
    "in hell we live, lament": ["Opening"],
    "between two worlds": ["Canto III", "Dungeon"],
    "fly, my wings": ["Canto IV", "Dungeon"],
    "compass": ["Canto V", "Dungeon"],
    "through patches of violet": ["Canto VI", "Dungeon"],
    "hero": ["Canto VII", "Dungeon"],
    "tian tian": ["Canto VIII"],
    "saikai": ["Canto IX"],
    "rendezvous": ["Canto X"],
}

# 章節編號 → 羅馬字（供 "Canto 6 Boss" → "Canto VI" 標準化）
# 父輩角色 ↔ 曲目（使用者 2026-11-17 指定：Rien 查詢要 SAIKAI 第一）
# 寫入 played（角色/用途標籤），不污染 factions（五指勢力）
_FATHER_TRACK_MAP = {
    "h0djuhl97Kw": ["Rien"],   # Mili - SAIKAI（Canto IX 高潮曲）
    "JFowD2qjdGg": ["Rien"],   # SAIKAI (Instrumental)
}
_ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI",
          "7": "VII", "8": "VIII", "9": "IX", "10": "X"}


# Boss 名提示詞：留言/描述提及即掛 played 標籤（wiki.gg "Played During" 對照，
# 已由快取留言實證：Ricardo→Canto V Boss 1、Ahab→Canto V Boss 2、Dulcinea→Canto VII Boss 2/3 等）
_BOSS_HINTS = ["ricardo", "ahab", "bamboo-hatted kim", "bamboo hatted kim",
               "heathcliff", "time ripper", "the barber", "dulcinea",
               "bloodfiend trio", "sancho", "rufo", "im gyeong-eop",
               "fabius", "mayors", "sweepers", "crimson god", "le noir",
               "the warden", "the chaplain", "valencina", "lucio", "kira",
               "ryoshu", "muga ryo", "sinclair"]


def _boss_labels(blob: str) -> list[str]:
    """由留言/描述自動挖掘 Boss 名標籤（保留原始大小寫形式）。"""
    low = blob.lower()
    out = []
    for h in _BOSS_HINTS:
        if h in low:
            # 以標題式大小寫呈現（the barber → The Barber）
            out.append(" ".join(w if w.isupper() else w.capitalize()
                                for w in h.split()))
    return out


def _played_labels(title: str) -> list[str]:
    """由 YouTube 標題（標準化後）查用途映射。"""
    norm = _norm_track(title)
    base = re.sub(r"\s*\([^)]*\)\s*", " ", norm).strip()
    for probe in (norm, base):
        if probe in _PLAYED_MAP:
            return list(_PLAYED_MAP[probe])
    # "Canto N" 數字形式標準化為羅馬字再查
    def _romanize(s):
        return re.sub(r"\bcanto (\d+)\b",
                      lambda m: f"canto {_ROMAN.get(m.group(1), m.group(1))}", s)
    for probe in (_romanize(norm), _romanize(base)):
        if probe in _PLAYED_MAP:
            return list(_PLAYED_MAP[probe])
    return []


def _faction_labels(title: str, text: str) -> list[str]:
    """由標題（弱詞可）+ 留言/描述（強詞）自動偵測五指勢力關聯（次要標籤）。"""
    tl, low = title.lower(), text.lower()
    out = []
    for f in _FINGER_HINTS_TITLE:
        if (any(h in tl for h in _FINGER_HINTS_TITLE[f])
                or any(h in low for h in _FINGER_HINTS_TEXT[f])):
            out.append(f)
    # Rien：大小寫敏感（法語小寫 rien=nothing 不觸發；專名 Rien 才觸發）
    if "Index" not in out and re.search(r"\bRien\b", text):
        out.append("Index")
    return out


_INGAME_CHARS = ["Malkuth", "Yesod", "Hod", "Netzach", "Tiphereth", "Gebura",
                 "Chesed", "Binah", "Hokma", "Keter", "Roland", "Angela"]


# 遊戲內 OST 清單（使用者提供之遊戲內部曲目名，權威來源）
_LIMBUS_INGAME_FILE = CACHE / "limbus_ingame_ost.txt"   # Limbus 10 CD
_RUINA_INGAME_FILE = CACHE / "ruina_ingame_ost.txt"     # 廢墟圖書館 80 首


def _load_ingame_file(p) -> set[str]:
    """解析遊戲內 OST 清單檔。兩種格式：
    Limbus（CD 行/曲名行/時長行/MB 行）與 Ruina（編號. 曲名）。"""
    if not p.exists():
        return set()
    names = set()
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^\d+\s+\d+\.?\s*$", line):
            continue
        if re.match(r"^\d{1,2}:\d{2}\s*$", line) or "MB" in line:
            continue
        line = re.sub(r"^\d+\.\s*", "", line)  # Ruina 編號前綴
        if line:
            names.add(line)
    return names


_LIMBUS_INGAME = _load_ingame_file(_LIMBUS_INGAME_FILE)
_RUINA_INGAME = _load_ingame_file(_RUINA_INGAME_FILE)


# 歌單標題前綴（依序剝離；長前綴先試）
_TITLE_PREFIXES = [
    r"^limbus company ost\s*-\s*",
    r"^\[?\s*limbus company\s*-\s*ost\s*\]?\s*",
    r"^limbus company\s*-\s*",
    r"^library of ruina ost\s*-\s*",
    r"^library of ruina st\s*-\s*",
    r"^\[?\s*library of ruina\s*\]?\s*-\s*",
    r"^\[?\s*limbus company\s*\]?\s*-\s*",
    r"^mili\s*-\s*",
    r"^\[?\s*limbus company - ost\s*\]?\s*",
]


def _norm_track(s: str) -> str:
    """標準化曲名以比對（剝前綴、數字去零、壓空白）。"""
    s = s.strip()
    for pat in _TITLE_PREFIXES:
        s2 = re.sub(pat, "", s, flags=re.I)
        if s2 != s:
            s = s2
            break
    # 剝尾隨的方括號歌單標記（[Limbus Company]、[ Library Of Ruina ]）與開頭 (Spoilers)
    s = re.sub(r"\s*\[[^\]]*\]\s*$", "", s.strip())
    s = re.sub(r"^\(?\s*spoilers\s*\)?\s*", "", s.strip(), flags=re.I)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"\b0(\d)\b", r"\1", s)
    return s


def _chapter_labels(name: str) -> list[str]:
    """由遊戲內曲名提取章節標籤（Canto I~X / Intervallo / Walpurgis）。"""
    out = []
    m = re.search(r"\bCanto\s+(I{1,3}|IV|V|VI{1,3}|IX|X)\b", name, re.I)
    if m:
        out.append(f"Canto {m.group(1)}")
    m = re.search(r"\bIntervallo\s+([IVX]+(?:-\d+)?)\b", name, re.I)
    if m:
        out.append(f"Intervallo {m.group(1)}")
    if "walpurgis" in name.lower():
        out.append("Walpurgis Night")
    return out


_ALIAS_STOP = {"story", "theme", "the", "version", "ver", "instrumental",
               "cut", "bgm", "ost"}


def _fold(s: str) -> str:
    """Unicode 變音符號摺疊（Erlkönig→Erlkonig、Zàng Huā Yín→Zang Hua Yin）。"""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _tok(s: str) -> list[str]:
    """標準化 token：小寫、變音符摺疊、剝標點、數字去零。"""
    s = re.sub(r"[_\W]+", " ", _fold(s).lower())
    toks = []
    for t in s.split():
        t = re.sub(r"^0+(?=\d)", "", t)
        if t:
            toks.append(t)
    # 縮寫：全單字元 token 黏回一個（S.E.A. → sea）
    if len(toks) > 1 and all(len(t) == 1 for t in toks):
        return ["".join(toks)]
    return toks


def _subset_match(query: str, cand: str) -> bool:
    """query 的每個具區辨度 token 都是 cand 的 token（或前綴 ≥4 字元）。
    例：Depressed⊆Depressed Story Theme、Extract 2⊆Extraction Theme 2、Casino 01⊆Casino Story Theme 1。"""
    qt = [t for t in _tok(query) if t not in _ALIAS_STOP]
    ct = _tok(cand)
    if not qt:
        return False
    dist = [t for t in qt if len(t) >= 3]
    if not dist:
        return False  # 無具區辨度 token（如純數字）不猜
    for t in qt:
        if not (t in ct or any(c != t and len(t) >= 4 and c.startswith(t)
                               for c in ct)):
            # 複合名：候選去空白後包含查詢 token（chesed⊆chesedbattle01、train⊆warptrain）
            # ⚠️ 下限 4：3 字元的 "vii" 會被 "cantoviii..." 誤放行
            cj = "".join(ct)
            if not (len(t) >= 4 and t in cj):
                return False
    # 至少一個 token 完全命中、前綴 ≥5 字元，或複合候選包含 ≥4 字元 token
    cj = "".join(ct)
    return any(t in ct
               or (len(t) >= 5 and any(c.startswith(t) for c in ct))
               or (len(t) >= 4 and t in cj)
               for t in dist)


def _ingame_match(title: str, ingame: set[str]) -> tuple[list[str], list[str]]:
    """YouTube 標題 ↔ 遊戲內曲目名比對。回（遊戲內名別名, 章節標籤）。
    四級比對：全名 → 剝括號基底 → token 子集（轉載短名）→ 去空白黏合（LaMancha）。"""
    if not ingame:
        return [], []
    norm = _norm_track(title)
    aliases, chapters = [], []
    if norm in {_norm_track(n) for n in _LIMBUS_INGAME}:
        for n in _LIMBUS_INGAME:
            if _norm_track(n) == norm:
                aliases.append(n)
                chapters = _chapter_labels(n)
                break
        return aliases, chapters
    # 剝括號基底比對
    base = re.sub(r"\s*\([^)]*\)\s*", " ", norm).strip()
    if base and base != norm:
        for n in _LIMBUS_INGAME:
            nb = re.sub(r"\s*\([^)]*\)\s*", " ", _norm_track(n)).strip()
            if nb == base:
                aliases.append(n)
                chapters = _chapter_labels(n)
                break
        if aliases:
            return aliases, chapters
    # token 子集比對（轉載短名）：取最短候選（差異最小）
    probes = [p for p in (base, norm) if p]
    # 去空白黏合探針（複合名：la mancha carnival 2 ↔ LaMancha Carnival 2）
    joined = [p.replace(" ", "") for p in probes]
    for probe in probes + joined:
        # 先試 token 子集；黏合探針改為「去空白全等」（複合名 LaMancha）
        if probe in joined and probe not in probes:
            cands = [n for n in ingame
                     if n.replace(" ", "").lower() == probe]
        else:
            cands = [n for n in ingame if _subset_match(probe, n)]
        if len(cands) == 1:
            aliases = [cands[0]]
            chapters = _chapter_labels(cands[0])
            break
        if len(cands) > 1:
            # 多候選時取 token 數最接近者（用停用詞過濾後的探針長度為基準）；
            # 仍並列則放棄（不猜）
            ref = len([t for t in _tok(probe) if t not in _ALIAS_STOP])
            best = min(cands, key=lambda n: abs(len(_tok(n)) - ref))
            ties = [n for n in cands
                    if abs(len(_tok(n)) - ref) == abs(len(_tok(best)) - ref)]
            if len(ties) > 1:
                break
            aliases = [best]
            chapters = _chapter_labels(best)
            break
    return aliases, chapters


def _limbus_ingame_aliases(title: str) -> tuple[list[str], list[str]]:
    """Limbus + Ruina 兩份遊戲內清單合併比對。"""
    a, c = _ingame_match(title, _LIMBUS_INGAME)
    if not a:
        a, c = _ingame_match(title, _RUINA_INGAME)
    return a, c


def _ingame_aliases(title: str) -> list[str]:
    """由 YouTube 標題推導遊戲內曲目名（供查詢 'RolandBattle01' 這類內部名命中）。"""
    aliases: list[str] = []
    for c in _INGAME_CHARS:
        if not re.search(rf"\b{c}\b", title, re.I):
            continue
        m = re.search(rf"{c}\s+Battle\s+Theme\s*(\d)?", title, re.I)
        if m:
            n = int(m.group(1)) if m.group(1) else None
            aliases.append(f"{c}Battle0{n}" if n else f"{c}Battle")
            continue
        if re.search(rf"{c}\s+Realization\s+Story", title, re.I):
            aliases.append(f"{c}Story")
            continue
        m = re.search(rf"{c}\s+Realization\s+Theme\s*(\d)?", title, re.I)
        if m:
            # 遊戲內 Roland01~03/Angela01~03（戰鬥曲）→ YouTube 上傳名
            # "Roland Realization Theme 1~3"；雙別名：Roland01 與 RolandBattle01 同曲
            n = int(m.group(1)) if m.group(1) else None
            if n:
                aliases += [f"{c}0{n}", f"{c}Battle0{n}"]
            else:
                aliases.append(c)
            continue
        aliases.append(c)
    return aliases


def _load_enrich() -> dict:
    return _merge_enriched()


def _load_wiki() -> dict:
    f = CACHE / "_wiki_labels.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _derive_labels(text: str) -> tuple[list[str], list[str]]:
    scenes, moods = [], []
    for pat, label in SCENE_PATTERNS:
        if re.search(pat, text, re.I):
            scenes.append(label)
    for pat, label in MOOD_PATTERNS:
        if re.search(pat, text, re.I):
            moods.append(label)
    return scenes[:6], moods[:6]


def _merge_enriched() -> dict:
    """cache/*.json + wiki labels + 規則標籤 → 統一 enrich dict。"""
    wiki = _load_wiki()
    # vid → 歌單標題（快取標題與歌單標題常不一致，別名比對兩個都試）
    pl_titles: dict[str, str] = {}
    for _vid, _title, _pl in iter_tracks(load_v1()):
        pl_titles.setdefault(_vid, _title)
    enrich = {}
    if not CACHE.exists():
        return enrich
    # 只跳過內部產物；不可用「_ 開頭」規則——有些 video_id 本身以 _ 開頭
    # （15 首曾因此被判為無 enrich，包含 Fly My Wings、Pass On 各章 ED 等）
    _INTERNAL = {"_vectors.json", "_vectors_meta.json", "_wiki_labels.json"}
    for f in CACHE.glob("*.json"):
        if f.name in _INTERNAL:
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        vid = d.get("video_id")
        if not vid:
            continue
        comments = sorted(d.get("comments", []), key=lambda c: -c.get("likes", 0))
        digest = " / ".join(c["text"].replace("\n", " ")[:100] for c in comments[:8])
        blob = " ".join([
            d.get("title", ""), d.get("description", ""),
            " ".join(d.get("tags", [])), " ".join(wiki.get(vid, [])),
            digest,
        ])
        scenes, moods = _derive_labels(blob)
        if vid in wiki:
            scenes = list(dict.fromkeys(scenes + wiki[vid][:2]))
        factions = _FINGER_TRACK_MAP.get(vid) or _faction_labels(
            d.get("title", ""), blob)
        enrich[vid] = {
            "description": d.get("description", "")[:1500],
            "tags": d.get("tags", [])[:20],
            "wiki_labels": wiki.get(vid, []),
            "ingame": _ingame_aliases(d.get("title", ""))
                      + _limbus_ingame_aliases(
                          pl_titles.get(vid, "") or d.get("title", ""))[0],
            "chapters": _limbus_ingame_aliases(d.get("title", ""))[1],
            "played": list(dict.fromkeys(
                _FATHER_TRACK_MAP.get(vid, [])
                + _played_labels(d.get("title", "")) + _boss_labels(blob))),
            "factions": factions,
            "scenes": scenes,
            "moods": moods,
            "comments_digest": digest[:600],
            "duration": d.get("duration"),
            "view_count": d.get("view_count"),
            "channel": d.get("channel"),
        }
    return enrich


def cmd_merge() -> None:
    enrich = _merge_enriched()
    vec_file = CACHE / "_vectors.json"
    vectors = json.loads(vec_file.read_text(encoding="utf-8")) if vec_file.exists() else {}
    model, dim = "gemini-embedding-001", 768
    vec_file = CACHE / "_vectors_meta.json"
    if vec_file.exists():
        meta = json.loads(vec_file.read_text(encoding="utf-8"))
        model, dim = meta.get("model", model), int(meta.get("dim", dim))
    elif vectors:
        with open(ASTRBOT_CFG, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        for pr in cfg.get("provider", []):
            if pr.get("type") == "gemini_embedding" and pr.get("enable"):
                model = pr.get("embedding_model", model)
                dim = int(pr.get("embedding_dimensions", dim))
                break

    # 輸出拆分：index_v2.json（歌單+enrich 標籤，~0.5MB，可進版控）
    #        + index_v2_vectors.json（向量，~27MB，gitignore、可重建）
    out = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "embedding_model": model,
        "embedding_dim": dim,
        "playlists": load_v1(),
        "enrich": enrich,
        "has_vectors": bool(vectors),
    }
    INDEX_V2.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[merge] enrich={len(enrich)} → {INDEX_V2}")
    if vectors:
        VECTORS_FILE.write_text(json.dumps(
            {"embedding_model": model, "embedding_dim": dim, "vectors": vectors},
            ensure_ascii=False), encoding="utf-8")
        print(f"[merge] vectors={len(vectors)} → {VECTORS_FILE}")


def main() -> None:
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    steps = {
        "enrich": cmd_enrich,
        "wiki": cmd_wiki,
        "embed": cmd_embed,
        "merge": cmd_merge,
    }
    if stage == "all":
        for fn in steps.values():
            fn()
    elif stage in steps:
        steps[stage]()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
