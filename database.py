import os
import sqlite3
import json
import glob
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



DATABASE_NAME = 'videos.db'

def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS videos
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  youtube_id TEXT UNIQUE,
                  title TEXT,
                  description TEXT,
                  creator TEXT,
                  timestamp TEXT,
                  duration TEXT,
                  language TEXT,
                  processed_at TEXT,
                  screenshots TEXT,
                  transcription TEXT,
                  translation TEXT,
                  summary TEXT,
                  subtitle_used BOOLEAN)
                 ''')
    conn.commit()
    conn.close()

def add_video(video_info):
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    c.execute(
        '''INSERT INTO videos (youtube_id, title, description, creator, timestamp, duration, language, processed_at, screenshots, transcription, translation, summary, subtitle_used)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            video_info['youtube_id'],
            video_info['title'],
            video_info['description'],
            video_info['creator'],
            video_info['timestamp'],
            video_info['duration'],
            video_info['language'],
            video_info['processed_at'],
            json.dumps(video_info['screenshots']),
            video_info.get('transcription'),
            video_info.get('translation'),
            video_info.get('summary'),
            video_info.get('subtitle_used', False)
        ))
    video_id = c.lastrowid
    conn.commit()
    conn.close()
    return video_id

def get_all_videos(youtube_id=None):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    if youtube_id is not None:
        c.execute('SELECT * FROM videos WHERE youtube_id = ?', (youtube_id,))
        row = c.fetchone()
        if row:
            video = dict(row)
            video['screenshots'] = json.loads(video['screenshots'])
            video['subtitle_used'] = bool(video.get('subtitle_used', False))
            conn.close()
            return video
        else:
            conn.close()
            return None
    else:
        c.execute(
            '''SELECT id, youtube_id, title, description, creator, timestamp, duration, language, MAX(processed_at) as processed_at, screenshots, subtitle_used
                     FROM videos
                     GROUP BY youtube_id
                     ORDER BY MAX(processed_at) DESC''')
        videos = []
        for row in c.fetchall():
            video = dict(row)
            video['screenshots'] = json.loads(video['screenshots'])
            video['subtitle_used'] = bool(video.get('subtitle_used', False))
            videos.append(video)
        conn.close()
        return videos

def update_video(video_info):
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    c.execute(
        '''UPDATE videos
           SET title = ?, description = ?, creator = ?, timestamp = ?, duration = ?, language = ?, processed_at = ?, screenshots = ?, transcription = ?, translation = ?, summary = ?, subtitle_used = ?
           WHERE youtube_id = ?''',
        (
            video_info['title'],
            video_info['description'],
            video_info['creator'],
            video_info['timestamp'],
            video_info['duration'],
            video_info['language'],
            video_info['processed_at'],
            json.dumps(video_info['screenshots']),
            video_info.get('transcription'),
            video_info.get('translation'),
            video_info.get('summary'),
            video_info.get('subtitle_used', False),
            video_info['youtube_id']))
    conn.commit()
    conn.close()

def search_videos(query):
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute(
        '''SELECT id, youtube_id, title, MAX(processed_at) as processed_at, screenshots
                 FROM videos
                 WHERE title LIKE ?
                 GROUP BY youtube_id
                 ORDER BY MAX(processed_at) DESC''', ('%' + query + '%', ))

    videos = []
    for row in c.fetchall():
        video = dict(row)
        video['screenshots'] = json.loads(video['screenshots'])
        video['subtitle_used'] = bool(video['subtitle_used'])  # 確保是布爾值
        videos.append(video)
    conn.close()
    return videos

def delete_video(youtube_id):
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    
    try:
        logger.debug(f"開始刪除 youtube_id 為 {youtube_id} 的視頻")
        
        # 獲取視頻信息
        c.execute('SELECT screenshots FROM videos WHERE youtube_id = ?', (youtube_id,))
        result = c.fetchone()
        if result:
            logger.debug(f"找到視頻記錄: {result}")
            screenshots = json.loads(result[0])
            
            # 刪除截圖文件
            screenshots_dir = 'static/screenshots'
            logger.debug(f"截圖目錄: {screenshots_dir}")
            
            # 使用 glob 查找匹配的文件
            file_pattern = os.path.join(screenshots_dir, f"{youtube_id}_*.jpg")
            matching_files = glob.glob(file_pattern)
            
            if matching_files:
                for file_path in matching_files:
                    logger.debug(f"嘗試刪除文件: {file_path}")
                    try:
                        os.remove(file_path)
                        logger.debug(f"已刪除文件: {file_path}")
                    except OSError as e:
                        logger.error(f"刪除文件時發生錯誤: {e}")
            else:
                logger.warning(f"未找到匹配的截圖文件: {file_pattern}")
            
            # 從數據庫中刪除視頻記錄
            c.execute('DELETE FROM videos WHERE youtube_id = ?', (youtube_id,))
            conn.commit()
            logger.debug(f"已從數據庫中刪除視頻記錄")
            return True
        else:
            logger.warning(f"未找到 youtube_id 為 {youtube_id} 的視頻記錄")
            return False
    except Exception as e:
        logger.error(f"刪除視頻時發生錯誤: {e}", exc_info=True)
        conn.rollback()
        return False
    finally:
        conn.close()
        logger.debug("資料庫連接已關閉")



def dump_database():
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    c.execute('SELECT * FROM videos')
    rows = c.fetchall()
    conn.close()
    return rows
