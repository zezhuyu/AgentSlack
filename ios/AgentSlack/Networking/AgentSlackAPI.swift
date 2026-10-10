import Foundation

enum APIError: LocalizedError {
    case invalidServerURL
    case invalidResponse
    case server(status: Int, message: String)
    case incompatibleService

    var errorDescription: String? {
        switch self {
        case .invalidServerURL:
            return "Enter a valid Agent Slack server URL."
        case .invalidResponse:
            return "The server returned an unreadable response."
        case let .server(status, message):
            return "Server error \(status): \(message)"
        case .incompatibleService:
            return "This address is not an Agent Slack API v1 server."
        }
    }
}

enum ServerEndpoint {
    static func normalize(_ value: String) throws -> URL {
        var candidate = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !candidate.isEmpty else { throw APIError.invalidServerURL }
        if !candidate.contains("://") {
            candidate = "http://\(candidate)"
        }
        guard var components = URLComponents(string: candidate),
              let host = components.host,
              !host.isEmpty else {
            throw APIError.invalidServerURL
        }
        var path = components.path
        while path.hasSuffix("/") { path.removeLast() }
        if path.hasSuffix("/api/v1") {
            path.removeLast("/api/v1".count)
        }
        components.path = path
        components.query = nil
        components.fragment = nil
        guard let url = components.url else { throw APIError.invalidServerURL }
        return url
    }
}

struct AgentSlackAPI {
    static let streamingTimeout: TimeInterval = 6 * 60 * 60

    let baseURL: URL
    var session: URLSession = .agentSlack

    func verify() async throws -> ServiceDocument {
        let document: ServiceDocument = try await request(
            path: "",
            method: "GET",
            workspaceID: nil,
            body: Optional<String>.none,
            timeoutInterval: 20
        )
        guard document.service == "agent-slack", document.apiVersion == "1" else {
            throw APIError.incompatibleService
        }
        return document
    }

    func servers() async throws -> ServerListResponse {
        try await request(path: "servers", method: "GET", workspaceID: nil, body: Optional<String>.none)
    }

    func health(workspaceID: String) async throws -> HealthResponse {
        try await request(path: "health", method: "GET", workspaceID: workspaceID, body: Optional<String>.none)
    }

    func agents(workspaceID: String) async throws -> [AgentProfile] {
        let response: AgentsResponse = try await request(path: "agents", method: "GET", workspaceID: workspaceID, body: Optional<String>.none)
        return response.agents
    }

    func chats(workspaceID: String) async throws -> [ChatSummary] {
        let response: ChatsResponse = try await request(path: "chats", method: "GET", workspaceID: workspaceID, body: Optional<String>.none)
        return response.chats
    }

    func chat(_ chatID: String, workspaceID: String) async throws -> Chat {
        try await request(path: "chats/\(chatID)", method: "GET", workspaceID: workspaceID, body: Optional<String>.none)
    }

    func createChat(_ payload: CreateChatRequest, workspaceID: String) async throws -> Chat {
        try await request(path: "chats", method: "POST", workspaceID: workspaceID, body: payload)
    }

    func deleteChat(_ chatID: String, workspaceID: String) async throws -> DeleteChatResponse {
        try await request(
            path: "chats/\(chatID)",
            method: "DELETE",
            workspaceID: workspaceID,
            body: Optional<String>.none
        )
    }

    func postMessage(_ body: String, chatID: String, workspaceID: String, idempotencyKey: String = UUID().uuidString) async throws -> Chat {
        try await request(
            path: "chats/\(chatID)/messages",
            method: "POST",
            workspaceID: workspaceID,
            body: PostMessageRequest(body: body, idempotencyKey: idempotencyKey)
        )
    }

    func postMessage(
        _ body: String,
        attachmentIDs: [String],
        chatID: String,
        workspaceID: String,
        idempotencyKey: String = UUID().uuidString
    ) async throws -> Chat {
        try await request(
            path: "chats/\(chatID)/messages",
            method: "POST",
            workspaceID: workspaceID,
            body: PostMessageRequest(
                body: body,
                attachmentIDs: attachmentIDs.isEmpty ? nil : attachmentIDs,
                idempotencyKey: idempotencyKey
            )
        )
    }

    func uploadAttachment(
        _ attachment: PendingAttachmentUpload,
        workspaceID: String
    ) async throws -> UploadedAttachment {
        var request = try makeRequest(
            path: "uploads",
            method: "POST",
            workspaceID: workspaceID
        )
        request.httpBody = attachment.data
        request.setValue(attachment.contentType, forHTTPHeaderField: "Content-Type")
        request.setValue(
            attachment.filename.agentSlackPercentEncodedFilename,
            forHTTPHeaderField: "X-Agent-Slack-Filename"
        )
        let (data, response) = try await requestBytes(request)
        try validate(response: response, data: data)
        do {
            return try JSONDecoder().decode(UploadedAttachment.self, from: data)
        } catch {
            throw APIError.invalidResponse
        }
    }

    func transcribeAudio(_ data: Data, filename: String = "voice.wav", workspaceID: String) async throws -> String {
        var request = try makeRequest(path: "voice/transcribe", method: "POST", workspaceID: workspaceID)
        request.httpBody = data
        request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        request.setValue(filename.agentSlackPercentEncodedFilename, forHTTPHeaderField: "X-Agent-Slack-Filename")
        let (data, response) = try await requestBytes(request)
        try validate(response: response, data: data)
        let result = try JSONDecoder().decode(VoiceTranscriptionResponse.self, from: data)
        return result.text
    }

    func createVoiceSession(sourceChatID: String, workspaceID: String) async throws -> Chat {
        try await request(
            path: "voice/sessions",
            method: "POST",
            workspaceID: workspaceID,
            body: ["source_chat_id": sourceChatID]
        )
    }

    func streamVoiceTurn(
        _ data: Data,
        filename: String = "voice.wav",
        chatID: String,
        workspaceID: String,
        requestID: String = UUID().uuidString
    ) -> AsyncThrowingStream<VoiceTurnEvent, Error> {
        AsyncThrowingStream { continuation in
            let producer = Task {
                do {
                    var request = try makeRequest(path: "voice/turns", method: "POST", workspaceID: workspaceID, timeoutInterval: Self.streamingTimeout)
                    request.httpBody = data
                    request.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
                    request.setValue(filename.agentSlackPercentEncodedFilename, forHTTPHeaderField: "X-Agent-Slack-Filename")
                    request.setValue(chatID, forHTTPHeaderField: "X-Agent-Slack-Voice-Chat")
                    request.setValue(requestID, forHTTPHeaderField: "Idempotency-Key")
                    let (bytes, response) = try await session.bytes(for: request)
                    try validate(response: response, data: nil)
                    for try await line in bytes.lines where !line.isEmpty {
                        guard let data = line.data(using: .utf8) else { continue }
                        continuation.yield(try JSONDecoder().decode(VoiceTurnEvent.self, from: data))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { @Sendable _ in producer.cancel() }
        }
    }

    func speechData(path: String) async throws -> Data {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else { throw APIError.invalidResponse }
        let (data, response) = try await session.data(from: url)
        try validate(response: response, data: data)
        return data
    }

    func answerQuestion(
        chatID: String,
        questionID: String,
        optionIDs: [String],
        workspaceID: String
    ) async throws -> Chat {
        try await request(
            path: "chats/\(chatID)/questions/\(questionID)/answer",
            method: "POST",
            workspaceID: workspaceID,
            body: AnswerQuestionRequest(optionIDs: optionIDs)
        )
    }

    func streamReply(chatID: String, workspaceID: String, runRequest: RunRequest) -> AsyncThrowingStream<RunEvent, Error> {
        AsyncThrowingStream { continuation in
            let producer = Task {
                do {
                    var request = try makeRequest(
                        path: "chats/\(chatID)/run-stream",
                        method: "POST",
                        workspaceID: workspaceID,
                        timeoutInterval: Self.streamingTimeout
                    )
                    request.httpBody = try JSONEncoder().encode(runRequest)
                    let (bytes, response) = try await session.bytes(for: request)
                    try validate(response: response, data: nil)
                    for try await line in bytes.lines where !line.isEmpty {
                        guard let data = line.data(using: .utf8) else { continue }
                        continuation.yield(try JSONDecoder().decode(RunEvent.self, from: data))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { @Sendable _ in
                producer.cancel()
            }
        }
    }

    func activeRuns(workspaceID: String) async throws -> [ActiveRun] {
        let response: ActiveRunsResponse = try await request(
            path: "runs/active",
            method: "GET",
            workspaceID: workspaceID,
            body: Optional<String>.none
        )
        return response.runs
    }

    func cancelRun(_ runID: String, workspaceID: String) async throws -> CancellationResponse {
        try await request(
            path: "runs/\(runID)/cancel",
            method: "POST",
            workspaceID: workspaceID,
            body: Optional<String>.none
        )
    }

    func cancelTask(_ taskID: String, runID: String, workspaceID: String) async throws -> CancellationResponse {
        try await request(
            path: "runs/\(runID)/tasks/\(taskID)/cancel",
            method: "POST",
            workspaceID: workspaceID,
            body: Optional<String>.none
        )
    }

    func chatSessions(workspaceID: String) async throws -> [IOSChatSession] {
        let response: ChatSessionsResponse = try await request(
            path: "chat/sessions", method: "GET", workspaceID: workspaceID, body: Optional<String>.none
        )
        return response.sessions
    }

    func createChatSession(title: String = "New chat", workspaceID: String) async throws -> IOSChatSession {
        try await request(path: "chat/sessions", method: "POST", workspaceID: workspaceID, body: ["title": title])
    }

    func chatSession(_ sessionID: String, workspaceID: String) async throws -> IOSChatSession {
        try await request(path: "chat/sessions/\(sessionID)", method: "GET", workspaceID: workspaceID, body: Optional<String>.none)
    }

    func streamChatModeMessage(
        _ text: String,
        sessionID: String,
        workspaceID: String
    ) -> AsyncThrowingStream<IOSChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let producer = Task {
                do {
                    var request = try makeRequest(
                        path: "chat/sessions/\(sessionID)/messages",
                        method: "POST",
                        workspaceID: workspaceID,
                        timeoutInterval: Self.streamingTimeout
                    )
                    request.httpBody = try JSONEncoder().encode(["text": text, "modality": "text"])
                    let (bytes, response) = try await session.bytes(for: request)
                    try validate(response: response, data: nil)
                    for try await line in bytes.lines where !line.isEmpty {
                        guard let data = line.data(using: .utf8) else { continue }
                        continuation.yield(try JSONDecoder().decode(IOSChatEvent.self, from: data))
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { @Sendable _ in producer.cancel() }
        }
    }

    func cancelChatResponse(_ responseID: String, sessionID: String, workspaceID: String) async throws {
        _ = try await request(
            path: "chat/sessions/\(sessionID)/cancel",
            method: "POST",
            workspaceID: workspaceID,
            body: ["response_id": responseID]
        ) as CancellationResponse
    }

    func materializeFile(url: URL, filename: String, workspaceID: String) async throws -> URL {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = Self.streamingTimeout
        request.setValue("1", forHTTPHeaderField: "X-Agent-Slack-Api-Version")
        request.setValue(workspaceID, forHTTPHeaderField: "X-Agent-Slack-Server")
        let (data, response) = try await requestBytes(request)
        try validate(response: response, data: data)
        let safeName = filename.replacingOccurrences(of: "/", with: "_")
        let destination = FileManager.default.temporaryDirectory
            .appendingPathComponent("agent-slack-preview-\(UUID().uuidString)-\(safeName)")
        try? FileManager.default.removeItem(at: destination)
        try data.write(to: destination, options: .atomic)
        return destination
    }

    private func requestBytes(_ request: URLRequest) async throws -> (Data, URLResponse) {
        // Use the completion-handler API deliberately. The async URLSession
        // bridge propagates SwiftUI row-task cancellation into the network
        // request, which made visible previews fail with NSURLErrorCancelled.
        try await withCheckedThrowingContinuation { continuation in
            let task = session.dataTask(with: request) { data, response, error in
                if let error {
                    continuation.resume(throwing: error)
                } else if let data, let response {
                    continuation.resume(returning: (data, response))
                } else {
                    continuation.resume(throwing: APIError.invalidResponse)
                }
            }
            task.resume()
        }
    }

    func imageURL(path: String?, revision: String? = nil) -> URL? {
        guard let path, !path.isEmpty else { return nil }
        let resolvedURL: URL?
        if let absolute = URL(string: path), absolute.scheme != nil {
            resolvedURL = absolute
        } else {
            resolvedURL = URL(string: path, relativeTo: baseURL)?.absoluteURL
        }
        guard let resolvedURL, let revision, !revision.isEmpty,
              var components = URLComponents(url: resolvedURL, resolvingAgainstBaseURL: false) else {
            return resolvedURL
        }
        var queryItems = components.queryItems ?? []
        queryItems.append(URLQueryItem(name: "revision", value: revision))
        components.queryItems = queryItems
        return components.url
    }

    func fileURL(for attachment: UploadedAttachment) -> URL? {
        if let path = attachment.url, let url = URL(string: path, relativeTo: baseURL)?.absoluteURL {
            return url
        }
        var url = baseURL.appendingPathComponent("api/v1/uploads")
            .appendingPathComponent(attachment.attachmentID)
        return url
    }

    func workspaceFileURL(forCitedPath rawPath: String) -> URL? {
        var components = URLComponents(url: baseURL.appendingPathComponent("api/v1/files"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "path", value: rawPath)]
        return components?.url
    }

    private func request<Response: Decodable, Body: Encodable>(
        path: String,
        method: String,
        workspaceID: String?,
        body: Body?,
        timeoutInterval: TimeInterval = 190
    ) async throws -> Response {
        var request = try makeRequest(
            path: path,
            method: method,
            workspaceID: workspaceID,
            timeoutInterval: timeoutInterval
        )
        if let body {
            request.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await session.data(for: request)
        try validate(response: response, data: data)
        do {
            return try JSONDecoder().decode(Response.self, from: data)
        } catch {
            throw APIError.invalidResponse
        }
    }

    private func makeRequest(
        path: String,
        method: String,
        workspaceID: String?,
        timeoutInterval: TimeInterval = 190
    ) throws -> URLRequest {
        var url = baseURL.appendingPathComponent("api/v1")
        if !path.isEmpty { url.append(path: path) }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeoutInterval
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("1", forHTTPHeaderField: "X-Agent-Slack-Api-Version")
        if let workspaceID {
            request.setValue(workspaceID, forHTTPHeaderField: "X-Agent-Slack-Server")
        }
        return request
    }

    private func validate(response: URLResponse, data: Data?) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            let message: String
            if let data,
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let error = object["error"] as? String {
                message = error
            } else {
                message = HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            }
            throw APIError.server(status: http.statusCode, message: message)
        }
    }
}

struct ChatSessionsResponse: Decodable {
    let sessions: [IOSChatSession]
}

private extension URLSession {
    static let agentSlack: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = AgentSlackAPI.streamingTimeout
        return URLSession(configuration: configuration)
    }()
}

private extension String {
    var agentSlackPercentEncodedFilename: String {
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
        return addingPercentEncoding(withAllowedCharacters: allowed) ?? self
    }
}
