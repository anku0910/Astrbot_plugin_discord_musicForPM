# astrbot_plugin_discord_music

[![AstrBot](https://img.shields.io/badge/AstrBot-v3.2.0+-blue)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)

Discord 音樂搜尋插件 — **Hybrid Search** 架構（語意向量 + 關鍵字 + LLM 重排序），
收錄 Project Moon 三部曲（邊獄巴士／腦葉公司／廢墟圖書館）+ Mili 共 **666 首**官方 OST。

> 原作者：anku0 ｜ 資料來源：YouTube 官方上傳曲目 + wiki.gg + 遊戲內部 OST 清單

---

## 功能特色

- 🧠 **自然語言理解**：中文、英文、縮寫、混合語言都能查（「圖書館大廳」「宗教層戰鬥」「中指父輩」）
- 🔍 **三階段 Hybrid Search**：LLM 查詢理解 → 語意向量/關鍵字雙層檢索 → LLM 重排序
- 🏷️ **五類權威標籤**：遊戲內曲名別名、五指勢力、章節、Boss 戰用途、留言自動挖掘
- ⏱️ **即時進度顯示**：每 3 秒更新經過時間，完成時顯示總耗時
- 📖 **後台日誌召喚**：`/music_log` 用 code fence 顯示完整執行過程（含錯誤）
- 🐛 **除錯模式**：`/music_debug` 記錄 LLM 原始回覆，診斷解析失敗
- 🛡️ **零外部依賴**：只用 Python 標準庫 + AstrBot 內建 `discord.py`

---

## 收錄內容

| 歌單 | 曲目數 | 內容 |
|------|--------|------|
| `Limbus` | 420 | 邊獄巴士（Limbus Company）OST + Mili 主題曲 |
| `廢墟圖書館` | 113 | 廢墟圖書館（Library of Ruina）OST |
| `腦葉公司` | 115 | 腦葉公司（Lobotomy Corporation）OST |
| `Mili頻道` | 18 | Mili 官方頻道歌曲 |
| **合計** | **666** | 650 個唯一 video_id |

---

## 安裝

### 1. 放置插件

把整個 repo 放到 AstrBot 的插件目錄：

```
<AstrBot根目錄>/data/plugins/astrbot_plugin_discord_music/
├── main.py
├── __init__.py
├── metadata.yaml
└── data/music_index/
    ├── index.json          # v1 歌單曲目
    ├── index_v2.json       # v2 標籤（enrich，0.5MB）
    └── cache/*.txt         # 遊戲內部 OST 權威清單
```

索引資料會由插件位置自動推導（`<AstrBot>/data/music_index/`），
也可用環境變數 `MUSIC_INDEX_DIR` 指定其他位置。

### 2. 重啟 AstrBot

插件會在啟動時載入索引並印出：

```
[MusicIndex] 已載入 666 首曲目，4 個歌單
[MusicIndex] v2 標籤資料：enrich=635 vectors=650 model=nvidia/nemotron-3.5-embed-8b dim=4096
```

若 `index_v2_vectors.json` 不存在，會顯示「（無向量 → 詞袋 IDF 檢索）」並**自動降級**，
功能仍可用（少了語意向量層的精確度）。

---

## 指令

| 指令 | 說明 | 範例 |
|------|------|------|
| `/music_search <關鍵字>` | 搜尋音樂（精確匹配即時回應；自然語言走 Hybrid Search） | `/music_search 圖書館大廳` |
| `/music_recommend <心情>` | 心情／場景推薦（附推薦理由） | `/music_recommend 放鬆` |
| `/music_log [行數]` | 召喚最近的後台執行過程（code fence） | `/music_log 30` |
| `/music_debug` | 切換除錯模式（記錄 LLM 原始回覆） | `/music_debug` |

---

## 運作原理

```
使用者查詢
    │
    ▼
┌─────────────────────────────────────────────┐
│ Stage 1 · Query Understanding（LLM）         │
│  「宗教層戰鬥」→                              │
│    game: [Library of Ruina]                 │
│    character: [Hokma]                       │
│    scene: [Battle]                          │
│  失敗 → 字典回退（仍做結構化分類）            │
└─────────────────────────────────────────────┘
    │  （查詢嵌入與此平行執行）
    ▼
┌─────────────────────────────────────────────┐
│ Stage 2 · 雙層檢索 + 合併                    │
│  · 語意層：NVIDIA nemotron 向量 cosine       │
│  · 關鍵字層：標題 / 歌單 / enrich 標籤        │
│    （標籤命中 2 分 > 標題命中 1 分）          │
│  · 四層回退：primary+歌單 → primary →        │
│    secondary+歌單 → 全部                    │
└─────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────┐
│ Stage 3 · LLM 重排序（20s 超時）              │
│  逾時 → 加權回退排序（角色名權重 ×3）         │
└─────────────────────────────────────────────┘
    │
    ▼
分頁 Embed（▶ 當前頁、💡→理由、◀▶ 按鈕翻頁）
```

### 五類 enrich 標籤

| 標籤 | 來源 | 範例 |
|------|------|------|
| `ingame` | 遊戲內部 OST 清單對照 | `KeterBattle01`、`Roland02`、`LaMancha Carnival 2` |
| `factions` | 五指勢力 + 留言挖掘 | `Thumb`、`Middle`、`Pinky` |
| `chapters` | 遊戲內曲名解析 | `Canto IX`、`Intervallo VII-2`、`Walpurgis Night` |
| `played` | wiki.gg「Played During」+ 留言挖掘 | `Ricardo`、`Dulcinea`、`The Chaplain`、`Rien` |

標籤同時餵給**詞袋索引**與**embedding 文本**，所以「里卡多」這種標題沒有的名字也能命中。

---

## 重建索引

索引由 `tools/build_music_index_v2.py` 產生（單一依賴：Python 3.10+ 標準庫）。

```bash
python tools/build_music_index_v2.py enrich   # 抓 YouTube 標題/留言/標籤 → cache/
python tools/build_music_index_v2.py wiki     # 抓 wiki.gg 場景/聽眾證據
python tools/build_music_index_v2.py embed    # 呼叫 embedding API 產生向量（選用）
python tools/build_music_index_v2.py merge    # 合併 → data/music_index/
```

| 檔案 | 大小 | 用途 |
|------|------|------|
| `index.json` | 129K | v1 歌單曲目清單 |
| `index_v2.json` | 0.5M | enrich 標籤（進版控） |
| `index_v2_vectors.json` | 27M | embedding 向量（gitignore，可重建） |

### 語意層金鑰（選用）

向量層需要任一 embedding provider：

```bash
export NVIDIA_API_KEY=...              # 優先：integrate.api.nvidia.com
export GEMINI_API_KEY=...              # 或 AstrBot 內建的 gemini_embedding
```

也可在 AstrBot 後台設定任一等價 provider — 插件只在**模型名稱與索引記錄一致**時使用它，
否則自動改用 NVIDIA NIM。

---

## 設定

| 環境變數 | 說明 | 預設 |
|----------|------|------|
| `MUSIC_INDEX_DIR` | 索引資料目錄 | `<AstrBot>/data/music_index` |
| `NVIDIA_API_KEY` | NVIDIA NIM embedding 金鑰 | 無（則僅詞袋） |

`metadata.yaml` 內含指令列表與版本號，AstrBot 會在插件市場顯示。

---

## 疑難排解

| 症狀 | 原因 | 處理 |
|------|------|------|
| `跳過 AstrBot embedding（provider=未知）` | AstrBot provider 的 `get_model()` 回空 | 正常，已自動改用 NVIDIA |
| `Rerank 失敗:`（空白錯誤） | LLM 20 秒超時 | 自動走加權回退排序，結果仍正確 |
| `Query Understanding 失敗: JSONDecodeError` | LLM 回傳非 JSON | `/music_debug` 開啟後 `/music_log` 看原始回覆 |
| 查英文片語結果不精準 | AstrBot 的 `GreedyStr` 只收第一個 token | 已由 `_recover_query()` 從原始訊息復原完整查詢 |
| 索引載入但沒有標籤 | `index_v2.json` 缺失或 `MUSIC_INDEX_DIR` 指錯 | 確認路徑與啟動日誌 |

---

## 版權與致謝

- 所有音樂版權屬 **Project Moon / Studio EIM / Mili** 及各自權利人。
- 本插件僅索引**官方公開上傳**的 YouTube 影片連結，不儲存、不轉載任何音訊。
- 資料來源：[Project Moon 官方 YouTube 頻道](https://www.youtube.com/@ProjectMoonOfficial)、
  [Studio EIM](https://www.youtube.com/@studioeim)、
  [limbuscompany.wiki.gg](https://limbuscompany.wiki.gg/)、
  遊戲內部 OST 曲目清單。
- 樓層↔Sephirah、五指↔父輩等對照經過 wiki 與遊戲資料交叉驗證。

## License

MIT