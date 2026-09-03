# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 這是什麼

一個 Flask 應用程式：輸入 YouTube URL，下載影片、擷取定期且去重複的截圖，並產生繁體中文的轉錄／翻譯／摘要，全部使用地端模型，不依賴任何雲端 LLM API。最初由 Replit Agent 產出原型，之後與 Claude 反覆迭代。

## 執行方式

```bash
pip install -r requirements.txt   # 或 requirements-dev.txt（多一個 pytest）
python main.py
```

- 服務執行於 `http://0.0.0.0:5001`（可用 `FLASK_HOST`/`FLASK_PORT` 覆寫），`debug` 預設關閉（`FLASK_DEBUG=true` 才開）。
- 需要系統 PATH 中有 `ffmpeg`（透過 `subprocess` 進行聲音擷取）。
- 語音轉錄後端由 `config.TRANSCRIPTION_BACKEND` 決定，預設 `'apple'`（地端 `Speech`/`SpeechAnalyzer`，見下方「語音轉錄後端」）；`'mlx_whisper'` 只在明確切換或 apple 轉錄失敗時才會被 import（延遲載入，`utils/video_processor.py:transcribe_audio_with_whisper`）——模型倉庫 `WHISPER_MODEL_REPO`（預設 `mlx-community/whisper-large-v3-mlx`）會在第一次使用時下載並快取。**用得到 `mlx-whisper` 的話，強烈建議用專屬的 conda/venv 環境跑這個專案**，不要用共用的 `base` 環境——`mlx-whisper` 依賴的 `numba` 要求 `numpy<2.0`（`requirements.txt` 已經釘 `numpy==1.24.3`），共用環境很容易因為其他工具把 numpy 動到 2.x 而讓 `mlx_whisper` 整個 import 失敗（`ImportError: Numba needs NumPy 2.0 or less`），修法是重新建一個乾淨的專屬環境裝 `requirements.txt`，而不是直接在共用環境裡降版 numpy（風險是波及其他不相關的工具）。預設的 `'apple'` 後端完全不需要 `mlx-whisper`/`numpy`/`numba` 這條依賴鏈，這個問題只有明確切到 `'mlx_whisper'` 後端才會遇到。
- 翻譯／摘要後端由 `config.TRANSLATION_BACKEND` 決定，預設 `'apple'`，見下方「翻譯／摘要後端」。
- `requirements.txt` 裡 `yt_dlp` 是 `>=` 而非 `==` 釘死版本，故意的：YouTube 常改防機器人機制，pin 死的舊版 yt-dlp 過一陣子就會開始 403/429，需要定期 `pip install --upgrade yt-dlp`。`openai` 這個套件也曾經因為版本太舊（1.46.0）跟新版 `httpx` 不相容（`Client.__init__() got an unexpected keyword argument 'proxies'`）而整個 import 失敗，現在已更新到驗證過可用的版本。
- 執行 `pytest`（`pytest.ini` 已設定 `testpaths = tests`）；測試都用 mock/monkeypatch 隔離外部依賴（Ollama client、apple_llm_bridge 的 subprocess、yt_dlp、cv2 等），不需要真的裝 Ollama、mlx-whisper 或 build 好 Swift 才能跑。

## 架構

**整條處理管線由 `utils/video_processor.py` 中的 `process_video()` 一手包辦**，由 `main.py` 的 `/process_video` route（背景執行緒）呼叫。理解這個函式就等於理解整個應用程式的核心：

1. 每次呼叫先用 `tempfile.mkdtemp()` 建立**獨立的工作目錄**（`work_dir`），所有下載/中間檔案都放在裡面——避免多支影片同時處理時互相覆寫固定檔名。結束時（無論成功或失敗）用 `shutil.rmtree` 整個刪除。
2. `yt-dlp` 將影片下載到 `work_dir/video.mp4`，並依 `config.SUBTITLE_LANGS`（預設 `['zh-TW', 'zh-Hant', 'en', 'ko', 'ja']`，此為優先順序）嘗試抓字幕檔 `work_dir/video.<lang>.vtt`——`writesubtitles` 和 `writeautomaticsub` 都開，同一個語言「有人工上傳字幕就用人工的，沒有才退回 YouTube 自動產生的字幕」，而不是完全跳過沒有人工字幕的語言、直接掉到後面更慢的語音轉錄。
3. **字幕優先策略**：若上述任一語言的字幕檔存在，就直接使用（`subtitle_used=True`），不進行聲音轉錄。否則透過 `ffmpeg` 擷取聲音，再用 `transcribe_audio()`（依 `config.TRANSCRIPTION_BACKEND` 分派，見下方「語音轉錄後端」）進行本地端轉錄，並整理成與字幕相同的 VTT 格式。**不論字幕來源是人工還是 YouTube 自動產生，讀進來後都會先過 `utils/vtt_cleaner.py:clean_vtt()`**：YouTube 自動字幕是「滾動式」格式（同一句話用逐字累加的內嵌標籤重複顯示好幾次），直接拿給後面的正則表達式解析器，時間戳記行格式對不上、句子也會重複，`clean_vtt()` 會去除重複、拆掉內嵌標籤、依句尾標點把過短的片段合併成完整句子（對已經乾淨的人工字幕是 no-op，不用另外判斷字幕來源）。
4. 語言處理邏輯集中在 `utils/vtt_translator.py`：
   - `detect_language()` 依 `config.TRANSLATION_BACKEND` 分派：`'apple'` 後端用 `apple_llm_bridge` 的 `NLLanguageRecognizer`（`NaturalLanguage` framework，統計式專用語言辨識器，非生成式 LLM，見下方說明）；`'ollama'` 後端或 apple 呼叫失敗時用 `langdetect`（+ Unicode 書寫系統判斷做前置修正）。兩條路徑都**不呼叫生成式 LLM**。
   - 若來源語言為中文 → `summarize()` 直接摘要，不翻譯。
   - 若來源語言非中文 → `process_vtt()` 呼叫 `translate_batch()` 把多句字幕（預設每批 20 句、用 `[編號]` 標記順序）一次送去翻譯，解析失敗才回退成逐句呼叫 `translate_text()`；翻譯完的文字再送 `summarize()` 摘要。
   - `summarize()` 回傳的 HTML 會先用 `bleach` 白名單清洗過才回傳（模板用 `| safe` 直接輸出，來源是不受信任的影片內容，要在這裡擋 XSS，不能指望模板層）。
   - 所有實際呼叫模型的地方都經過 `_generate(prompt)` 這個統一入口，見下方「翻譯／摘要後端」。

5. 截圖透過 OpenCV（`cv2.VideoCapture`）擷取，依 `capture_interval`（秒）與影片 FPS 計算出固定的 frame step，存入 `static/screenshots/`，之後由 `utils/image_processor.py:remove_duplicate_images` 以 phash + 漢明距離門檻進行感知去重複——此函式會直接刪除資料夾內的相似截圖檔案，是一個有副作用的操作，而不只是回傳過濾後的清單。
6. 所有資料（影片中繼資料、轉錄文字、翻譯文字、摘要、截圖清單以 JSON 儲存）透過 `database.py` 寫入 SQLite（`videos.db`，僅有一張 `videos` 資料表），以 `youtube_id` 為鍵值。

**`/process_video` 是非同步的**：route 只負責啟動一個背景執行緒（`main.py` 的 `_run_video_processing`）並立即回傳 `{'status': 'processing', 'job_id': ...}`（HTTP 202）；實際的下載/翻譯/摘要在背景跑，資料庫的新增/更新也在背景執行緒裡完成。前端（`static/js/app.js`）輪詢 `/job_status/<job_id>` 直到狀態變成 `done`/`error`。這是進程內的簡易佇列（`main.py` 的 `_jobs` dict + `_jobs_lock`），重啟 Flask process 會遺失所有工作紀錄，也不支援多 worker process 共享狀態。

**用於顯示頁面的截圖／字幕配對邏輯是獨立的**，位於 `main.py:process_video_data()`，只在 `/video/<youtube_id>` route 中被呼叫：它會重新以正則表達式解析資料庫中儲存的 `translation` 與 `transcription` VTT 文字、把時間戳轉換為秒數，並依相鄰兩張截圖的時間戳範圍將字幕句子分桶，讓影片詳情頁能把每張截圖對應到原文／翻譯字幕。

**資料模型**：只有一張 SQLite 資料表（`videos`），每個處理結果對應一列（重新處理相同 `youtube_id` 會執行 `UPDATE`，而非新增一列——儘管 `get_all_videos`／`search_videos` 中的 `GROUP BY youtube_id` / `MAX(processed_at)` 查詢語法看起來像是在對多筆歷史紀錄去重複，實際上每個影片本來就只會有一列）。`screenshots` 欄位以 JSON 字串儲存，每次讀取時都要反序列化。`database.py:get_connection()` 是唯一的連線建立入口（統一加 `timeout`）。

**前端**是傳統的 Jinja2 伺服端渲染（`templates/index.html`、`templates/video_screenshots.html`），搭配純 vanilla JS（`static/js/app.js`）透過 `fetch()` 呼叫 Flask 的 JSON 端點做漸進式增強——沒有前端 build 步驟或框架。

## 翻譯／摘要後端（`config.TRANSLATION_BACKEND`）

`utils/vtt_translator.py` 裡所有需要模型的地方（`translate_text`、`translate_batch`、`summarize`）都透過 `_generate(prompt) -> str` 這個統一入口，依 `config.TRANSLATION_BACKEND` 分派：

- **`'apple'`（預設）**：透過 `subprocess` 呼叫 `apple_llm_bridge/`（獨立的 Swift Package）。介面是一行 JSON stdin → 一行 JSON stdout，見 `apple_llm_bridge/Sources/apple-llm-bridge/main.swift` 開頭註解，支援兩種 `mode`：
  - `"generate"`（翻譯/摘要，預設）：跑地端 **Apple Intelligence（`FoundationModels` framework）**。
  - `"detect_language"`：跑 **`NaturalLanguage` framework 的 `NLLanguageRecognizer`**——統計式專用語言辨識器，不是生成式 LLM，沒有 guardrail、也不需要多打一次模型呼叫；對短句、簡繁中文的分辨也比 `langdetect`準（實測「歡迎收看今天的影片...」這種短句能以 99.99% 信心值判為 zh-Hant，`langdetect` 會誤判成韓文）。`detect_language()` 呼叫這個模式失敗時（binary 沒 build、逾時等）會退回 `langdetect`，不會讓整支影片處理中斷。
  - 只能在啟用 Apple Intelligence 的 Mac 上跑（`SystemLanguageModel.default.availability == .available`），需要先在 `apple_llm_bridge/` 目錄執行 `swift build -c release`（只需 Command Line Tools，不需要完整 Xcode.app；已實測可行）。
  - **`FoundationModels` 有內建、無法完全關閉的內容安全防護**：政治／社會爭議主題（例如貧富差距、民主議題這類新聞時事內容）即使只是「翻譯」也可能被拒絕生成——這是平台限制，不是 prompt 沒寫好。已實測會發生在真實的 YouTube 影片內容上，而且是機率性的（同一句話重跑不一定每次都會觸發，猜測跟取樣結果有關）。`main.swift` 用 `SystemLanguageModel(guardrails: .permissiveContentTransformations)`（語意：這是內容轉換／翻譯，不是生成全新內容，guardrail 明顯較寬鬆）取代 `.default`，是嚴格放寬、不會提高擋下率。有兩種拒絕形式都要處理：
    - **硬拒絕**：直接拋出 `guardrailViolation` exception → `reason: "guardrail_violation"`。
    - **軟拒絕**：即使放寬了 guardrails，模型有時還是不拋例外，而是用「客氣婉拒」的自然語言文字（例如 "I'm sorry, but I can't help with that."）取代真正的翻譯結果——`main.swift` 的 `looksLikeRefusal()` 檢查回應開頭是否命中已知拒絕語句樣式，命中則回報 `reason: "refusal_detected"`。這個判斷刻意只看**開頭**而非全文搜尋，降低對正常翻譯內容（影片對白本身可能包含「很抱歉」之類的詞）的誤判風險。
  - `_generate()` 對這兩種 reason 都有專門處理：**自動 fallback 到 Ollama 重試同一次呼叫**（`_APPLE_RETRYABLE_REASONS`），讓整支影片不會因為單一段落被擋下就整個處理失敗。其他失敗原因（`unavailable`／`process_error`／一般 `generation_failed`）不會 fallback，直接往上拋，因為這些通常代表需要人工處理的真正問題（Apple Intelligence 沒開、binary 沒 build、逾時等）。
  - 批次翻譯（`[編號]` 格式）在句數較多（實測 20 句一批）時，`FoundationModels` 遵守格式的穩定度不如小批次穩定，常觸發 `_parse_numbered_translation` 失敗、回退成逐句呼叫——這會顯著拉長處理時間，是已知的效能限制。
  - **重複迴圈退化**：`_generate()` 對每次生成結果都跑 `_looks_like_repetition_loop()` 檢查（同一行內容去除編號後連續出現 3 次以上視為異常）。實際踩過的案例：翻譯一個很短的原文片段（`to "all are created equal"`）時，模型生成了 50 幾行「我們應該努力消除不平等的機會」的重複內容，跟原文完全無關，格式也不符合 `[n]` 批次格式所以逃過了批次的解析檢查，直接被存進資料庫、顯示在頁面上。偵測到退化時，`_generate()` 一樣走 `_APPLE_RETRYABLE_REASONS` 的 fallback 邏輯換 Ollama 重試；`translate_text()` 在換過後端仍然退化時會優雅降級成保留原文（而不是讓整支影片處理中斷），`translate_batch()` 則把整批退化視同格式解析失敗、回退成逐句翻譯。這個檢查對兩個後端都生效，不是 Apple 專屬（Ollama 的小模型理論上也可能有同樣的失敗模式）。
  - **`exceededContextWindowSize`（4096 token 上限）**：實測發現這個錯誤不是單純「輸入太長」——同樣長度的輸入，重跑不一定會觸發，猜測是 context window 算的是輸入加輸出的總和，模型偶爾生成異常冗長的回應（跟重複迴圈退化是同一類問題）就會把額度吃光。三層因應：(1) `main.swift` 呼叫 `session.respond(to:options:)` 時帶 `GenerationOptions(maximumResponseTokens: 1024)`，直接限制單次回應長度上限；(2) `summarize()` 用 `_SUMMARY_CHUNK_CHAR_LIMIT`（1800 字元）先把長逐字稿切成多段，超過門檻就先個別摘要重點、再合併段落摘要做最終摘要（map-reduce，遞迴處理合併後還是太長的情況）；(3) `translate_batch()` 的分批邏輯（`_build_translation_batches`）除了原本「最多 20 句」的限制，也加了 `_TRANSLATION_BATCH_CHAR_LIMIT`（1200 字元）——YouTube 自動字幕合併成完整句子後單句可能較長，只看句數不夠。這三個門檻都是實測調出來、不是精確的 token 計算，未來如果又踩到這個錯誤，代表門檻還要再調低。
- **`'ollama'`**：走 `openai` client 打 Ollama 的 OpenAI 相容端點（`config.OLLAMA_BASE_URL`，預設 `http://localhost:11434/v1/`），模型是 `config.OLLAMA_MODEL_NAME`（預設 `gemma2:9b`，也可用 `utils/vtt_translator.py` 的 `set_model()` 在執行期切換）。保留這條路徑是為了非 Mac 主機部署，或想切換/比較其他地端或雲端模型。

`GenerationError`（`utils/vtt_translator.py`）是兩個後端共用的例外類型，帶 `.reason` 屬性方便呼叫端判斷失敗種類，不用理會底層是哪個後端在報錯。

## 語音轉錄後端（`config.TRANSCRIPTION_BACKEND`）

只有**沒有字幕、需要轉錄**時才會用到，`utils/video_processor.py:transcribe_audio()` 是統一入口：

- **`'apple'`（預設）**：透過 `apple_llm_bridge` 的 `mode: "transcribe"` 呼叫 **macOS 26 新的 `Speech`/`SpeechAnalyzer` + `SpeechTranscriber` API**（跟舊版 `SFSpeechRecognizer` 是不同東西）。純語音辨識、不是生成式模型，沒有 guardrail 疑慮；已實測正確涵蓋這個 app 需要的所有語言（`zh-TW`/`zh-CN`/`en-*`/`ja-JP`/`ko-KR`，`SpeechTranscriber.supportedLocales` 實際支援更多），對乾淨語音（TTS）幾乎完全正確，對真實世界音訊（背景音、口語含糊）也可用、跟 whisper 一樣會有偶發辨識誤差。回傳的是逐句（`isFinal` 結果）加逐字時間戳記，比 whisper 的 segment 級別更細；`AVAudioFile` 直接吃 `extract_audio()` 產生的 16kHz mono mp3，不需要額外轉檔。
  - `SpeechTranscriber(locale:)` 需要先指定語言，不像 whisper 會自動偵測音訊語言——`utils/video_processor.py:pick_speech_locale()` 借用既有的 `detect_language()`（餵影片標題+簡介）先猜一次語言，猜不到或沒對應到支援清單就退回 `en-US`。
  - 對應語言的模型資源第一次使用時可能需要透過 `AssetInventory` 下載（實測常見語言通常已經隨系統安裝好）。
  - `transcribe_audio_with_apple_speech()` 失敗（locale 不支援、Speech 服務未啟用、binary 沒 build 等）時回傳 `None`，`transcribe_audio()` 會自動退回 `'mlx_whisper'` 後端重試——跟翻譯／摘要那邊 Apple 後端失敗會退回 Ollama 是同一種設計精神。
- **`'mlx_whisper'`**：走 `mlx_whisper.transcribe(...)`（`transcribe_audio_with_whisper()`）。保留這條路徑是為了非 Mac 主機部署，或 apple 後端失敗時的備援；會踩到上面提到的 `numpy`/`numba` 依賴問題。

## 在此程式庫中作業時的注意事項

- `static/screenshots/`（截圖資料夾結構透過 `.gitkeep` 保留）、`videos.db`、`videos.db-wal`/`videos.db-shm` 都已列入 `.gitignore`，不要假設範例資料已被提交進版本庫。**`videos.db` 是有真實內容的既有資料庫檔案，不是空白樣板，處理前務必先確認再覆寫/刪除。**
- `apple_llm_bridge/.build/` 是 Swift Package Manager 的 build 產物目錄，同樣不進版本庫；換一台機器或 clean checkout 後要記得重新 `swift build -c release`。
- 修改 `apple_llm_bridge/Sources/apple-llm-bridge/main.swift` 時：**不要用 `Task { ... } + DispatchSemaphore.wait()` 包裝非同步呼叫**——實測這個組合在主執行緒上會死鎖（`FoundationModels` 的非同步呼叫似乎需要用到主執行緒，而 `semaphore.wait()` 又把主執行緒同步卡住）。改用最上層程式碼直接 `await`（目前的寫法），沒有這個問題。
- Swift 的 `Decodable` 自動合成的 `init(from:)`**不會套用屬性預設值**——`var mode: String = "generate"` 這種寫法，JSON 沒給 `mode` 這個 key 就會直接解碼失敗，跟一般 Swift 建構子的預設值行為不同。要讓欄位可省略，得宣告成 `Optional`（`var mode: String?`），呼叫端自己在用到的地方 `?? "generate"`（見 `Request` struct）。
