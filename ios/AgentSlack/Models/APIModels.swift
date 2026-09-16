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

struct HealthResponse: Decodable {
    let ok: Bool
    let architecture: ArchitectureSummary?
}

struct ArchitectureSummary: Decodable {
    let orchestratorIDs: [String]

    enum CodingKeys: String, CodingKey {
        case orchestratorIDs = "orchestrator_ids"
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
    let metadata: ChatMessageMetadata?

    var id: String { messageID }

    init(
        messageID: String,
        authorType: String,
        authorID: String?,
        authorLabel: String?,
        body: String,
        createdAt: String?,
        metadata: ChatMessageMetadata? = nil
    ) {
        self.messageID = messageID
        self.authorType = authorType
        self.authorID = authorID
        self.authorLabel = authorLabel
        self.body = body
        self.createdAt = createdAt
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case messageID = "message_id"
        case authorType = "author_type"
        case authorID = "author_id"
        case authorLabel = "author_label"
        case body
        case createdAt = "created_at"
        case metadata
    }
}

struct ChatMessageMetadata: Codable, Hashable {
    let interaction: ChatInteraction?
    let attachments: [UploadedAttachment]?

    init(
        interaction: ChatInteraction? = nil,
        attachments: [UploadedAttachment]? = nil
    ) {
        self.interaction = interaction
        self.attachments = attachments
    }
}

struct PendingAttachmentUpload: Identifiable, Equatable {
    let id: UUID
    let filename: String
    let contentType: String
    let data: Data

    init(
        id: UUID = UUID(),
        filename: String,
        contentType: String,
        data: Data
    ) {
        self.id = id
        self.filename = filename
        self.contentType = contentType
        self.data = data
    }

    var size: Int { data.count }
}

struct UploadedAttachment: Codable, Identifiable, Hashable {
    let attachmentID: String
    let filename: String
    let path: String
    let contentType: String
    let size: Int
    let url: String?

    var id: String { attachmentID }

    enum CodingKeys: String, CodingKey {
        case attachmentID = "attachment_id"
        case filename
        case path
        case contentType = "content_type"
        case size
        case url
    }
}

struct VoiceTranscriptionResponse: Decodable {
    let text: String
}

struct VoiceTurnEvent: Decodable {
    let type: String
    let text: String?
    let message: String?
    let url: String?
}

struct ChatInteraction: Codable, Hashable, Identifiable {
    let kind: String
    let questionID: String
    let prompt: String
    let options: [ChatInteractionOption]
    let multiple: Bool
    let status: String
    let selectedOptionIDs: [String]?

    var id: String { questionID }
    var isChoice: Bool { kind == "choice" && options.count >= 2 }
    var isPendingChoice: Bool { kind == "choice" && status == "pending" && !options.isEmpty }

    enum CodingKeys: String, CodingKey {
        case kind
        case questionID = "question_id"
        case prompt
        case options
        case multiple
        case status
        case selectedOptionIDs = "selected_option_ids"
    }
}

struct ChatInteractionOption: Codable, Hashable, Identifiable {
    let id: String
    let label: String
    let description: String?
}

struct RunEvent: Decodable, Identifiable {
    let type: String
    let runID: String?
    let taskID: String?
    let chatID: String?
    let sourceChatID: String?
    let agentID: String?
    let agentLabel: String?
    let title: String?
    let text: String?
    let message: String?

    var id: String { [runID, taskID, type, UUID().uuidString].compactMap { $0 }.joined(separator: ":") }

    enum CodingKeys: String, CodingKey {
        case type
        case runID = "run_id"
        case taskID = "task_id"
        case chatID = "chat_id"
        case sourceChatID = "source_chat_id"
        case agentID = "agent_id"
        case agentLabel = "agent_label"
        case title
        case text
        case message
    }
}

struct ActiveRunsResponse: Decodable {
    let runs: [ActiveRun]
}

struct ActiveRun: Decodable, Identifiable {
    let runID: String
    let chatID: String
    let mode: String
    let leadAgentID: String
    let status: String
    let startedAt: String?
    let tasks: [ActiveRunTask]

    var id: String { runID }

    enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case chatID = "chat_id"
        case mode
        case leadAgentID = "lead_agent_id"
        case status
        case startedAt = "started_at"
        case tasks
    }
}

struct ActiveRunTask: Decodable, Identifiable {
    let taskID: String
    let agentID: String
    let agentLabel: String
    let status: String
    let text: String

    var id: String { taskID }

    enum CodingKeys: String, CodingKey {
        case taskID = "task_id"
        case agentID = "agent_id"
        case agentLabel = "agent_label"
        case status
        case text
    }
}

struct StreamingReply: Identifiable, Equatable {
    let id: String
    let agentID: String
    let agentLabel: String
    var text: String
}

struct DeleteChatResponse: Decodable {
    let deleted: Bool
    let chatID: String

    enum CodingKeys: String, CodingKey {
        case deleted
        case chatID = "chat_id"
    }
}

struct CancellationResponse: Decodable {
    let accepted: Bool
    let runID: String
    let taskID: String?
    let status: String

    enum CodingKeys: String, CodingKey {
        case accepted
        case runID = "run_id"
        case taskID = "task_id"
        case status
    }
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
    let attachmentIDs: [String]?
    let idempotencyKey: String?

    init(body: String, attachmentIDs: [String]? = nil, idempotencyKey: String? = nil) {
        self.body = body
        self.attachmentIDs = attachmentIDs
        self.idempotencyKey = idempotencyKey
    }

    enum CodingKeys: String, CodingKey {
        case body
        case attachmentIDs = "attachment_ids"
        case idempotencyKey = "idempotency_key"
    }
}

struct AnswerQuestionRequest: Encodable {
    let optionIDs: [String]

    enum CodingKeys: String, CodingKey {
        case optionIDs = "option_ids"
    }
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

    static func autoMeeting(leadAgentID: String, objective: String) -> RunRequest {
        RunRequest(
            mode: "auto_meeting",
            agentIDs: nil,
            leadAgentID: leadAgentID,
            participantIDs: nil,
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

enum RunRouting {
    static func request(
        chat: Chat,
        mentionedAgentIDs: [String],
        orchestratorIDs: [String],
        objective: String
    ) -> RunRequest {
        if mentionedAgentIDs.count > 1 {
            return .meeting(agentIDs: mentionedAgentIDs, objective: objective)
        }
        if let mentionedAgentID = mentionedAgentIDs.first {
            return .response(agentIDs: [mentionedAgentID], objective: objective)
        }
        if chat.kind == "direct",
           chat.memberIDs.count == 1,
           let directAgentID = chat.memberIDs.first,
           orchestratorIDs.contains(directAgentID) {
            return .autoMeeting(leadAgentID: directAgentID, objective: objective)
        }
        if let leadAgentID = orchestratorIDs.first(where: chat.memberIDs.contains),
           chat.memberIDs.count > 1 {
            return .meeting(
                agentIDs: [leadAgentID] + chat.memberIDs.filter { $0 != leadAgentID },
                objective: objective
            )
        }
        return .response(agentIDs: chat.memberIDs, objective: objective)
    }
}

enum InteractionFollowUp {
    static func objective(prompt: String, optionLabels: [String]) -> String {
        let labels = optionLabels.joined(separator: ", ")
        return #"The user answered the question "\#(prompt)" by selecting: \#(labels). Continue from that choice."#
    }
}
