"""集中管理環境相關設定，全部可用環境變數覆寫，方便切換開發/正式環境或不同機器上的模型設定。"""
import os


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ('1', 'true', 'yes', 'on')


# Flask
FLASK_HOST = os.environ.get('FLASK_HOST', '0.0.0.0')
FLASK_PORT = int(os.environ.get('FLASK_PORT', '5001'))
FLASK_DEBUG = _env_bool('FLASK_DEBUG', default=False)
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'static/screenshots')

# 截圖依影片原始解析度存檔，4K/2K 影片截圖會不必要地大（單張可能好幾 MB），
# 網頁上顯示也用不到這麼高的解析度。寬度超過這個門檻時等比例縮小再存檔；
# 影片本身比這個窄就不放大，維持原本大小。
SCREENSHOT_MAX_WIDTH = int(os.environ.get('SCREENSHOT_MAX_WIDTH', '1440'))

# SQLite
DATABASE_NAME = os.environ.get('DATABASE_NAME', 'videos.db')

# 翻譯／摘要後端：'apple'（地端 Apple Intelligence / FoundationModels，預設）或 'ollama'
# 'apple' 只能在有 Apple Intelligence 的 Mac 上跑；'ollama' 保留給非 Mac 主機部署，
# 或想換回／比較其他地端或雲端模型的情境。
TRANSLATION_BACKEND = os.environ.get('TRANSLATION_BACKEND', 'apple')

# Ollama（OpenAI 相容端點）——僅在 TRANSLATION_BACKEND=ollama 時使用
OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434/v1/')
OLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY', 'ollama')
OLLAMA_MODEL_NAME = os.environ.get('OLLAMA_MODEL_NAME', 'gemma2:9b')

# Apple LLM 橋接工具（apple_llm_bridge/）——僅在 TRANSLATION_BACKEND=apple 時使用
APPLE_LLM_BRIDGE_PATH = os.environ.get(
    'APPLE_LLM_BRIDGE_PATH',
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'apple_llm_bridge', '.build', 'release', 'apple-llm-bridge'
    )
)
APPLE_LLM_TIMEOUT_SECONDS = int(os.environ.get('APPLE_LLM_TIMEOUT_SECONDS', '120'))

# 語音轉錄後端：'apple'（地端 Speech / SpeechAnalyzer，預設）或 'mlx_whisper'
# 'apple' 只能在有 SpeechAnalyzer 的 Mac（macOS 26+）上跑，完全不需要 mlx-whisper
# 那條 numpy/numba 的 Python 依賴鏈；'mlx_whisper' 保留給非 Mac 主機部署，
# 或 apple 轉錄失敗時的備援（transcribe_audio() 會自動 fallback）。
TRANSCRIPTION_BACKEND = os.environ.get('TRANSCRIPTION_BACKEND', 'apple')

# mlx-whisper——僅在 TRANSCRIPTION_BACKEND=mlx_whisper，或 apple 轉錄失敗需要備援時使用
WHISPER_MODEL_REPO = os.environ.get('WHISPER_MODEL_REPO', 'mlx-community/whisper-large-v3-mlx')

# 支援的字幕/語言優先順序
SUBTITLE_LANGS = ['zh-TW', 'zh-Hant', 'en', 'ko', 'ja']
