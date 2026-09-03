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
    藉此驗證 process_video 自己建立、清理的獨立暫存目錄運作正常。"""
    files = extra_files or {}

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=True):
            outtmpl = self.opts['outtmpl']
            base = outtmpl[:-len('.%(ext)s')]
            with open(base + '.mp4', 'wb') as f:
                f.write(b"fake video data")
            for suffix, content in files.items():
                with open(base + suffix, 'w', encoding='utf-8') as f:
                    f.write(content)
            return {
                'title': 'Test Video',
                'id': 'abc123',
                'description': 'desc',
                'uploader': 'creator',
                'timestamp': 0,
                'duration_string': '1:00',
                'language': 'en',
            }

    return FakeYDL


def _apply_common_patches(monkeypatch, extra_files=None):
    monkeypatch.setattr(vp, "get_all_videos", lambda youtube_id=None: None)
    monkeypatch.setattr(vp, "remove_duplicate_images", lambda folder, screenshots: screenshots)
    monkeypatch.setattr(vp.cv2, "VideoCapture", lambda path: FakeVideoCapture(path))
    monkeypatch.setattr(vp.cv2, "imwrite", lambda path, frame: True)
    monkeypatch.setattr(vp, "detect_language", lambda text: "english")
    monkeypatch.setattr(vp.yt_dlp, "YoutubeDL", _make_fake_ydl(extra_files))


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
    monkeypatch.setattr(vp, "process_vtt", lambda content, lang: ("TRANSLATED_VTT", "translated text"))
    monkeypatch.setattr(vp, "summarize", lambda text: "SUMMARY")

    result = vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10
    )

    assert result['subtitle_used'] is True
    assert result['translation'] == "TRANSLATED_VTT"
    assert result['summary'] == "SUMMARY"


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
