"""
astrbot_plugin_discord_music
Discord 音樂插件：智慧音樂搜尋與推薦（Hybrid Search 架構）。

指令：
1. /music_search <查詢>                -> 快路徑精確匹配；失敗走 Hybrid Search（LLM 理解→檢索→重排序）
2. /music_recommend <心情/場景/風格>   -> Hybrid Search 推薦（含理由）

本地音樂索引：F:/StuffCop/DiscordBot/data/music_index/index.json (666 首)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

import discord
from discord.abc import Messageable

from astrbot import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

# ===================================================================== #
# 本地音樂索引
# ===================================================================== #
# 索引路徑：預設 <astrbot_data>/music_index（由插件位置推導，可攜帶）
# 可用環境變數 MUSIC_INDEX_DIR 覆寫
_DEFAULT_INDEX_DIR = Path(__file__).resolve().parent.parent.parent / "music_index"
INDEX_DIR = Path(os.environ.get("MUSIC_INDEX_DIR") or _DEFAULT_INDEX_DIR)
INDEX_PATH = INDEX_DIR / "index.json"          # v1：歌單曲目（build index）
INDEX_V2_PATH = INDEX_DIR / "index_v2.json"     # v2：歌單 + enrich 標籤（~0.5MB）
VECTORS_PATH = INDEX_DIR / "index_v2_vectors.json"  # 向量（~27MB，選用）


@dataclass
class IndexTrack:
    title: str
    url: str
    playlist: str
    video_id: str = ""


# 字典輔助：基礎中英對照（只作為兜底）
CHINESE_TO_ENGLISH = {
    # 遊戲/作品
    "腦葉公司": "Lobotomy Corporation",
    "腦葉": "Lobotomy Corporation",
    "L公司": "Lobotomy Corporation",
    "L crop": "Lobotomy Corporation",
    "L_crop": "Lobotomy Corporation",
    "LobotomyCorporation": "Lobotomy Corporation",
    "Lobotomy Corporation": "Lobotomy Corporation",
    "廢墟圖書館": "Library of Ruina",
    "廢墟": "Library of Ruina",
    "圖書館": "Library of Ruina",
    "LibraryOfRuina": "Library of Ruina",
    "Library of Ruina": "Library of Ruina",
    "LoR": "Library of Ruina",
    "限界": "Limbus Company",
    "限界公司": "Limbus Company",
    "林布斯": "Limbus Company",
    "LimbusCompany": "Limbus Company",
    "Limbus Company": "Limbus Company",
    "LC": "Limbus Company",
    "邊獄巴士": "Limbus Company",
    "邊獄": "Limbus Company",
    "第八章": "Canto VIII",
    "第九章": "Canto IX",
    "第十章": "Canto X",
    "間章": "Intervallo",
    "间章": "Intervallo",
    "瓦爾普吉斯": "Walpurgis Night",
    "瓦尔普吉斯": "Walpurgis Night",
    # Canto V 大湖篇 Boss（wiki.gg：Canto V 相關 Boss 名）
    "里卡多": "Ricardo",
    "亚哈": "Ahab",
    "亞哈": "Ahab",
    "竹帽金": "Bamboo-hatted Kim",
    # Canto VI 希斯克利夫篇
    "希斯克利夫": "Heathcliff",
    "时间撕裂者": "Time Ripper",
    "時間撕裂者": "Time Ripper",
    # Canto VII 堂吉訶德篇
    "理发师": "The Barber",
    "理髮師": "The Barber",
    "杜尔西内": "Dulcinea",
    "杜爾西內": "Dulcinea",
    "桑乔": "Sancho",
    "桑丘": "Sancho",
    # 間章/EX Boss
    "费边": "Fabius",
    "費邊": "Fabius",
    "市长": "Mayors",
    "市長": "Mayors",
    "鲁福": "Rufo",
    "魯福": "Rufo",
    "林庆烨": "Im Gyeong-eop",
    "林慶燁": "Im Gyeong-eop",
    # Canto X 新篇 Boss
    "深红之神": "Crimson God",
    "深紅之神": "Crimson God",
    "黑派": "Le Noir",
    "典狱长": "The Warden",
    "典獄長": "The Warden",
    "牧师": "The Chaplain",
    "牧師": "The Chaplain",
    "里奥斯": "Ryoshu",
    "里奧斯": "Ryoshu",
    "瓦伦希娜": "Valencina",
    "瓦倫希娜": "Valencina",
    "卢西奥": "Lucio",
    "盧西奧": "Lucio",
    "绮拉": "Kira",
    "綺拉": "Kira",
    "Mili": "Mili",
    "Mili頻道": "Mili Channel",
    "音樂": "music",
    "音乐": "music",
    "歌曲": "song",
    "Mili频道": "Mili Channel",
    "MiliChannel": "Mili Channel",
    # 場景/位置
    "大廳": "Lobby",
    "大厅": "Lobby",
    "主選單": "Main Menu",
    "主菜单": "Main Menu",
    "選單": "Menu",
    "菜单": "Menu",
    "戰鬥": "Battle",
    "战斗": "Battle",
    "Boss": "Boss",
    "結局": "Ending",
    "结局": "Ending",
    "過場": "Cutscene",
    "过场": "Cutscene",
    "探索": "Exploration",
    "核心抑制": "Core Suppression",
    "收尾人": "Fixer",
    # 風格/情緒
    "療癒": "Healing",
    "治癒": "Healing",
    "悲傷": "Sad",
    "憂鬱": "Melancholic",
    "忧郁": "Melancholic",
    "輕鬆": "Relaxing",
    "轻松": "Relaxing",
    "緊張": "Tense",
    "紧张": "Tense",
    "史詩": "Epic",
    "史诗": "Epic",
    "神聖": "Sacred",
    "莊嚴": "Solemn",
    "庄严": "Solemn",
    "黑暗": "Dark",
    "恐怖": "Horror",
    "神秘": "Mysterious",
    # Library of Ruina 樓層（層名 → Sephirah，命中 Battle Theme 標題）
    "總類層": "Roland",
    "宗教層": "Hokma",
    "哲學層": "Binah",
    "社會層": "Chesed",
    "社會科學層": "Chesed",
    "語言層": "Gebura",
    "自然層": "Tiphereth",
    "自然科學層": "Tiphereth",
    "藝術層": "Netzach",
    "文學層": "Hod",
    "科技層": "Yesod",
    "科學層": "Yesod",
    "歷史層": "Malkuth",
    # 廢墟圖書館樓層 → Sephirah（曲目標題以 Sephirah 命名，如 "Hokma Battle Theme"）
    # 遊戲內曲目：KeterBattle01~03、Roland01~03（Roland 為角色，非 Sephirah）
    "羅蘭": "Roland",
    "罗兰": "Roland",
    "總類層": "Keter",
    "总类层": "Keter",
    "Keter": "Keter",
    # 五指（The Fingers）勢力：曲目如 "Thumbs Down"、"Middle Finger Sanjou"、"Call New Index"
    "五指": "Fingers",
    "五指": "Fingers",
    "拇指": "Thumb",
    "拇指": "Thumb",
    "食指": "Index",
    "食指": "Index",
    "中指": "Middle",
    "中指": "Middle",
    "環指": "Ring",
    "环指": "Ring",
    "無名指": "Ring",
    "无名指": "Ring",
    "小指": "Pinky",
    "小指": "Pinky",
    "父輩": "Nursefather",
    "父辈": "Nursefather",
    # 五指父輩本名（術語定案：#97 Valencina=瓦倫希娜、Callisto=卡利斯托、Rien=里恩；#70 Araya=阿賴耶）
    "瓦倫希娜": "Valencina",
    "瓦伦希娜": "Valencina",
    "里恩": "Rien",
    "马蒂亚斯": "Matthias",
    "馬蒂亞斯": "Matthias",
    "卡利斯托": "Callisto",
    "阿賴耶": "Araya",
    "阿赖耶": "Araya",
    "解放戰": "Realization",
    "解放战": "Realization",
    "安潔拉": "Angela",
    "安洁拉": "Angela",
    "安吉拉": "Angela",
    "宗教層": "Hokma",
    "宗教层": "Hokma",
    "哲學層": "Binah",
    "哲学层": "Binah",
    "社會層": "Chesed",
    "社会层": "Chesed",
    "語言層": "Gebura",
    "语言层": "Gebura",
    "自然層": "Tiphereth",
    "自然层": "Tiphereth",
    "藝術層": "Netzach",
    "艺术层": "Netzach",
    "文學層": "Hod",
    "文学层": "Hod",
    "科技層": "Yesod",
    "科技层": "Yesod",
    "歷史層": "Malkuth",
    "历史层": "Malkuth",
    # 章節
    "序章": "Prologue",
    "第一章": "Chapter 1",
    "第二章": "Chapter 2",
    "第三章": "Chapter 3",
    "第四章": "Chapter 4",
    "第五章": "Chapter 5",
    "第六章": "Chapter 6",
    "第七章": "Chapter 7",
    "終章": "Epilogue",
    "Canto I": "Canto I",
    "Canto II": "Canto II",
    "Canto III": "Canto III",
    "Canto IV": "Canto IV",
    "Canto V": "Canto V",
    "Canto VI": "Canto VI",
    "Canto VII": "Canto VII",
    # 音樂類型
    "重金屬": "Heavy Metal",
    "重金屬音樂": "Heavy Metal",
    "搖滾": "Rock",
    "搖滾樂": "Rock",
    "電子": "Electronic",
    "電子音樂": "Electronic",
    "管弦樂": "Orchestral",
    "交響樂": "Symphonic",
    "鋼琴": "Piano",
    "鋼琴曲": "Piano",
    "環境音樂": "Ambient",
    "氛圍音樂": "Ambient",
    "爵士": "Jazz",
    "爵士樂": "Jazz",
    "古典": "Classical",
    "古典樂": "Classical",
}


def translate_query_to_english(query: str) -> str:
    """基礎字典替換，作為兜底。替換值帶空格，避免相鄰替換黏成 Battlemusic。
    ⚠️ 只匹配「原文」且長 key 優先：字典有 "Canto I" 這類短 key，
    若允許匹配已替換的英文，"Canto IX" 會被拆成 "Canto I X"。"""
    result = query
    for zh, en in sorted(CHINESE_TO_ENGLISH.items(), key=lambda x: -len(x[0])):
        if zh in query:
            result = result.replace(zh, f" {en} ")
    # 保險：camelCase 邊界再拆一次
    result = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", result)
    return re.sub(r"\s+", " ", result).strip()


# 依英譯值分類（字典回退時自動結構化，不再全丟 secondary）
_SCENE_VALUES = {"Realization", "Lobby", "Main Menu", "Menu", "Battle", "Combat", "Boss", "Boss Battle",
                 "Ending", "Cutscene", "Title", "Refraction Railway", "Walpurgis Night",
                 "Mirror Dungeon", "Exploration", "Core Suppression", "Fixer",
                 "Prologue", "Epilogue", "Chapter 1", "Chapter 2", "Chapter 3", "Chapter 4",
                 "Chapter 5", "Chapter 6", "Chapter 7", "Canto I", "Canto II", "Canto III",
                 "Canto IV", "Canto V", "Canto VI", "Canto VII"}
_GENRE_VALUES = {"Heavy Metal", "Metal", "Rock", "Electronic", "EDM", "Orchestral",
                 "Symphonic", "Piano", "Ambient", "Atmospheric", "Jazz", "Classical",
                 "Sacred", "Solemn", "Grandiose", "Grand"}
_MOOD_VALUES = {"Sad", "Melancholic", "Relaxing", "Tense", "Intense", "Epic", "Dark",
                "Horror", "Mysterious", "Emotional", "Calm", "Hopeful", "Healing",
                "Comforting", "Nostalgic", "Unsettling"}
_GAME_VALUES = {"Lobotomy Corporation", "Library of Ruina", "Limbus Company",
                "Mili", "Mili Channel"}
_CHARACTER_VALUES = {"Roland", "Keter", "Hokma", "Binah", "Chesed", "Gebura",
                     "Tiphereth", "Netzach", "Hod", "Yesod", "Malkuth"}
# 角色（樓層 Sephirah 與 Roland）皆屬《廢墟圖書館》
_CHARACTER_GAME = {c: "Library of Ruina" for c in _CHARACTER_VALUES}
# 五指勢力（The Fingers）屬《邊獄巴士》；Nursefather 本名視為角色詞
_FACTION_VALUES = {"Thumb", "Index", "Middle", "Ring", "Pinky"}
_FACTION_GAME = {f: "Limbus Company" for f in _FACTION_VALUES}
_NURSEFATHER = {"Valencina", "Rien", "Matthias", "Callisto", "Araya"}


def _fallback_classify(query: str) -> dict:
    """字典回退的結構化分類：角色→歸屬遊戲、場景/類型/情緒→primary。"""
    game_filters, character_terms = [], []
    scene, genre, mood = [], [], []
    english_parts = []
    for zh, en in CHINESE_TO_ENGLISH.items():
        if zh not in query:
            continue
        en_clean = en.strip()
        english_parts.append(en_clean)
        if en_clean in _GAME_VALUES:
            game_filters.append(en_clean)
        elif en_clean in _CHARACTER_VALUES:
            character_terms.append(en_clean)
            g = _CHARACTER_GAME.get(en_clean)
            if g:
                game_filters.append(g)
        elif en_clean in _FACTION_VALUES:
            # 五指勢力 → primary（標題精確匹配：Thumbs Down / Middle Finger…）+ Limbus 歸屬
            scene.append(en_clean)
            game_filters.append(_FACTION_GAME[en_clean])
        elif en_clean in _NURSEFATHER or en_clean in ("Nursefather", "Fingers"):
            character_terms.append(en_clean)
        elif en_clean in _SCENE_VALUES:
            scene.append(en_clean)
        elif en_clean in _GENRE_VALUES:
            genre.append(en_clean)
        elif en_clean in _MOOD_VALUES:
            mood.append(en_clean)
        # music/song 等泛用詞不入任何分類（stop-words 亦會濾除）
    return {
        "original_query": query,
        "intent": "find",
        "game_filters": list(dict.fromkeys(game_filters)),
        "character_terms": list(dict.fromkeys(character_terms)),
        "scene_terms": list(dict.fromkeys(scene)),
        "genre_terms": list(dict.fromkeys(genre)),
        "mood_terms": list(dict.fromkeys(mood)),
        "instrument_terms": [],
        "semantic_concepts": [],
        "english_keywords": [re.sub(r"\s+", " ", " ".join(dict.fromkeys(english_parts))).strip()],
        "synonyms": [],
        "negative_keywords": [],
        "search_strategy": "keyword_first",
        "interpretation": "LLM 不可用，字典回退（含結構化分類）",
    }


# LLM 輸出的 game_filters（英文）→ 實際歌單名（中文）別名表
# ⚠️ 歌單名為中文（廢墟圖書館/腦葉公司），英文過濾器直接比對會全部落空，
#    一直靠「無過濾」的回退層掩蓋（導致第 1 層精確過濾從未生效）。
_GAME_PLAYLIST_ALIASES = {
    "library of ruina": ["廢墟圖書館", "library of ruina"],
    "lobotomy corporation": ["腦葉公司", "lobotomy"],
    "limbus company": ["limbus"],
    "mili": ["mili"],
}


# 使用者指定的角色優先曲目序（標題關鍵字，依序提前）
# ⚠️ SAIKAI 雖有 Rien 標籤但標題不含 "rien"，與其他曲平手會被字母序擠到最後
_CHAR_PRIORITY = {
    "rien": ["saikai", "canto ix boss 4", "call new index"],
    # 使用者 2026-11-17 指定：Boss 9 → Boss 5 → Yumenomaichiru → Boss 10
    "araya": ["canto ix boss 9", "canto ix boss 5", "yumenomaichiru",
              "canto ix boss 10"],
}


class MusicIndex:
    """本地音樂索引：讀取 index.json 並提供搜尋。"""

    def __init__(self, path: Path = INDEX_PATH):
        self.path = path
        self.tracks: List[IndexTrack] = []
        self.playlists: set = set()
        self.playlist_tracks: dict = {}
        self.by_vid: Dict[str, IndexTrack] = {}
        # v2 語意資料（index_v2.json 存在時）
        self.enrich: Dict[str, dict] = {}
        self.vectors: Dict[str, list] = {}
        self.embedding_model: str = ""
        self.embedding_dim: int = 0
        self._load()

    def _load(self):
        if not self.path.exists():
            logger.warning(f"[MusicIndex] 索引檔不存在：{self.path}")
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 索引結構：{"created_at": ..., "source": ..., "playlists": [{name, tracks: [...]}]}
            playlists = data.get("playlists", [])
            self.playlist_tracks = {}  # playlist_name -> list of IndexTrack
            for pl in playlists:
                playlist_name = pl.get("name", "")
                tracks = pl.get("tracks", [])
                self.playlist_tracks[playlist_name] = []
                for item in tracks:
                    track = IndexTrack(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        playlist=playlist_name,
                        video_id=item.get("video_id", ""),
                    )
                    self.tracks.append(track)
                    self.playlist_tracks[playlist_name].append(track)
                    if track.video_id:
                        self.by_vid[track.video_id] = track
                    if playlist_name:
                        self.playlists.add(playlist_name)
            logger.info(f"[MusicIndex] 已載入 {len(self.tracks)} 首曲目，{len(self.playlists)} 個歌單")
        except Exception as e:
            logger.exception(f"[MusicIndex] 載入失敗: {e}")
        self._load_v2()

    def _load_v2(self) -> None:
        """載入 v2 語意資料：index_v2.json（enrich 標籤）＋ index_v2_vectors.json（向量，選用）。"""
        if not INDEX_V2_PATH.exists():
            logger.info(f"[MusicIndex] {INDEX_V2_PATH.name} 不存在，標籤層停用")
            return
        try:
            with open(INDEX_V2_PATH, "r", encoding="utf-8") as f:
                v2 = json.load(f)
            self.enrich = v2.get("enrich", {})
            self.embedding_model = v2.get("embedding_model", "")
            self.embedding_dim = int(v2.get("embedding_dim", 0))
            # 向量為選用：缺檔時自動降級為詞袋（IDF）檢索
            if VECTORS_PATH.exists():
                with open(VECTORS_PATH, "r", encoding="utf-8") as f:
                    vd = json.load(f)
                self.vectors = vd.get("vectors", {})
                if not self.embedding_model:
                    self.embedding_model = vd.get("embedding_model", "")
            else:
                self.vectors = {}
            self._build_inverted()
            logger.info(
                f"[MusicIndex] v2 標籤資料：enrich={len(self.enrich)} "
                f"vectors={len(self.vectors)}"
                + (f" model={self.embedding_model} dim={self.embedding_dim}"
                   if self.vectors else "（無向量 → 詞袋 IDF 檢索）")
            )
        except Exception as e:
            logger.warning(f"[MusicIndex] index_v2 載入失敗，標籤層停用: {e}")

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """中英混合分詞：中文單字+相鄰雙字，英文/數字整詞。"""
        tokens = []
        buf = []
        for ch in text.lower():
            if ch.isascii() and (ch.isalnum() or ch in "_-"):
                buf.append(ch)
                continue
            if buf:
                tokens.append("".join(buf))
                buf = []
            if "\u4e00" <= ch <= "\u9fff":
                tokens.append(ch)
        if buf:
            tokens.append("".join(buf))
        # 中文相鄰雙字
        out = []
        for i, t in enumerate(tokens):
            out.append(t)
            if len(t) == 1 and i + 1 < len(tokens) and len(tokens[i + 1]) == 1:
                out.append(t + tokens[i + 1])
        return out

    def _build_inverted(self) -> None:
        """vid -> token set 倒排索引（詞袋語意層，無 embedding 時的 fallback）。"""
        import math
        self._inv: Dict[str, list] = {}
        self._idf: Dict[str, float] = {}
        doc_count: Dict[str, int] = {}
        for vid, e in self.enrich.items():
            t = self.by_vid.get(vid)
            if not t:
                continue
            blob = " ".join([
                t.title, t.playlist,
                " ".join(e.get("tags", [])),
                " ".join(e.get("scenes", [])),
                " ".join(e.get("moods", [])),
                " ".join(e.get("wiki_labels", [])),
                " ".join(e.get("ingame", [])),
                " ".join(e.get("factions", [])),
                " ".join(e.get("chapters", [])),
                " ".join(e.get("played", [])),
                (e.get("description") or "")[:400],
                e.get("comments_digest", "")[:400],
            ])
            toks = set(self._tokenize(blob))
            self._inv[vid] = list(toks)
            for tok in toks:
                doc_count[tok] = doc_count.get(tok, 0) + 1
        n = max(1, len(self._inv))
        self._idf = {tok: math.log(n / c) + 1.0 for tok, c in doc_count.items()}

    def bag_scores(self, query: str, k: int = 15) -> list[tuple[str, float]]:
        """詞袋語意評分：查詢 token 對每首的 IDF 加權命中。回 [(vid, score)]。"""
        if not getattr(self, "_inv", None):
            return []
        qtoks = [t for t in set(self._tokenize(query)) if t in self._idf]
        # 英文單複數變體：查詢 "thumb" 也命中索引 token "thumbs"（反之亦然）
        variants = set(qtoks)
        for t in qtoks:
            if len(t) >= 3 and t.isascii():
                for v in (t + "s", t.rstrip("s"), t + "es"):
                    if v != t and v in self._idf:
                        variants.add(v)
        qtoks = list(variants)
        if not qtoks:
            return []
        scores: Dict[str, float] = {}
        for tok in qtoks:
            w = self._idf.get(tok, 0.0)
            for vid, toks in self._inv.items():
                if tok in toks:
                    scores[vid] = scores.get(vid, 0.0) + w
        if not scores:
            return []
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        top = ranked[:k]
        max_s = top[0][1] or 1.0
        return [(vid, s / max_s) for vid, s in top]

    def evidence(self, vid: str) -> Dict[str, Any]:
        """取得曲目的真實證據（場景/情緒/留言摘要）。無資料回空 dict。"""
        e = self.enrich.get(vid) or {}
        return {
            "scenes": e.get("scenes", []),
            "moods": e.get("moods", []),
            "tags": e.get("tags", []),
            "wiki_labels": e.get("wiki_labels", []),
            "comments_digest": e.get("comments_digest", ""),
        }

    def search(self, keyword: str, limit: int = 0) -> List[IndexTrack]:
        """關鍵字搜尋。
        1. 關鍵字完整包含在**唯一一個**歌單名中 → 返回該歌單所有曲目
        2. 關鍵字拆分後，任一部分唯一匹配某歌單 → 返回該歌單曲目
        3. 否則在所有曲目中搜尋標題/歌單名包含關鍵字
        limit=0 表示不限制。"""
        if not keyword:
            return []
        kw = keyword.lower().strip()
        
        # 1. 完整關鍵字唯一匹配歌單名 → 整份歌單（複合查詢如「腦葉公司 大廳」不劫持）
        matched_playlists = [pl for pl in self.playlists if kw == pl.lower()]
        if not matched_playlists:
            matched_playlists = [pl for pl in self.playlists if kw in pl.lower()]
        
        if len(matched_playlists) == 1:
            return self.playlist_tracks.get(matched_playlists[0], [])
        
        # 2. 退回全域搜尋
        results = []
        for t in self.tracks:
            if kw in t.title.lower() or kw in t.playlist.lower():
                results.append(t)
        
        if limit > 0:
            results = results[:limit]
        return results

    def get_playlist_names(self) -> List[str]:
        return sorted(list(self.playlists))

    def get_tracks_for_search(self, keywords: List[str], limit: int = 50, playlist_filters: List[str] = None) -> List[Dict[str, Any]]:
        """混合關鍵字檢索：對多組關鍵字做 OR 搜尋，去重。
        
        Args:
            keywords: 搜尋關鍵字列表
            limit: 最大返回數量
            playlist_filters: 歌單過濾列表（支援部分匹配，如 ["Library of Ruina", "Limbus"]）
        """
        if not keywords:
            return []
        
        seen = set()
        results = []
        
        # 標準化歌單過濾器（英文遊戲名 → 實際歌單名別名）
        playlist_filters_lower = []
        for f in (playlist_filters or []):
            fl = f.lower().strip()
            playlist_filters_lower.extend(
                _GAME_PLAYLIST_ALIASES.get(fl, [fl]))
        
        # 標籤 blob 快取：Boss 名（played）/五指/遊戲內名/章節/wiki 標籤
        # ⚠️ 沒有它，「里卡多」查不到 Canto V Boss 1（Ricardo 只在 played 標籤裡）
        label_blob: Dict[str, str] = {}

        def _labels(t) -> str:
            if t.video_id in label_blob:
                return label_blob[t.video_id]
            e = self.enrich.get(t.video_id, {}) if t.video_id else {}
            blob = " | ".join([
                " ".join(e.get("played", [])),
                " ".join(e.get("factions", [])),
                " ".join(e.get("ingame", [])),
                " ".join(e.get("chapters", [])),
                " ".join(e.get("wiki_labels", [])),
            ]).lower()
            if t.video_id:
                label_blob[t.video_id] = blob
            return blob

        for kw in keywords:
            # 底線正規為空白（city_blue ↔ City Blue）
            kw_lower = kw.lower().strip().replace("_", " ")
            if not kw_lower:
                continue
            for t in self.tracks:
                # 歌單過濾
                if playlist_filters_lower:
                    playlist_match = any(f in t.playlist.lower() for f in playlist_filters_lower)
                    if not playlist_match:
                        continue
                
                title_match = kw_lower in t.title.lower()
                playlist_match_kw = kw_lower in t.playlist.lower()
                label_match = kw_lower in _labels(t)
                
                # 命中加權：標籤（Boss 名/五指等 curated 標籤）2 分 > 標題 1 分
                # （「Ricardo+Battle」命中 Canto V Boss 1 = 3 分，壓過僅標題含 Battle 的泛用曲）
                if title_match or playlist_match_kw or label_match:
                    delta = (2 if label_match else 0) + (
                        1 if (title_match or playlist_match_kw) else 0)
                    # 片語整體命中加 3 分（"city blue" 比拆成 city/blue 更精確，
                    # 壓過僅中 "city" 且標籤也中的 City Hero）
                    if " " in kw_lower and (title_match or label_match):
                        delta += 3
                    key = f"{t.title}|{t.url}"
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "index": len(results) + 1,
                            "title": t.title,
                            "url": t.url,
                            "playlist": t.playlist,
                            "video_id": t.video_id,
                            "_title_match": title_match,  # 標記是否標題匹配
                            "_score": delta,
                        })
                    else:
                        for r in results:
                            if r["title"] == t.title and r["url"] == t.url:
                                r["_score"] += delta
                                break
            # ⚠️ 不可在此 break： limit 滿就中斷會讓後續關鍵字（如 Boss 名）永遠不被搜
            #     （「Battle」瞬間填入 30 首 → 「Ricardo」從未處理 → Canto V Boss 1 只能靠語意層）
        
        # 排序：加權分數高者優先 → 標題匹配優先 → 標題字母序
        results.sort(key=lambda x: (-x.get("_score", 1),
                                    not x.get("_title_match", False),
                                    x["title"]))
        for r in results:
            r.pop("_title_match", None)
            r.pop("_score", None)
        return results[:limit]


def _resp_text(resp) -> str:
    """從 AstrBot LLMResponse 取純文字。
    ⚠️ LLMResponse 沒有 .content 欄位；str(resp) 會得到物件 repr（導致 JSON 解析
    永遠失敗）。正確路徑：completion_text → result_chain.get_plain_text() → str()。"""
    try:
        t = resp.completion_text
        if t:
            return t
    except Exception:
        pass
    try:
        rc = getattr(resp, "result_chain", None)
        if rc is not None and hasattr(rc, "get_plain_text"):
            t = rc.get_plain_text()
            if t:
                return t
    except Exception:
        pass
    return str(resp)


def _llm_json(content: str):
    """解析 LLM 回傳的 JSON；容忍 markdown 圍欄與前後文字。
    先找第一個 { 或 [，配對最後一個 } 或 ]，失敗再回拋原解析錯誤。"""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    if content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    i_obj, i_arr = content.find("{"), content.find("[")
    starts = [i for i in (i_obj, i_arr) if i >= 0]
    if not starts:
        raise json.JSONDecodeError("no JSON found", content, 0)
    start = min(starts)
    closer = "}" if content[start] == "{" else "]"
    end = content.rfind(closer)
    if end <= start:
        raise json.JSONDecodeError("unbalanced JSON", content, start)
    return json.loads(content[start:end + 1])


# ===================================================================== #
# Hybrid Search LLM
# ===================================================================== #
class MusicHybridLLM:
    """Hybrid Search：Query Understanding → 檢索 → LLM 重排序/推薦"""

    def __init__(self, context: Context):
        self.context = context
        self.debug = False  # /music_debug 切換；開啟時 log LLM 原始回覆
        self._analysis_cache: dict[str, dict] = {}  # query → 理解結果（重複查詢秒回）

    def _get_provider(self, umo: str | None = None):
        try:
            prov = self.context.get_using_provider(umo)
            if prov:
                logger.info(f"[MusicHybridLLM] Provider: {prov.meta().id} (model={prov.get_model()})")
            return prov
        except Exception as e:
            logger.warning(f"[MusicHybridLLM] 獲取 Provider 失敗: {e}")
            return None

    def _get_embedding_provider(self):
        """取得 AstrBot embedding provider（語意層用）。不可用回 None。"""
        try:
            provs = self.context.get_all_embedding_providers()
            if provs:
                return provs[0]
        except Exception as e:
            logger.warning(f"[MusicHybridLLM] 無 embedding provider: {e}")
        return None

    def _nvidia_api_key(self) -> str:
        """讀 NVIDIA 金鑰（環境變數或 HKCU\Environment）。"""
        import os
        key = os.environ.get("NVIDIA_API_KEY", "")
        if not key:
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as reg:
                    key, _ = winreg.QueryValueEx(reg, "NVIDIA_API_KEY")
            except Exception:
                pass
        return key

    _NVIDIA_OPENER = None  # 惰性建立的直連 opener（忽略環境代理變數）

    def _nvidia_embed_sync(self, text: str, model: str) -> list:
        """同步呼叫 NVIDIA NIM embeddings（供 to_thread）。強制直連（不走環境代理）。"""
        import urllib.request
        if self.__class__._NVIDIA_OPENER is None:
            self.__class__._NVIDIA_OPENER = urllib.request.build_opener(
                urllib.request.ProxyHandler({}))
        key = self._nvidia_api_key()
        if not key:
            return []
        url = "https://integrate.api.nvidia.com/v1/embeddings"
        body = {"model": model, "input": [text], "input_type": "query",
                "encoding_format": "float", "truncate": "END"}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": f"Bearer {key}"},
            method="POST",
        )
        with self.__class__._NVIDIA_OPENER.open(req, timeout=20) as r:
            resp = json.loads(r.read().decode("utf-8"))
        return resp["data"][0]["embedding"]

    async def _query_vector(self, query: str, index: "MusicIndex") -> list:
        """取得查詢向量：AstrBot embedding provider 優先，失敗回退 NVIDIA NIM。"""
        # 1) AstrBot provider（僅當模型名「嚴格相等」才用；未知模型一律跳過，
        #    避免拿不相容的向量空間或失效金鑰撞牆後才回退）
        prov = self._get_embedding_provider()
        if prov:
            try:
                prov_model = prov.get_model() or ""
            except Exception:
                prov_model = ""
            if prov_model and prov_model == index.embedding_model:
                try:
                    vec = await asyncio.wait_for(prov.get_embedding(query), timeout=10.0)
                    if vec:
                        return vec
                except Exception as e:
                    logger.warning(f"[MusicHybridLLM] AstrBot embedding 失敗: {repr(e)}")
            else:
                logger.info(
                    f"[MusicHybridLLM] 跳過 AstrBot embedding（index={index.embedding_model} "
                    f"provider={prov_model or '未知'}），直接用 NVIDIA")
        # 2) NVIDIA NIM（模型名與 index 記錄一致）
        if index.embedding_model and index.embedding_model.startswith("nvidia/"):
            key = self._nvidia_api_key()
            if not key:
                logger.warning("[MusicHybridLLM] NVIDIA_API_KEY 不存在，語意向量層停用")
                return []
            try:
                logger.info(f"[MusicHybridLLM] 查詢嵌入經 NVIDIA NIM（{index.embedding_model}）...")
                vec = await asyncio.wait_for(
                    asyncio.to_thread(self._nvidia_embed_sync, query, index.embedding_model),
                    timeout=20.0,
                )
                logger.info(f"[MusicHybridLLM] 查詢嵌入完成 dim={len(vec)}")
                return vec
            except Exception as e:
                logger.warning(f"[MusicHybridLLM] NVIDIA embedding 失敗: {repr(e)}")
                return []
        return []

    async def semantic_candidates(self, query: str, index: "MusicIndex",
                                  k: int = 15) -> List[Dict[str, Any]]:
        """語意層：嵌入查詢 → cosine top-K。向量或 provider 不可用回 []。

        模型一致性檢查：index 向量的 embedding_model 必須與 provider 相符（或 dim 一致），
        否則向量空間不匹配，直接停用。
        """
        # 詞袋 fallback：無向量資料時走 IDF 倒排（零依賴）
        if not index.vectors:
            # 雙路查詢：原文 + 字典翻譯（解決中文查詢 vs 英文語料），分數合併取最大
            scores: Dict[str, float] = {}
            for q in dict.fromkeys([query, translate_query_to_english(query)]):
                for vid, s in index.bag_scores(q, k=k * 2):
                    scores[vid] = max(scores.get(vid, 0.0), s)
            ranked = sorted(scores.items(), key=lambda x: -x[1])[:k]
            results = []
            for vid, score in ranked:
                t = index.by_vid.get(vid)
                if t is None:
                    continue
                results.append({
                    "index": len(results) + 1,
                    "title": t.title,
                    "url": t.url,
                    "playlist": t.playlist,
                    "video_id": vid,
                    "semantic_score": round(score, 4),
                })
            return results
        qvec = await self._query_vector(query, index)
        if not qvec:
            return []

        scored = []
        for vid, vec in index.vectors.items():
            if len(vec) != len(qvec):
                continue
            dot = sum(a * b for a, b in zip(qvec, vec))
            scored.append((dot, vid))
        scored.sort(reverse=True)

        results = []
        seen = set()
        for score, vid in scored:
            if vid in seen:
                continue
            # 由 video_id 反查 track（by_vid O(1)）
            t = index.by_vid.get(vid)
            if t is None:
                continue
            seen.add(vid)
            results.append({
                "index": len(results) + 1,
                "title": t.title,
                "url": t.url,
                "playlist": t.playlist,
                "video_id": vid,
                "semantic_score": round(score, 4),
            })
            if len(results) >= k:
                break
        return results

    # ----------------------------------------------------------------- #
    # Stage 1: Query Understanding
    # ----------------------------------------------------------------- #
    async def understand_query(self, query: str, umo: str | None = None) -> Dict[str, Any]:
        """將自然語言查詢轉為結構化搜尋條件。"""
        cached = self._analysis_cache.get(query)
        if cached:
            logger.info("[MusicHybridLLM] 理解快取命中（跳過 LLM 呼叫）")
            return dict(cached)
        provider = self._get_provider(umo)
        if not provider:
            return _fallback_classify(query)

        system_prompt = """你是一個專業的音樂搜尋語意分析助手。

你的任務是理解使用者以中文、英文、縮寫、別名或混合語言提出的音樂搜尋需求，並將其轉換成適合 Hybrid Search 的搜尋條件。

你必須理解：
- 遊戲、作品、音樂頻道及其縮寫。
- 角色名稱、劇情術語、遊戲場景。
- 音樂類型、情緒、氛圍及聽感。
- 中英文同義詞、不同翻譯及常見錯字。

重要規則：

1. **精確提取關鍵字**：不要只進行字面翻譯，必須理解使用者真正想找的音樂並提取具體可搜尋的英文關鍵字。
2. **場景映射**：
   - "大廳"、"主選單"、"選單" → 必須提取 "Lobby", "Main Menu" 加入 scene_terms
   - "戰鬥"、"戰鬥音樂" → "Battle", "Combat" 加入 scene_terms
   - "Boss戰"、"王戰" → "Boss Battle" 加入 scene_terms
3. **作品識別**：
   - "圖書館"、"廢墟圖書館" → "Library of Ruina" 加入 game_filters
   - "腦葉"、"腦葉公司"、"L公司" → "Lobotomy Corporation" 加入 game_filters
   - "邊獄"、"邊獄巴士"、"限界" → "Limbus Company" 加入 game_filters
   - "Mili" → "Mili" 加入 game_filters
4. **關鍵字優先級**：
   - english_keywords 必須包含具體可搜尋的詞（如 "Lobby", "Main Menu", "Battle", "Library of Ruina"）
   - 避免泛用詞（如 "music", "song", "library", "of" 等無意義詞）
5. **章節對照**："第三章" → "Chapter 3", "Canto III" 加入 scene_terms
6. **音樂類型**："重金屬" → "Heavy Metal", "Metal" 加入 genre_terms
7. **廢墟圖書館樓層**（"XX層" 查詢 → game_filters=["Library of Ruina"] + character_terms=[Sephirah 名] + 有戰鬥字樣時 scene_terms=["Battle"]）：
   總類層=Keter、宗教層=Hokma、哲學層=Binah、社會層=Chesed、語言層=Gebura、
   自然層=Tiphereth、藝術層=Netzach、文學層=Hod、科技層=Yesod、歷史層=Malkuth
   例：「宗教層戰鬥」→ character_terms=["Hokma"], scene_terms=["Battle"]，曲目名為 "Hokma Battle Theme"
   「羅蘭」→ character_terms=["Roland"]（主角角色，非 Sephirah；其戰鬥曲為 Roland01~03）
   「解放戰」= Realization（覺醒階段曲；「羅蘭解放戰」→ character_terms=["Roland"], scene_terms=["Realization"]）
   安潔拉/安吉拉 → Angela（其戰鬥曲為 Angela01~03 = Angela Realization Theme 1~3）

【五指對照】（The Fingers，屬 Limbus Company；曲目如 "Thumbs Down"、"Middle Finger Sanjou"、"Call New Index"）
拇指 → Thumb；食指 → Index；中指 → Middle；環指（無名指）→ Ring；小指 → Pinky；父輩 → Nursefather
父輩本名：瓦倫希娜=Valencina（拇指）、里恩=Rien（食指）、馬蒂亞斯=Matthias（中指）、
卡利斯托=Callisto（環指）、阿賴耶=Araya（小指現任）。
例：「中指父輩 馬蒂亞斯」→ scene_terms=["Middle"], character_terms=["Matthias"], game_filters=["Limbus Company"]

輸出格式嚴格遵循 JSON，scene_terms、game_filters、genre_terms 必須填入具體值，不要留空陣列除非確實無相關資訊。

只回傳 JSON，不要輸出其他文字。"""

        user_prompt = f"""使用者查詢：
"{query}"

請分析並輸出：

{{
  "original_query": "原始查詢",
  "intent": "find 或 recommend",
  "game_filters": [],
  "character_terms": [],
  "scene_terms": [],
  "mood_terms": [],
  "genre_terms": [],
  "instrument_terms": [],
  "semantic_concepts": [],
  "english_keywords": [],
  "synonyms": [],
  "negative_keywords": [],
  "search_strategy": "keyword_first / semantic_first / balanced",
  "interpretation": "對使用者需求的簡短理解"
}}

搜尋詞請同時考慮中英文，但不要無意義地堆疊大量同義詞。

沒有指定的欄位請使用空陣列。

【歌單分類及別名】
Limbus：Limbus Company、LC、Limbus、限界公司、林布斯、邊獄巴士
Mili：Mili、Mili Channel
Library of Ruina：Library of Ruina、LoR、Ruina、廢墟圖書館
Lobotomy Corporation：Lobotomy Corporation、LC、L Corp、Lobotomy、腦葉公司
注意：LC 可能同時指 Limbus Company 或 Lobotomy Corporation，必須依照上下文判斷。

【常見場景】
大廳、主選單 → Lobby / Main Menu
戰鬥 → Battle / Combat
Boss、王戰 → Boss Battle
結局 → Ending
過場 → Cutscene
探索 → Exploration
核心抑制 → Core Suppression
收尾人 → Fixer

【章節對照】（Limbus 章節 = Canto；第九/十章曲目名如 "Canto IX Battle Theme A"）
序章 → Prologue
第一章 → Canto I；第二章 → Canto II；第三章 → Canto III；第四章 → Canto IV
第五章 → Canto V；第六章 → Canto VI；第七章 → Canto VII
第八章 → Canto VIII；第九章 → Canto IX；第十章 → Canto X
間章 → Intervallo（如 "Intervallo VII-2"）；瓦爾普吉斯之夜 → Walpurgis Night
查詢「第九章戰鬥」→ scene_terms=["Battle"], english_keywords=["Canto IX"]

【Boss 名對照】（wiki.gg 用途表；查詢 Boss 名 → english_keywords=[Boss 英文名]）
里卡多↔Ricardo、亞哈↔Ahab、希斯克利夫↔Heathcliff、杜爾西內↔Dulcinea、桑喬↔Sancho、
費邊↔Fabius、魯福↔Rufo、林慶燁↔Im Gyeong-eop、深紅之神↔Crimson God、
牧師↔The Chaplain、典獄長↔The Warden、里奧斯↔Ryoshu、瓦倫希娜↔Valencina、
卢西奥↔Lucio、綺拉↔Kira、竹帽金↔Bamboo-hatted Kim。

【Library of Ruina 樓層對照】（樓層名 → Sephirah，其戰鬥音樂標題含 Sephirah 名）
總類層 → Roland；宗教層 → Hokma；哲學層 → Binah；社會層（社會科學層） → Chesed；
語言層 → Gebura；自然層（自然科學層） → Tiphereth；藝術層 → Netzach；
文學層 → Hod；科技層 → Yesod；歷史層 → Malkuth。
使用者問「XX層 戰鬥音樂」時，必須把對應 Sephirah 英文名放入 english_keywords 或 character_terms，
並把 Battle 放入 scene_terms。

【音樂類型】
重金屬、重金屬音樂 → Heavy Metal / Metal
搖滾、搖滾樂 → Rock
電子、電子音樂 → Electronic / EDM
管弦樂、交響樂 → Orchestral / Symphonic
鋼琴、鋼琴曲 → Piano
環境音樂、氛圍音樂 → Ambient / Atmospheric
爵士、爵士樂 → Jazz
古典、古典樂 → Classical

【常見情緒】
悲傷 → Sad / Melancholic
孤獨 → Lonely / Loneliness / Isolation
絕望 → Despair / Hopelessness
壓迫感 → Oppressive / Tense
神秘 → Mysterious / Enigmatic
莊嚴 → Solemn / Grand
療癒 → Healing / Comforting
懷舊 → Nostalgic
不安 → Unsettling / Uneasy
史詩感 → Epic / Grandiose"""

        try:
            # 10 秒超時：避免 LLM 卡住拖慢回應（發送走 channel.send，無 interaction 期限）
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider.meta().id,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                ),
                timeout=20.0,  # 20 秒超時（15 候選 prompt 小；超時快速回退好過白等）
            )
            content = _resp_text(resp)
            if self.debug:
                logger.info(f"[Debug] LLM 原始回覆: {content[:500]}")
            try:
                result = _llm_json(content)
            except Exception:
                logger.warning(
                    f"[MusicHybridLLM] JSON 解析失敗，原始回覆前 200 字: {content[:200]!r}")
                raise
            defaults = {
                "original_query": query,
                "intent": "find",
                "game_filters": [],
                "character_terms": [],
                "scene_terms": [],
                "mood_terms": [],
                "genre_terms": [],
                "instrument_terms": [],
                "semantic_concepts": [],
                "english_keywords": [translate_query_to_english(query)],
                "synonyms": [],
                "negative_keywords": [],
                "search_strategy": "balanced",
                "interpretation": "",
            }
            for k, v in defaults.items():
                if k not in result:
                    result[k] = v
            if len(self._analysis_cache) > 100:
                self._analysis_cache.clear()
            self._analysis_cache[query] = dict(result)
            return result
        except Exception as e:
            logger.warning(f"[MusicHybridLLM] Query Understanding 失敗: {repr(e)}")
            result = _fallback_classify(query)
            result["interpretation"] = f"理解失敗，字典回退: {repr(e)[:60]}"
            return result

    # ----------------------------------------------------------------- #
    # Stage 2: music_find — 重排序
    # ----------------------------------------------------------------- #
    async def rerank_find(self, query: str, analysis: Dict[str, Any], 
                          candidates: List[Dict[str, Any]], 
                          umo: str | None = None) -> List[Dict[str, Any]]:
        """根據理解結果，對候選歌曲重排序。"""
        provider = self._get_provider(umo)
        if not provider:
            return candidates[:5]
        # LLM 只看前 15（prompt 限制）；回退排序用全量 candidates
        llm_candidates = candidates[:15]

        system_prompt = """你是一個精準的音樂搜尋與排序助手。

你的任務是根據使用者的查詢，以及 Hybrid Search 提供的候選歌曲，選出最相關的音樂。

你必須同時理解：

A. 關鍵字匹配
- 歌曲名稱
- 遊戲名稱
- 角色名稱
- 場景名稱
- 官方別名
- 英文及中文術語

B. 語意匹配
- 使用者真正想找的音樂。
- 情緒、氛圍及聽感。
- 音樂類型與編曲特色。
- 劇情背景及角色關聯。

C. 資料可信度
- 官方資料優先用於確認歌曲名稱、作品及使用場景。
- 不得因為留言提及某個角色，就認定歌曲屬於該角色的主題曲。

【排序原則】

1. 優先符合使用者明確指定的條件。
2. **查詢含角色名時，標題包含該角色英文名的曲目必須排在最前**（如查「安潔拉」→ Angela 標題曲優先於僅標題含 Battle 的其他角色曲）。
3. 精確的歌曲、角色、作品及場景匹配具有較高優先度。
4. 語意相似但關鍵字不同的歌曲仍然可以入選。
5. 音樂氛圍及情緒匹配可作為重要的補充依據。
6. 不要為了湊足結果而加入不相關歌曲。
7. **固定回傳 5 首**（候選足夠時）；只有在候選不足 5 首時才可少於 5 首。
8. 不得捏造候選資料中不存在的歌曲資訊。

你收到的候選歌曲已經由外部 Hybrid Search 系統檢索。
你只負責理解、比較及重新排序。

只回傳有效 JSON，不要輸出 Markdown 或額外文字。"""

        def _ev_line(c: dict) -> str:
            ev = c.get("evidence") or {}
            bits = []
            if ev.get("scenes"):
                bits.append(f"場景: {', '.join(ev['scenes'][:4])}")
            if ev.get("moods"):
                bits.append(f"情緒: {', '.join(ev['moods'][:4])}")
            if ev.get("wiki_labels"):
                bits.append(f"遊戲內用途: {', '.join(ev['wiki_labels'][:2])}")
            cd = ev.get("comments_digest", "")
            if cd:
                bits.append(f"聽眾留言: {cd[:150]}")
            return " | ".join(bits)

        tracks_text = "\n".join([
            f"{c['index']}. {c['title']} | {c['playlist']}"
            + (f"\n   證據: {_ev_line(c)}" if _ev_line(c) else "")
            for c in candidates
        ])

        analysis_json = json.dumps(analysis, ensure_ascii=False)

        user_prompt = f"""使用者查詢：
"{query}"

Query Understanding 結果：
{analysis_json}

【Hybrid Search 候選歌曲】（附真實證據：場景標籤、遊戲內用途、聽眾留言）

{tracks_text}

請從候選歌曲中選出最相關的 1-5 首。理由必須引用上方真實證據（場景標籤、遊戲內用途或聽眾留言），不得捏造。

評估以下因素：

- Query Match：與使用者原始需求的符合程度。
- Keyword Match：關鍵字及術語匹配程度。
- Semantic Match：整體語意符合程度。
- Context Match：作品、角色及場景關聯。
- Mood Match：音樂情緒及氛圍符合程度。

回傳格式：

[
  {{
    "index": 1,
    "reason": "推薦原因",
    "matched_concepts": ["Melancholic", "Loneliness"],
    "match_type": ["semantic", "mood"]
  }}
]

注意：
- index 必須使用候選歌曲提供的原始 index。
- 不得修改歌曲資料。
- 不得輸出不存在的 index。
- reason 使用自然的繁體中文。
- 不需要為每首歌強行匹配所有條件。
- 只回傳 JSON。"""

        try:
            # 10 秒超時：避免 LLM 卡住拖慢回應（發送走 channel.send，無 interaction 期限）
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider.meta().id,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                ),
                timeout=12.0,  # 12 秒超時：回退排序品質已好，LLM 超時白等是純浪費
            )
            content = _resp_text(resp)
            if self.debug:
                logger.info(f"[Debug] LLM 原始回覆: {content[:500]}")
            try:
                reranked = _llm_json(content)
            except Exception:
                logger.warning(
                    f"[MusicHybridLLM] JSON 解析失敗，原始回覆前 200 字: {content[:200]!r}")
                raise
            
            candidate_map = {c['index']: c for c in candidates}
            results = []
            for r in reranked[:5]:
                idx = r.get('index')
                if idx in candidate_map:
                    c = candidate_map[idx]
                    results.append({
                        "title": c['title'],
                        "url": c['url'],
                        "playlist": c['playlist'],
                        "reason": r.get('reason', ''),
                        "matched_concepts": r.get('matched_concepts', []),
                        "match_type": r.get('match_type', []),
                    })
            return results if results else candidates[:5]
        except Exception as e:
            logger.warning(f"[MusicHybridLLM] Rerank 失敗: {e}")
            # LLM 失敗時，根據關鍵字匹配生成基本理由
            fallback_results = []
            query_lower = query.lower()
            analysis_keywords = []
            for key in ["english_keywords", "synonyms", "game_filters", "character_terms", "scene_terms", "mood_terms", "genre_terms"]:
                analysis_keywords.extend(analysis.get(key, []))
            analysis_keywords = [k.lower() for k in analysis_keywords if k]
            
            # 標題精確命中優先（語意層分數雖高但可能漏掉關鍵角色名）
            # 角色名權重 3（羅蘭查詢不被泛用的 Battle/Library 擠掉）
            char_kws = [k.lower() for k in
                        analysis.get("character_terms", []) if k]
            other_kws = [k for k in analysis_keywords if k not in char_kws]

            def _title_score(c):
                tl = c['title'].lower() + " | " + " ".join(
                    c.get("ingame", []) + c.get("played", [])
                    + c.get("factions", []) + c.get("chapters", [])
                ).lower()
                return -(3 * sum(1 for kw in char_kws if kw in tl)
                         + sum(1 for kw in other_kws if kw in tl))
            ranked = [(c, _title_score(c)) for c in candidates]
            matched = [x for x in ranked if x[1] < 0]
            # 有匹配就不混入不相干曲目（Araya 查詢不該出現 Ruina 的 Reverberation）
            ordered = ([c for c, _ in sorted(matched, key=lambda x: x[1])]
                       if matched else [c for c, _ in ranked])
            ordered = ordered[:5]
            for c in ordered:
                title_lower = c['title'].lower()
                playlist_lower = c['playlist'].lower()
                matched = []
                for kw in analysis_keywords:
                    if kw in title_lower or kw in playlist_lower:
                        matched.append(kw)
                reason = f"關鍵字匹配: {', '.join(matched[:3])}" if matched else "關鍵字匹配"
                fallback_results.append({
                    "title": c['title'],
                    "url": c['url'],
                    "playlist": c['playlist'],
                    "reason": reason,
                    "matched_concepts": [],  # reason 已含匹配詞，不再重複顯示
                    "match_type": ["keyword"],
                })
            return fallback_results

    # ----------------------------------------------------------------- #
    # Stage 2: music_recommend — 推薦
    # ----------------------------------------------------------------- #
    async def recommend(self, mood: str, analysis: Dict[str, Any],
                        candidates: List[Dict[str, Any]],
                        umo: str | None = None) -> List[Dict[str, Any]]:
        """根據心情/場景推薦歌曲。"""
        provider = self._get_provider(umo)
        if not provider:
            return [{"title": c['title'], "url": c['url'], "playlist": c['playlist'],
                     "reason": "關鍵字比對", "matched_moods": [], "matched_features": [],
                     "community_insight": None} for c in candidates[:3]]

        system_prompt = """你是一位專業的遊戲 OST 與 BGM 音樂推薦助手。

你的任務是根據使用者描述的心情、場景、音樂氛圍及偏好，從 Hybrid Search 提供的候選歌曲中找出最適合的音樂。

你需要理解的不只是音樂標籤，還包括：

1. 情緒：
Sad, Melancholic, Lonely, Despair, Hopeful,
Nostalgic, Peaceful, Tragic, Emotional。

2. 氛圍：
Dark, Mysterious, Oppressive, Dreamlike,
Atmospheric, Solemn, Ethereal, Unsettling。

3. 強度：
Calm, Relaxing, Intense, Dramatic,
Aggressive, Epic, Climactic。

4. 音樂特色：
Orchestral, Electronic, Ambient, Piano,
Choir, Rock, Jazz, Classical, Industrial。

5. 使用場景：
Lobby, Main Menu, Battle, Boss Battle,
Final Battle, Ending, Cutscene, Exploration。

【推薦原則】

1. 優先考慮整體聽感是否符合使用者需求。
2. 不要只依照相同遊戲或相同角色推薦。
3. 允許推薦語意相近但不同作品的音樂。
4. 如果使用者指定特定作品，必須尊重該限制。
5. 推薦理由必須根據候選資料。
6. 不要捏造樂器、編曲、劇情或玩家評論。
7. 如果候選歌曲不足，不要強行湊滿三首。

只回傳有效 JSON。"""

        def _ev_line(c: dict) -> str:
            ev = c.get("evidence") or {}
            bits = []
            if ev.get("scenes"):
                bits.append(f"場景: {', '.join(ev['scenes'][:4])}")
            if ev.get("moods"):
                bits.append(f"情緒: {', '.join(ev['moods'][:4])}")
            if ev.get("wiki_labels"):
                bits.append(f"遊戲內用途: {', '.join(ev['wiki_labels'][:2])}")
            cd = ev.get("comments_digest", "")
            if cd:
                bits.append(f"聽眾留言: {cd[:150]}")
            return " | ".join(bits)

        tracks_text = "\n".join([
            f"{c['index']}. {c['title']} | {c['playlist']}"
            + (f"\n   證據: {_ev_line(c)}" if _ev_line(c) else "")
            for c in candidates
        ])

        analysis_json = json.dumps(analysis, ensure_ascii=False)

        user_prompt = f"""使用者想聽：
"{mood}"

Query Understanding 結果：
{analysis_json}

【Hybrid Search 候選歌曲】（附真實證據：場景標籤、遊戲內用途、聽眾留言）

{tracks_text}

請推薦 3 首最適合的歌曲。

每首歌曲需要評估：

- Mood Compatibility：情緒符合程度。
- Atmosphere Compatibility：氛圍符合程度。
- Musical Characteristics：音樂特色符合程度。
- Context Compatibility：場景及劇情關聯。

回傳格式：

[
  {{
    "index": 1,
    "reason": "使用繁體中文說明推薦理由",
    "matched_moods": ["Melancholic", "Loneliness"],
    "matched_features": ["Piano", "Ambient"],
    "community_insight": "引用候選歌曲附的聽眾留言證據；該曲沒有留言證據則填 null"
  }}
]

規則：
- index 必須使用候選歌曲的原始 index。
- 不得捏造歌曲資訊。
- 可以少於 3 首。
- 只回傳 JSON。"""

        try:
            # 10 秒超時：避免 LLM 卡住拖慢回應（發送走 channel.send，無 interaction 期限）
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider.meta().id,
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                ),
                timeout=20.0,  # 20 秒超時（15 候選 prompt 小；超時快速回退好過白等）
            )
            content = _resp_text(resp)
            if self.debug:
                logger.info(f"[Debug] LLM 原始回覆: {content[:500]}")
            try:
                recommended = _llm_json(content)
            except Exception:
                logger.warning(
                    f"[MusicHybridLLM] JSON 解析失敗，原始回覆前 200 字: {content[:200]!r}")
                raise
            
            candidate_map = {c['index']: c for c in candidates}
            results = []
            for r in recommended[:3]:
                idx = r.get('index')
                if idx in candidate_map:
                    c = candidate_map[idx]
                    results.append({
                        "title": c['title'],
                        "url": c['url'],
                        "playlist": c['playlist'],
                        "reason": r.get('reason', ''),
                        "matched_moods": r.get('matched_moods', []),
                        "matched_features": r.get('matched_features', []),
                        "community_insight": r.get('community_insight', None),
                    })
            return results if results else [
                {"title": c['title'], "url": c['url'], "playlist": c['playlist'],
                 "reason": "關鍵字比對", "matched_moods": [], "matched_features": [],
                 "community_insight": None} for c in candidates[:3]
            ]
        except Exception as e:
            logger.warning(f"[MusicHybridLLM] Recommend 失敗: {e}")
            return [
                {"title": c['title'], "url": c['url'], "playlist": c['playlist'],
                 "reason": "關鍵字比對", "matched_moods": [], "matched_features": [],
                 "community_insight": None} for c in candidates[:3]
            ]


# ===================================================================== #
# Discord 判斷 & 共用
# ===================================================================== #
def _recover_query(event, keyword: str, cmd: str) -> str:
    """從原始訊息復原完整查詢。

    ⚠️ Discord 前綴指令的 GreedyStr 實測只收到第一個 token
    （"city blue" → keyword="city"），但 event.message_str 保有完整原文。
    以訊息原文（去掉指令名與前綴）為準，取較長者。
    """
    q = (keyword or "").strip()
    raw = (getattr(event, "message_str", "") or "").strip()
    if not raw:
        return q
    i = raw.lower().find(cmd)
    if i < 0:
        return q
    tail = raw[i + len(cmd):].strip().lstrip(":： ").strip()
    return tail if len(tail) > len(q) else q


def _is_discord(event: AstrMessageEvent) -> bool:
    return getattr(event.platform_meta, "id", "").lower() == "discord"


def _format_track(t: IndexTrack, reason: str = "") -> str:
    r = f" · {reason}" if reason else ""
    return f"🎵 **{t.title}**\n📂 {t.playlist}\n🔗 {t.url}{r}"


def _extract_youtube_id(url: str) -> Optional[str]:
    """從 YouTube URL 提取 video_id。"""
    import re
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        r'youtube\.com/v/([a-zA-Z0-9_-]{11})',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def _fmt_elapsed(sec: float) -> str:
    """秒 → MM:SS（0X:XX 格式，四捨五入到秒）。"""
    sec = max(0, round(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _make_search_embed(tracks: List[IndexTrack], keyword: str, page: int, total: int,
                       notes: Optional[Dict[str, str]] = None) -> discord.Embed:
    """建立單首歌曲的 Embed：歌名 + 理由 + 完整搜索結果列表（當前 ▶ 標記）。"""
    track = tracks[page]
    desc = f"🎵 **[{track.title}]({track.url})**"
    if notes and track.url in notes:
        desc += f"\n{notes[track.url]}"

    # 搜索結果列表：≤25 首全列；更多時顯示當前頁 ±10（防止超過 embed 4096 字元）
    lines = []
    if total <= 25:
        rng = range(total)
        head, tail = None, None
    else:
        rng = range(max(0, page - 10), min(total, page + 11))
        head = "…" if rng.start > 0 else None
        tail = "…" if rng.stop < total else None
    if head:
        lines.append(head)
    for i in rng:
        t = tracks[i]
        mark = "▶" if i == page else f"{i + 1}."
        note = ""
        lines.append(f"{mark} {t.title[:60]}{note}")
    if tail:
        lines.append(tail)
    desc += "\n\n**搜索結果：**\n" + "\n".join(lines)

    embed = discord.Embed(
        title=f"{keyword} （第 {page + 1} / {total} 首）",
        description=desc[:4000],
        color=0x5865F2,
    )
    # 設定 YouTube 縮圖
    video_id = _extract_youtube_id(track.url)
    if video_id:
        embed.set_image(url=f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg")
    embed.set_footer(text=f"共 {total} 首結果 · 使用按鈕翻頁")
    return embed


class SearchPaginationView(discord.ui.View):
    """搜尋結果分頁 View：上一首 / YouTube / DC端私人觀賞 / 下一首 + 頁碼選單。"""

    def __init__(self, tracks: List[IndexTrack], keyword: str, timeout: float = 120.0,
                 notes: Optional[Dict[str, str]] = None):
        super().__init__(timeout=timeout)
        self.tracks = tracks
        self.keyword = keyword
        self.notes = notes or {}
        self.page = 0
        self.total = len(tracks)
        self.message = None  # 由 _send_paginated 填入，供 on_timeout 編輯
        # 建立選單與按鈕
        self._build_page_select()
        self._add_action_buttons()

    def _add_action_buttons(self):
        """按順序添加四個動作按鈕：上一首 | YouTube | DC端私人觀賞 | 下一首。"""
        # 先移除所有舊的動作按鈕（保留選單）
        for item in self.children[:]:
            if isinstance(item, discord.ui.Button):
                self.remove_item(item)
        
        track = self.tracks[self.page]
        
        # 1. 上一首 (最左)
        btn_prev = discord.ui.Button(
            label="◀ 上一首",
            style=discord.ButtonStyle.secondary,
            custom_id="music_prev",
            disabled=(self.page == 0),
            row=0,
        )
        btn_prev.callback = self._prev_callback
        self.add_item(btn_prev)
        
        # 2. YouTube 播放 (中左) - Link Button 無 custom_id
        btn_yt = discord.ui.Button(
            label="YouTube 播放",
            style=discord.ButtonStyle.link,
            url=track.url,
            row=0,
        )
        self.add_item(btn_yt)
        
        # 3. DC端私人觀賞 (中右)
        btn_send = discord.ui.Button(
            label="DC端私人觀賞",
            style=discord.ButtonStyle.primary,
            custom_id="music_send_link",
            row=0,
        )
        btn_send.callback = self._send_link_callback
        self.add_item(btn_send)
        
        # 4. 下一首 (最右)
        btn_next = discord.ui.Button(
            label="下一首 ▶",
            style=discord.ButtonStyle.secondary,
            custom_id="music_next",
            disabled=(self.page >= self.total - 1),
            row=0,
        )
        btn_next.callback = self._next_callback
        self.add_item(btn_next)

    async def _prev_callback(self, interaction: discord.Interaction):
        if self.page > 0:
            self.page -= 1
            self._update_buttons_state()
            self._build_page_select()
            self._add_action_buttons()
            embed = _make_search_embed(self.tracks, self.keyword, self.page, self.total, self.notes)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _next_callback(self, interaction: discord.Interaction):
        if self.page < self.total - 1:
            self.page += 1
            self._update_buttons_state()
            self._build_page_select()
            self._add_action_buttons()
            embed = _make_search_embed(self.tracks, self.keyword, self.page, self.total, self.notes)
            await interaction.response.edit_message(embed=embed, view=self)

    async def _send_link_callback(self, interaction: discord.Interaction):
        """發送當前歌曲的 YouTube 連結訊息（私密）。"""
        track = self.tracks[self.page]
        await interaction.response.send_message(
            f"🎵 **{track.title}**\n🔗 {track.url}",
            ephemeral=True,
        )

    def _build_page_options(self) -> List[discord.SelectOption]:
        """構建頁碼選項，當前頁設為 default。"""
        options = []
        if self.total <= 25:
            page_range = range(self.total)
        else:
            step = max(1, self.total // 25)
            page_range = range(0, self.total, step)
            page_range = list(page_range)
            if page_range[-1] != self.total - 1:
                page_range.append(self.total - 1)
        
        for i in page_range:
            options.append(discord.SelectOption(
                label=f"第 {i + 1} 首",
                value=str(i),
                default=(i == self.page),  # 當前頁設為預設
            ))
        return options[:25]

    def _build_page_select(self):
        """建立或更新頁碼選單。"""
        # 移除舊的選單（如果有）
        for item in self.children[:]:
            if isinstance(item, discord.ui.Select) and item.custom_id == "music_page_select":
                self.remove_item(item)
        
        options = self._build_page_options()
        select = discord.ui.Select(
            placeholder=f"跳轉頁面 (共 {self.total} 首，當前第 {self.page + 1} 首)",
            options=options,
            custom_id="music_page_select",
            row=1,  # 選單放第 2 行
        )
        select.callback = self._page_select_callback
        self.add_item(select)

    async def _page_select_callback(self, interaction: discord.Interaction):
        new_page = int(interaction.data["values"][0])
        self.page = new_page
        self._update_buttons_state()
        self._build_page_select()
        self._add_action_buttons()
        embed = _make_search_embed(self.tracks, self.keyword, self.page, self.total, self.notes)
        await interaction.response.edit_message(embed=embed, view=self)

    def _update_buttons_state(self):
        """更新按鈕啟用/禁用狀態。"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "music_prev":
                    item.disabled = (self.page == 0)
                elif item.custom_id == "music_next":
                    item.disabled = (self.page >= self.total - 1)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # 真實編輯訊息，讓使用者看到按鈕已失效（而非點了沒反應）
        if getattr(self, "message", None):
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


class PluginLogCapture(logging.Handler):
    """捕捉本插件的後台日誌行（供 /music_log 召喚）。只收自己的標籤前綴。"""

    PREFIXES = ("[Hybrid]", "[MusicHybridLLM]", "[MusicIndex]", "[DiscordMusic]")

    def __init__(self, maxlen: int = 200):
        super().__init__(level=logging.INFO)
        self.buf: deque = deque(maxlen=maxlen)
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            # 本插件標籤全收；其他模組只收 WARNING 以上（錯誤可被 /music_log 召喚）
            if msg.startswith(self.PREFIXES) or record.levelno >= logging.WARNING:
                self.buf.append(self.format(record))
        except Exception:
            pass


# ===================================================================== #
# 插件主體
# ===================================================================== #
@register(
    "astrbot_plugin_discord_music",
    "anku0",
    "Discord 音樂插件：智慧搜尋與 LLM 推薦（Hybrid Search 架構）。本地索引 666 首曲目。",
    "3.1.0",
)
class DiscordMusicPlugin(Star):
    # 泛用停用詞：出現在 english_keywords 也無助於檢索
    _STOP_WORDS = {"music", "song", "songs", "ost", "bgm", "theme", "soundtrack",
                   "library", "of", "the", "and", "ruina", "company"}

    def __init__(self, context: Context):
        super().__init__(context)
        self.index = MusicIndex()
        self.hybrid = MusicHybridLLM(context)
        # 後台日誌捕捉（/music_log 召喚用）
        self.log_capture = PluginLogCapture()
        logger.addHandler(self.log_capture)

    # ----------------------------------------------------------------- #
    # 共用 Hybrid Search 管線
    # ----------------------------------------------------------------- #
    async def _hybrid_pipeline(self, event: AstrMessageEvent, query: str,
                               intent: str = "find"
                               ) -> tuple[Optional[List[IndexTrack]], Dict[str, str]]:
        """共用三階段管線：理解 → 檢索（四層回退）→ LLM 重排序/推薦。

        回傳 (IndexTrack 列表, 理由備註)；完全找不到時 tracks 為 None。
        """
        umo = getattr(event, "unified_msg_origin", None) or getattr(event, "session_id", None)
        t0 = time.perf_counter()
        logger.info(f"[Hybrid] ▶ 管線開始 intent={intent} query={query!r}")

        # Stage 1: Query Understanding（LLM；失敗時內部回退字典翻譯）
        # ⚡ 理解（LLM）與查詢嵌入（NVIDIA）平行：嵌入只用 query 原文，不依賴理解結果
        self._sem_task = asyncio.create_task(
            self.hybrid.semantic_candidates(query, self.index, k=10))
        analysis = await self.hybrid.understand_query(query, umo)
        analysis["intent"] = intent
        t1 = time.perf_counter()
        logger.info(
            f"[Hybrid] ①理解完成 {t1 - t0:.1f}s 來源={analysis.get('interpretation', '')[:40]} "
            f"game={analysis.get('game_filters', [])} "
            f"primary={analysis.get('character_terms', []) + analysis.get('scene_terms', []) + analysis.get('genre_terms', []) + analysis.get('mood_terms', [])} "
            f"secondary={analysis.get('english_keywords', [])[:5]}"
        )

        # 型別防禦：LLM 可能回傳字串而非 list；字串包成單元素、非預期型別丟棄
        def _as_list(v) -> List[str]:
            if isinstance(v, str):
                return [v] if v.strip() else []
            if isinstance(v, list):
                return [str(x).strip() for x in v if x and str(x).strip()]
            return []

        game_filters = _as_list(analysis.get("game_filters"))
        # primary = 角色名優先（「安潔拉解放戰」→ Angela 是主搜尋詞），其次場景/類型/情緒
        primary = [k for k in (
            _as_list(analysis.get("character_terms"))
            + _as_list(analysis.get("scene_terms"))
            + _as_list(analysis.get("genre_terms"))
            + _as_list(analysis.get("mood_terms"))
        ) if k]
        # secondary = 語意輔助詞（去除泛用停用詞）
        secondary = [k for k in (
            _as_list(analysis.get("english_keywords"))
            + _as_list(analysis.get("synonyms"))
        ) if k and k.lower().strip() not in self._STOP_WORDS]
        # 片語關鍵字：英文多詞查詢把整句也當一個關鍵字
        # （"city blue" 不被拆成 city/blue 而與 "Children of the City" 平手）
        if query.isascii() and " " in query.strip():
            phrase = " ".join(query.split())
            if phrase.lower() not in [s.lower() for s in secondary]:
                secondary.append(phrase)
                analysis.setdefault("english_keywords", []).append(phrase)

        char_terms = _as_list(analysis.get("character_terms"))

        # Stage 2: 檢索（語意層優先，關鍵字四層回退兜底）
        candidates = []
        # 0. 語意層：embedding 向量 cosine top-K（index_v2 存在且模型相符時啟用）
        try:
            sem = await self._sem_task
            # ⚡ 角色加權：查詢帶角色名時，標題含該角色的曲目 ×1.5 重排
            #   （壓制「Chesed Battle Theme」這類泛用戰鬥曲霸占向量 top）
            char_terms = _as_list(analysis.get("character_terms"))
            if sem and char_terms:
                for c in sem:
                    tl = c["title"].lower()
                    if any(k.lower() in tl for k in char_terms):
                        c["semantic_score"] = c.get("semantic_score", 0) * 1.5
                sem.sort(key=lambda c: -c.get("semantic_score", 0))
            logger.info(f"[Hybrid] ②語意層：{'向量' if self.index.vectors else '詞袋'} 命中 {len(sem)} 首")
            if sem:
                for c in sem[:3]:
                    logger.info(f"[Hybrid]    ↳ {c.get('semantic_score', 0):.3f} {c['title'][:50]}")
                candidates = sem
        except Exception as e:
            logger.warning(f"[Hybrid] ②語意層失敗: {repr(e)}")

        # 1. 具體關鍵字 + 歌單過濾（最精確：如 Lobby + Library of Ruina）
        kw_hits = []
        if primary and game_filters:
            kw_hits = self.index.get_tracks_for_search(primary, limit=30, playlist_filters=game_filters)
        # 2. 具體關鍵字，不過濾歌單
        if not kw_hits and primary:
            kw_hits = self.index.get_tracks_for_search(primary, limit=30)
        # 3. 次要關鍵字 + 歌單過濾
        if not kw_hits and secondary and game_filters:
            kw_hits = self.index.get_tracks_for_search(secondary, limit=30, playlist_filters=game_filters)
        # 4. 全部關鍵字，不過濾（兜底）
        if not kw_hits:
            all_kw = list(dict.fromkeys(primary + secondary)) or [translate_query_to_english(query), query]
            kw_hits = self.index.get_tracks_for_search(all_kw, limit=30)

        if not candidates and not kw_hits:
            return None, {}

        logger.info(f"[Hybrid] ②關鍵字層命中 {len(kw_hits)} 首")
        # 合併：語意命中在前，關鍵字命中補足（去重）
        seen_urls = set()
        merged = []
        for c in list(candidates) + list(kw_hits):
            u = c.get("url", "")
            if u in seen_urls:
                continue
            seen_urls.add(u)
            # 附帶 enrich 標籤（遊戲內名/Boss 名/勢力/章節）供回退排序匹配
            # ⚠️ 由 plugin 層附帶：MusicHybridLLM 無 index 屬性，回退排序只能讀 candidates
            _e = self.index.enrich.get(c.get("video_id", ""), {})
            for _k in ("ingame", "played", "factions", "chapters"):
                c[_k] = _e.get(_k, [])
            merged.append(c)
        candidates = merged  # 不在此截斷：LLM 路徑取前 15，回退對全量排序
        # 角色優先序：穩定重排（同組保持原相對順序）
        prio = _CHAR_PRIORITY.get((char_terms[0] if char_terms else "").lower())
        if prio and candidates:
            def _prio_key(c, _prio=prio):
                tl = c.get("title", "").lower()
                for i, kw in enumerate(_prio):
                    if kw in tl:
                        return i
                return len(_prio)
            candidates.sort(key=_prio_key)
        logger.info(f"[Hybrid] ②合併候選 {len(candidates)} 首（{time.perf_counter() - t1:.1f}s）")

        # Stage 3: LLM 重排序 / 推薦（失敗時內部回退關鍵字理由）
        t2 = time.perf_counter()
        if intent == "recommend":
            recs = await self.hybrid.recommend(query, analysis, candidates, umo)
            logger.info(f"[Hybrid] ③LLM 推薦完成 {time.perf_counter() - t2:.1f}s → {len(recs)} 首")
            tracks, notes = [], {}
            for r in recs:
                t = IndexTrack(title=r["title"], url=r["url"], playlist=r.get("playlist", ""))
                tracks.append(t)
                parts = []
                if r.get("reason"):
                    parts.append(r["reason"])
                if r.get("matched_moods"):
                    parts.append(f"情緒: {', '.join(r['matched_moods'])}")
                if r.get("matched_features"):
                    parts.append(f"特色: {', '.join(r['matched_features'])}")
                notes[t.url] = "；".join(parts) if parts else "推薦"
            return (tracks or None), notes

        # rerank 候選縮減至 30：VL 120B 對 60 首 prompt 45s 內生成不完
        reranked = await self.hybrid.rerank_find(query, analysis, candidates, umo)
        logger.info(f"[Hybrid] ③LLM 重排序完成 {time.perf_counter() - t2:.1f}s → {len(reranked)} 首")
        tracks, notes = [], {}
        for r in reranked:
            orig = next((t for t in self.index.tracks
                         if t.title == r["title"] and t.url == r["url"]), None)
            t = orig or IndexTrack(title=r["title"], url=r["url"],
                                   playlist=r.get("playlist", ""))
            tracks.append(t)
            reason = r.get("reason", "")
            concepts = r.get("matched_concepts", [])
            if concepts:
                reason = f"{reason} [{', '.join(concepts[:3])}]" if reason else f"匹配: {', '.join(concepts[:3])}"
            if reason:
                notes[t.url] = reason
        logger.info(f"[Hybrid] ✔ 管線完成 總耗時 {time.perf_counter() - t0:.1f}s 產出 {len(tracks)} 首")
        return (tracks or None), notes

    async def _get_channel(self, event: AstrMessageEvent):
        """取得 Discord 頻道物件；不可用回 None。"""
        client = getattr(event, "client", None)
        if client is None:
            logger.warning("[DiscordMusic] 無法取得 Discord client")
            return None
        try:
            channel_id = int(event.session_id)
        except (TypeError, ValueError):
            logger.warning(f"[DiscordMusic] 無效頻道 ID: {event.session_id}")
            return None
        channel = client.get_channel(channel_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(channel_id)
            except Exception:
                channel = None
        if not channel or not isinstance(channel, Messageable):
            logger.warning(f"[DiscordMusic] 無法取得頻道 {channel_id}")
            return None
        return channel

    async def _send_status(self, event: AstrMessageEvent, text: str):
        """送出一條狀態訊息（可事後編輯）。回訊息物件；失敗回 None。"""
        try:
            channel = await self._get_channel(event)
        except Exception:
            return None
        if channel is None:
            return None
        try:
            return await channel.send(text)
        except Exception as e:
            logger.warning(f"[DiscordMusic] 狀態訊息送出失敗: {repr(e)}")
            return None

    @staticmethod
    async def _edit_status(msg, text: str) -> None:
        """編輯狀態訊息；失敗只記 log（不影響主流程）。"""
        if msg is None:
            return
        try:
            await msg.edit(content=text)
        except Exception as e:
            logger.warning(f"[DiscordMusic] 狀態訊息編輯失敗: {repr(e)}")

    async def _elapsed_ticker(self, msg, t0: float, stop: asyncio.Event) -> None:
        """每 3 秒更新狀態訊息的經過時間（格式 0X:XX），直到 stop 被設置。"""
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=3.0)
                return
            except asyncio.TimeoutError:
                pass
            await self._edit_status(
                msg, f"🤔 正在理解您的需求並搜尋… （{_fmt_elapsed(time.perf_counter() - t0)}）")

    async def _send_paginated(self, event: AstrMessageEvent, tracks: List[IndexTrack],
                              label: str, notes: Optional[Dict[str, str]] = None) -> None:
        """發送分頁 Embed（只發 Embed 本體，不發額外文字訊息）。"""
        channel = await self._get_channel(event)
        if channel is None:
            return
        embed = _make_search_embed(tracks, label, 0, len(tracks), notes)
        view = SearchPaginationView(tracks, label, notes=notes)
        try:
            msg = await channel.send(embed=embed, view=view)
            view.message = msg
            logger.info(f"[DiscordMusic] ✅ 分頁 Embed 已發送：{label!r} 共 {len(tracks)} 首")
        except Exception as e:
            logger.exception(f"[DiscordMusic] 發送分頁結果失敗: {e}")

    # ----------------------------------------------------------------- #
    # 指令 1：搜尋（快路徑精確匹配 → 失敗才走 Hybrid LLM 管線）
    # ----------------------------------------------------------------- #
    @filter.command("music_search")
    async def music_search(self, event: AstrMessageEvent, keyword: GreedyStr = ""):
        """
        用法：/music_search <關鍵字或自然語言>
        快路徑：精確歌單/標題匹配（免 LLM，即時）
        慢路徑：Hybrid Search（LLM 理解 → 檢索 → LLM 重排序，附理由）
        例：/music_search Mili            ← 直接命中歌單
            /music_search 圖書館大廳       ← LLM 理解後搜尋
            /music_search 腦葉公司 大廳
            /music_search 邊獄巴士 第三章
        """
        if not _is_discord(event):
            yield event.plain_result("❌ 僅限 Discord 平台使用。")
            return
        if not keyword or not keyword.strip():
            yield event.plain_result("🔍 用法：`/music_search <關鍵字或自然語言>`\n範例：`/music_search Mili` `/music_search 圖書館大廳`")
            return

        q = _recover_query(event, keyword, "music_search")

        # 快路徑：精確歌單名匹配或標題包含（免 LLM、零延遲）
        direct = self.index.search(q)
        if direct:
            await self._send_paginated(event, direct, q)
            return

        # 慢路徑：Hybrid Search（LLM 理解 → 檢索 → 重排序，附理由）
        t0 = time.perf_counter()
        status = await self._send_status(
            event, f"🤔 正在理解您的需求並搜尋… （{_fmt_elapsed(0)}）")
        if status is None:
            yield event.plain_result("🤔 正在理解您的需求並搜尋…")
        stop = asyncio.Event()
        ticker = asyncio.create_task(self._elapsed_ticker(status, t0, stop))
        try:
            tracks, notes = await self._hybrid_pipeline(event, q, "find")
        finally:
            stop.set()
            await ticker
        elapsed = time.perf_counter() - t0
        if not tracks:
            await self._edit_status(
                status, f"❌ 找不到符合「{q}」的歌曲。（花了 {elapsed:.1f} 秒）")
            return
        await self._edit_status(status, f"✅ 搜尋完成，花了 {elapsed:.1f} 秒")
        await self._send_paginated(event, tracks, q, notes=notes)

    # ----------------------------------------------------------------- #
    # 指令 3：心情/場景推薦（同管線，結果附推薦理由）
    # ----------------------------------------------------------------- #
    @filter.command("music_recommend")
    async def music_recommend(self, event: AstrMessageEvent, mood: GreedyStr = ""):
        """
        用法：/music_recommend <心情/場景/風格>
        例：/music_recommend 深夜獨處
            /music_recommend 悲傷療癒
        """
        if not _is_discord(event):
            yield event.plain_result("❌ 僅限 Discord 平台使用。")
            return
        if not mood or not mood.strip():
            yield event.plain_result("🎵 用法：`/music_recommend <心情/場景/風格>`\n範例：`/music_recommend 深夜獨處` `/music_recommend 專注寫代碼`")
            return

        m = mood.strip()
        t0 = time.perf_counter()
        status = await self._send_status(
            event, f"🤔 正在理解您的需求並搜尋… （{_fmt_elapsed(0)}）")
        if status is None:
            yield event.plain_result("🤔 正在理解您的需求並搜尋…")
        stop = asyncio.Event()
        ticker = asyncio.create_task(self._elapsed_ticker(status, t0, stop))
        try:
            tracks, notes = await self._hybrid_pipeline(event, m, "recommend")
        finally:
            stop.set()
            await ticker
        elapsed = time.perf_counter() - t0
        if not tracks:
            await self._edit_status(
                status, f"❌ 找不到符合「{m}」氛圍的歌曲。（花了 {elapsed:.1f} 秒）")
            return
        await self._edit_status(status, f"✅ 推薦完成，花了 {elapsed:.1f} 秒")
        await self._send_paginated(event, tracks, m, notes=notes)

    # ----------------------------------------------------------------- #
    # 指令 3：召喚後台執行過程日誌
    # ----------------------------------------------------------------- #
    @filter.command("music_log")
    async def music_log(self, event: AstrMessageEvent, lines: GreedyStr = ""):
        """
        用法：/music_log [行數]
        召喚最近一次搜尋的後台執行過程（markdown code fence 包住）。
        例：/music_log 30  → 最近 30 行（預設 40）
        """
        if not _is_discord(event):
            yield event.plain_result("❌ 僅限 Discord 平台使用。")
            return

        n = 40
        s = (lines or "").strip()
        if s.isdigit():
            n = max(1, min(int(s), 100))

        buf = list(self.log_capture.buf)[-n:]
        if not buf:
            yield event.plain_result("（尚無後台日誌。先跑一次 /music_search）")
            return

        text = "\n".join(buf)
        if len(text) > 1900:  # Discord 單訊息 2000 字元上限
            text = "…\n" + text[-1890:]
        yield event.plain_result(f"```\n{text}\n```")

    # ----------------------------------------------------------------- #
    # 指令 4：除錯模式切換
    # ----------------------------------------------------------------- #
    @filter.command("music_debug")
    async def music_debug(self, event: AstrMessageEvent):
        """切換除錯模式：log LLM 原始回覆與各階段明細。"""
        if not _is_discord(event):
            yield event.plain_result("❌ 僅限 Discord 平台使用。")
            return
        self.hybrid.debug = not self.hybrid.debug
        state = "🟢 開啟" if self.hybrid.debug else "🔴 關閉"
        yield event.plain_result(
            f"音樂插件除錯模式：{state}\n"
            f"開啟時後台會記錄 LLM 原始回覆（用 /music_log 召喚）")
