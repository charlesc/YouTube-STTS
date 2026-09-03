import os
import shutil
import tempfile
import yt_dlp
import cv2
import subprocess
from datetime import datetime
from utils.image_processor import remove_duplicate_images
from utils.vtt_translator import summarize, process_vtt, extract_text_from_vtt, detect_language
from database import get_all_videos
import config
import logging

logger = logging.getLogger(__name__)


def cleanup_temp_files(work_dir):
    """移除整個請求專屬的暫存工作目錄。"""
    if work_dir and os.path.exists(work_dir):
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.info(f"Removed temporary working directory: {work_dir}")


def extract_audio(input_video_path, output_audio_path):
    """從視頻中提取音頻並保存為 MP3 文件。"""
    # 如果文件已存在，可以选择先删除或者重命名
    if os.path.exists(output_audio_path):
        os.remove(output_audio_path)
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
        import mlx_whisper  # 延遲載入：僅限 Apple Silicon，且屬於重量級依賴，
                             # 只有在沒有字幕、真的需要轉錄時才載入。

        if not os.path.exists(audio_path):
            raise ValueError(f"音頻文件 '{audio_path}' 不存在。")

        file_size = os.path.getsize(audio_path)
        print(f"音頻文件大小: {file_size} 字節")

        if file_size == 0:
            raise ValueError(f"音頻文件 '{audio_path}' 為空。")

        # 使用 mlx-whisper 模型進行轉錄
        result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=config.WHISPER_MODEL_REPO)

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
    # 每次請求都用獨立的暫存工作目錄，避免多支影片同時處理時互相覆寫
    # temp_video.mp4 / temp_audio.mp3 / 字幕檔等固定檔名。
    work_dir = tempfile.mkdtemp(prefix='ytstts_')
    video_path = os.path.join(work_dir, 'video.mp4')

    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
        'outtmpl': os.path.join(work_dir, 'video.%(ext)s'),
        'writesubtitles': True,
        'subtitleslangs': config.SUBTITLE_LANGS,
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

        if not os.path.exists(video_path):
            raise FileNotFoundError("視頻文件未成功下載")

        subtitle_path = None
        subtitle_used = False

        for lang in config.SUBTITLE_LANGS:
            potential_subtitle_path = os.path.join(work_dir, f"video.{lang}.vtt")
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
            translation, translated_text = process_vtt(subtitle_content, detected_language)
            summary = summarize(translated_text)

            transcription = subtitle_content

        else:
            subtitle_used = False
            logger.info("沒有找到合適的字幕檔案，將進行音頻提取和轉錄")
            output_audio_path = os.path.join(work_dir, 'audio.mp3')
            audio_path = extract_audio(video_path, output_audio_path)

            if audio_path and os.path.exists(audio_path):
                logger.info(f"開始處理音頻: {audio_path}")
                transcription = transcribe_audio_with_whisper(audio_path)
                if transcription:
                    detected_language = detect_language(extract_text_from_vtt(transcription))
                    logger.info(f"檢測到的語言: {detected_language}")

                    translation, translated_text = process_vtt(transcription, detected_language)
                    logger.info(f"翻譯後的VTT文本 (前100字符): {translation[:100]}...")
                    logger.info(f"翻譯後的文本 (前100字符): {translated_text[:100]}...")

                    summary = summarize(translated_text)

                    logger.info(f"翻譯後的摘要 (前100字符): {summary[:100]}...")

                else:
                    logger.error("轉錄失敗")
                    translation, summary = "轉錄失敗", "無法生成摘要"
            else:
                logger.error(f"音頻提取失敗或文件不存在: {output_audio_path}")
                transcription = ''
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

        video = cv2.VideoCapture(video_path)
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
        logger.info(f"翻譯內容 (first 100 characters): {translation[:100]}")
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
            'translation': translation,
            'summary': summary,
            'subtitle_used': subtitle_used
        }

        return result

    except Exception as e:
        logger.error(f"處理視頻時發生錯誤: {str(e)}", exc_info=True)
        return {
            'error': str(e),
            'youtube_id': youtube_url.split('v=')[-1] if 'v=' in youtube_url else 'unknown'
        }
    finally:
        cleanup_temp_files(work_dir)


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
