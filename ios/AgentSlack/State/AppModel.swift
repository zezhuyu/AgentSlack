import Foundation
import SwiftUI

enum StartupConnectionState: Equatable {
    case checking
    case needsConnection(message: String?)
    case connected
}

@MainActor
final class AppModel: ObservableObject {
    @Published private(set) var connections: [SavedConnection] = []
    @Published var selectedConnectionID: UUID?
    @Published private(set) var workspaces: [AgentServer] = []
    @Published var selectedWorkspaceID: String?
    @Published private(set) var agents: [AgentProfile] = []
    @Published private(set) var orchestratorIDs: [String] = []
    @Published private(set) var chats: [ChatSummary] = []
    @Published var selectedChat: Chat?
    @Published private(set) var streamingReplies: [StreamingReply] = []
    @Published private(set) var activeRunID: String?
    @Published private(set) var activeRunChatID: String?
    @Published private(set) var activeRunChatIDs: Set<String> = []
    @Published private(set) var stoppingTaskIDs: Set<String> = []
    @Published private(set) var finishedTaskIDs: Set<String> = []
    @Published private(set) var submittingQuestionKeys: Set<String> = []
    @Published private(set) var isStoppingRun = false
    @Published var isLoading = false
    @Published var isSending = false
    @Published var errorMessage: String?
    @Published private(set) var startupConnectionState: StartupConnectionState

    private let defaults: UserDefaults
    private let connectionsKey = "agent-slack-ios-connections-v1"
    private var activeStreamTask: Task<Void, Error>?
    private var suppressNextStreamTransportError = false

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let data = defaults.data(forKey: connectionsKey),
           let decoded = try? JSONDecoder().decode([SavedConnection].self, from: data) {
            connections = decoded
            selectedConnectionID = decoded.first?.id
            startupConnectionState = decoded.isEmpty ? .needsConnection(message: nil) : .checking
        } else {
            startupConnectionState = .needsConnection(message: nil)
        }
    }

#if DEBUG
    func configureVisualFixture(chat: Chat, agents: [AgentProfile]) {
        selectedChat = chat
        self.agents = agents
    }
#endif

    var selectedConnection: SavedConnection? {
        connections.first { $0.id == selectedConnectionID }
    }

    var selectedWorkspace: AgentServer? {
        workspaces.first { $0.id == selectedWorkspaceID }
    }

    var api: AgentSlackAPI? {
        selectedConnection.map { AgentSlackAPI(baseURL: $0.baseURL) }
    }

    var canStartRun: Bool {
        guard !isSending, activeRunID == nil else { return false }
        guard let chatID = selectedChat?.id else { return true }
        return !activeRunChatIDs.contains(chatID)
    }

    func prepareForBackground() {
        guard activeStreamTask != nil || isSending else { return }
        suppressNextStreamTransportError = true
        activeStreamTask?.cancel()
        isSending = false
    }

    func resumeAfterForeground() async {
        if let task = activeStreamTask {
            task.cancel()
            _ = try? await task.value
        }
        isSending = false
        await refreshSelectedChat()
    }

    func bootstrap() async {
        guard let api else {
            startupConnectionState = .needsConnection(message: nil)
            return
        }
        startupConnectionState = .checking
        do {
            _ = try await api.verify()
            startupConnectionState = .connected
            await loadWorkspaces()
        } catch {
            clearWorkspaceState()
            startupConnectionState = .needsConnection(message: connectionMessage(for: error))
        }
    }

    func connectFromStartup(address: String) async -> Bool {
        do {
            let url = try ServerEndpoint.normalize(address)
            _ = try await AgentSlackAPI(baseURL: url).verify()
            if let selectedConnectionID,
               let index = connections.firstIndex(where: { $0.id == selectedConnectionID }) {
                connections[index].baseURL = url
            } else {
                let connection = SavedConnection(name: url.host ?? "Agent Slack", baseURL: url)
                connections.append(connection)
                selectedConnectionID = connection.id
            }
            persistConnections()
            startupConnectionState = .connected
            clearWorkspaceState()
            await loadWorkspaces()
            return true
        } catch {
            startupConnectionState = .needsConnection(message: connectionMessage(for: error))
            return false
        }
    }

    func addConnection(name: String, address: String) async -> Bool {
        do {
            let url = try ServerEndpoint.normalize(address)
            let client = AgentSlackAPI(baseURL: url)
            _ = try await client.verify()
            let displayName = name.trimmingCharacters(in: .whitespacesAndNewlines)
            if let index = connections.firstIndex(where: { $0.baseURL == url }) {
                connections[index].name = displayName.isEmpty ? (url.host ?? "Agent Slack") : displayName
                selectedConnectionID = connections[index].id
            } else {
                let connection = SavedConnection(
                    name: displayName.isEmpty ? (url.host ?? "Agent Slack") : displayName,
                    baseURL: url
                )
                connections.append(connection)
                selectedConnectionID = connection.id
            }
            persistConnections()
            startupConnectionState = .connected
            await loadWorkspaces()
            return true
        } catch {
            present(error)
            return false
        }
    }

    func removeConnection(_ connection: SavedConnection) {
        connections.removeAll { $0.id == connection.id }
        if selectedConnectionID == connection.id {
            selectedConnectionID = connections.first?.id
            clearWorkspaceState()
            if selectedConnectionID != nil {
                startupConnectionState = .checking
                Task { await bootstrap() }
            } else {
                startupConnectionState = .needsConnection(message: nil)
            }
        }
        persistConnections()
    }

    func selectConnection(_ connection: SavedConnection) async {
        guard selectedConnectionID != connection.id else { return }
        selectedConnectionID = connection.id
        clearWorkspaceState()
        await loadWorkspaces()
    }

    func loadWorkspaces() async {
        guard let api else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await api.servers()
            workspaces = response.servers
            let retained = workspaces.contains { $0.id == selectedWorkspaceID }
            if !retained {
                selectedWorkspaceID = response.activeServerID ?? workspaces.first(where: \.available)?.id
            }
            if selectedWorkspaceID != nil {
                await loadWorkspace()
            } else {
                agents = []
                orchestratorIDs = []
                chats = []
                selectedChat = nil
                activeRunChatIDs = []
                clearRunState()
            }
        } catch {
            present(error)
        }
    }

    func selectWorkspace(_ workspace: AgentServer) async {
        guard workspace.available else { return }
        selectedWorkspaceID = workspace.id
        agents = []
        orchestratorIDs = []
        chats = []
        selectedChat = nil
        activeRunChatIDs = []
        clearRunState()
        await loadWorkspace()
    }

    func loadWorkspace() async {
        guard let api, let workspaceID = selectedWorkspaceID else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            // Chats are the primary workspace payload. Optional status
            // endpoints must not prevent the conversation list from loading.
            let newChats = try await api.chats(workspaceID: workspaceID)
            chats = newChats

            do { agents = try await api.agents(workspaceID: workspaceID) }
            catch { agents = [] }
            do {
                let health = try await api.health(workspaceID: workspaceID)
                orchestratorIDs = health.architecture?.orchestratorIDs ?? []
            } catch { orchestratorIDs = [] }
            do {
                let runs = try await api.activeRuns(workspaceID: workspaceID)
                activeRunChatIDs = Set(runs.map(\.chatID))
            } catch {
                activeRunChatIDs = []
                clearRunState()
            }
#if DEBUG
            if ProcessInfo.processInfo.arguments.contains("--open-first-chat"),
               selectedChat == nil,
               let firstChat = newChats.first {
                selectedChat = try await api.chat(firstChat.id, workspaceID: workspaceID)
            }
#endif
            if let currentID = selectedChat?.id, newChats.contains(where: { $0.id == currentID }) {
                selectedChat = try await api.chat(currentID, workspaceID: workspaceID)
            }
        } catch {
            present(error)
        }
    }

    func openChat(_ summary: ChatSummary) async {
        guard let api, let workspaceID = selectedWorkspaceID else { return }
        do {
            // Opening a chat must not depend on the optional run/status poll.
            // A stale run, temporary network failure, or server restart should
            // never make an otherwise readable conversation appear broken.
            selectedChat = try await api.chat(summary.id, workspaceID: workspaceID)
            do {
                let runs = try await api.activeRuns(workspaceID: workspaceID)
                activeRunChatIDs = Set(runs.map(\.chatID))
                applyActiveRun(runs.first { $0.chatID == summary.id })
            } catch {
                activeRunChatIDs = []
                clearRunState()
            }
        } catch {
            present(error)
        }
    }

    func openDirectMessage(with agent: AgentProfile) async {
        if let existing = chats.first(where: {
            $0.kind == "direct" && $0.memberIDs == [agent.id]
        }) {
            await openChat(existing)
            return
        }
        await createChat(title: agent.title, memberIDs: [agent.id])
    }

    func createChat(title: String, memberIDs: [String]) async {
        guard let api, let workspaceID = selectedWorkspaceID, !memberIDs.isEmpty else { return }
        do {
            let chat = try await api.createChat(
                CreateChatRequest(
                    title: title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "New conversation" : title,
                    memberIDs: memberIDs,
                    kind: memberIDs.count == 1 ? "direct" : "group"
                ),
                workspaceID: workspaceID
            )
            selectedChat = chat
            chats = try await api.chats(workspaceID: workspaceID)
        } catch {
            present(error)
        }
    }

    func deleteChat(_ summary: ChatSummary) async {
        guard let api, let workspaceID = selectedWorkspaceID else { return }
        do {
            _ = try await api.deleteChat(summary.id, workspaceID: workspaceID)
            chats = try await api.chats(workspaceID: workspaceID)
            if selectedChat?.id == summary.id {
                selectedChat = nil
                clearRunState()
            }
        } catch {
            present(error)
        }
    }

    func send(
        _ body: String,
        attachments: [PendingAttachmentUpload] = [],
        mentionedAgentIDs: [String] = []
    ) async -> Bool {
        let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (!trimmed.isEmpty || !attachments.isEmpty),
              let api,
              let workspaceID = selectedWorkspaceID,
              let chat = selectedChat,
              canStartRun else { return false }

        suppressNextStreamTransportError = false
        errorMessage = nil
        isSending = true
        streamingReplies = []
        do {
            let uploadedAttachmentIDs = try await uploadAttachments(
                attachments,
                api: api,
                workspaceID: workspaceID
            )
            let runObjective = trimmed.isEmpty
                ? attachmentOnlyObjective(for: attachments)
                : trimmed
            try await runTrackedStream {
                self.selectedChat = try await api.postMessage(
                    trimmed,
                    attachmentIDs: uploadedAttachmentIDs,
                    chatID: chat.id,
                    workspaceID: workspaceID
                )
                let runRequest = RunRouting.request(
                    chat: chat,
                    mentionedAgentIDs: mentionedAgentIDs,
                    orchestratorIDs: self.orchestratorIDs,
                    objective: runObjective
                )
                var runChatID = chat.id
                for try await event in api.streamReply(
                    chatID: chat.id,
                    workspaceID: workspaceID,
                    runRequest: runRequest
                ) {
                    try Task.checkCancellation()
                    if event.type == "meeting_created", let meetingChatID = event.chatID {
                        runChatID = meetingChatID
                        self.selectedChat = try await api.chat(meetingChatID, workspaceID: workspaceID)
                        self.chats = try await api.chats(workspaceID: workspaceID)
                    }
                    self.applyRunEvent(event)
                }
                try Task.checkCancellation()
                self.selectedChat = try await api.chat(runChatID, workspaceID: workspaceID)
                self.chats = try await api.chats(workspaceID: workspaceID)
                self.streamingReplies = []
                self.clearRunState()
            }
        } catch {
            isSending = false
            if !shouldSuppressStreamError(error) {
                present(error)
                await refreshSelectedChat()
            }
            return false
        }
        suppressNextStreamTransportError = false
        isSending = false
        return true
    }

    func refreshSelectedChat() async {
        guard !isSending,
              let api,
              let workspaceID = selectedWorkspaceID,
              let chatID = selectedChat?.id else { return }
        do {
            async let loadedChat = api.chat(chatID, workspaceID: workspaceID)
            async let loadedRuns = api.activeRuns(workspaceID: workspaceID)
            selectedChat = try await loadedChat
            chats = try await api.chats(workspaceID: workspaceID)
            let runs = try await loadedRuns
            activeRunChatIDs = Set(runs.map(\.chatID))
            applyActiveRun(runs.first { $0.chatID == chatID })
        } catch {
            // Polling failures are intentionally quiet; an explicit user action will surface the error.
        }
    }

    func stopAgent(taskID: String) async {
        guard let api,
              let workspaceID = selectedWorkspaceID,
              let runID = activeRunID,
              !stoppingTaskIDs.contains(taskID) else { return }
        stoppingTaskIDs.insert(taskID)
        do {
            _ = try await api.cancelTask(taskID, runID: runID, workspaceID: workspaceID)
        } catch {
            stoppingTaskIDs.remove(taskID)
            present(error)
        }
    }

    func stopCurrentRun() async {
        guard let api,
              let workspaceID = selectedWorkspaceID,
              let runID = activeRunID,
              !isStoppingRun else { return }
        isStoppingRun = true
        do {
            _ = try await api.cancelRun(runID, workspaceID: workspaceID)
        } catch {
            isStoppingRun = false
            present(error)
        }
    }

    func isStoppingTask(_ taskID: String) -> Bool {
        stoppingTaskIDs.contains(taskID)
    }

    func isRunningTask(_ taskID: String) -> Bool {
        activeRunID != nil && !finishedTaskIDs.contains(taskID)
    }

    func isSubmittingQuestion(chatID: String, questionID: String) -> Bool {
        submittingQuestionKeys.contains(questionKey(chatID: chatID, questionID: questionID))
    }

    func answerQuestion(
        _ interaction: ChatInteraction,
        optionIDs: [String]
    ) async {
        guard !optionIDs.isEmpty,
              interaction.isPendingChoice,
              let api,
              let workspaceID = selectedWorkspaceID,
              let chat = selectedChat else { return }

        let key = questionKey(chatID: chat.id, questionID: interaction.questionID)
        guard !submittingQuestionKeys.contains(key) else { return }

        errorMessage = nil
        submittingQuestionKeys.insert(key)
        defer { submittingQuestionKeys.remove(key) }

        do {
            let updatedChat = try await api.answerQuestion(
                chatID: chat.id,
                questionID: interaction.questionID,
                optionIDs: optionIDs,
                workspaceID: workspaceID
            )
            selectedChat = updatedChat

            let selectedLabels = interaction.options
                .filter { optionIDs.contains($0.id) }
                .map(\.label)
            let objective = InteractionFollowUp.objective(
                prompt: interaction.prompt,
                optionLabels: selectedLabels.isEmpty ? optionIDs : selectedLabels
            )
            suppressNextStreamTransportError = false
            try await runTrackedStream {
                try await self.continueRun(
                    chat: updatedChat,
                    workspaceID: workspaceID,
                    objective: objective,
                    mentionedAgentIDs: []
                )
            }
        } catch {
            if !shouldSuppressStreamError(error) {
                present(error)
                await refreshSelectedChat()
            }
        }
    }

    func dismissError() {
        errorMessage = nil
    }

    func presentAttachmentError(_ message: String) {
        errorMessage = message
    }

    func applyRunEvent(_ event: RunEvent) {
        switch event.type {
        case "run_started":
            activeRunID = event.runID
            activeRunChatID = event.chatID
            if let chatID = event.chatID {
                activeRunChatIDs.insert(chatID)
            }
            isStoppingRun = false
        case "agent_started":
            let taskID = event.taskID ?? event.agentID ?? UUID().uuidString
            if !streamingReplies.contains(where: { $0.id == taskID }) {
                streamingReplies.append(
                    StreamingReply(
                        id: taskID,
                        agentID: event.agentID ?? "agent",
                        agentLabel: event.agentLabel ?? event.agentID ?? "Agent",
                        text: ""
                    )
                )
            }
        case "delta":
            let taskID = event.taskID ?? event.agentID ?? "agent"
            if let index = streamingReplies.firstIndex(where: { $0.id == taskID }) {
                streamingReplies[index].text += event.text ?? ""
            } else {
                streamingReplies.append(
                    StreamingReply(
                        id: taskID,
                        agentID: event.agentID ?? "agent",
                        agentLabel: event.agentLabel ?? event.agentID ?? "Agent",
                        text: event.text ?? ""
                    )
                )
            }
        case "agent_completed":
            if let taskID = event.taskID {
                finishedTaskIDs.insert(taskID)
            }
        case "agent_cancelled":
            if let taskID = event.taskID {
                stoppingTaskIDs.remove(taskID)
                finishedTaskIDs.insert(taskID)
            }
        case "run_completed", "run_cancelled":
            if let chatID = activeRunChatID {
                activeRunChatIDs.remove(chatID)
            }
            clearRunState()
        case "agent_failed":
            if let taskID = event.taskID {
                finishedTaskIDs.insert(taskID)
            }
            errorMessage = event.message ?? "The agent run failed."
        case "error":
            errorMessage = event.message ?? "The agent run failed."
        default:
            break
        }
    }

    private func persistConnections() {
        guard let data = try? JSONEncoder().encode(connections) else { return }
        defaults.set(data, forKey: connectionsKey)
    }

    private func clearWorkspaceState() {
        workspaces = []
        selectedWorkspaceID = nil
        agents = []
        orchestratorIDs = []
        chats = []
        selectedChat = nil
        activeRunChatIDs = []
        clearRunState()
    }

    private func applyActiveRun(_ run: ActiveRun?) {
        guard let run else {
            clearRunState()
            return
        }
        activeRunID = run.id
        activeRunChatID = run.chatID
        isStoppingRun = run.status == "stopping"
        streamingReplies = run.tasks
            .filter { $0.status == "running" || $0.status == "stopping" }
            .map {
                StreamingReply(
                    id: $0.id,
                    agentID: $0.agentID,
                    agentLabel: $0.agentLabel,
                    text: $0.text
                )
            }
        stoppingTaskIDs = Set(run.tasks.filter { $0.status == "stopping" }.map(\.id))
        finishedTaskIDs = Set(run.tasks.filter { $0.status != "running" && $0.status != "stopping" }.map(\.id))
    }

    private func clearRunState() {
        activeRunID = nil
        activeRunChatID = nil
        streamingReplies = []
        stoppingTaskIDs = []
        finishedTaskIDs = []
        isStoppingRun = false
    }

    private func present(_ error: Error) {
        errorMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }

    private func runTrackedStream(
        _ operation: @escaping @MainActor () async throws -> Void
    ) async throws {
        let task = Task {
            try await operation()
        }
        activeStreamTask = task
        defer { activeStreamTask = nil }
        try await task.value
    }

    private func shouldSuppressStreamError(_ error: Error) -> Bool {
        if error is CancellationError {
            suppressNextStreamTransportError = false
            return true
        }
        guard suppressNextStreamTransportError,
              let urlError = error as? URLError,
              [.cancelled, .timedOut, .networkConnectionLost, .notConnectedToInternet]
                .contains(urlError.code) else {
            suppressNextStreamTransportError = false
            return false
        }
        suppressNextStreamTransportError = false
        return true
    }

    private func questionKey(chatID: String, questionID: String) -> String {
        "\(chatID):\(questionID)"
    }

    private func uploadAttachments(
        _ attachments: [PendingAttachmentUpload],
        api: AgentSlackAPI,
        workspaceID: String
    ) async throws -> [String] {
        guard !attachments.isEmpty else { return [] }
        var uploadedIDs: [String] = []
        uploadedIDs.reserveCapacity(attachments.count)
        for attachment in attachments {
            let uploaded = try await api.uploadAttachment(attachment, workspaceID: workspaceID)
            uploadedIDs.append(uploaded.attachmentID)
        }
        return uploadedIDs
    }

    private func attachmentOnlyObjective(for attachments: [PendingAttachmentUpload]) -> String {
        let filenames = attachments.map(\.filename)
        if filenames.count == 1, let filename = filenames.first {
            return "Review the attached file \(filename)."
        }
        return "Review the attached files: \(filenames.joined(separator: ", "))."
    }

    private func continueRun(
        chat: Chat,
        workspaceID: String,
        objective: String,
        mentionedAgentIDs: [String]
    ) async throws {
        guard let api else { return }
        isSending = true
        streamingReplies = []
        var runChatID = chat.id
        defer { isSending = false }

        let runRequest = RunRouting.request(
            chat: chat,
            mentionedAgentIDs: mentionedAgentIDs,
            orchestratorIDs: orchestratorIDs,
            objective: objective
        )
        for try await event in api.streamReply(
            chatID: chat.id,
            workspaceID: workspaceID,
            runRequest: runRequest
        ) {
            if event.type == "meeting_created", let meetingChatID = event.chatID {
                runChatID = meetingChatID
                selectedChat = try await api.chat(meetingChatID, workspaceID: workspaceID)
                chats = try await api.chats(workspaceID: workspaceID)
            }
            applyRunEvent(event)
        }
        selectedChat = try await api.chat(runChatID, workspaceID: workspaceID)
        chats = try await api.chats(workspaceID: workspaceID)
        streamingReplies = []
        clearRunState()
    }

    private func connectionMessage(for error: Error) -> String {
        if let urlError = error as? URLError, urlError.code == .timedOut {
            return "The request timed out. Confirm that Agent Slack has Local Network access in iPad Settings, that the iPad and Mac are on the same network, and then retry."
        }
        let detail = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        return "The backend server could not be reached. Check that Agent Slack is running, then retry or modify the URL.\n\n\(detail)"
    }
}
