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
    monkeypatch.setattr(vp, "transcribe_audio_with_whisper", lambda path, language=None: None)

    result = vp.process_video(
        "https://youtube.com/watch?v=abc123", str(output_folder), capture_interval=10
    )

    assert 'error' not in result
    assert result['translation'] == '轉錄失敗'
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
