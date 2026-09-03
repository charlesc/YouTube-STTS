import types

import main


class _ImmediateThread:
    """讓背景執行緒在測試中同步跑完，測試才能立刻檢查 job 的最終狀態，
    不需要真的等待執行緒、也不會有測試間的競態問題。"""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def _run_jobs_synchronously(monkeypatch):
    monkeypatch.setattr(
        main, "threading",
        types.SimpleNamespace(Thread=_ImmediateThread, Lock=main.threading.Lock)
    )


def test_timestamp_to_seconds():
    assert main.timestamp_to_seconds("00:01:02.500") == 62.5


def test_format_timestamp_under_an_hour():
    assert main.format_timestamp("125.00s") == "02:05"


def test_format_timestamp_over_an_hour():
    assert main.format_timestamp("3661.00s") == "01:01:01"


def test_process_video_data_pairs_screenshots_with_subtitles():
    video_info = {
        'screenshots': [
            {'filename': 'a.jpg', 'timestamp': '0.00s'},
            {'filename': 'b.jpg', 'timestamp': '5.00s'},
        ],
        'translation': (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\nHello\n\n"
            "00:00:05.000 --> 00:00:07.000\nWorld\n\n"
        ),
        'transcription': (
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000\nOriginal Hello\n\n"
            "00:00:05.000 --> 00:00:07.000\nOriginal World\n\n"
        ),
    }

    paired = main.process_video_data(video_info)

    assert len(paired) == 2
    assert paired[0]['translated_subtitles'][0]['text'] == 'Hello'
    assert paired[0]['original_subtitles'][0]['text'] == 'Original Hello'
    assert paired[1]['translated_subtitles'][0]['text'] == 'World'
    assert paired[1]['original_subtitles'][0]['text'] == 'Original World'


def test_process_video_route_starts_a_background_job(monkeypatch):
    """/process_video 不應該同步阻塞到整支影片處理完成才回應——長影片的處理
    （下載/轉錄/翻譯/截圖）可能長達數分鐘。改為立即回傳 job_id，
    實際結果透過 /job_status/<job_id> 輪詢取得。"""
    _run_jobs_synchronously(monkeypatch)
    monkeypatch.setattr(
        main, "process_video",
        lambda *a, **k: {'youtube_id': 'abc123', 'title': 'T', 'description': '',
                          'creator': '', 'timestamp': '2024-01-01T00:00:00', 'duration': '',
                          'language': 'en', 'processed_at': '2024-01-01T00:00:00',
                          'screenshots': [], 'transcription': '', 'translation': '',
                          'summary': '', 'subtitle_used': False}
    )
    monkeypatch.setattr(main, "get_all_videos", lambda youtube_id=None: None)
    monkeypatch.setattr(main, "add_video", lambda video_info: 1)
    client = main.app.test_client()

    response = client.post('/process_video', data={'youtube_url': 'https://youtu.be/abc123'})

    assert response.status_code == 202
    payload = response.get_json()
    assert payload['status'] == 'processing'
    job_id = payload['job_id']

    status_response = client.get(f'/job_status/{job_id}')
    assert status_response.status_code == 200
    status_payload = status_response.get_json()
    assert status_payload['status'] == 'done'
    assert status_payload['video_info']['youtube_id'] == 'abc123'


def test_process_video_route_rejects_error_result_from_process_video(monkeypatch):
    """迴歸測試：process_video() 若因例外回傳 {'error': ...}，
    過去會忽略這個 key、直接嘗試存入資料庫，
    因缺少 'title' 等欄位而觸發難懂的 KeyError。"""
    _run_jobs_synchronously(monkeypatch)
    monkeypatch.setattr(
        main, "process_video",
        lambda *a, **k: {'error': 'boom', 'youtube_id': 'abc123'}
    )
    client = main.app.test_client()

    response = client.post('/process_video', data={'youtube_url': 'https://youtu.be/abc123'})
    assert response.status_code == 202
    job_id = response.get_json()['job_id']

    status_response = client.get(f'/job_status/{job_id}')
    assert status_response.status_code == 200
    payload = status_response.get_json()
    assert payload['status'] == 'error'
    assert '影片處理失敗' in payload['error']


def test_job_status_returns_404_for_unknown_job(monkeypatch):
    client = main.app.test_client()
    response = client.get('/job_status/does-not-exist')
    assert response.status_code == 404
