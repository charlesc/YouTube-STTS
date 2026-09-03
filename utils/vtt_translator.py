import json
import re
import logging
import subprocess
from collections import Counter
import bleach
from openai import OpenAI
from langdetect import detect as _detect_lang, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

import config

# langdetect 內部用隨機取樣做語言判斷，不設定 seed 的話同一段文字每次呼叫
# 可能得到不同結果；固定 seed 讓偵測結果可重現。
DetectorFactory.seed = 0

logger = logging.getLogger(__name__)

# 摘要要求模型輸出 HTML（例如 <ul><li>），但轉錄文字最終來自任何人上傳的 YouTube
# 影片語音/字幕、屬於不受信任的輸入。頁面以 `| safe` 直接輸出摘要 HTML，
# 因此在這裡先用白名單清洗，只保留摘要真正需要的排版標籤，避免 stored XSS。
_SUMMARY_ALLOWED_TAGS = ['ul', 'ol', 'li', 'p', 'br', 'strong', 'b', 'em', 'i', 'h3', 'h4', 'blockquote']


# 全局變量用於儲存模型名稱（gemma2:9b,llama3.1:latest），可用環境變數 OLLAMA_MODEL_NAME 覆寫預設值
MODEL_NAME = config.OLLAMA_MODEL_NAME

# 初始化 Ollama API 客戶端
client = OpenAI(base_url=config.OLLAMA_BASE_URL, api_key=config.OLLAMA_API_KEY)

# langdetect 回傳的 ISO 語言代碼 -> 人類可讀名稱（用於翻譯 prompt 及顯示）
_LANGUAGE_NAMES = {
    'zh-cn': 'Simplified Chinese',
    'zh-tw': 'Traditional Chinese',
    'en': 'English',
    'ko': 'Korean',
    'ja': 'Japanese',
}

# 批次翻譯時，每個請求最多包含幾句字幕（避免單一 prompt 過長、也避免逐句呼叫模型太慢）
_TRANSLATION_BATCH_SIZE = 20

# Unicode 分區，用來在統計式語言偵測之前，先用字元本身的書寫系統做判斷。
# langdetect 對中/韓文的短句（尤其標點符號較多、字數少）常誤判——實測同一批
# 日常中文短句有超過一半被誤判為韓文。中文、日文假名、韓文諺文在 Unicode 上
# 是不重疊的獨立分區，用這個判斷比統計模型更準也完全不會因輸入長度而飄動。
_HANGUL_RANGE = (0xAC00, 0xD7A3)
_HIRAGANA_RANGE = (0x3040, 0x309F)
_KATAKANA_RANGE = (0x30A0, 0x30FF)
_CJK_IDEOGRAPH_RANGE = (0x4E00, 0x9FFF)


def _contains_in_range(text, start, end):
    return any(start <= ord(ch) <= end for ch in text)


def _script_based_language_hint(text):
    """只處理「肉眼就能分辨」的情況：完全沒有諺文、也沒有假名，但含有中日
    共用的表意文字，判定為中文。其餘（含韓文諺文、含日文假名、或非 CJK）一律
    交給 langdetect 判斷，不強行覆蓋。"""
    if _contains_in_range(text, *_HANGUL_RANGE):
        return None
    if _contains_in_range(text, *_HIRAGANA_RANGE) or _contains_in_range(text, *_KATAKANA_RANGE):
        return None
    if _contains_in_range(text, *_CJK_IDEOGRAPH_RANGE):
        return 'Traditional Chinese'
    return None


def set_model(model_name):
    global MODEL_NAME
    MODEL_NAME = model_name
    logger.info(f"Model set to: {MODEL_NAME}")


class GenerationError(RuntimeError):
    """翻譯/摘要後端（Ollama 或 Apple LLM 橋接工具）呼叫失敗時統一拋出的例外。

    `reason` 帶著 apple_llm_bridge 回報的分類（見 main.swift）：
    'unavailable' / 'invalid_input' / 'generation_failed' / 'guardrail_violation'，
    或是 Python 這層自己判斷出的 'process_error'（binary 不存在、逾時、輸出無法解析）。
    """

    def __init__(self, message, reason=None):
        super().__init__(message)
        self.reason = reason


def _generate_via_ollama(prompt):
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content


def _call_apple_bridge(payload):
    """呼叫 apple_llm_bridge（Swift CLI）的底層邏輯：序列化請求、解析回應、
    把各種失敗情況（binary 不存在、逾時、輸出無法解析、回應狀態為 error）
    統一轉成 GenerationError，回傳原始的回應 dict 給呼叫端依模式取值。

    介面是一行 JSON stdin -> 一行 JSON stdout，詳見 apple_llm_bridge/Sources/
    apple-llm-bridge/main.swift 開頭的說明。
    """
    try:
        result = subprocess.run(
            [config.APPLE_LLM_BRIDGE_PATH],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=config.APPLE_LLM_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise GenerationError(
            f"找不到 apple_llm_bridge 執行檔（{config.APPLE_LLM_BRIDGE_PATH}）。"
            f"請先在 apple_llm_bridge/ 目錄執行 `swift build -c release`。",
            reason='process_error',
        )
    except subprocess.TimeoutExpired:
        raise GenerationError(
            f"apple_llm_bridge 逾時（超過 {config.APPLE_LLM_TIMEOUT_SECONDS} 秒）",
            reason='process_error',
        )

    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        raise GenerationError(
            f"apple_llm_bridge 回應無法解析為 JSON（returncode={result.returncode}）："
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
            reason='process_error',
        )

    if response.get('status') != 'ok':
        raise GenerationError(
            f"apple_llm_bridge 失敗（{response.get('reason')}）：{response.get('error')}",
            reason=response.get('reason'),
        )

    return response


def _generate_via_apple(prompt):
    response = _call_apple_bridge({"mode": "generate", "prompt": prompt})
    return response['content']


def _detect_language_via_apple(text):
    """用 NaturalLanguage 框架的 NLLanguageRecognizer 做語言偵測（見 main.swift
    的 detectLanguage()）。這是統計式的專用語言辨識器，不是生成式 LLM，
    沒有 guardrail、也不會有「拒答」的問題，對短句、簡繁中文的分辨也比
    langdetect 準（實測能把 langdetect 誤判為韓文的中文短句正確判為中文）。"""
    response = _call_apple_bridge({"mode": "detect_language", "text": text})
    return response['language']


# 這幾種失敗原因都代表「這一次生成的內容本身不能用」，適合自動改用 Ollama
# 重試同一個 prompt（而不是讓整支影片處理因為單一段落而整個失敗）：
# - guardrail_violation：FoundationModels 直接拋出例外拒絕生成。
# - refusal_detected：即使用了較寬鬆的 .permissiveContentTransformations，
#   模型有時還是不會拋例外，而是用「客氣拒絕」的自然語言文字回應取代真正的
#   翻譯結果（例如 "I'm sorry, but I can't help with that."）——main.swift 的
#   looksLikeRefusal() 會攔下這種情況並回報這個 reason。
# - degenerate_output：模型陷入重複迴圈退化（見 _looks_like_repetition_loop），
#   跟安全防護無關，是純粹的生成失敗，但同樣適合換一個後端重試。
# 其他 reason（逾時、binary 不存在、Apple Intelligence 未啟用等）屬於需要
# 使用者處理的真正問題，不做靜默 fallback。
_APPLE_RETRYABLE_REASONS = {'guardrail_violation', 'refusal_detected', 'degenerate_output'}

# 重複迴圈退化的偵測門檻：同一行內容（去除開頭編號後）連續出現達到這個次數
# 就視為異常。3 次已經是很保守的門檻——正常的翻譯/摘要輸出裡，同一句話
# 逐字重複 3 次以上幾乎不可能是刻意的，這通常代表模型卡進了重複輸出的迴圈。
_REPETITION_THRESHOLD = 3
_REPETITION_MIN_LINE_LENGTH = 6
_NUMBERED_LINE_PREFIX = re.compile(r'^(\[?\d+\]?[.\)、]?\s*)')


def _looks_like_repetition_loop(text):
    """偵測模型輸出是否退化成重複迴圈：同一行內容（去除開頭的 "12. " 或
    "[12]" 這類編號後）連續出現多次幾乎一模一樣的內容。

    實測踩過的真實案例：翻譯一句很短的原文片段（"to \"all are created
    equal\""）時，模型生成了「1. ... 2. ... 3. 我們應該努力消除不平等的機會。
    4. 我們應該努力消除不平等的機會。...」一路重複到 50 幾行才停——內容
    跟原文完全無關，格式也不符合 _parse_numbered_translation 要求的
    `[n]` 格式，所以批次翻譯的格式檢查攔不到，只有逐句翻譯（translate_text）
    這條路徑會直接把這種輸出原樣存進資料庫，沒有任何驗證。
    """
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    normalized = [_NUMBERED_LINE_PREFIX.sub('', line) for line in lines]
    normalized = [line for line in normalized if len(line) >= _REPETITION_MIN_LINE_LENGTH]
    if len(normalized) < _REPETITION_THRESHOLD:
        return False
    _, most_common_count = Counter(normalized).most_common(1)[0]
    return most_common_count >= _REPETITION_THRESHOLD


def _generate(prompt):
    """依 config.TRANSLATION_BACKEND 分派到 Ollama 或 Apple LLM 橋接工具。
    translate_text / translate_batch / summarize 都透過這裡呼叫模型，
    所以切換後端不需要更動這幾個函式內的邏輯。

    Apple 後端因內容安全防護拒絕處理，或任一後端輸出疑似重複迴圈退化時
    （見 _APPLE_RETRYABLE_REASONS），自動改用 Ollama 重試這一次呼叫。
    最終結果（不論來自哪個後端）都會再做一次退化檢查——如果換了後端還是
    退化，就讓例外往上拋，由呼叫端（例如 translate_text）決定如何優雅降級，
    而不是把明顯有問題的內容存進資料庫。
    """
    def _raise_if_degenerate(text):
        if _looks_like_repetition_loop(text):
            raise GenerationError(
                f"模型輸出疑似陷入重複迴圈退化（同一行內容重複 {_REPETITION_THRESHOLD} 次以上）："
                f"{text[:200]!r}...",
                reason='degenerate_output',
            )

    if config.TRANSLATION_BACKEND == 'apple':
        try:
            content = _generate_via_apple(prompt)
            # 退化檢查放在 try 區塊內：apple 後端本身「成功回應」但內容
            # 是重複迴圈退化的情況，要跟 guardrail/refusal 一樣被下面的
            # except 攔到、改用 Ollama 重試，而不是直接放行或直接報錯。
            _raise_if_degenerate(content)
        except GenerationError as e:
            if e.reason in _APPLE_RETRYABLE_REASONS:
                logger.warning(f"Apple Intelligence 產出的內容不可用（{e.reason}），改用 Ollama 重試：{e}")
                content = _generate_via_ollama(prompt)
            else:
                raise
    else:
        content = _generate_via_ollama(prompt)

    # 換了後端之後的最終結果也要驗證一次：如果 Ollama 的回應同樣退化
    # （或本來就是走 'ollama' 後端），這裡會攔下來，讓例外往上拋給呼叫端
    # 決定如何處理（例如 translate_text 會優雅降級成保留原文）。
    _raise_if_degenerate(content)

    return content


def _detect_language_via_langdetect(text):
    """用 langdetect 這種專用的語言偵測函式庫（搭配 Unicode 書寫系統作為前置
    判斷）做語言偵測，而非讓 LLM 自由回覆語言名稱再用子字串比對（`"chinese"
    in response`）判斷是否為中文——原本的作法只要模型回覆類似 "This is not
    Chinese" 的句子就會被誤判為中文而跳過翻譯，也額外多花一次模型呼叫。

    給 'ollama' 後端使用，也是 'apple' 後端呼叫 apple_llm_bridge 失敗時的
    備援（見 detect_language()）。text 需已確認非空字串。
    """
    hint = _script_based_language_hint(text)
    if hint:
        return hint

    try:
        code = _detect_lang(text)
    except LangDetectException:
        logger.warning("語言偵測失敗，文字可能過短或不含足夠特徵，視為 Unknown")
        return 'Unknown'
    return _LANGUAGE_NAMES.get(code, code)


def detect_language(text):
    """偵測文字語言，回傳給翻譯 prompt 使用的可讀語言名稱。

    'apple' 後端用 NaturalLanguage 框架的 NLLanguageRecognizer（見
    _detect_language_via_apple）：這是統計式的專用語言辨識器，不是生成式
    LLM，沒有 guardrail、也不需要多打一次模型呼叫；對短句、簡繁中文的分辨
    也比 langdetect 準。若 apple_llm_bridge 呼叫失敗（binary 沒 build、逾時
    等），視為非致命問題，退回 langdetect 而不是讓整支影片處理中斷——語言
    偵測不像翻譯/摘要那樣是使用者真正要的產出，沒必要為了這個失敗整支失敗。
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return 'Unknown'

    if config.TRANSLATION_BACKEND == 'apple':
        try:
            return _detect_language_via_apple(cleaned)
        except GenerationError as e:
            logger.warning(f"apple_llm_bridge 語言偵測失敗，改用 langdetect：{e}")

    return _detect_language_via_langdetect(cleaned)


def _is_chinese(language_label):
    label = (language_label or "").lower()
    return 'chinese' in label or 'taiwanese mandarin' in label


def translate_text(text, source_language, target_language):
    if source_language == target_language:
        return text

    prompt = f"請將以下 {source_language} 的內容翻譯為 {target_language}。請保持原文的段落結構，直接翻譯，不要做其他任何回覆或說明: {text}"
    try:
        return _generate(prompt)
    except GenerationError as e:
        if e.reason == 'degenerate_output':
            # 換過後端還是退化（極少見，通常代表這段原文本身很短/很怪、
            # 容易讓模型卡進重複迴圈），優雅降級成保留原文，好過整支影片
            # 處理直接失敗，或把明顯錯誤的重複內容存進資料庫。
            logger.warning(f"翻譯結果疑似模型退化重複輸出，保留原文：{e}")
            return text
        raise


def _parse_numbered_translation(content, expected_count):
    """解析批次翻譯回應中的 [編號] 前綴，確認句數與編號都對得上才採用。"""
    pattern = re.compile(r'\[(\d+)\]\s*(.*?)(?=\n\[\d+\]|\Z)', re.DOTALL)
    matches = pattern.findall(content)
    if len(matches) != expected_count:
        return None
    ordered = sorted(matches, key=lambda m: int(m[0]))
    if [int(n) for n, _ in ordered] != list(range(1, expected_count + 1)):
        return None
    return [text.strip() for _, text in ordered]


def translate_batch(texts, source_language, target_language, batch_size=_TRANSLATION_BATCH_SIZE):
    """將多句字幕分批一次送給模型翻譯，取代逐句各發一次請求。

    每個批次用 [編號] 標示句子順序，若模型回應的句數或編號對不上（解析失敗），
    該批次會回退為逐句翻譯，確保正確性優先於效能。
    """
    if source_language == target_language:
        return list(texts)

    translated = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        numbered = "\n".join(f"[{i + 1}] {text}" for i, text in enumerate(batch))
        prompt = (
            f"請將以下用 [編號] 標示的 {source_language} 字幕逐句翻譯為 {target_language}。"
            f"保留每一行前面的 [編號]，每個編號對應一行翻譯結果，"
            f"不要合併、拆分或增減行數，不要做其他任何回覆或說明:\n{numbered}"
        )
        try:
            content = _generate(prompt)
            parsed = _parse_numbered_translation(content, len(batch))
        except GenerationError as e:
            if e.reason != 'degenerate_output':
                raise
            # 換過後端整批還是退化，走跟「格式解析失敗」一樣的回退路徑：
            # 改成逐句翻譯，translate_text() 對單句的退化情況還有自己的
            # 優雅降級（保留原文），不會讓整支影片處理中斷。
            logger.warning(f"批次翻譯疑似模型退化重複輸出，改為逐句翻譯這一批（{len(batch)} 句）：{e}")
            parsed = None
        if parsed is None:
            logger.warning(f"批次翻譯結果無法解析，改為逐句翻譯這一批（{len(batch)} 句）")
            parsed = [translate_text(text, source_language, target_language) for text in batch]
        translated.extend(parsed)
    return translated


def process_vtt(vtt_content, source_language):
    # 分離 WEBVTT 標頭和內容
    parts = vtt_content.split('\n\n', 1)
    header = parts[0]
    content = parts[1] if len(parts) > 1 else ""

    # 使用正則表達式來分離時間戳和文本內容
    pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3})\n((?:(?!\n\d{2}:\d{2}:\d{2}\.\d{3}).)+)'
    matches = re.findall(pattern, content, re.DOTALL)

    # 检查是否为中文
    if _is_chinese(source_language):
        # 如果是中文，直接使用原文
        translated_vtt = vtt_content
        all_text = " ".join(text.strip() for _, text in matches)
    else:
        # 非中文時才進行批次翻譯
        texts = [text.strip() for _, text in matches]
        translated_texts = translate_batch(texts, source_language, "Traditional Chinese")

        translated_vtt = header + "\n\n"
        all_text_parts = []
        for (timestamp, _), translated_text in zip(matches, translated_texts):
            translated_vtt += f"{timestamp}\n{translated_text}\n\n"
            all_text_parts.append(translated_text)
        all_text = " ".join(all_text_parts)

    return translated_vtt.strip(), all_text.strip()


def summarize(translated_text):
    """為已經是繁體中文的文字產生摘要，回傳的 HTML 已用白名單清洗過。

    只接受「已翻譯完成」的文字：呼叫端（`process_vtt` 的輸出）已經保證內容是
    繁體中文，這裡不會重新偵測語言或嘗試翻譯，避免重複呼叫模型。
    """
    prompt = f"""請根據以下影片轉錄文字稿生成一份簡潔的繁體中文摘要（直接回答，不要做其他說明或評論，並提供HTML格式的內容，例如<ul><li>）。摘要應包含以下內容:

            1. 影片的主要主題或目的
            2. 3-5個關鍵要點或主要論點
            3. 任何重要的結論或呼籲行動
            4. 總結全文的簡短段落

            請基於以下內容生成：：
            {translated_text}
            """
    raw_summary = _generate(prompt)
    return bleach.clean(raw_summary, tags=_SUMMARY_ALLOWED_TAGS, attributes={}, strip=True)


def translate_and_summarize(text):
    """保留給需要「從任意語言的原文」直接翻譯並摘要的呼叫端；
    若文字已經是繁體中文（例如 process_vtt 的輸出），請直接呼叫 summarize()。"""
    source_language = detect_language(text)

    if _is_chinese(source_language):
        translated_text = text
    else:
        translated_text = translate_text(text, source_language, "Traditional Chinese")

    return translated_text, summarize(translated_text)


def extract_text_from_vtt(vtt_content):
    lines = vtt_content.split('\n')
    text_lines = []
    for line in lines:
        if not line.strip() or '-->' in line or line.strip() == 'WEBVTT':
            continue
        text_lines.append(line.strip())
    return ' '.join(text_lines)
