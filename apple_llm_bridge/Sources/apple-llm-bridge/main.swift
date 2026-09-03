import Foundation
import FoundationModels
import NaturalLanguage
import Speech
import AVFoundation

// 極簡的 stdin(JSON) -> stdout(JSON) 橋接工具，讓 Python 端可以用 subprocess
// 呼叫地端的 Apple 框架做翻譯/摘要/語言偵測/語音轉錄，不需要引入完整的
// Xcode App 或額外的 HTTP server。
//
// 輸入（stdin，一行 JSON）：
//   生成（翻譯/摘要，預設模式）：{"mode": "generate", "prompt": "..."}（"mode" 可省略）
//   語言偵測：{"mode": "detect_language", "text": "..."}
//   語音轉錄：{"mode": "transcribe", "audio_path": "...", "locale": "en-US"}
// 輸出（stdout，一行 JSON）：
//   生成成功：{"status": "ok", "content": "..."}
//   語言偵測成功：{"status": "ok", "language": "..."}
//   轉錄成功：{"status": "ok", "segments": [{"start": 0.0, "end": 1.91, "text": "..."}]}
//   失敗：{"status": "error", "error": "...", "reason":
//     "unavailable" | "generation_failed" | "invalid_input" |
//     "guardrail_violation" | "refusal_detected" | "unsupported_locale"}
//
// 注意：故意不要用 `Task { ... } + DispatchSemaphore.wait()` 包裝——
// 這個組合在主執行緒上會死鎖（FoundationModels 的非同步呼叫似乎需要用到主執行緒，
// 而 semaphore.wait() 又把主執行緒同步卡住，兩邊互相等待）。改用最上層程式碼直接
// `await`，讓 Swift 用內建的 async main 機制執行，實測不會有這個問題。

struct Request: Decodable {
    // 注意：`Decodable` 自動合成的 init(from:) 不會套用屬性預設值——沒給
    // "mode" 這個 key 就會直接解碼失敗，跟一般 Swift 建構子的預設值行為不同。
    // 用 Optional 讓這個欄位可以缺席，缺席時在下面 dispatch 時當成 "generate"。
    var mode: String?
    var prompt: String?
    var text: String?
    var audio_path: String?
    var locale: String?
}

struct GenerateSuccessResponse: Encodable {
    let status = "ok"
    let content: String
}

struct LanguageSuccessResponse: Encodable {
    let status = "ok"
    let language: String
}

struct TranscribeSegment: Encodable {
    let start: Double
    let end: Double
    let text: String
}

struct TranscribeSuccessResponse: Encodable {
    let status = "ok"
    let segments: [TranscribeSegment]
}

struct ErrorResponse: Encodable {
    let status = "error"
    let error: String
    let reason: String
}

func writeJSON<T: Encodable>(_ value: T) {
    let encoder = JSONEncoder()
    if let data = try? encoder.encode(value), let text = String(data: data, encoding: .utf8) {
        print(text)
    } else {
        // 連錯誤訊息都編碼失敗時的最後防線，避免輸出空白讓呼叫端解析 JSON 失敗。
        print("{\"status\": \"error\", \"error\": \"internal encoding failure\", \"reason\": \"generation_failed\"}")
    }
}

func fail(_ message: String, reason: String) -> Never {
    writeJSON(ErrorResponse(error: message, reason: reason))
    exit(1)
}

// MARK: - 語言偵測（NaturalLanguage / NLLanguageRecognizer）
//
// 刻意不用 FoundationModels 做語言偵測：NLLanguageRecognizer 是專門的統計式
// 語言辨識器，不是生成式 LLM，沒有 guardrail 這回事，也不會有「拒答」的問題，
// 對短句、簡繁中文的分辨也比通用語言偵測套件準（實測「歡迎收看今天的影片...」
// 這種短句能以 99.99% 信心值判斷為 zh-Hant，langdetect 之前會誤判成韓文）。

let _languageNames: [String: String] = [
    "zh-Hans": "Simplified Chinese",
    "zh-Hant": "Traditional Chinese",
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
]

func detectLanguage(_ text: String) -> String {
    let recognizer = NLLanguageRecognizer()
    recognizer.processString(text)
    guard let language = recognizer.dominantLanguage else {
        return "Unknown"
    }
    return _languageNames[language.rawValue] ?? language.rawValue
}

// MARK: - 生成（翻譯/摘要，FoundationModels）

// .permissiveContentTransformations：語意是「這是內容轉換（翻譯/改寫），不是
// 生成全新內容」，guardrail 會比預設寬鬆很多——實測預設 guardrails 在處理新聞
// 時事類主題（例如貧富差距、民主議題）的翻譯時,偶發性地會被判定為
// guardrailViolation 而拒絕生成（同一段文字重跑不一定每次都會觸發，
// 看起來跟取樣結果有關,不是每次都可重現）。放寬這一層不會讓安全防護變嚴格，
// 只會維持或降低擋下率,所以沒有下修安全性的疑慮。
let generationModel = SystemLanguageModel(guardrails: .permissiveContentTransformations)

// 即使放寬了 guardrails,FoundationModels 仍然可能不丟出 guardrailViolation
// exception,而是直接用「客氣拒絕」的自然語言文字回應（例如 "I'm sorry, but I
// can't help with that."）取代真正的翻譯結果——這種「軟性拒絕」不會被
// `catch` 抓到,必須另外檢查生成出來的文字本身。這裡的判斷刻意用「開頭是否為
// 已知的拒絕語句」而非全文搜尋,降低誤判風險：影片對白本身翻譯出來的內容也可能
// 包含「很抱歉」「對不起」這類詞,但不太可能整段翻譯結果「一開始」就是完整的
// 拒絕句型。
let refusalPrefixes = [
    "i'm sorry, but i can't", "i am sorry, but i can't",
    "i can't help with that", "i cannot help with that",
    "i can't assist with that", "i cannot assist with that",
    "i'm unable to help with that", "i am unable to help with that",
    "as an ai", "i'm just an ai", "i am just an ai",
    "很抱歉，我無法", "很抱歉,我無法", "抱歉，我無法", "抱歉,我無法",
    "我無法協助處理這個請求", "我不能協助處理這個請求",
    "身為一個 ai", "作為一個 ai", "身為一個人工智慧", "作為一個人工智慧",
]

func looksLikeRefusal(_ text: String) -> Bool {
    let normalized = text.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    return refusalPrefixes.contains { normalized.hasPrefix($0.lowercased()) }
}

func generate(_ prompt: String) async {
    switch generationModel.availability {
    case .available:
        break
    case .unavailable(let reason):
        fail("Apple Intelligence 目前無法使用：\(reason)", reason: "unavailable")
    @unknown default:
        fail("Apple Intelligence 狀態未知", reason: "unavailable")
    }

    do {
        let session = LanguageModelSession(model: generationModel)
        // maximumResponseTokens 上限：實測發現 exceededContextWindowSize
        // 不是單純「輸入太長」造成的——同一段輸入文字重跑，有時候會失敗、
        // 有時候不會，且輸入字數跟是否失敗沒有穩定的對應關係。真正原因是
        // context window（4096 token）算的是輸入加輸出的總和，模型偶爾會
        // 生成異常冗長或跑掉的回應（呼應 Python 那邊已知的重複迴圈退化
        // 問題），把輸出長度也一起吃光了額度。限制回應長度上限可以同時
        // 從根本降低這兩個問題的發生機率。
        let options = GenerationOptions(maximumResponseTokens: 1024)
        let response = try await session.respond(to: prompt, options: options)

        if looksLikeRefusal(response.content) {
            fail(
                "模型以自然語言婉拒回應（未拋出 guardrailViolation exception）：\(response.content.prefix(200))",
                reason: "refusal_detected"
            )
        }

        writeJSON(GenerateSuccessResponse(content: response.content))
    } catch {
        // FoundationModels 內建、無法完全關閉的內容安全防護：政治／社會爭議性主題
        // （例如貧富差距、民主危機這類新聞/時事內容）即使只是「翻譯」也可能被擋下。
        // 這跟一般生成失敗（暫時性、可重試）性質不同，用獨立的 reason 讓呼叫端可以
        // 分開處理（例如記錄下來、換一段落跳過，或改走其他後端），而不是當成同一種錯誤。
        let description = "\(error)"
        if description.contains("guardrailViolation") {
            fail("內容被 Apple Intelligence 的安全防護擋下（可能觸及政治/社會敏感主題）：\(error)", reason: "guardrail_violation")
        } else {
            fail("生成失敗：\(error)", reason: "generation_failed")
        }
    }
}

// MARK: - 語音轉錄（Speech / SpeechAnalyzer + SpeechTranscriber）
//
// 這是 macOS 26 新的語音辨識 API（跟舊版 SFSpeechRecognizer 是不同東西），
// 完全地端執行、不是生成式模型，沒有 guardrail 疑慮。實測過 SpeechTranscriber
// 支援的語言（zh-TW/zh-CN/zh-HK/en-*/ja-JP/ko-KR 等）剛好涵蓋這個 app 需要的
// 語言；轉錄品質對乾淨語音（TTS）幾乎完全正確，對真實世界音訊（背景音、
// 口語含糊）也可用，跟 whisper 一樣會有偶發的辨識誤差。

func transcribe(audioPath: String, localeIdentifier: String) async {
    let locale = Locale(identifier: localeIdentifier)
    let supported = await SpeechTranscriber.supportedLocales
    guard supported.contains(where: { $0.identifier(.bcp47) == locale.identifier(.bcp47) }) else {
        fail("SpeechAnalyzer 不支援語言: \(localeIdentifier)", reason: "unsupported_locale")
    }

    let transcriber = SpeechTranscriber(
        locale: locale,
        transcriptionOptions: [],
        reportingOptions: [],
        attributeOptions: [.audioTimeRange]
    )
    let analyzer = SpeechAnalyzer(modules: [transcriber])

    do {
        // 對應語言的模型資源第一次使用時可能需要下載（實測常見語言通常
        // 已經隨系統安裝好，這裡呼叫是保險，已安裝的話幾乎立即回傳）。
        if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            try await request.downloadAndInstall()
        }

        let audioFile = try AVAudioFile(forReading: URL(fileURLWithPath: audioPath))

        // Task 閉包直接回傳收集到的結果，而不是捕捉、修改外層的 var——
        // Swift 嚴格併發檢查會把後者當成潛在的資料競爭擋下來。
        let resultsTask = Task { () -> [TranscribeSegment] in
            var collected: [TranscribeSegment] = []
            for try await result in transcriber.results where result.isFinal {
                let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
                guard !text.isEmpty else { continue }
                let start = result.range.start.seconds
                let end = (result.range.start + result.range.duration).seconds
                collected.append(TranscribeSegment(start: start, end: end, text: text))
            }
            return collected
        }

        _ = try await analyzer.analyzeSequence(from: audioFile)
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        let segments = try await resultsTask.value

        writeJSON(TranscribeSuccessResponse(segments: segments))
    } catch {
        fail("語音轉錄失敗：\(error)", reason: "generation_failed")
    }
}

// MARK: - 進入點

let inputData = FileHandle.standardInput.readDataToEndOfFile()
guard let request = try? JSONDecoder().decode(Request.self, from: inputData) else {
    fail("無法解析輸入的 JSON", reason: "invalid_input")
}

switch request.mode ?? "generate" {
case "detect_language":
    guard let text = request.text else {
        fail("detect_language 模式需要 {\"text\": \"...\"}", reason: "invalid_input")
    }
    writeJSON(LanguageSuccessResponse(language: detectLanguage(text)))
case "generate":
    guard let prompt = request.prompt else {
        fail("generate 模式需要 {\"prompt\": \"...\"}", reason: "invalid_input")
    }
    await generate(prompt)
case "transcribe":
    guard let audioPath = request.audio_path, let localeIdentifier = request.locale else {
        fail("transcribe 模式需要 {\"audio_path\": \"...\", \"locale\": \"...\"}", reason: "invalid_input")
    }
    await transcribe(audioPath: audioPath, localeIdentifier: localeIdentifier)
default:
    fail("未知的 mode: \(request.mode ?? "nil")", reason: "invalid_input")
}
