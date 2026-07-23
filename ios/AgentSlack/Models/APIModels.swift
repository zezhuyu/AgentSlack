import Foundation

struct SavedConnection: Codable, Identifiable, Hashable {
    let id: UUID
    var name: String
    var baseURL: URL

    init(id: UUID = UUID(), name: String, baseURL: URL) {
        self.id = id
        self.name = name
        self.baseURL = baseURL
    }
}

struct ServiceDocument: Decodable {
    let service: String
    let apiVersion: String

    enum CodingKeys: String, CodingKey {
        case service
        case apiVersion = "api_version"
    }
}

struct ServerListResponse: Decodable {
    let servers: [AgentServer]
    let activeServerID: String?

    enum CodingKeys: String, CodingKey {
        case servers
        case activeServerID = "active_server_id"
    }
}

struct AgentServer: Codable, Identifiable, Hashable {
    let serverID: String
    let name: String
    let projectName: String?
    let runner: String?
    let model: String?
    let available: Bool
    let active: Bool
    let logoURL: String?
    let logoRevision: String?

    var id: String { serverID }

    enum CodingKeys: String, CodingKey {
        case serverID = "server_id"
        case name
        case projectName = "project_name"
        case runner
        case model
        case available
        case active
        case logoURL = "logo_url"
        case logoRevision = "logo_revision"
    }
}

struct AgentsResponse: Decodable {
    let agents: [AgentProfile]
}

struct AgentProfile: Codable, Identifiable, Hashable {
    let agentID: String
    let name: String?
    let title: String
    let summary: String?
    let group: String?
    let kind: String?

    var id: String { agentID }

    enum CodingKeys: String, CodingKey {
        case agentID = "agent_id"
        case name
        case title
        case summary
        case group
        case kind
    }
}

struct ChatsResponse: Decodable {
    let chats: [ChatSummary]
}

struct ChatSummary: Codable, Identifiable, Hashable {
    let chatID: String
    let title: String
    let kind: String
    let memberIDs: [String]
    let memberTitles: [String]?
    let lastMessagePreview: String?
    let messageCount: Int?
    let updatedAt: String?

    var id: String { chatID }

    enum CodingKeys: String, CodingKey {
        case chatID = "chat_id"
        case title
        case kind
        case memberIDs = "member_ids"
        case memberTitles = "member_titles"
        case lastMessagePreview = "last_message_preview"
        case messageCount = "message_count"
        case updatedAt = "updated_at"
    }
}

struct Chat: Codable, Identifiable, Hashable {
    let chatID: String
    let title: String
    let kind: String
    let memberIDs: [String]
    let messages: [ChatMessage]
    let createdAt: String?
    let updatedAt: String?

    var id: String { chatID }

    enum CodingKeys: String, CodingKey {
        case chatID = "chat_id"
        case title
        case kind
        case memberIDs = "member_ids"
        case messages
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

struct ChatMessage: Codable, Identifiable, Hashable {
    let messageID: String
    let authorType: String
    let authorID: String?
    let authorLabel: String?
    let body: String
    let createdAt: String?

    var id: String { messageID }

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case authorType = "author_type"
        case authorID = "author_id"
        case authorLabel = "author_label"
        case body
        case createdAt = "created_at"
    }
}

struct RunEvent: Decodable, Identifiable {
    let type: String
    let runID: String?
    let taskID: String?
    let agentID: String?
    let agentLabel: String?
    let text: String?
    let message: String?

    var id: String { [runID, taskID, type, UUID().uuidString].compactMap { $0 }.joined(separator: ":") }

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case taskID = "task_id"
        case agentID = "agent_id"
        case agentLabel = "agent_label"
        case text
        case message
    }
}

struct StreamingReply: Identifiable, Equatable {
    let id: String
    let agentID: String
    let agentLabel: String
    var text: String
}

struct CreateChatRequest: Encodable {
    let title: String
    let memberIDs: [String]
    let kind: String

    enum CodingKeys: String, CodingKey {
        case title
        case memberIDs = "member_ids"
        case kind
    }
}

struct PostMessageRequest: Encodable {
    let body: String
}

struct RunRequest: Encodable {
    let mode: String
    let agentIDs: [String]?
    let leadAgentID: String?
    let participantIDs: [String]?
    let objective: String?

    static func response(agentIDs: [String], objective: String) -> RunRequest {
        RunRequest(
            mode: "respond",
            agentIDs: agentIDs,
            leadAgentID: nil,
            participantIDs: nil,
            objective: objective
        )
    }

    static func meeting(agentIDs: [String], objective: String) -> RunRequest {
        RunRequest(
            mode: "meeting",
            agentIDs: nil,
            leadAgentID: agentIDs.first,
            participantIDs: agentIDs,
            objective: objective
        )
    }

    enum CodingKeys: String, CodingKey {
        case mode
        case agentIDs = "agent_ids"
        case leadAgentID = "lead_agent_id"
        case participantIDs = "participant_ids"
        case objective
    }
}
