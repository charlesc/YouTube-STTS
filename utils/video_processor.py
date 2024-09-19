import os
from openai import OpenAI
import yt_dlp
import cv2
import subprocess
from datetime import datetime
from utils.image_processor import remove_duplicate_images
from utils.vtt_translator import translate_and_summarize, process_vtt, extract_text_from_vtt, detect_language
from database import get_all_videos 
import mlx_whisper
import logging


# 設置日誌記錄
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cleanup_temp_files():
    temp_files = ['temp_video.mp4', 'temp_audio.mp3'] + [f"temp_video.{lang}.vtt" for lang in ['zh-TW','zh-Hant', 'en', 'ko', 'ja']]
    for file in temp_files:
        if os.path.exists(file):
            os.remove(file)
            logger.info(f"Removed temporary file: {file}")


def extract_audio(input_video_path, output_audio_path):
    # 如果文件已存在，可以选择先删除或者重命名
    if os.path.exists(output_audio_path):
        os.remove(output_audio_path)
    """從視頻中提取音頻並保存為 MP3 文件。"""
    command = [
        'ffmpeg',
        '-i',
        input_video_path,
        '-ar',
        '16000',  # 設置取樣率為 16000Hz
        '-ab',
        '128k',  # 128 kbps
        '-ac',
        '1',  # 設置聲道為單聲道
        output_audio_path
    ]

    try:
        subprocess.run(command, check=True)
        print(f"Successfully extracted audio to {output_audio_path}")
        return output_audio_path  # 返回音頻文件路徑
    except subprocess.CalledProcessError as e:
        print(f"Error during audio extraction: {e}")
        return None  # 返回 None 代表提取失敗

def format_timestamp(seconds):
    """將秒數轉換為 VTT 格式的時間戳"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = seconds % 60
    milliseconds = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{int(seconds):02d}.{milliseconds:03d}"


def transcribe_audio_with_whisper(audio_path, language=None):
    """使用 mlx-whisper 進行音頻轉錄，並返回 VTT 格式的字符串。"""
    try:
        if not os.path.exists(audio_path):
            raise ValueError(f"音頻文件 '{audio_path}' 不存在。")

        file_size = os.path.getsize(audio_path)
        print(f"音頻文件大小: {file_size} 字節")

        if file_size == 0:
            raise ValueError(f"音頻文件 '{audio_path}' 為空。")

        # 初始化 mlx-whisper 模型
        # model = WhisperModel('small')  # 可以選擇 'tiny', 'base', 'small', 'medium', 'large'

        # 使用 mlx-whisper 模型x``進行轉錄
        result = mlx_whisper.transcribe(audio_path, path_or_hf_repo="mlx-community/whisper-large-v3-mlx")

        # 將結果轉換為 VTT 格式的字符串
        transcription = "WEBVTT\n\n"
        for segment in result["segments"]:
            start = format_timestamp(segment['start'])
            end = format_timestamp(segment['end'])
            text = segment['text'].strip()
            transcription += f"{start} --> {end}\n{text}\n\n"

        print("轉錄完成。")
        print("VTT 內容預覽:", transcription[:200] + "..." if len(transcription) > 200 else transcription)

        return transcription

    except Exception as e:
        print(f"轉錄過程中發生錯誤: {str(e)}")
    return None


def process_video(youtube_url, output_folder, capture_interval=10):
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': 'temp_video.%(ext)s',
        'writesubtitles': True,
        'subtitleslangs': ['zh-TW','zh-Hant', 'en', 'ko', 'ja'],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=True)
            video_title = info['title']
            video_id = info['id']
            video_description = info.get('description', '')
            video_creator = info.get('uploader', '')
            video_timestamp = datetime.fromtimestamp(info.get('timestamp', 0)).isoformat()
            video_duration = info.get('duration_string', '')
            video_language = info.get('language', '') or detect_language(video_title + ' ' + video_description)

        logger.info(f"視頻下載完成: {video_title}")

        if not os.path.exists('temp_video.mp4'):
            raise FileNotFoundError("視頻文件未成功下載")

        subtitle_path = None
        subtitle_used = False

        for lang in ['zh-TW','zh-Hant', 'en', 'ko', 'ja']:
            potential_subtitle_path = f"temp_video.{lang}.vtt"
            if os.path.exists(potential_subtitle_path):
                subtitle_path = potential_subtitle_path
                subtitle_used = True
                logger.info(f"找到{get_language_name(lang)}字幕: {subtitle_path}")
                break

        if subtitle_used and os.path.exists(subtitle_path) and os.path.getsize(subtitle_path) > 0:
            logger.info(f"開始處理字幕檔案: {subtitle_path}")
            with open(subtitle_path, 'r', encoding='utf-8') as file:
                subtitle_content = file.read()
            
            detected_language = detect_language(extract_text_from_vtt(subtitle_content))
            translated_vtt, translated_text = process_vtt(subtitle_content, detected_language)
            _, summary = translate_and_summarize(translated_text)
        
            transcription = subtitle_content
            translation = translated_vtt

        else:
            subtitle_used = False
            logger.info("沒有找到合適的字幕檔案，將進行音頻提取和轉錄")
            input_video_path = 'temp_video.mp4'
            output_audio_path = 'temp_audio.mp3'
            audio_path = extract_audio(input_video_path, output_audio_path)

            if audio_path and os.path.exists(audio_path):
                logger.info(f"開始處理音頻: {audio_path}")
                transcription = transcribe_audio_with_whisper(audio_path)
                if transcription:
                    detected_language = detect_language(extract_text_from_vtt(transcription))
                    logger.info(f"檢測到的語言: {detected_language}")
                    
                    translated_vtt, translated_text = process_vtt(transcription, detected_language)
                    logger.info(f"翻譯後的VTT文本 (前100字符): {translated_vtt[:100]}...")
                    logger.info(f"翻譯後的文本 (前100字符): {translated_text[:100]}...")
                    
                    _, summary = translate_and_summarize(translated_text)
                    translation = translated_vtt
                    
                    logger.info(f"翻譯後的摘要 (前100字符): {summary[:100]}...")
                
                else:
                    logger.error("轉錄失敗")
                    translation, summary = "轉錄失敗", "無法生成摘要"
            else:
                logger.error(f"音頻提取失敗或文件不存在: {output_audio_path}")
                translation, summary = "音頻提取失敗", "無法生成摘要"

        # 處理影片截圖
        logger.info("開始處理影片截圖")
        # 檢查視頻是否已存在於資料庫
        existing_video = get_all_videos(youtube_id=video_id)
        if existing_video:
            # 移除現有的截圖
            for screenshot in existing_video['screenshots']:
                filepath = os.path.join(output_folder, screenshot['filename'])
                if os.path.exists(filepath):
                    os.remove(filepath)
                    logger.info(f"移除舊的截圖: {filepath}")

        video = cv2.VideoCapture('temp_video.mp4')
        if not video.isOpened():
            raise IOError("無法打開視頻文件")

        fps = video.get(cv2.CAP_PROP_FPS)
        total_frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))

        logger.info(f"視頻 FPS: {fps}, 總幀數: {total_frames}")

        screenshots = []
        step = int(fps * capture_interval)
        if step <= 0:
            logger.warning(f"Invalid step size calculated: {step}. Using default step of 1 second.")
            step = int(fps) if fps > 0 else 30  # 假設 30fps 如果無法獲取 fps

        for i in range(0, total_frames, step):
            video.set(cv2.CAP_PROP_POS_FRAMES, i)
            success, frame = video.read()
            if success:
                timestamp = i / fps if fps > 0 else i / 30
                filename = f"{video_id}_{timestamp:.2f}.jpg"
                filepath = os.path.join(output_folder, filename)
                cv2.imwrite(filepath, frame)
                screenshots.append({
                    'filename': filename,
                    'timestamp': f"{timestamp:.2f}s"
                })
                logger.info(f"截圖保存: {filepath}")

        video.release()

        # 移除重複的圖像
        screenshots = remove_duplicate_images(output_folder, screenshots)
        logger.info(f"去重後的截圖數量: {len(screenshots)}")
        logging.info(f"翻譯內容 (first 100 characters): {translation[:100]}")
        result = {
            'title': video_title,
            'youtube_id': video_id,
            'description': video_description,
            'creator': video_creator,
            'timestamp': video_timestamp,
            'duration': video_duration,
            'language': video_language,
            'processed_at': datetime.now().isoformat(),
            'screenshots': screenshots,
            'transcription': transcription,
            'translation': translated_vtt,
            'summary': summary,
            'subtitle_used': subtitle_used
        }

        cleanup_temp_files()

        return result
    
    except Exception as e:
        logger.error(f"處理視頻時發生錯誤: {str(e)}", exc_info=True)
        cleanup_temp_files()
        return {
            'error': str(e),
            'youtube_id': youtube_url.split('v=')[-1] if 'v=' in youtube_url else 'unknown'
        }


def download_subtitle(ydl, info, lang, subtitle_dict):
    try:
        subtitle_url = subtitle_dict[lang][0]['url']
        expected_filename = f"{info['id']}.{lang}.vtt"
        actual_filename = f"temp_video.{lang}.vtt"  # yt-dlp 使用的實際文件名

        ydl.download([subtitle_url])

        if os.path.exists(actual_filename):
            # 如果需要，重命名文件
            if actual_filename != expected_filename:
                os.rename(actual_filename, expected_filename)
            logger.info(f"成功下載字幕: {expected_filename}")
            return expected_filename
        else:
            logger.warning(f"字幕文件未找到: {actual_filename}")
            return None
    except Exception as e:
        logger.error(f"下載字幕時發生錯誤: {str(e)}")
        return None

def get_language_name(lang_code):
    language_names = {
        'zh-TW': '繁體中文（台灣）',
        'zh-Hant': '繁體中文',
        'en': '英文',
        'ko': '韓文',
        'ja': '日文'
    }
    return language_names.get(lang_code, lang_code)


# 这段代码是用于独立运行文件时，手动测试或处理指定的 YouTube 视频。这种结构允许您在命令行中运行该脚本而无需通过 Web 服务器或 API 调用

if __name__ == "__main__":
    youtube_url = "YOUR_YOUTUBE_URL"  # 這裡填寫你想處理的 YouTube 影片 URL
    output_folder = "static/screenshots"  # 存放影片截圖的資料夾
    capture_interval = 10  # 設定截圖的時間間隔（秒）

    video_info = process_video(youtube_url, output_folder, capture_interval)

    # 列印結果
    print(video_info)

    # 刪除臨時文件
    if os.path.exists('temp_video.mp4'):
        os.remove('temp_video.mp4')
        print("Removed temporary video file: temp_video.mp4")
    if os.path.exists('temp_audio.mp3'):
        os.remove('temp_audio.mp3')
        print("Removed temporary audio file: temp_audio.mp3")
