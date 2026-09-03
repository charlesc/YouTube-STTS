# YouTube Video Screenshot-Transcription-Translation-Summary App

這是一個用於處理YouTube影片的Flask應用程序。它可以下載影片,生成截圖,提取聲音,進行轉錄和翻譯,並提供影片摘要。
(透過 Replit Agent 製作原型程式，以及數百次與 Claude 的愉快交談@@)

## 功能

- 從YouTube URL下載影片
- 生成影片截圖
- 提取聲音並進行轉錄
- 翻譯影片內容 (支持多種語言)
- 生成影片摘要
- 儲存處理後的影片訊息到資料庫
- 提供Web界面顯示處理結果

以及：
偵測影片是否有字幕檔案，若有合適的字幕檔案：
1. 若有中文字幕檔案，不進行翻譯，直接進行摘要。
2. 若是英文字幕檔案，進行翻譯，再進行摘要。
3. 若是其他語言的字幕檔案，先翻譯為英文、再翻譯為中文，再進行摘要。

B. 若沒有合適的字幕檔案：
進行轉錄的動作，偵測轉錄內容的語文，然後依照上述做法進行翻譯和/或摘要。

## 展示
- [首頁](https://github.com/charlesc/YouTube-STTS/blob/main/demo/index.png)
- [影片頁](https://github.com/charlesc/YouTube-STTS/blob/main/demo/sample-1.jpg)
- [原文切換](https://github.com/charlesc/YouTube-STTS/blob/main/demo/sample-2.jpg)
- [光箱顯示](https://github.com/charlesc/YouTube-STTS/blob/main/demo/sample-3.jpg)


## 安裝

1. 複製此程式庫:
   ```
   git clone https://github.com/charlesc/YouTube-STTS.git
   cd YouTube-STTS
   ```

2. 安裝依賴:
   ```
   pip install -r requirements.txt
   ```

3. 安裝FFmpeg (用於聲音提取):
   - 在Ubuntu上: `sudo apt-get install ffmpeg`
   - 在macOS上 (使用Homebrew): `brew install ffmpeg`
   - 在Windows上: 下載FFmpeg並將其添加到系統PATH中

4. 安裝 mlx-whisper（在 Apple M1/2/3/4 處理器上進行聲音轉錄，僅在沒有字幕、需要轉錄時才會用到）:
   - 請參考[mlx-whisper 官方文件](https://pypi.org/project/mlx-whisper/)

5. 翻譯／摘要後端（擇一）:

   **A. Apple Intelligence（預設，`TRANSLATION_BACKEND=apple`）**——完全地端、免額外服務，但只能在有 Apple Intelligence 的 Mac（macOS 26+，系統設定裡「Apple Intelligence」已啟用）上跑：
   ```bash
   cd apple_llm_bridge
   swift build -c release   # 只需 Xcode Command Line Tools，不用裝完整 Xcode.app
   cd ..
   ```
   注意：`FoundationModels`（Apple Intelligence 用的框架）有內建、無法關閉的內容安全防護，翻譯政治／社會議題等敏感主題的內容時可能會被擋下（`guardrail_violation`）。程式遇到這種情況會自動改用 Ollama 重試，所以**建議即使主要用 Apple 後端，也把下面的 Ollama 設定跑起來當備援**。

   **B. Ollama（`TRANSLATION_BACKEND=ollama`，或 Apple 後端的備援）**：
   - 請按照[Ollama官方文件](https://github.com/jmorganca/ollama)的說明進行安裝。
   - 確保 Ollama 服務正在運行，並監聽在 `http://localhost:11434`（`ollama serve`）。
   - 拉取模型：`ollama pull gemma2:9b`（或透過 `OLLAMA_MODEL_NAME` 環境變數換成別的模型）。

## 配置

所有設定值集中在 `config.py`，都可以用環境變數覆寫，例如：

```bash
export TRANSLATION_BACKEND=apple      # 或 ollama
export FLASK_DEBUG=true               # 開發時才需要
export OLLAMA_MODEL_NAME=gemma2:9b
```

## 使用方法

1. 運行Flask應用:
   ```
   python main.py
   ```

2. 在瀏覽器中打開 `http://localhost:5001`

3. 輸入YouTube影片URL、設定截圖頻率，並點擊"Process Video"

4. 等待處理完成,結果將顯示在頁面上

## 程式結構

- `main.py`: Flask應用的主入口，`/process_video` 以背景執行緒處理、前端輪詢 `/job_status/<id>` 取得結果
- `config.py`: 集中管理所有環境相關設定（可用環境變數覆寫）
- `utils/video_processor.py`: 影片處理的核心邏輯
- `utils/vtt_translator.py`: 字幕處理和翻譯/摘要功能，依 `TRANSLATION_BACKEND` 分派到 Apple Intelligence 或 Ollama
- `utils/image_processor.py`: 圖像處理和去重複功能
- `database.py`: 資料庫操作
- `apple_llm_bridge/`: 獨立的 Swift Package，橋接地端 Apple Intelligence（`FoundationModels` framework）
- `templates/`: HTML模板
- `static/`: 靜態文件 (CSS, JS, 截圖等)
- `tests/`: pytest 測試（外部依賴皆用 mock 隔離）

## 技術堆疊

- Python / Flask
- OpenCV
- yt-dlp
- mlx-whisper（聲音轉錄）
- Apple Intelligence（`FoundationModels`，透過 `apple_llm_bridge/` 這個 Swift Package）／Ollama（可切換）
- SQLite

## 注意事項

- 此應用僅用於教育和研究目的。請遵守YouTube的服務條款、並尊重智慧財產權。
- 確保您有足夠的磁盤空間來儲存下載的影片（暫存）和生成的截圖。
- 處理長影片可能需要較長時間,請耐心等待。

## 貢獻

歡迎提交問題和拉取請求來改進此項目。

## 許可證

本項目採用 MIT 許可證。詳情請見 [LICENSE](LICENSE) 文件。
