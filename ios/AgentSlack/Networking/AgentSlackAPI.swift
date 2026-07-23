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

    func postMessage(_ body: String, chatID: String, workspaceID: String) async throws -> Chat {
        try await request(
            path: "chats/\(chatID)/messages",
            method: "POST",
            workspaceID: workspaceID,
            body: PostMessageRequest(body: body)
        )
    }

    func streamReply(chatID: String, workspaceID: String, runRequest: RunRequest) -> AsyncThrowingStream<RunEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
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

private extension URLSession {
    static let agentSlack: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.waitsForConnectivity = true
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = AgentSlackAPI.streamingTimeout
        return URLSession(configuration: configuration)
    }()
}
