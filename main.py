import os
import logging
import threading
import uuid
from flask import Flask, render_template, request, jsonify
from utils.video_processor import process_video # 確保導入所需函數
from database import init_db, get_all_videos, add_video, update_video, dump_database, search_videos, delete_video
from datetime import datetime,  timedelta
import re

import config

# 整個應用程式唯一的 logging 進入點設定，其餘模組只呼叫 getLogger(__name__)，
# 避免重複呼叫 basicConfig 導致設定互相覆蓋或重複輸出。
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = config.UPLOAD_FOLDER

# 確保截圖資料夾存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 初始化數據庫
init_db()


# 影片處理是長時間、同步阻塞的流程（下載、可能的轉錄、逐句/批次翻譯、截圖擷取），
# 若直接在 request handler 內執行，會讓整個請求掛著等到全部完成才回應，且無法讓
# 使用者看到進度、也擋住同一個 worker 處理其他請求。這裡改用背景執行緒 + 記憶體內
# 的工作狀態表，/process_video 啟動工作後立即回應 job_id，前端再輪詢 /job_status/<id>。
#
# 這是進程內的簡易佇列，跟著 Flask process 的生命週期走：重啟後工作紀錄會消失，
# 也不支援多台機器/多個 worker process 共享狀態。若未來需要多 worker 部署，
# 應改用 Redis/RQ、Celery 等跨進程的工作佇列。
_jobs_lock = threading.Lock()
_jobs = {}


def _run_video_processing(job_id, youtube_url, capture_interval):
    def on_progress(message):
        # process_video() 在每個階段開始時呼叫這個 callback 回報一句進度
        # 說明，寫進 job 狀態讓前端輪詢 /job_status/<id> 時能顯示即時進度，
        # 而不是整個處理過程中畫面只停在「正在處理視頻...」不會變化。
        logger.info(f"Progress (job {job_id}): {message}")
        with _jobs_lock:
            if job_id in _jobs:
                _jobs[job_id] = {'status': 'processing', 'progress': message}

    try:
        video_info = process_video(
            youtube_url, app.config['UPLOAD_FOLDER'], capture_interval, on_progress=on_progress
        )

        if 'error' in video_info:
            logger.error(f"Error processing video (job {job_id}): {video_info['error']}")
            with _jobs_lock:
                _jobs[job_id] = {
                    'status': 'error',
                    'error': '影片處理失敗，請確認 URL 是否正確或稍後再試。'
                }
            return

        video_info['subtitle_used'] = bool(video_info.get('subtitle_used', False))
        logger.info(f"Subtitle used (job {job_id}): {video_info['subtitle_used']}")
        logger.info(f"Translation (job {job_id}, 前100字符): {video_info.get('translation', 'Not found')[:100]}...")
        logger.info(f"Summary (job {job_id}, 前100字符): {video_info.get('summary', 'Not found')[:100]}...")

        existing_video = get_all_videos(youtube_id=video_info['youtube_id'])
        if existing_video:
            update_video(video_info)
        else:
            add_video(video_info)

        with _jobs_lock:
            _jobs[job_id] = {'status': 'done', 'video_info': video_info}

    except Exception as e:
        logger.error(f"Unexpected error processing video (job {job_id}): {e}", exc_info=True)
        with _jobs_lock:
            _jobs[job_id] = {
                'status': 'error',
                'error': '影片處理失敗，請確認 URL 是否正確或稍後再試。'
            }


@app.route('/')
def index():
    videos = get_all_videos()
    return render_template('index.html', videos=videos)


@app.route('/api/videos', methods=['GET'])
def get_videos():
    videos = get_all_videos()  # 获取视频列表
    return jsonify(videos)  # 返回 JSON 格式的视频



@app.route('/video/<string:youtube_id>')
def video_screenshots(youtube_id):
    video = get_all_videos(youtube_id=youtube_id)
    if video:
        paired_data = process_video_data(video)
        return render_template('video_screenshots.html', video=video, paired_data=paired_data)
    else:
        return "Video not found", 404



@app.route('/process_video', methods=['POST'])
def process_video_route():
    youtube_url = request.form.get('youtube_url')
    capture_interval = int(request.form.get('capture_interval', 10))
    if not youtube_url:
        return jsonify({'error': 'No YouTube URL provided'}), 400

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {'status': 'processing'}

    thread = threading.Thread(
        target=_run_video_processing,
        args=(job_id, youtube_url, capture_interval),
        daemon=True,
    )
    thread.start()

    return jsonify({'status': 'processing', 'job_id': job_id}), 202


@app.route('/job_status/<job_id>')
def job_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        return jsonify({'status': 'error', 'error': '找不到這個處理工作，請重新提交。'}), 404
    return jsonify(job), 200



@app.route('/search')
def search():
    query = request.args.get('q', '')
    videos = search_videos(query)
    return jsonify(videos)


@app.route('/debug/db')
def debug_db():
    app.logger.info(f"Database contents: {dump_database()}")
    return "Database dumped to logs", 200


@app.route('/delete_video/<youtube_id>', methods=['POST'])
def delete_video_route(youtube_id):
    logger.debug(f"收到刪除 youtube_id 為 {youtube_id} 的視頻請求")
    success = delete_video(youtube_id)
    if success:
        logger.info(f"成功刪除 youtube_id 為 {youtube_id} 的視頻")
        return jsonify({
            "status": "success",
            "message": "視頻記錄已從數據庫中刪除，相關截圖文件（如果存在）也已刪除"
        }), 200
    else:
        logger.error(f"刪除 youtube_id 為 {youtube_id} 的視頻失敗")
        return jsonify({
            "status": "error",
            "message": "刪除視頻失敗，請查看服務器日誌以獲取更多信息"
        }), 400



def process_video_data(video_info):
    screenshots = video_info['screenshots']
    # 用 `or ''` 而非 `.get(key, '')`：資料庫裡的 transcription/translation
    # 欄位可能存的是 NULL（例如轉錄失敗那次留下的紀錄），這種情況下 key 是
    # 存在的、只是值為 None，`.get(key, default)` 不會套用 default，
    # 後面的正則表達式吃到 None 會直接丟 TypeError。
    translation = video_info.get('translation') or ''
    transcription = video_info.get('transcription') or ''
    
    # 解析翻譯後的字幕
    translated_parts = re.findall(r'(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})\n(.*?)(?:\n\n|$)', translation, re.DOTALL)
    
    # 解析原始字幕
    original_parts = re.findall(r'(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})\n(.*?)(?:\n\n|$)', transcription, re.DOTALL)
    
    # 將字幕轉換為更易於處理的格式
    translated_subtitles = [
        {
            'start': timestamp_to_seconds(start),
            'end': timestamp_to_seconds(end),
            'text': text.strip()
        }
        for start, end, text in translated_parts
    ]
    
    original_subtitles = [
        {
            'start': timestamp_to_seconds(start),
            'end': timestamp_to_seconds(end),
            'text': text.strip()
        }
        for start, end, text in original_parts
    ]
    
    # 將截圖與字幕配對
    paired_data = []
    for i, screenshot in enumerate(screenshots):
        current_time = float(screenshot['timestamp'].replace('s', ''))
        next_time = float(screenshots[i+1]['timestamp'].replace('s', '')) if i+1 < len(screenshots) else float('inf')
        
        matching_translated = find_matching_subtitles(current_time, next_time, translated_subtitles)
        matching_original = find_matching_subtitles(current_time, next_time, original_subtitles)
        
        paired_data.append({
            'screenshot': screenshot,
            'translated_subtitles': matching_translated,
            'original_subtitles': matching_original
        })
    
    return paired_data

def timestamp_to_seconds(timestamp):
    t = datetime.strptime(timestamp, '%H:%M:%S.%f')
    return timedelta(hours=t.hour, minutes=t.minute, seconds=t.second, microseconds=t.microsecond).total_seconds()

def find_matching_subtitles(current_time, next_time, subtitles):
    matching_subtitles = []
    for subtitle in subtitles:
        if current_time <= subtitle['start'] < next_time:
            matching_subtitles.append(subtitle)
    return matching_subtitles


def datetime_format(value, format='%Y-%m-%d %H:%M:%S'):
    return datetime.strptime(value.split('.')[0], '%Y-%m-%dT%H:%M:%S').strftime(format)

app.jinja_env.filters['datetime_format'] = datetime_format

def format_timestamp(seconds):
    if seconds is None:
        return ''
    
    seconds = float(seconds.replace('s', ''))
    total_seconds = int(float(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"

app.jinja_env.filters['format_timestamp'] = format_timestamp


if __name__ == '__main__':
    init_db()  # 初始化數據庫
    # debug 模式預設關閉（Werkzeug debugger 在 debug=True 時可執行任意程式碼，
    # 不應在對外環境開啟）；需要時設定環境變數 FLASK_DEBUG=true。
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, debug=config.FLASK_DEBUG, threaded=True)
