"""清理 YouTube 自動產生字幕（auto-generated captions）的「滾動式」VTT 格式。

YouTube 的自動字幕跟人工上傳的字幕格式差很多：同一句話會用逐字累加的方式
重複出現好幾次（打字機效果 + 一行滾動顯示），時間戳記行後面還會多出
` align:start position:0%` 這類 cue setting。這個模組把它轉成跟人工字幕
一樣乾淨、不重複的 VTT，讓 utils/vtt_translator.py 既有的正則表達式解析
邏輯可以直接處理，不用另外分兩套。

實測範例（原始）：
    00:00:00.080 --> 00:00:01.910 align:start position:0%
    There's<00:00:00.320><c> been</c><00:00:00.480><c> a</c>...

    00:00:01.910 --> 00:00:01.920 align:start position:0%
    There's been a lot of misinformation

    00:00:01.920 --> 00:00:03.110 align:start position:0%
    There's been a lot of misinformation
    about<00:00:02.320><c> AI.</c>
    ...（每次累加都重複前面已經顯示過的字）

清理後：
    00:00:01.910 --> 00:00:03.120
    There's been a lot of misinformation about AI.

對已經是乾淨格式的人工字幕（沒有內嵌標籤、每個 cue 已經以句尾標點結束）
執行這個函式基本上是無副作用的 no-op，所以不需要另外判斷「這是不是自動
字幕」——所有下載到的字幕檔都可以統一先過一次這個函式再往下用。
"""
import re
import html

_TIMESTAMP_RE = re.compile(r'^(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})')
_TAG_RE = re.compile(r'<[^>]*>')
_SENTENCE_END_RE = re.compile(r'[.!?]["\')\]]?$')

# 把連續的 cue 合併成完整句子的時長上限（秒）。YouTube 自動字幕去重複後
# 大約每 2 秒一個 cue，常常從句子中間硬切，不適合直接拿去逐句翻譯（少了
# 上下文、批次翻譯的 API 呼叫次數也會暴增）；遇到句尾標點或超過這個時長
# 就切下一句，讓最終粒度接近一般人工字幕的「一句話一個 cue」。
_MAX_MERGED_DURATION_SECONDS = 15.0


def _parse_cues(vtt_content):
    """解析 VTT 內容為 (header, [(start, end, text), ...])，text 已經去除
    <...> 標籤並做過 HTML unescape，但還沒去重複／合併。"""
    lines = vtt_content.replace('\r\n', '\n').split('\n')

    header_lines = []
    i = 0
    while i < len(lines) and '-->' not in lines[i]:
        header_lines.append(lines[i])
        i += 1

    cues = []
    while i < len(lines):
        m = _TIMESTAMP_RE.match(lines[i])
        if not m:
            i += 1
            continue
        start, end = m.group(1), m.group(2)
        i += 1
        text_lines = []
        while i < len(lines) and lines[i].strip() != '' and '-->' not in lines[i]:
            text_lines.append(lines[i])
            i += 1
        raw_text = ' '.join(text_lines)
        clean_text = html.unescape(_TAG_RE.sub('', raw_text)).strip()
        clean_text = re.sub(r'\s+', ' ', clean_text)
        if clean_text:
            cues.append((start, end, clean_text))

    header = '\n'.join(header_lines).strip() or 'WEBVTT'
    return header, cues


def _new_words(prev_words, curr_words):
    """找出 prev_words 的字尾跟 curr_words 的字首最長重疊的部分，回傳
    curr_words 扣掉重疊部分後、真正新增的字。用來去除滾動字幕的重複——
    每個 cue 通常是「前一個 cue 的內容 + 新增的幾個字」。"""
    max_overlap = min(len(prev_words), len(curr_words))
    for overlap_len in range(max_overlap, 0, -1):
        if prev_words[-overlap_len:] == curr_words[:overlap_len]:
            return curr_words[overlap_len:]
    return curr_words


def _dedupe_rolling_captions(cues):
    deduped = []
    prev_words = []
    for start, end, text in cues:
        words = text.split(' ')
        new = _new_words(prev_words, words)
        prev_words = words
        if not new:
            # 這個 cue 沒有任何新內容（純粹重複前一句），把時間範圍併進
            # 上一個保留下來的 cue，而不是整個丟掉時間資訊。
            if deduped:
                deduped[-1] = (deduped[-1][0], end, deduped[-1][2])
            continue
        deduped.append((start, end, ' '.join(new)))
    return deduped


def _to_seconds(timestamp):
    h, m, s = timestamp.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


def _merge_into_sentences(cues, max_duration_seconds=_MAX_MERGED_DURATION_SECONDS):
    result = []
    buf_start, buf_end, buf_words = None, None, []

    for start, end, text in cues:
        if buf_start is None:
            buf_start = start
        buf_end = end
        buf_words.append(text)

        duration = _to_seconds(end) - _to_seconds(buf_start)
        if _SENTENCE_END_RE.search(text) or duration >= max_duration_seconds:
            result.append((buf_start, buf_end, ' '.join(buf_words)))
            buf_start, buf_end, buf_words = None, None, []

    if buf_words:
        result.append((buf_start, buf_end, ' '.join(buf_words)))

    return result


def clean_vtt(vtt_content):
    """把（可能是滾動式自動字幕的）VTT 內容轉成乾淨、去重複、以句子為
    單位的 VTT 字串。對已經乾淨的人工字幕基本上是 no-op。"""
    header, cues = _parse_cues(vtt_content)
    cues = _dedupe_rolling_captions(cues)
    cues = _merge_into_sentences(cues)

    body = '\n\n'.join(f'{start} --> {end}\n{text}' for start, end, text in cues)
    return f'{header}\n\n{body}\n' if body else f'{header}\n'
