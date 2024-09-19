# YouTube Video Screenshot-Transcription-Translation-Summary App

這是一個用於處理YouTube影片的Flask應用程序。它可以下載影片,生成截圖,提取聲音,進行轉錄和翻譯,並提供影片摘要。

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

4. 安裝Ollama (使用本地端AI模型)和 mlx-whisper（在 Apple M1/2/3/4處理器上進行聲音轉錄）:
   - 請按照[Ollama官方文件](https://github.com/jmorganca/ollama)的說明進行安裝。
   - 請參考[mlx-whispere官方文件](https://pypi.org/project/mlx-whisper/)

## 配置

1. 確保Ollama服務正在運行,並監聽在`http://localhost:11434`。

## 使用方法

1. 運行Flask應用:
   ```
   python main.py
   ```

2. 在瀏覽器中打開 `http://localhost:5001`

3. 輸入YouTube影片URL、設定截圖頻率，並點擊"Process Video"

4. 等待處理完成,結果將顯示在頁面上

## 程式結構

- `main.py`: Flask應用的主入口
- `video_processor.py`: 影片處理的核心邏輯
- `vtt_translator.py`: 字幕處理和翻譯功能
- `image_processor.py`: 圖像處理和去重複功能
- `database.py`: 資料庫操作
- `templates/`: HTML模板
- `static/`: 靜態文件 (CSS, JS, 截圖等)

## 技術堆疊

- Python
- Flask
- OpenCV
- yt-dlp
- mlx-whisper
- Ollama
- SQLite

## 注意事項

- 此應用僅用於教育和研究目的。請遵守YouTube的服務條款、並尊重智慧財產權。
- 確保您有足夠的磁盤空間來儲存下載的影片（暫存）和生成的截圖。
- 處理長影片可能需要較長時間,請耐心等待。

## 貢獻

歡迎提交問題和拉取請求來改進此項目。

## 許可證

本項目採用 MIT 許可證。詳情請見 [LICENSE](LICENSE) 文件。
