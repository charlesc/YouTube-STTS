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

# mlx-whisper
WHISPER_MODEL_REPO = os.environ.get('WHISPER_MODEL_REPO', 'mlx-community/whisper-large-v3-mlx')

# 支援的字幕/語言優先順序
SUBTITLE_LANGS = ['zh-TW', 'zh-Hant', 'en', 'ko', 'ja']
