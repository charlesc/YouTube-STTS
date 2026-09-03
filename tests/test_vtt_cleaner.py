from utils.vtt_cleaner import clean_vtt

# 真實從 YouTube 自動字幕（o-wv_szZ0V0，這支影片完全沒有人工上傳的字幕）
# 截取的一段原始內容，保留「滾動式」格式的關鍵特徵：逐字累加的 <c> 標籤、
# 幾乎零長度的「已完成」重複 cue、時間戳記行後面的 cue setting。
_REAL_AUTO_CAPTION_SAMPLE = """WEBVTT
Kind: captions
Language: en

00:00:00.080 --> 00:00:01.910 align:start position:0%

There's<00:00:00.320><c> been</c><00:00:00.480><c> a</c><00:00:00.640><c> lot</c><00:00:00.719><c> of</c><00:00:00.960><c> misinformation</c>

00:00:01.910 --> 00:00:01.920 align:start position:0%
There's been a lot of misinformation


00:00:01.920 --> 00:00:03.110 align:start position:0%
There's been a lot of misinformation
about<00:00:02.320><c> AI.</c>

00:00:03.110 --> 00:00:03.120 align:start position:0%
about AI.


00:00:03.120 --> 00:00:05.829 align:start position:0%
about AI.
&gt;&gt; This<00:00:03.520><c> is</c><00:00:03.919><c> Andrew.</c>

00:00:05.829 --> 00:00:05.839 align:start position:0%
&gt;&gt; This is Andrew.

"""


def test_clean_vtt_removes_rolling_caption_duplication():
    result = clean_vtt(_REAL_AUTO_CAPTION_SAMPLE)

    # 重建出來的完整文字要跟原始語音內容一致、且每個片段只出現一次。
    assert result.count("There's been a lot of misinformation") == 1
    assert result.count("about AI.") == 1
    assert "misinformation about AI." in result
    assert ">> This is Andrew." in result


def test_clean_vtt_strips_inline_timing_tags_and_cue_settings():
    result = clean_vtt(_REAL_AUTO_CAPTION_SAMPLE)

    assert '<c>' not in result
    assert '<00:00:' not in result
    assert 'align:start' not in result
    assert 'position:0%' not in result


def test_clean_vtt_unescapes_html_entities():
    result = clean_vtt(_REAL_AUTO_CAPTION_SAMPLE)
    assert '&gt;' not in result
    assert '>> This is Andrew.' in result


def test_clean_vtt_is_a_no_op_on_already_clean_manual_subtitles():
    clean_manual_vtt = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.000\nHello world.\n\n"
        "00:00:02.000 --> 00:00:05.500\nThis is a normal, clean, manually uploaded subtitle file.\n\n"
        "00:00:05.500 --> 00:00:08.000\nNo repetition here.\n"
    )

    result = clean_vtt(clean_manual_vtt)

    assert "Hello world." in result
    assert "This is a normal, clean, manually uploaded subtitle file." in result
    assert "No repetition here." in result
    # 三句都以句尾標點結束，不該被合併句子的邏輯黏在一起。
    assert result.count(' --> ') == 3


def test_clean_vtt_merges_short_cues_into_sentences():
    # 模擬去重複後、還沒合併句子前的狀態：同一句話被切成好幾個 ~2 秒的片段。
    fragmented = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:02.000\nThis is\n\n"
        "00:00:02.000 --> 00:00:04.000\na single sentence\n\n"
        "00:00:04.000 --> 00:00:06.000\nsplit across cues.\n\n"
        "00:00:06.000 --> 00:00:08.000\nAnd a second one.\n"
    )

    result = clean_vtt(fragmented)

    assert "This is a single sentence split across cues." in result
    assert "And a second one." in result
    assert result.count(' --> ') == 2  # 兩句話合併成兩個 cue，而不是四個


def test_clean_vtt_handles_empty_content():
    assert clean_vtt("WEBVTT\n\n") == "WEBVTT\n"
