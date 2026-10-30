# KONG

![KONG](docs/key-visual.webp)

**Windows 上的繁體中文語音輸入。在任何文字框連按兩下右 Ctrl、講一句話，整理好的書面繁中直接貼進游標。**

受 Mac 上的 [Talky](https://github.com/intentionltd888/talky) 啟發而做的獨立專案，跟 Talky 或 INTENTION® 沒有關係，也沒有用到它的程式碼。

## 主視窗

雙擊 `Kong.exe`（或點右下角系統匣的 KONG 圖示）會打開主視窗：

- **首頁**：現在用哪個辨識引擎、有沒有顯示卡、Claude Code 整理是否正常。按「**測試整理**」會直接告訴你整理為什麼沒成功；「**試講一句**」可以講一句話，看辨識和整理的結果（不會貼到別的地方）。
- **設定**：辨識引擎、整理方式、Claude 模型、快捷鍵、貼上方式、開機自動啟動。
- **最近說過的話**：最近 20 句，按兩下就複製。
- **常用詞**：人名、公司名、專有名詞，整理時優先用這些寫法。

關掉視窗 KONG 還是在背景待命；再雙擊一次 `Kong.exe` 就會把視窗叫回來。

## 會議記錄

一整場會議（一小時以上也可以）：主視窗的「會議記錄」分頁，或系統匣選單的「開始會議記錄」。

- **邊錄邊轉成文字**：每次有人停頓，前面講完的就先辨識好，主視窗看得到逐字稿一段一段出現。結束時只剩最後幾秒要處理。
- **結束後整理成檔案**：請你的 Claude Code 寫出摘要、重點（附時間）、決議、待辦事項和待確認的問題，連同完整逐字稿存成 Markdown，自動打開。放在「文件\Kong 會議記錄」。
- **不怕當掉**：錄音中每一段都馬上寫進「（錄音中）.txt」；就算中途結束 KONG，逐字稿也會留下。
- 錄音檔預設不留；要的話在「會議記錄」分頁勾「保留錄音檔」（一小時約 110 MB）。
- 會議記錄和聽寫共用麥克風，錄音期間不能聽寫。
- 只錄得到麥克風聽見的聲音：線上會議戴耳機時，對方的聲音不會被錄進去。

## 三步

1. 在任何文字框（記事本、瀏覽器、LINE、VS Code、終端機）**連按兩下右 Ctrl**。螢幕下方出現面板，邊講邊出字。
2. 講完**按一下右 Ctrl**。面板寫「整理中」。
3. 整理好的字**貼進游標處**，面板寫「已貼上」，然後自己消失。

講到一半講錯，直接說「啊不對，是……」，整理時只會留改口後的版本。按 **Esc** 隨時取消。

## 需要什麼

- Windows 10 或 11（64 位元）
- 要「整理」（去贅字、修改口、修錯字）：電腦上裝好 [Claude Code](https://code.claude.com/docs)，並用你自己的 Claude Pro／Max 訂閱登入過一次（在終端機執行 `claude`）。沒有的話 KONG 照樣能用，只是貼上的是轉成繁體、加好標點的原稿。
- 有 NVIDIA 顯示卡更好：KONG 會自己偵測，問你要不要下載高準確度模型。

## 安裝

1. 到 [Releases](../../releases) 下載 `Kong-windows-x64.zip`（或 [Actions](../../actions) 最新一次成功建置的 Artifacts），解壓縮到你想放的地方（例如「文件\Kong」）。
2. 雙擊 `Kong.exe`。因為還沒有程式碼簽章，Windows 可能會跳出「Windows 已保護您的電腦」：按「其他資訊」→「仍要執行」。
3. 第一次開啟會下載語音模型（163 MB），下載完就能離線辨識。
4. 右下角系統匣出現藍色麥克風圖示，就可以開始用了。想要開機自動啟動：圖示按右鍵 →「開機時自動啟動」。

## 速度

講完到貼上，在一般電腦上大約 2 秒多：

| 步驟 | 時間 | 怎麼做到的 |
|---|---|---|
| 辨識 | 約 0.3～0.5 秒 | SenseVoice 在 CPU 上跑；有 NVIDIA 顯示卡則用 Whisper |
| 整理 | 約 1.5～2.5 秒 | Claude Code 先在背景開好待命，模型一寫完就拿結果，不等 Claude Code 收尾 |
| 貼上 | 不到 0.1 秒 | 剪貼簿＋Ctrl+V，一秒後把你原本的剪貼簿放回去 |

Talky 每講一句都重新啟動一次 Claude Code，光啟動就要好幾秒；KONG 改成讓一個 Claude Code 程式預先待命，每句用完就換一個新的，所以每句仍然是乾淨的、不帶前文。

## 辨識引擎

| 引擎 | 適合 | 說明 |
|---|---|---|
| **SenseVoice**（CPU） | 任何電腦，預設 | 最快（一句不到半秒），163 MB；碰到口音、講很快、中英夾雜時比較容易錯，整理時會修正大部分 |
| **Qwen3-ASR**（CPU） | 沒有顯示卡但想更準 | 口音、講很快、有雜音時準很多；每句多等 1～3 秒，另外下載約 880 MB。講話時先用 SenseVoice 顯示文字，講完用 Qwen3-ASR 定稿；你停頓時會先把前面講完的部分辨識掉，長段落講完不用等太久 |
| **Whisper large-v3-turbo**（GPU） | 有 NVIDIA 顯示卡（4 GB 以上） | Talky 用的同一個模型，又快又準；另外下載約 2 GB |

預設「自動」：偵測到可用的 NVIDIA 顯示卡就問你要不要下載 Whisper；下載期間照常用 SenseVoice，下載完自動切換。在主視窗的「設定」可以隨時換。

## 整理：用你自己的 Claude 訂閱

KONG 不經手任何帳號或金鑰：它呼叫你電腦上已經登入的官方 Claude Code，登入、計費都是 Claude Code 自己處理，算你自己訂閱的額度。

呼叫時完全隔離：不給任何工具、不載入你的 CLAUDE.md、外掛和 MCP（`--safe-mode`）、在一個空資料夾裡跑、不留對話紀錄、移除 `ANTHROPIC_API_KEY`（確保用訂閱，不是 API 計費）。它只做一件事：把這一句口語整理成書面中文。

整理失敗（沒登入、逾時、額度用完）就貼原稿，面板會寫出原因，**不會假裝成功**。

## 隱私

- **聲音不出電腦**：語音辨識全部在你的電腦上跑。
- **會離開電腦的只有辨識出來的文字**，而且只在整理方式是 Claude Code 時，送到你自己的 Claude 帳號。選「不整理」就什麼都不送。
- 對外連線只有：下載模型（GitHub、HuggingFace、PyPI，每個檔案都比對 SHA256）和你的 Claude Code。沒有遙測、沒有帳號、沒有自家伺服器。
- 記錄檔預設**不記你說了什麼**，只記時間和步驟。

## 設定（系統匣圖示按右鍵）

- **整理方式**：Claude Code／不整理
- **Claude 模型**：Sonnet（預設，最穩定）、Haiku（較省額度，偶爾會多加說明）、Opus
- **辨識引擎**：自動／SenseVoice／Whisper（NVIDIA）
- **快捷鍵**：右 Ctrl／右 Alt／右 Shift
- **貼上方式**：剪貼簿＋Ctrl+V（預設）／模擬打字（不動剪貼簿）
- **編輯常用詞**：一行一個人名、公司名、專有名詞，辨識和整理時會優先用這些寫法
- **複製上一句**、**最近說過的話**（最近 20 句，只存在這台電腦）
- **匯出診斷檔到桌面**：回報問題時附上（不含你說過的話）

## 你的東西放哪

| 東西 | 位置 |
|---|---|
| 設定 | `%APPDATA%\Kong\config.json` |
| 常用詞 | `%APPDATA%\Kong\glossary.txt` |
| 最近說過的話 | `%APPDATA%\Kong\history.json` |
| 記錄檔 | `%APPDATA%\Kong\logs\kong.log` |
| 語音模型 | `%LOCALAPPDATA%\Kong\models\` |

## 常見問題

**筆電沒有右 Ctrl（被 Copilot 鍵取代）**：系統匣圖示 →「快捷鍵」改成右 Alt 或右 Shift。注意右 Alt 在某些程式會順便打開選單列。

**連按沒反應**：確認系統匣有 KONG 圖示。部分防毒軟體會擋「監聽鍵盤」的程式，請把 KONG 加進白名單。

**貼不進去，面板說「已放進剪貼簿」**：目標視窗是以系統管理員身分執行的（例如系統管理員的終端機），Windows 不允許一般程式對它送按鍵。按 Ctrl+V 就好，或把 KONG 也用系統管理員身分執行。

**麥克風沒有聲音**：Windows 設定 → 隱私權與安全性 → 麥克風 → 打開「讓桌面應用程式存取您的麥克風」。

**整理失敗：Claude Code 還沒登入**：打開終端機（PowerShell）執行 `claude`，照指示登入一次。Claude Code 在 Windows 也需要 [Git for Windows](https://git-scm.com/downloads/win)。

## 從原始碼執行／自己打包

需要 Python 3.12（Windows 64 位元）：

```bat
pip install -r requirements-dev.txt
python -m kong
```

測試與打包：

```bat
python -m pytest -q tests
python -c "from kong.ui.icon import write_ico; write_ico('packaging/kong.ico')"
pyinstaller packaging/kong.spec --noconfirm
```

`dist\Kong\Kong.exe` 就是成品。推上 GitHub 後，`.github/workflows/build.yml` 會在 Windows 主機上自動測試、打包、跑自我檢查；推 `v*` 標籤會建立 Release。

命令列工具（排查問題用）：`Kong.exe doctor`、`Kong.exe transcribe a.wav --polish`、`Kong.exe polish "那個我們明天開會"`。

## 用到的開源專案

[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)（Apache-2.0）、[SenseVoice](https://github.com/FunAudioLLM/SenseVoice) 模型（[FunASR Model License](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)）、[Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR) 模型（Apache-2.0）、[Sun Valley ttk theme](https://github.com/rdbende/Sun-Valley-ttk-theme)（MIT）、[faster-whisper](https://github.com/SYSTRAN/faster-whisper)（MIT）、[CTranslate2](https://github.com/OpenNMT/CTranslate2)（MIT）、Whisper large-v3-turbo 模型（MIT）、[OpenCC](https://github.com/BYVoid/OpenCC)（Apache-2.0）、[pynput](https://github.com/moses-palmer/pynput) 與 [pystray](https://github.com/moses-palmer/pystray)（LGPL-3.0）、[python-sounddevice](https://github.com/spatialaudio/python-sounddevice)／PortAudio（MIT）、[Pillow](https://github.com/python-pillow/Pillow)、[NumPy](https://numpy.org)、[PyAV](https://github.com/PyAV-Org/PyAV)（FFmpeg，LGPL）。NVIDIA cuBLAS 不隨 KONG 散布：選用顯示卡模式時，從 PyPI 上 NVIDIA 官方套件直接下載到你的電腦。
