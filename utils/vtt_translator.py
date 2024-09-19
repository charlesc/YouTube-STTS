import re
import logging
from openai import OpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



# 全局變量用於儲存模型名稱（gemma2:9b,llama3.1:latest）
MODEL_NAME = "gemma2:9b"


# 初始化 Ollama API 客戶端
client = OpenAI(base_url='http://localhost:11434/v1/', api_key='ollama')


def set_model(model_name):
    global MODEL_NAME
    MODEL_NAME = model_name
    logger.info(f"Model set to: {MODEL_NAME}")
    
def detect_language(text):
    detect_language_response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{
            "role": "user",
            "content": f"Please detect the language of the following text and respond with only the language name in English: {text[:200]}"
        }]
    )
    return detect_language_response.choices[0].message.content.strip().lower()



def translate_text(text, source_language, target_language):
    if source_language == target_language:
        return text
    
    translate_response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{
            "role": "user",
            "content": f"請將以下 {source_language} 的內容翻譯為 {target_language}。請保持原文的段落結構，直接翻譯，不要做其他任何回覆或說明: {text}"
        }]
    )
    return translate_response.choices[0].message.content



def process_vtt(vtt_content, source_language):
    # 分離 WEBVTT 標頭和內容
    parts = vtt_content.split('\n\n', 1)
    header = parts[0]
    content = parts[1] if len(parts) > 1 else ""

    # 使用正則表達式來分離時間戳和文本內容
    pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3})\n((?:(?!\n\d{2}:\d{2}:\d{2}\.\d{3}).)+)'
    matches = re.findall(pattern, content, re.DOTALL)
    
    translated_vtt = header + "\n\n"
    all_text = ""
    
    # 检查是否为中文
    if "chinese" in source_language.lower() or "taiwanese mandarin" in source_language.lower():
        # 如果是中文，直接使用原文
        translated_vtt = vtt_content
        for _, text in matches:
            all_text += text.strip() + " "
    else:
        # 非中文时才进行翻译
        translated_vtt = header + "\n\n"
        for timestamp, text in matches:
            translated_text = translate_text(text.strip(), source_language, "Traditional Chinese")
            translated_vtt += f"{timestamp}\n{translated_text}\n\n"
            all_text += translated_text + " "
    
    return translated_vtt.strip(), all_text.strip()

def translate_and_summarize(text):
    source_language = detect_language(text)
    
    if "chinese" in source_language or "taiwanese mandarin" in source_language:
        translated_text = text
    else:
        translated_text = translate_text(text, source_language, "Traditional Chinese")
    
    summary_response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[{
            "role": "user",
            "content": f"""請根據以下影片轉錄文字稿生成一份簡潔的繁體中文摘要（直接回答，不要做其他說明或評論，並提供HTML格式的內容，例如<ul><li>）。摘要應包含以下內容:

            1. 影片的主要主題或目的
            2. 3-5個關鍵要點或主要論點
            3. 任何重要的結論或呼籲行動
            4. 總結全文的簡短段落

            請基於以下內容生成：：
            {translated_text}
            """
        }])
    
    summary = summary_response.choices[0].message.content
    return translated_text, summary



def extract_text_from_vtt(vtt_content):
    lines = vtt_content.split('\n')
    text_lines = []
    for line in lines:
        if not line.strip() or '-->' in line or line.strip() == 'WEBVTT':
            continue
        text_lines.append(line.strip())
    return ' '.join(text_lines)