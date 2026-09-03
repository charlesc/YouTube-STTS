import os

import database as db


def _use_isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_videos.db")
    monkeypatch.setattr(db, "DATABASE_NAME", db_path)
    db.init_db()
    return db_path


def _sample_video(youtube_id="abc123", title="Title"):
    return {
        'youtube_id': youtube_id,
        'title': title,
        'description': 'Desc',
        'creator': 'Creator',
        'timestamp': '2024-01-01T00:00:00',
        'duration': '1:00',
        'language': 'en',
        'processed_at': '2024-01-01T00:00:00',
        'screenshots': [{'filename': f'{youtube_id}_0.00.jpg', 'timestamp': '0.00s'}],
        'transcription': 'transcript',
        'translation': 'translation',
        'summary': 'summary',
        'subtitle_used': True,
    }


def test_add_and_get_video(tmp_path, monkeypatch):
    _use_isolated_db(tmp_path, monkeypatch)
    db.add_video(_sample_video())

    video = db.get_all_videos(youtube_id="abc123")
    assert video['title'] == 'Title'
    assert video['subtitle_used'] is True
    assert video['screenshots'][0]['filename'] == 'abc123_0.00.jpg'


def test_update_video_overwrites_existing_row_instead_of_appending(tmp_path, monkeypatch):
    """同一支影片重新處理應該是 UPDATE，而不是多一列歷史紀錄。"""
    _use_isolated_db(tmp_path, monkeypatch)
    db.add_video(_sample_video())

    updated = _sample_video(title="New Title")
    db.update_video(updated)

    videos = db.get_all_videos()
    assert len(videos) == 1
    assert videos[0]['title'] == 'New Title'


def test_search_videos_matches_title_substring(tmp_path, monkeypatch):
    _use_isolated_db(tmp_path, monkeypatch)
    db.add_video(_sample_video(youtube_id="abc123", title="Cats are great"))
    db.add_video(_sample_video(youtube_id="xyz789", title="Completely different"))

    results = db.search_videos("cats")
    assert len(results) == 1
    assert results[0]['youtube_id'] == 'abc123'


def test_search_videos_includes_columns_used_by_frontend_table(tmp_path, monkeypatch):
    """迴歸測試：search_videos() 過去的 SELECT 沒有選取
    creator/timestamp/duration/language/subtitle_used，
    前端表格會缺欄位，且存取 video['subtitle_used'] 必定 KeyError。"""
    _use_isolated_db(tmp_path, monkeypatch)
    db.add_video(_sample_video(youtube_id="abc123", title="Searchable Title"))

    results = db.search_videos("Searchable")
    assert len(results) == 1
    video = results[0]
    for field in ('creator', 'timestamp', 'duration', 'language', 'subtitle_used'):
        assert field in video
    assert video['subtitle_used'] is True


def test_delete_video_removes_row_and_screenshot_files(tmp_path, monkeypatch):
    _use_isolated_db(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    os.makedirs("static/screenshots", exist_ok=True)
    open("static/screenshots/abc123_0.00.jpg", "wb").close()

    db.add_video(_sample_video())
    assert db.delete_video("abc123") is True
    assert db.get_all_videos(youtube_id="abc123") is None
    assert not os.path.exists("static/screenshots/abc123_0.00.jpg")


def test_delete_video_returns_false_for_unknown_id(tmp_path, monkeypatch):
    _use_isolated_db(tmp_path, monkeypatch)
    assert db.delete_video("does-not-exist") is False
