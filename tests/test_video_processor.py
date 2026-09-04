import numpy as np

import utils.video_processor as vp


class FakeVideoCapture:
    """避免測試依賴真實影片檔案，模擬 cv2.VideoCapture 的最小介面。"""

    def __init__(self, path):
        self.path = path

    def isOpened(self):
        return True

    def get(self, prop):
        import cv2
        if prop == cv2.CAP_PROP_FPS:
            return 1.0
        if prop == cv2.CAP_PROP_FRAME_COUNT:
            return 5
        return 0

    def set(self, prop, value):
        pass

    def read(self):
        return True, "fake_frame"

    def release(self):
        pass


def _make_fake_ydl(extra_files=None):
    """建立一個假的 yt_dlp.YoutubeDL：把假影片（以及選擇性的字幕檔）
    寫進 process_video() 這次呼叫實際配置的 outtmpl 工作目錄下，
    藉此驗證 process_video 自己建立、清理的獨立暫存目錄運作正常。

    process_video() 現在會呼叫兩次 YoutubeDL：先用 `download=False` 探查
    metadata（含字幕可用性，決定要用哪一種語言，見
    video_processor._pick_subtitle_language），再用 `download=True` 真的
    下載。探查呼叫的 opts 沒有 'outtmpl'，也不該寫任何檔案；這裡從
    extra_files 的副檔名（例如 '.en.vtt' -> 'en'）反推出要回報「有哪些
    語言可用」，讓探查結果跟真正下載那次寫出來的檔案一致。
    """
    files = extra_files or {}
    available_langs = {suffix[1:-len('.vtt')] for suffix in files if suffix.endswith('.vtt')}

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            info = {
                'title': 'Test Video',
                'id': 'abc123',
                'description': 'desc',
                'uploader': 'creator',
                'timestamp': 0,
                'duration_string': '1:00',
                'language': 'en',
            }
            if not download:
                info['subtitles'] = {lang: [{}] for lang in available_langs}
                info['automatic_captions'] = {}
                return info

            outtmpl = self.opts['outtmpl']
            base = outtmpl[:-len('.%(ext)s')]
            with open(base + '.mp4', 'wb') as f:
                f.write(b"fake video data")
            for suffix, content in files.items():
                with open(base + suffix, 'w', encoding='utf-8') as f:
                    f.write(content)
            return info

    return FakeYDL


def _apply_common_patches(monkeypatch, extra_files=None):
    monkeypatch.setattr(vp, "get_all_videos", lambda youtube_id=None: None)
    monkeypatch.setattr(vp, "remove_duplicate_images", lambda folder, screenshots: screenshots)
    monkeypatch.setattr(vp.cv2, "VideoCapture", lambda path: FakeVideoCapture(path))
    monkeypatch.setattr(vp.cv2, "imwrite", lambda path, frame: True)
    monkeypatch.setattr(vp, "_resize_frame_if_too_wide", lambda frame, max_width=None: frame)
    monkeypatch.setattr(vp, "detect_language", lambda text: "english")
    monkeypatch.setattr(vp.yt_dlp, "YoutubeDL", _make_fake_ydl(extra_files))


def test_process_video_falls_back_to_video_only_when_subtitle_download_fails(tmp_path, monkeypatch):
    """迴歸測試：yt-dlp 把「影片下載成功、但字幕下載失敗」也當成整個
    extract_info() 呼叫失敗（字幕是同一次 process_info() 裡、影片下載完
    之後才寫入的最後一步）。實測踩過的真實案例：即使只跟 yt-dlp 要一種
    字幕語言，還是可能被 YouTube 限流（HTTP 429），導致已經下載成功的
    影片本體也跟著作廢。應該改成不帶字幕重新下載一次、轉去做語音轉錄，
    而不是讓整支影片處理直接失敗。"""
    output_folder = tmp_path / "screenshots"
    output_folder.mkdir()

    call_log = []

    class FlakyFakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            info = {
                'title': 'Test Video', 'id': 'abc123', 'description': 'desc',
                'uploader': 'creator', 'timestamp': 0, 'duration_string': '1:00',
                'language': 'en',
            }
            if not download:
                info['subtitles'] = {}
                info['automatic_captions'] = {'en': [{}]}
                return info

            wants_subs = self.opts.get('writesubtitles')
            call_log.append(wants_subs)
            if wants_subs:
                raise RuntimeError("字幕下載被限流（模擬 HTTP 429 Too Many Requests）")

            # fallback（不含字幕）呼叫：模擬影片本體下載成功。
            outtmpl = self.opts['outtmpl']
            base = outtmpl[:-len('.%(ext)s')]
            with open(base + '.mp4', 'wb') as f:
                f.write(b"fake video data")
            return info

    monkeypatch.setattr(vp.yt_dlp, "YoutubeDL", FlakyFakeYDL)
    monkeypatch.setattr(vp, "get_all_videos", lambda youtube_id=None: None)
    monkeypatch.setattr(vp, "remove_duplicate_images", lambda folder, screenshots: screenshots)
    monkeypatch.setattr(vp.cv2, "VideoCapture", lambda path: FakeVideoCapture(path))
    monkeypatch.setattr(vp.cv2, "imwrite", lambda path, frame: True)
    monkeypatch.setattr(vp, "_resize_frame_if_too_wide", lambda frame, max_width=None: frame)
    monkeypatch.setattr(vp, "detect_language", lambda text: "english")
    # 轉去轉錄之後怎麼樣不是這個測試關心的重點，讓它乾脆地失敗即可，
    # 重點是驗證流程有沒有走到這裡、而不是整支直接報錯。
    monkeypatch.setattr(vp, "extract_audio", lambda i, o: None)

    result = vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10
    )

    assert 'error' not in result
    assert result['subtitle_used'] is False
    assert call_log == [True, False]  # 先嘗試帶字幕、失敗後才退回不帶字幕重試


def test_process_video_survives_audio_extraction_failure(tmp_path, monkeypatch):
    """迴歸測試：音頻提取失敗時，過去 `transcription`/`translation` 變數
    未被賦值就拿去組 result dict，會丟出 UnboundLocalError 而非回傳明確錯誤訊息。"""
    output_folder = tmp_path / "screenshots"
    output_folder.mkdir()
    _apply_common_patches(monkeypatch)
    monkeypatch.setattr(vp, "extract_audio", lambda i, o: None)

    result = vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10
    )

    assert 'error' not in result
    assert result['transcription'] == ''
    assert result['translation'] == '音頻提取失敗'
    assert result['summary'] == '無法生成摘要'
    assert result['subtitle_used'] is False


def test_process_video_survives_transcription_failure(tmp_path, monkeypatch):
    """迴歸測試：轉錄失敗時，過去 `translated_vtt` 變數未賦值就拿去組
    result dict，會丟出 UnboundLocalError 而非回傳明確錯誤訊息。"""
    output_folder = tmp_path / "screenshots"
    output_folder.mkdir()
    _apply_common_patches(monkeypatch)

    def fake_extract_audio(input_path, output_path):
        with open(output_path, 'wb') as f:
            f.write(b"fake audio")
        return output_path

    monkeypatch.setattr(vp, "extract_audio", fake_extract_audio)
    # 兩個轉錄後端都模擬失敗，才會真的走到「轉錄失敗」這個分支——
    # 否則預設的 'apple' 後端會先被嘗試（雖然對假音檔會自然失敗，但明確
    # mock 掉比較不依賴 apple_llm_bridge 真的被建置、也比較快）。
    monkeypatch.setattr(vp, "transcribe_audio_with_apple_speech", lambda path, locale_id: None)
    monkeypatch.setattr(vp, "transcribe_audio_with_whisper", lambda path, language=None: None)

    result = vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10
    )

    assert 'error' not in result
    assert result['translation'] == '轉錄失敗'
    # 迴歸測試：transcribe_audio_with_whisper() 回傳 None 時，
    # transcription 過去會直接沿用這個 None 存進資料庫（NULL），
    # 讓 main.py:process_video_data() 讀回來時對 None 做正則比對而 500。
    assert result['transcription'] == ''
    assert result['summary'] == '無法生成摘要'


def test_process_video_success_path_uses_translated_content(tmp_path, monkeypatch):
    """確認正常路徑下 result['translation'] 真的是翻譯後內容，
    而不是重構前遺留、從未被賦值的 translated_vtt 變數。"""
    output_folder = tmp_path / "screenshots"
    output_folder.mkdir()
    subtitle_content = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world\n\n"
    _apply_common_patches(monkeypatch, extra_files={'.en.vtt': subtitle_content})
    monkeypatch.setattr(vp, "process_vtt", lambda content, lang, on_progress=None: ("TRANSLATED_VTT", "translated text"))
    monkeypatch.setattr(vp, "summarize", lambda text, on_progress=None: "SUMMARY")

    result = vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10
    )

    assert result['subtitle_used'] is True
    assert result['translation'] == "TRANSLATED_VTT"
    assert result['summary'] == "SUMMARY"


def test_process_video_reports_progress_through_each_stage(tmp_path, monkeypatch):
    """迴歸測試：process_video() 過去完全沒有回報進度，前端輪詢期間畫面
    只會停在「正在處理視頻...」不會變化，使用者看不出卡在哪個階段。"""
    output_folder = tmp_path / "screenshots"
    output_folder.mkdir()
    subtitle_content = "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world\n\n"
    _apply_common_patches(monkeypatch, extra_files={'.en.vtt': subtitle_content})
    monkeypatch.setattr(vp, "process_vtt", lambda content, lang, on_progress=None: ("TRANSLATED_VTT", "translated text"))
    monkeypatch.setattr(vp, "summarize", lambda text, on_progress=None: "SUMMARY")

    progress_messages = []
    vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10,
        on_progress=progress_messages.append,
    )

    assert "正在下載影片與字幕..." in progress_messages
    assert any("字幕" in m and "整理" in m for m in progress_messages)
    assert "正在偵測字幕語言..." in progress_messages
    assert any("截圖" in m for m in progress_messages)
    # 呼叫順序也要合理：先下載、才輪到截圖
    assert progress_messages.index("正在下載影片與字幕...") < progress_messages.index("正在擷取影片截圖...")


def test_process_video_cleans_up_its_own_work_dir(tmp_path, monkeypatch):
    """迴歸測試：每次呼叫應該使用獨立的暫存工作目錄（避免並行處理互相
    覆寫檔名固定的 temp_video.mp4 等檔案），且結束後會自行清理，不留下暫存檔。"""
    output_folder = tmp_path / "screenshots"
    output_folder.mkdir()
    _apply_common_patches(monkeypatch)

    captured_work_dir = {}
    original_mkdtemp = vp.tempfile.mkdtemp

    def spying_mkdtemp(*args, **kwargs):
        work_dir = original_mkdtemp(*args, **kwargs)
        captured_work_dir['path'] = work_dir
        return work_dir

    monkeypatch.setattr(vp.tempfile, "mkdtemp", spying_mkdtemp)
    monkeypatch.setattr(vp, "extract_audio", lambda i, o: None)

    vp.process_video("https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10)

    assert 'path' in captured_work_dir
    assert not vp.os.path.exists(captured_work_dir['path'])


# --- 截圖縮放（_resize_frame_if_too_wide）---
#
# 截圖依影片原始解析度存檔，4K/2K 影片截圖會不必要地大、網頁上也用不到
# 這麼高的解析度。寬度超過 config.SCREENSHOT_MAX_WIDTH 時等比例縮小；
# 比門檻窄的影片（例如直式短影音）不放大。

def test_resize_frame_if_too_wide_downscales_wide_frame():
    frame = np.zeros((1080, 3840, 3), dtype=np.uint8)  # 4K 橫向影片截圖
    resized = vp._resize_frame_if_too_wide(frame, max_width=1440)
    assert resized.shape[1] == 1440
    assert resized.shape[0] == 405  # 1080 * (1440/3840)，維持長寬比


def test_resize_frame_if_too_wide_leaves_narrow_frame_untouched():
    frame = np.zeros((1920, 1080, 3), dtype=np.uint8)  # 直式短影音，比門檻窄
    resized = vp._resize_frame_if_too_wide(frame, max_width=1440)
    assert resized.shape == frame.shape


def test_resize_frame_if_too_wide_uses_config_default(monkeypatch):
    monkeypatch.setattr(vp.config, "SCREENSHOT_MAX_WIDTH", 640)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    resized = vp._resize_frame_if_too_wide(frame)
    assert resized.shape[1] == 640


# --- 字幕語言探查（_pick_subtitle_language）---
#
# 迴歸測試：process_video() 過去一次跟 yt-dlp 要 SUBTITLE_LANGS 裡全部
# 語言的字幕，就算只會用到優先順序最前面那一個。實測踩過的真實案例：
# 排最後、根本用不到的語言字幕下載被 YouTube 限流（429），整個
# extract_info(download=True) 直接拋例外，連已經下載成功的影片本體都
# 作廢。改成先探查 metadata 決定唯一要用的語言，只下載那一個。

def test_pick_subtitle_language_prefers_earlier_priority_language():
    info = {
        'subtitles': {'en': [{}], 'zh-TW': [{}]},
        'automatic_captions': {},
    }
    # config.SUBTITLE_LANGS 預設 zh-TW 優先於 en
    assert vp._pick_subtitle_language(info) == 'zh-TW'


def test_pick_subtitle_language_falls_back_to_automatic_captions():
    info = {
        'subtitles': {},  # 沒有人工字幕
        'automatic_captions': {'en': [{}]},
    }
    assert vp._pick_subtitle_language(info) == 'en'


def test_pick_subtitle_language_returns_none_when_nothing_available():
    info = {'subtitles': {}, 'automatic_captions': {}}
    assert vp._pick_subtitle_language(info) is None


def test_pick_subtitle_language_ignores_languages_not_in_priority_list():
    info = {
        'subtitles': {'de': [{}], 'fr': [{}]},  # 都不在 config.SUBTITLE_LANGS 裡
        'automatic_captions': {},
    }
    assert vp._pick_subtitle_language(info) is None


# 迴歸測試：真實踩過的 bug（duuqEo1r8rU，一支完全沒有人工字幕的英文影片）。
# YouTube 把語音辨識出來的英文原文自動字幕，又機器翻譯成上百種語言塞進
# automatic_captions（包含 zh-Hant），舊版邏輯直接照 SUBTITLE_LANGS 的
# 優先順序（zh-Hant 排在 en 前面）去挑，結果把 YouTube 自動翻譯出來的
# 中文版本當成「原文」，網頁上「原文」欄位整個顯示成中文、內容還被機器
# 翻譯了兩次。修法是優先採用 info['language']（yt-dlp 回報的影片本身
# 語音語言），因為 automatic_captions 裡只有這個語言代碼對應的那條，才是
# 語音辨識直接產生、沒有被再翻譯過的原文。
def test_pick_subtitle_language_prefers_native_language_over_translated_auto_captions():
    info = {
        'language': 'en',
        'subtitles': {},
        # 157 種自動翻譯語言的簡化版：只留下真正會造成問題的 zh-Hant，
        # 跟語音辨識原文所在的 en。
        'automatic_captions': {'en': [{}], 'zh-Hant': [{}]},
    }
    assert vp._pick_subtitle_language(info) == 'en'


def test_pick_subtitle_language_prefers_native_language_over_manual_priority_list():
    info = {
        'language': 'en',
        'subtitles': {'en': [{}], 'zh-TW': [{}]},
        'automatic_captions': {},
    }
    assert vp._pick_subtitle_language(info) == 'en'


def test_pick_subtitle_language_falls_back_to_priority_list_when_native_language_unavailable():
    # info['language'] 存在，但那個語言剛好沒有任何字幕/自動字幕可用
    # （不常見，但 yt-dlp 回報的 language 不保證跟可用字幕語言一致）；
    # 這時退回舊有的 SUBTITLE_LANGS 優先順序邏輯。
    info = {
        'language': 'fr',
        'subtitles': {'en': [{}], 'zh-TW': [{}]},
        'automatic_captions': {},
    }
    assert vp._pick_subtitle_language(info) == 'zh-TW'


def test_pick_subtitle_language_falls_back_to_priority_list_when_native_language_missing():
    info = {
        'subtitles': {},
        'automatic_captions': {'en': [{}], 'zh-Hant': [{}]},
    }
    # 沒有 info['language'] 可用時沒有更好的依據，退回舊行為。
    assert vp._pick_subtitle_language(info) == 'zh-Hant'


# 迴歸測試：真實踩過的第二個 bug（5bxp78i96S8，PG 的英文訪談）。
# info['language'] 有時候是帶地區碼的完整 locale（例如 'en-US'），但
# subtitles/automatic_captions 字典的 key 常常是不帶地區碼的短代碼
# （'en'）。只做精確字串比對的話，'en-US' in auto 是 False，於是照樣掉回
# SUBTITLE_LANGS 的 fallback 順序、選到排在 en 前面的 zh-Hant 自動翻譯
# 版本——跟完全沒有 info['language'] 時一樣的錯誤結果。修法是精確比對
# 失敗時，再用主要語言子代碼（'-' 前面那段）比對一次。
def test_pick_subtitle_language_matches_native_language_ignoring_region_subtag():
    info = {
        'language': 'en-US',
        'subtitles': {},
        'automatic_captions': {'en': [{}], 'zh-Hant': [{}]},
    }
    assert vp._pick_subtitle_language(info) == 'en'


def test_pick_subtitle_language_matches_native_language_ignoring_region_subtag_manual():
    info = {
        'language': 'en-US',
        'subtitles': {'en': [{}], 'zh-TW': [{}]},
        'automatic_captions': {},
    }
    assert vp._pick_subtitle_language(info) == 'en'


# --- 語音轉錄後端（config.TRANSCRIPTION_BACKEND）---

def test_pick_speech_locale_maps_detected_language_to_locale(monkeypatch):
    monkeypatch.setattr(vp, "detect_language", lambda text: "Traditional Chinese")
    assert vp.pick_speech_locale("某段文字") == "zh-TW"

    monkeypatch.setattr(vp, "detect_language", lambda text: "Korean")
    assert vp.pick_speech_locale("某段文字") == "ko-KR"


def test_pick_speech_locale_falls_back_to_default_for_unknown_language(monkeypatch):
    monkeypatch.setattr(vp, "detect_language", lambda text: "Klingon")
    assert vp.pick_speech_locale("某段文字") == "en-US"


def test_transcribe_audio_with_apple_speech_builds_vtt_from_segments(monkeypatch):
    monkeypatch.setattr(vp, "_call_apple_bridge", lambda payload: {
        "status": "ok",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": "Hello world."},
            {"start": 1.5, "end": 3.2, "text": "Second sentence."},
        ],
    })

    result = vp.transcribe_audio_with_apple_speech("/tmp/audio.mp3", "en-US")

    assert result.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.500" in result
    assert "Hello world." in result
    assert "Second sentence." in result


def test_transcribe_audio_with_apple_speech_returns_none_on_failure(monkeypatch):
    def fake_call(payload):
        raise vp.GenerationError("locale 不支援", reason="unsupported_locale")

    monkeypatch.setattr(vp, "_call_apple_bridge", fake_call)

    assert vp.transcribe_audio_with_apple_speech("/tmp/audio.mp3", "xx-XX") is None


def test_transcribe_audio_dispatches_to_apple_backend_by_default(monkeypatch):
    assert vp.config.TRANSCRIPTION_BACKEND == "apple"

    def fail_if_called(path, language=None):
        raise AssertionError("apple 後端成功時不應該退回 mlx-whisper")

    monkeypatch.setattr(vp, "transcribe_audio_with_apple_speech", lambda path, locale_id: "WEBVTT\n\napple 轉錄結果\n")
    monkeypatch.setattr(vp, "transcribe_audio_with_whisper", fail_if_called)

    assert vp.transcribe_audio("/tmp/audio.mp3", "some english text") == "WEBVTT\n\napple 轉錄結果\n"


def test_transcribe_audio_reports_progress(monkeypatch):
    monkeypatch.setattr(vp, "transcribe_audio_with_apple_speech", lambda path, locale_id: None)
    monkeypatch.setattr(vp, "transcribe_audio_with_whisper", lambda path, language=None: "WEBVTT\n\nresult\n")

    progress_messages = []
    vp.transcribe_audio("/tmp/audio.mp3", "some english text", on_progress=progress_messages.append)

    assert progress_messages == [
        "正在使用 Apple SpeechAnalyzer 轉錄語音...",
        "Apple SpeechAnalyzer 轉錄失敗，改用 mlx-whisper 重試...",
    ]


def test_transcribe_audio_falls_back_to_whisper_when_apple_fails(monkeypatch):
    """迴歸測試：apple 轉錄失敗（locale 不支援、Speech 服務未啟用、
    binary 沒 build 等）時應該退回 mlx-whisper，而不是讓整支影片處理失敗。"""
    assert vp.config.TRANSCRIPTION_BACKEND == "apple"

    monkeypatch.setattr(vp, "transcribe_audio_with_apple_speech", lambda path, locale_id: None)
    monkeypatch.setattr(vp, "transcribe_audio_with_whisper", lambda path, language=None: "WEBVTT\n\nwhisper 轉錄結果\n")

    assert vp.transcribe_audio("/tmp/audio.mp3", "some english text") == "WEBVTT\n\nwhisper 轉錄結果\n"


def test_transcribe_audio_skips_apple_when_backend_is_mlx_whisper(monkeypatch):
    monkeypatch.setattr(vp.config, "TRANSCRIPTION_BACKEND", "mlx_whisper")

    def fail_if_called(path, locale_id):
        raise AssertionError("TRANSCRIPTION_BACKEND=mlx_whisper 時不該呼叫 apple 後端")

    monkeypatch.setattr(vp, "transcribe_audio_with_apple_speech", fail_if_called)
    monkeypatch.setattr(vp, "transcribe_audio_with_whisper", lambda path, language=None: "WEBVTT\n\nwhisper 轉錄結果\n")

    assert vp.transcribe_audio("/tmp/audio.mp3", "some english text") == "WEBVTT\n\nwhisper 轉錄結果\n"
