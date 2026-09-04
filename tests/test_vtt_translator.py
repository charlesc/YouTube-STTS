import json
import types

import utils.vtt_translator as vt


def _use_ollama_backend(monkeypatch):
    """這些測試針對的是走 Ollama（openai client）的呼叫邏輯，明確切到這個
    後端再 mock `vt.client.chat.completions.create`——config.TRANSLATION_BACKEND
    預設是 'apple'，不切換的話這些 mock 根本不會被呼叫到。"""
    monkeypatch.setattr(vt.config, "TRANSLATION_BACKEND", "ollama")


def test_extract_text_from_vtt_strips_headers_and_timestamps():
    vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.000\nHello\n\n"
        "00:00:02.000 --> 00:00:04.000\nWorld\n\n"
    )
    assert vt.extract_text_from_vtt(vtt) == "Hello World"


def test_process_vtt_chinese_source_skips_translation_call(monkeypatch):
    """中文來源不應該呼叫翻譯 API，直接沿用原文。"""
    _use_ollama_backend(monkeypatch)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("中文來源不應該呼叫翻譯 API")

    monkeypatch.setattr(vt.client.chat.completions, "create", fail_if_called)

    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n你好世界\n\n"
    translated_vtt, all_text = vt.process_vtt(vtt, "Traditional Chinese")

    assert translated_vtt == vtt.strip()
    assert "你好世界" in all_text


def test_process_vtt_non_chinese_source_translates_each_cue(monkeypatch):
    _use_ollama_backend(monkeypatch)

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    def fake_create(model, messages):
        assert "Hello" in messages[0]["content"]
        return FakeResponse("翻譯結果")

    monkeypatch.setattr(vt.client.chat.completions, "create", fake_create)

    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n\n"
    translated_vtt, all_text = vt.process_vtt(vtt, "English")

    assert "翻譯結果" in translated_vtt
    assert "00:00:00.000 --> 00:00:02.000" in translated_vtt
    assert all_text == "翻譯結果"


def test_process_vtt_updates_language_line_in_header(monkeypatch):
    """迴歸測試：翻譯後的 VTT 檔頭原本沿用原文字幕的 header，內容已經翻成
    繁體中文，Language 那行卻還寫著原文的語言代碼（例如英文字幕翻完還是
    `Language: en`）。"""
    _use_ollama_backend(monkeypatch)

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(
        vt.client.chat.completions, "create",
        lambda model, messages: FakeResponse("翻譯結果"),
    )

    vtt = "WEBVTT\nKind: captions\nLanguage: en\n\n00:00:00.000 --> 00:00:02.000\nHello\n\n"
    translated_vtt, _ = vt.process_vtt(vtt, "English")

    assert "Language: zh-Hant" in translated_vtt
    assert "Language: en" not in translated_vtt
    assert "Kind: captions" in translated_vtt


def test_process_vtt_leaves_header_without_language_line_untouched(monkeypatch):
    """本地語音轉錄產生的檔頭只有單獨一行 WEBVTT，沒有 Language 行——這種
    情況不該被硬加一行進去。"""
    _use_ollama_backend(monkeypatch)

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(
        vt.client.chat.completions, "create",
        lambda model, messages: FakeResponse("翻譯結果"),
    )

    vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello\n\n"
    translated_vtt, _ = vt.process_vtt(vtt, "English")

    assert "Language:" not in translated_vtt


def test_translate_text_short_circuits_when_languages_match(monkeypatch):
    _use_ollama_backend(monkeypatch)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("來源與目標語言相同時不應呼叫翻譯 API")

    monkeypatch.setattr(vt.client.chat.completions, "create", fail_if_called)

    assert vt.translate_text("hello", "English", "English") == "hello"


def test_detect_language_via_langdetect_uses_langdetect_not_llm_free_text(monkeypatch):
    """迴歸測試：detect_language 過去讓 LLM 自由回覆語言名稱，再用
    `"chinese" in response` 子字串比對，只要回覆含有 "not Chinese" 之類的
    句子就會誤判。現在改用 langdetect，且完全不應呼叫模型。"""
    def fail_if_called(*args, **kwargs):
        raise AssertionError("detect_language 不應該再呼叫 LLM")

    monkeypatch.setattr(vt.client.chat.completions, "create", fail_if_called)

    assert vt._detect_language_via_langdetect("這是一段繁體中文測試文字，用來確認語言偵測功能是否正常運作。") == "Traditional Chinese"
    assert vt._detect_language_via_langdetect("This is an English sentence used to verify language detection.") == "English"


def test_detect_language_via_langdetect_short_chinese_sentences_are_not_misread_as_korean(monkeypatch):
    """迴歸測試：langdetect 對中文短句（標點多、字數少）在實測中經常誤判為
    韓文；Unicode 書寫系統判斷應該先攔下這類含中日共用表意文字、
    但不含諺文/假名的文字，直接判為中文。"""
    monkeypatch.setattr(vt.client.chat.completions, "create",
                         lambda *a, **k: (_ for _ in ()).throw(AssertionError("不應呼叫 LLM")))

    short_chinese_sentences = [
        "這是一段繁體中文測試文字，用來確認語言偵測功能是否正常運作。",
        "歡迎收看今天的影片，我們將會介紹如何使用這個工具。",
        "謝謝大家的收看，我們下次影片見。",
        "這個功能非常實用，可以幫助你節省很多時間。",
    ]
    for sentence in short_chinese_sentences:
        assert vt._detect_language_via_langdetect(sentence) == "Traditional Chinese"


def test_detect_language_via_langdetect_recognizes_korean_and_japanese():
    assert vt._detect_language_via_langdetect("안녕하세요 이것은 한국어 테스트 문장입니다") == "Korean"
    assert vt._detect_language_via_langdetect("こんにちは、これは日本語のテスト文です") == "Japanese"


def test_detect_language_handles_empty_or_undetectable_text():
    # 空字串在 detect_language() 一開始就短路回傳，不會呼叫任何後端。
    assert vt.detect_language("") == "Unknown"
    assert vt.detect_language("   ") == "Unknown"


def test_detect_language_uses_apple_backend_by_default(monkeypatch):
    """config.TRANSLATION_BACKEND 預設是 'apple'，detect_language() 應該呼叫
    apple_llm_bridge 的 detect_language 模式（NLLanguageRecognizer），
    而不是 langdetect。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    captured = {}

    def fake_run(args, input, capture_output, text, encoding, timeout):
        captured['input'] = json.loads(input)
        return _FakeCompletedProcess(stdout=json.dumps({"status": "ok", "language": "Traditional Chinese"}))

    def fail_if_called(*a, **k):
        raise AssertionError("apple 後端可用時不應該退回 langdetect")

    monkeypatch.setattr(vt.subprocess, "run", fake_run)
    monkeypatch.setattr(vt, "_detect_language_via_langdetect", fail_if_called)

    assert vt.detect_language("你好世界") == "Traditional Chinese"
    assert captured['input'] == {"mode": "detect_language", "text": "你好世界"}


def test_detect_language_falls_back_to_langdetect_when_apple_bridge_fails(monkeypatch):
    """apple_llm_bridge 呼叫失敗（binary 沒 build、逾時等）時，語言偵測不是
    使用者真正要的產出，不該讓整支影片處理中斷——應該退回 langdetect。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    def fake_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(vt.subprocess, "run", fake_run)

    assert vt.detect_language("這是一段繁體中文測試文字。") == "Traditional Chinese"


def test_build_translation_batches_respects_count_limit():
    texts = [f"句子{i}" for i in range(25)]
    batches = vt._build_translation_batches(texts, batch_size=20, char_limit=100000)
    assert [len(b) for b in batches] == [20, 5]


def test_build_translation_batches_respects_char_limit():
    """迴歸測試：YouTube 自動字幕合併成完整句子後，單句可能很長，
    20 句一批的固定數量已經不夠，會讓 prompt 超出模型的 context window
    （實測踩過 exceededContextWindowSize）。字元數上限要能在句數上限之前
    先切下一批。"""
    long_sentence = "字" * 60  # 5 句就會超過 char_limit=100
    texts = [long_sentence] * 10

    batches = vt._build_translation_batches(texts, batch_size=20, char_limit=100)

    assert all(len(b) <= 2 for b in batches)  # 每批最多兩句（2*60=120 但第三句會超過 100）
    assert sum(len(b) for b in batches) == 10


def test_build_translation_batches_keeps_oversized_single_item_alone():
    # 單一句子本身就超過 char_limit 時，不該卡在空批次的迴圈裡出不來。
    texts = ["A" * 200, "short"]
    batches = vt._build_translation_batches(texts, batch_size=20, char_limit=100)
    assert batches == [["A" * 200], ["short"]]


def test_translate_batch_numbers_cues_and_preserves_order(monkeypatch):
    _use_ollama_backend(monkeypatch)

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    def fake_create(model, messages):
        # 模擬模型依照 [編號] 格式逐句回覆翻譯結果
        return FakeResponse("[1] 你好\n[2] 世界")

    monkeypatch.setattr(vt.client.chat.completions, "create", fake_create)

    result = vt.translate_batch(["Hello", "World"], "English", "Traditional Chinese")
    assert result == ["你好", "世界"]


def test_translate_batch_reports_progress_per_batch(monkeypatch):
    _use_ollama_backend(monkeypatch)

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(vt.client.chat.completions, "create",
                         lambda model, messages: FakeResponse("[1] 你好"))

    # 每句都超過 _TRANSLATION_BATCH_CHAR_LIMIT 的一半，逼出兩個批次。
    long_sentence = "A" * (vt._TRANSLATION_BATCH_CHAR_LIMIT // 2 + 10)
    progress_messages = []

    vt.translate_batch(
        [long_sentence, long_sentence], "English", "Traditional Chinese",
        on_progress=progress_messages.append,
    )

    assert progress_messages == [
        "正在翻譯字幕（第 1/2 批）...",
        "正在翻譯字幕（第 2/2 批）...",
    ]


def test_translate_batch_falls_back_to_per_cue_when_response_unparseable(monkeypatch):
    _use_ollama_backend(monkeypatch)

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    call_count = {"n": 0}

    def fake_create(model, messages):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # 第一次（批次）呼叫回傳格式不對（缺編號），應觸發回退邏輯
            return FakeResponse("這不是預期的格式")
        return FakeResponse("逐句翻譯結果")

    monkeypatch.setattr(vt.client.chat.completions, "create", fake_create)

    result = vt.translate_batch(["Hello", "World"], "English", "Traditional Chinese")
    assert result == ["逐句翻譯結果", "逐句翻譯結果"]
    assert call_count["n"] == 3  # 1 次批次嘗試 + 2 次逐句回退


class _FakeCompletedProcess:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_generate_dispatches_to_apple_backend_by_default(monkeypatch):
    """config.TRANSLATION_BACKEND 預設是 'apple'，_generate() 應該呼叫
    apple_llm_bridge，而不是 Ollama 的 openai client。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    captured = {}

    def fake_run(args, input, capture_output, text, encoding, timeout):
        captured['args'] = args
        captured['input'] = input
        return _FakeCompletedProcess(stdout=json.dumps({"status": "ok", "content": "地端翻譯結果"}))

    monkeypatch.setattr(vt.subprocess, "run", fake_run)

    result = vt._generate("翻譯這句話")

    assert result == "地端翻譯結果"
    assert captured['args'] == [vt.config.APPLE_LLM_BRIDGE_PATH]
    assert json.loads(captured['input']) == {"mode": "generate", "prompt": "翻譯這句話"}


def test_generate_via_apple_raises_when_bridge_reports_error(monkeypatch):
    monkeypatch.setattr(
        vt.subprocess, "run",
        lambda *a, **k: _FakeCompletedProcess(
            stdout=json.dumps({"status": "error", "reason": "unavailable", "error": "Apple Intelligence 未啟用"})
        )
    )

    try:
        vt._generate_via_apple("摘要這段文字")
        assert False, "應該要拋出 GenerationError"
    except vt.GenerationError as e:
        assert "Apple Intelligence 未啟用" in str(e)


def test_generate_via_apple_raises_when_binary_missing(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(vt.subprocess, "run", fake_run)

    try:
        vt._generate_via_apple("翻譯這句話")
        assert False, "應該要拋出 GenerationError"
    except vt.GenerationError as e:
        assert "swift build" in str(e)


def test_generate_falls_back_to_ollama_on_guardrail_violation(monkeypatch):
    """迴歸測試：Apple Intelligence 的內容安全防護（無法關閉）擋下請求時，
    _generate() 應該自動改用 Ollama 重試這一次呼叫，而不是讓整支影片處理失敗。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(stdout=json.dumps({
            "status": "error",
            "reason": "guardrail_violation",
            "error": "Response may contain sensitive or unsafe content",
        }))

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(vt.subprocess, "run", fake_run)
    monkeypatch.setattr(vt.client.chat.completions, "create",
                         lambda model, messages: FakeResponse("Ollama 救援翻譯結果"))

    assert vt._generate("翻譯這段敏感內容") == "Ollama 救援翻譯結果"


def test_generate_falls_back_to_ollama_on_soft_refusal(monkeypatch):
    """迴歸測試：即使用了較寬鬆的 guardrails，FoundationModels 有時仍會用
    「客氣拒絕」的自然語言文字回應取代真正的翻譯結果，而不拋出例外
    （main.swift 的 looksLikeRefusal 會攔下並回報 reason='refusal_detected'）。
    這種情況也應該跟 guardrail_violation 一樣自動改用 Ollama 重試。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(stdout=json.dumps({
            "status": "error",
            "reason": "refusal_detected",
            "error": "模型以自然語言婉拒回應",
        }))

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(vt.subprocess, "run", fake_run)
    monkeypatch.setattr(vt.client.chat.completions, "create",
                         lambda model, messages: FakeResponse("Ollama 救援翻譯結果"))

    assert vt._generate("翻譯這段敏感內容") == "Ollama 救援翻譯結果"


def test_generate_does_not_fall_back_on_other_apple_errors(monkeypatch):
    """非 guardrail 的失敗（逾時、binary 不存在等）是真正需要處理的問題，
    不應該被靜默吞掉、改走 Ollama——應該直接往上拋讓呼叫端知道。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(stdout=json.dumps({
            "status": "error",
            "reason": "unavailable",
            "error": "Apple Intelligence 未啟用",
        }))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("非 guardrail 錯誤不應該 fallback 到 Ollama")

    monkeypatch.setattr(vt.subprocess, "run", fake_run)
    monkeypatch.setattr(vt.client.chat.completions, "create", fail_if_called)

    try:
        vt._generate("翻譯這句話")
        assert False, "應該要拋出 GenerationError"
    except vt.GenerationError as e:
        assert e.reason == "unavailable"


def test_generate_via_apple_raises_on_timeout(monkeypatch):
    import subprocess as real_subprocess

    def fake_run(*args, **kwargs):
        raise real_subprocess.TimeoutExpired(cmd="apple-llm-bridge", timeout=kwargs.get("timeout", 120))

    monkeypatch.setattr(vt.subprocess, "run", fake_run)

    try:
        vt._generate_via_apple("翻譯這句話")
        assert False, "應該要拋出 GenerationError"
    except vt.GenerationError as e:
        assert "逾時" in str(e)


# --- 重複迴圈退化偵測 ---
#
# 真實案例：翻譯原文片段 `to "all are created equal"`（很短、像斷句）時，
# FoundationModels 生成了「1. ... 2. ... 3. 我們應該努力消除不平等的機會。
# 4. 我們應該努力消除不平等的機會。...」一路重複到 50 幾行，內容跟原文完全
# 無關，直接被存進資料庫、顯示在影片詳情頁上（使用者截圖回報「這個段落有點
# 奇怪」）。格式不符合 _parse_numbered_translation 要求的 `[n]` 樣式，所以
# 批次翻譯的格式檢查攔不住；問題出在 translate_text()（逐句翻譯／批次解析
# 失敗後的回退路徑）完全沒有驗證模型輸出品質。

_REAL_DEGENERATE_OUTPUT = (
    "1.  所有人都平等地誕生。\n"
    "2.  在這個世界上，每個人都有權利享有平等的機會。\n"
    "3.  平等的機會不是給予的，而是獲得的。\n"
    "4.  我們應該對平等的機會和機會的不平等感到憤怒。\n"
    "5.  我們應該努力消除不平等的機會。\n"
    + "\n".join(f"{i}. 我們應該努力消除不平等的機會。" for i in range(6, 51))
)


def test_looks_like_repetition_loop_detects_real_degenerate_output():
    assert vt._looks_like_repetition_loop(_REAL_DEGENERATE_OUTPUT) is True


def test_looks_like_repetition_loop_ignores_normal_translation():
    normal = (
        "歡迎回到這個頻道。\n"
        "今天我們要討論大型語言模型實際上是如何運作的。\n"
        "我們會介紹 transformer 架構跟注意力機制。\n"
        "感謝收看，下次再見。"
    )
    assert vt._looks_like_repetition_loop(normal) is False


def test_looks_like_repetition_loop_ignores_short_repeated_boilerplate():
    # 短行（例如摘要裡重複的小標題字樣）不該被當成退化，避免誤判。
    short_repeats = "\n".join(["小結", "小結", "小結", "小結"])
    assert vt._looks_like_repetition_loop(short_repeats) is False


def test_generate_falls_back_to_ollama_when_apple_output_is_degenerate(monkeypatch):
    """apple 後端沒有拋例外（回應狀態是 'ok'），但內容本身是重複迴圈退化——
    _generate() 應該偵測出來、視為跟 guardrail 一樣的情況，改用 Ollama 重試。"""
    assert vt.config.TRANSLATION_BACKEND == "apple"

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(stdout=json.dumps({"status": "ok", "content": _REAL_DEGENERATE_OUTPUT}))

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(vt.subprocess, "run", fake_run)
    monkeypatch.setattr(vt.client.chat.completions, "create",
                         lambda model, messages: FakeResponse("to \"all are created equal\" 的正常翻譯"))

    assert vt._generate("翻譯這段話") == 'to "all are created equal" 的正常翻譯'


def test_generate_raises_when_both_backends_produce_degenerate_output(monkeypatch):
    assert vt.config.TRANSLATION_BACKEND == "apple"

    def fake_run(*args, **kwargs):
        return _FakeCompletedProcess(stdout=json.dumps({"status": "ok", "content": _REAL_DEGENERATE_OUTPUT}))

    class FakeChoice:
        def __init__(self, content):
            self.message = types.SimpleNamespace(content=content)

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    monkeypatch.setattr(vt.subprocess, "run", fake_run)
    monkeypatch.setattr(vt.client.chat.completions, "create",
                         lambda model, messages: FakeResponse(_REAL_DEGENERATE_OUTPUT))

    try:
        vt._generate("翻譯這段話")
        assert False, "兩個後端都退化時應該要拋出 GenerationError"
    except vt.GenerationError as e:
        assert e.reason == "degenerate_output"


def test_translate_text_gracefully_degrades_to_original_on_degenerate_output(monkeypatch):
    """迴歸測試：換過後端還是退化時，translate_text() 不該讓整支影片處理
    崩潰，也不該把重複的垃圾內容存進資料庫——保留原文是相對安全的選擇。"""
    def fake_generate(prompt):
        raise vt.GenerationError("疑似退化", reason='degenerate_output')

    monkeypatch.setattr(vt, "_generate", fake_generate)

    result = vt.translate_text('to "all are created equal"', "English", "Traditional Chinese")
    assert result == 'to "all are created equal"'


def test_translate_batch_falls_back_to_per_cue_when_batch_output_is_degenerate(monkeypatch):
    """批次翻譯整批的生成結果退化時，應該跟「格式解析失敗」一樣回退成
    逐句翻譯，而不是把退化內容原樣塞進結果或讓整支影片處理中斷。"""
    call_count = {"n": 0}

    def fake_generate(prompt):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise vt.GenerationError("疑似退化", reason='degenerate_output')
        return "逐句翻譯結果"

    monkeypatch.setattr(vt, "_generate", fake_generate)

    result = vt.translate_batch(["Hello", "World"], "English", "Traditional Chinese")
    assert result == ["逐句翻譯結果", "逐句翻譯結果"]
    assert call_count["n"] == 3  # 1 次批次嘗試（退化）+ 2 次逐句回退


# --- summarize() 分段摘要 ---
#
# 真實案例：一支 37 分鐘影片的完整逐字稿，加上摘要 prompt 一起送給
# FoundationModels，總共 4089 token，超過它 4096 token 的 context window，
# 直接失敗（exceededContextWindowSize）。改用分段摘要（map-reduce）解決。

def test_split_into_chunks_keeps_short_text_as_single_chunk():
    short_text = "這是一段很短的文字。"
    assert vt._split_into_chunks(short_text) == [short_text]


def test_split_into_chunks_splits_long_text_on_sentence_boundaries():
    sentence = "這是一句大約十個字的句子。"
    long_text = sentence * 300  # 遠超過 _SUMMARY_CHUNK_CHAR_LIMIT

    chunks = vt._split_into_chunks(long_text)

    assert len(chunks) > 1
    assert all(len(c) <= vt._SUMMARY_CHUNK_CHAR_LIMIT for c in chunks)
    # 每個區塊都應該在句尾標點結束，不應該把句子從中間切開
    assert all(c.endswith('。') for c in chunks)
    assert ''.join(chunks) == long_text


def test_summarize_uses_single_call_for_short_text(monkeypatch):
    calls = []

    def fake_generate(prompt):
        calls.append(prompt)
        return "<ul><li>摘要</li></ul>"

    monkeypatch.setattr(vt, "_generate", fake_generate)

    result = vt.summarize("一段不長的逐字稿內容。")

    assert len(calls) == 1
    assert "摘要" in result


def test_summarize_uses_map_reduce_for_long_text(monkeypatch):
    """迴歸測試：逐字稿超過長度門檻時，應該先分段各自摘要重點，
    再把段落摘要合併起來做最終摘要，而不是把整段長文字一次送進模型。"""
    sentence = "這是一句大約十個字的句子。"
    long_text = sentence * 300

    calls = []

    def fake_generate(prompt):
        calls.append(prompt)
        if "以一段 2 到 3 句話的簡短段落" in prompt:
            return "- 段落重點"
        return "<ul><li>最終摘要</li></ul>"

    monkeypatch.setattr(vt, "_generate", fake_generate)

    result = vt.summarize(long_text)

    expected_chunk_count = len(vt._split_into_chunks(long_text))
    assert expected_chunk_count > 1
    # 每段各呼叫一次做段落摘要，加上最後合併的那一次
    assert len(calls) == expected_chunk_count + 1
    # 送進模型的每個 prompt 都不該包含完整的原始長文字
    assert all(len(p) < len(long_text) for p in calls)
    assert "最終摘要" in result


def test_summarize_reports_progress(monkeypatch):
    monkeypatch.setattr(vt, "_generate", lambda prompt: "<ul><li>摘要</li></ul>")

    progress_messages = []
    vt.summarize("一段不長的逐字稿內容。", on_progress=progress_messages.append)

    assert progress_messages == ["正在產生摘要..."]


def test_summarize_reports_progress_for_map_reduce(monkeypatch):
    sentence = "這是一句大約十個字的句子。"
    long_text = sentence * 300

    def fake_generate(prompt):
        if "以一段 2 到 3 句話的簡短段落" in prompt:
            return "- 段落重點"
        return "<ul><li>最終摘要</li></ul>"

    monkeypatch.setattr(vt, "_generate", fake_generate)

    progress_messages = []
    vt.summarize(long_text, on_progress=progress_messages.append)

    chunk_count = len(vt._split_into_chunks(long_text))
    assert progress_messages[:-1] == [
        f"逐字稿較長，正在分段摘要（第 {i + 1}/{chunk_count} 段）..." for i in range(chunk_count)
    ]
    assert progress_messages[-1] == "正在合併分段摘要..."


# --- 摘要品質：巢狀結構、"重點"數量上限 ---
#
# 真實案例：原本的 prompt 只說「用 <ul><li> 格式」，沒有規範巢狀結構，
# 模型會把「主題/重點/結論/總結」四個部分全部攤平成同一層的 <li>；分段
# 摘要合併時更明顯——模型常常把每段落各自的 3-5 點原封不動串接，變成
# 十幾二十點的落落長清單，而不是真的重新統整。改用 <h4> 分節的固定模板 +
# 明確的數量上限指示，並加上 _cap_bullet_points() 作為不依賴模型自律的
# 保險。

def test_summarize_uses_distinct_prompt_for_single_chunk_vs_merge(monkeypatch):
    """單一段落（不需要 map-reduce）跟合併分段摘要，應該用不同的 prompt——
    合併步驟收到的是「好幾段摘要」，需要的是統整、去重複，跟單純摘要一段
    完整文字的情境不一樣。"""
    captured_prompts = []

    def fake_generate(prompt):
        captured_prompts.append(prompt)
        return "<ul><li>摘要</li></ul>"

    monkeypatch.setattr(vt, "_generate", fake_generate)

    vt.summarize("一段不長的逐字稿內容。")
    assert len(captured_prompts) == 1
    single_chunk_prompt = captured_prompts[0]

    captured_prompts.clear()
    long_text = ("這是一句大約十個字的句子。") * 300
    vt.summarize(long_text)
    merge_prompt = captured_prompts[-1]

    assert single_chunk_prompt != merge_prompt
    assert "統整" not in single_chunk_prompt
    assert "統整" in merge_prompt


def test_summarize_output_uses_headed_sections_not_flat_list(monkeypatch):
    monkeypatch.setattr(vt, "_generate", lambda prompt: (
        "<h4>主題</h4><p>主題內容</p>"
        "<h4>重點</h4><ul><li>要點一</li><li>要點二</li></ul>"
        "<h4>結論</h4><p>結論內容</p>"
        "<h4>總結</h4><p>總結內容</p>"
    ))

    result = vt.summarize("一段不長的逐字稿內容。")

    assert result.count('<h4>') == 4
    assert '<ul><ul>' not in result  # 不該有巢狀清單


def test_cap_bullet_points_keeps_output_unchanged_when_within_limit():
    html = "<ul><li>一</li><li>二</li><li>三</li></ul>"
    assert vt._cap_bullet_points(html) == html


def test_cap_bullet_points_truncates_excess_items():
    items = "".join(f"<li>第{i}點</li>" for i in range(1, 11))  # 10 個 <li>
    html = f"<h4>重點</h4><ul>{items}</ul><h4>結論</h4><p>無</p>"

    result = vt._cap_bullet_points(html, max_items=5)

    assert result.count('<li>') == 5
    assert "第1點" in result
    assert "第5點" in result
    assert "第6點" not in result
    # 被砍掉的是多餘的 <li>，其他區塊（結論）應該保留完整。
    assert "<h4>結論</h4><p>無</p>" in result


def test_summarize_applies_bullet_point_cap_as_safety_net(monkeypatch):
    """迴歸測試：分段摘要合併時，模型即使被明確要求「最多 5 點」還是可能
    不遵守（實測踩過：直接把每段落的要點原封不動串接，變成十幾二十點）。
    summarize() 最終回傳的內容不該依賴模型自律，一定要套用數量上限。"""
    too_many_items = "".join(f"<li>第{i}點</li>" for i in range(1, 21))  # 20 個
    monkeypatch.setattr(vt, "_generate", lambda prompt: f"<ul>{too_many_items}</ul>")

    result = vt.summarize("一段不長的逐字稿內容。")

    assert result.count('<li>') == vt._SUMMARY_MAX_BULLET_POINTS
