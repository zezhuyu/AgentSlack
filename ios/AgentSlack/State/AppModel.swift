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
    @Published private(set) var chats: [ChatSummary] = []
    @Published var selectedChat: Chat?
    @Published private(set) var streamingReplies: [StreamingReply] = []
    @Published var isLoading = false
    @Published var isSending = false
    @Published var errorMessage: String?
    @Published private(set) var startupConnectionState: StartupConnectionState

    private let defaults: UserDefaults
    private let connectionsKey = "agent-slack-ios-connections-v1"

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

    var selectedConnection: SavedConnection? {
        connections.first { $0.id == selectedConnectionID }
    }

    var selectedWorkspace: AgentServer? {
        workspaces.first { $0.id == selectedWorkspaceID }
    }

    var api: AgentSlackAPI? {
        selectedConnection.map { AgentSlackAPI(baseURL: $0.baseURL) }
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
                chats = []
                selectedChat = nil
            }
        } catch {
            present(error)
        }
    }

    func selectWorkspace(_ workspace: AgentServer) async {
        guard workspace.available else { return }
        selectedWorkspaceID = workspace.id
        agents = []
        chats = []
        selectedChat = nil
        await loadWorkspace()
    }

    func loadWorkspace() async {
        guard let api, let workspaceID = selectedWorkspaceID else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            async let loadedAgents = api.agents(workspaceID: workspaceID)
            async let loadedChats = api.chats(workspaceID: workspaceID)
            let (newAgents, newChats) = try await (loadedAgents, loadedChats)
            agents = newAgents
            chats = newChats
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
            selectedChat = try await api.chat(summary.id, workspaceID: workspaceID)
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

    func send(_ body: String, mentionedAgentIDs: [String] = []) async {
        let trimmed = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              let api,
              let workspaceID = selectedWorkspaceID,
              let chat = selectedChat,
              !isSending else { return }

        isSending = true
        streamingReplies = []
        defer { isSending = false }
        do {
            selectedChat = try await api.postMessage(trimmed, chatID: chat.id, workspaceID: workspaceID)
            let selectedAgentIDs = mentionedAgentIDs.isEmpty ? chat.memberIDs : mentionedAgentIDs
            let runRequest = selectedAgentIDs.count > 1 && !mentionedAgentIDs.isEmpty
                ? RunRequest.meeting(agentIDs: selectedAgentIDs, objective: trimmed)
                : RunRequest.response(agentIDs: selectedAgentIDs, objective: trimmed)
            for try await event in api.streamReply(
                chatID: chat.id,
                workspaceID: workspaceID,
                runRequest: runRequest
            ) {
                applyRunEvent(event)
            }
            selectedChat = try await api.chat(chat.id, workspaceID: workspaceID)
            chats = try await api.chats(workspaceID: workspaceID)
            streamingReplies = []
        } catch {
            present(error)
            await refreshSelectedChat()
        }
    }

    func refreshSelectedChat() async {
        guard !isSending,
              let api,
              let workspaceID = selectedWorkspaceID,
              let chatID = selectedChat?.id else { return }
        do {
            selectedChat = try await api.chat(chatID, workspaceID: workspaceID)
            chats = try await api.chats(workspaceID: workspaceID)
        } catch {
            // Polling failures are intentionally quiet; an explicit user action will surface the error.
        }
    }

    func dismissError() {
        errorMessage = nil
    }

    func applyRunEvent(_ event: RunEvent) {
        switch event.type {
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
        case "error", "agent_failed":
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
        chats = []
        selectedChat = nil
        streamingReplies = []
    }

    private func present(_ error: Error) {
        errorMessage = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
    }

    private func connectionMessage(for error: Error) -> String {
        if let urlError = error as? URLError, urlError.code == .timedOut {
            return "The request timed out. Confirm that Agent Slack has Local Network access in iPad Settings, that the iPad and Mac are on the same network, and then retry."
        }
        let detail = (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        return "The backend server could not be reached. Check that Agent Slack is running, then retry or modify the URL.\n\n\(detail)"
    }
}
