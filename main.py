import os
import logging
from flask import Flask, render_template, request, jsonify
from utils.video_processor import process_video # 確保導入所需函數
import sqlite3
from database import init_db, get_all_videos, add_video, update_video, dump_database, search_videos, delete_video
from datetime import datetime,  timedelta
import re

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'static/screenshots'
DATABASE_NAME = 'videos.db'  # 添加此行定义数据库名称

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 確保截圖資料夾存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 初始化數據庫
init_db()


@app.route('/clear_temp_files', methods=['POST'])
def clear_temp_files():
    # 删除临时视频文件
    if os.path.exists('temp_video.mp4'):
        os.remove('temp_video.mp4')
    if os.path.exists('temp_audio.mp3'):
        os.remove('temp_audio.mp3')
    return jsonify({
        'status': 'success',
        'message': 'Temporary files cleared.'
    }), 200


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
    try:
        video_info = process_video(youtube_url, app.config['UPLOAD_FOLDER'], capture_interval)
        
        # Check if subtitles were used
        subtitle_used = 'subtitle_used' in video_info and video_info['subtitle_used']

        app.logger.info(f"Subtitle used: {video_info.get('subtitle_used', False)}")
        
        if subtitle_used:
            # If subtitles were used
            video_info['subtitle_used'] = True
            logging.info("使用字幕檔案進行翻譯與摘要")
            existing_video = get_all_videos(youtube_id=video_info['youtube_id'])
            
            app.logger.info(f"Translation: {video_info.get('translation', 'Not found')[:100]}...")
            app.logger.info(f"Summary: {video_info.get('summary', 'Not found')[:100]}...")
           
            if existing_video:
                update_video(video_info)
            else:
                add_video(video_info)
            return jsonify({
                'status': 'success',
                'message': 'Video processed with subtitles.',
                'video_info': video_info
            }), 200
        else:
            # If subtitles were not used
            video_info['subtitle_used'] = False
            logging.info("使用聲音檔案進行轉錄/翻譯與摘要")
            existing_video = get_all_videos(youtube_id=video_info['youtube_id'])

            app.logger.info(f"Translation: {video_info.get('translation', 'Not found')[:100]}...")
            app.logger.info(f"Summary: {video_info.get('summary', 'Not found')[:100]}...")

            if existing_video:
                update_video(video_info)
            else:
                add_video(video_info)
            return jsonify({
                'status': 'success',
                'message': 'Video processed without YouTube subtitles.',
                'video_info': video_info
            }), 200

    except Exception as e:
        app.logger.error(f"Error processing video: {str(e)}")
        return jsonify({'status': 'error', 'error': str(e)}), 500
    



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
    translation = video_info.get('translation', '')
    transcription = video_info.get('transcription', '')
    
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
    app.run(host='0.0.0.0', port=5001, debug=True)
