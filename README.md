# astrbot_plugin_discord_music

AstrBot 的 Discord 音樂搜尋插件，以 Hybrid Search 架構（語意向量 + 關鍵字 + LLM 重排序）搜尋 Project Moon 三部曲的官方 OST，共 666 首。

## 收錄範圍

| 歌單 | 曲目數 | 內容 |
|------|--------|------|
| Limbus | 420 | 邊獄巴士（Limbus Company）OST 與 Mili 主題曲 |
| 廢墟圖書館 | 113 | 廢墟圖書館（Library of Ruina）OST |
| 腦葉公司 | 115 | 腦葉公司（Lobotomy Corporation）OST |
| Mili頻道 | 18 | Mili 官方頻道歌曲 |

合計 666 首，650 個唯一 video_id。

## 安裝

將本 repo 放到 AstrBot 的插件目錄：

```
<AstrBot根目錄>/data/plugins/astrbot_plugin_discord_music/
├── main.py
├── __init__.py
├── metadata.yaml
└── data/music_index/
    ├── index.json          # v1 歌單曲目
    ├── index_v2.json       # v2 標籤
    └── cache/*.txt         # 遊戲內部 OST 權威清單
```

索引路徑由插件位置推導（`<AstrBot>/data/music_index/`），也可用環境變數 `MUSIC_INDEX_DIR` 指定其他位置。

重啟 AstrBot 後，啟動日誌應顯示：

```
[MusicIndex] 已載入 666 首曲目，4 個歌單
[MusicIndex] v2 標籤資料：enrich=635 vectors=650 model=nvidia/nemotron-3.5-embed-8b dim=4096
```

若 `index_v2_vectors.json` 不存在，會顯示「無向量 → 詞袋 IDF 檢索」並自動降級，功能仍可用。

## 指令

| 指令 | 說明 | 範例 |
|------|------|------|
| `/music_search <關鍵字>` | 搜尋；自然語言走 Hybrid Search | `/music_search 圖書館大廳` |
| `/music_recommend <心情>` | 心情或場景推薦，附理由 | `/music_recommend 放鬆` |
| `/music_log [行數]` | 召喚後台執行過程，以 code fence 顯示 | `/music_log 30` |
| `/music_debug` | 切換除錯模式，記錄 LLM 原始回覆 | `/music_debug` |

## 運作方式

```
使用者查詢
    │
    ▼
Stage 1 · Query Understanding（LLM）
    「宗教層戰鬥」→ game: [Library of Ruina]
                    character: [Hokma]
                    scene: [Battle]
    失敗時改用字典回退，仍做結構化分類
    │
    │（查詢嵌入與此階段平行執行）
    ▼
Stage 2 · 雙層檢索後合併
    · 語意層：NVIDIA nemotron 向量 cosine
    · 關鍵字層：標題 / 歌單 / enrich 標籤
      標籤命中 2 分，標題命中 1 分
    · 四層退回：primary+歌單 → primary → secondary+歌單 → 全部
    │
    ▼
Stage 3 · LLM 重排序（20 秒逾時）
    逾時改用加權退回排序，角色名權重 ×3
    │
    ▼
分頁 Embed（按鈕翻頁，附匹配理由）
```

### enrich 標籤

| 標籤 | 來源 | 例 |
|------|------|-----|
| `ingame` | 遊戲內部 OST 清單對照 | KeterBattle01、Roland02、LaMancha Carnival 2 |
| `factions` | 五指勢力與留言挖掘 | Thumb、Middle、Pinky |
| `chapters` | 遊戲內曲名解析 | Canto IX、Intervallo VII-2、Walpurgis Night |
| `played` | wiki.gg Played During 與留言挖掘 | Ricardo、Dulcinea、The Chaplain、Rien |

標籤同時寫入詞袋索引與 embedding 文本，因此「里卡多」這類標題中不出現的名字也能命中。

## 重建索引

索引由 `tools/build_music_index_v2.py` 產生，只需 Python 3.10 以上標準庫：

```bash
python tools/build_music_index_v2.py enrich   # 抓 YouTube 標題、留言、標籤 → cache/
python tools/build_music_index_v2.py wiki     # 抓 wiki.gg 場景與聽眾證據
python tools/build_music_index_v2.py embed    # 呼叫 embedding API 產生向量（選用）
python tools/build_music_index_v2.py merge    # 合併 → data/music_index/
```

| 檔案 | 大小 | 用途 |
|------|------|------|
| index.json | 129K | v1 歌單曲目 |
| index_v2.json | 483K | enrich 標籤，進版控 |
| index_v2_vectors.json | 27M | 向量，gitignore，可重建 |

語意層需要任一 embedding provider：

```bash
export NVIDIA_API_KEY=...     # integrate.api.nvidia.com
export GEMINI_API_KEY=...     # 或 AstrBot 內建的 gemini_embedding
```

也可在 AstrBot 後台設定同等 provider。插件只在模型名稱與索引記錄一致時使用它，否則改用 NVIDIA NIM。

## 環境變數

| 變數 | 說明 | 預設 |
|------|------|------|
| `MUSIC_INDEX_DIR` | 索引資料目錄 | `<AstrBot>/data/music_index` |
| `NVIDIA_API_KEY` | NVIDIA NIM embedding 金鑰 | 無，僅詞袋 |

## 疑難排解

| 症狀 | 原因 | 處理 |
|------|------|------|
| `跳過 AstrBot embedding（provider=未知）` | provider 的 get_model() 回空 | 正常，已自動改用 NVIDIA |
| `Rerank 失敗:`，錯誤訊息空白 | LLM 20 秒逾時 | 自動走加權退回排序，結果仍正確 |
| `Query Understanding 失敗: JSONDecodeError` | LLM 回傳非 JSON | 開 `/music_debug` 後以 `/music_log` 看原始回覆 |
| 英文片語查詢結果不精準 | AstrBot 的 GreedyStr 只收第一個 token | 已由 `_recover_query()` 從原始訊息復原完整查詢 |
| 索引載入但沒有標籤 | index_v2.json 缺失或路徑指錯 | 確認 `MUSIC_INDEX_DIR` 與啟動日誌 |

## 版權

音樂版權屬 Project Moon、Mili 及各自權利人。本插件僅索引官方公開上傳的影片連結，不儲存、不轉載音訊。

資料來源：Project Moon 官方 YouTube 頻道、limbuscompany.wiki.gg、遊戲內部 OST 曲目清單。樓層與 Sephirah、五指與父輩等對照經過 wiki 與遊戲資料交叉驗證。