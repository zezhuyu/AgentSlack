import Foundation
import SwiftUI

struct ChatView: View {
    @ObservedObject var model: AppModel
    @State private var draft = ChatView.initialDraft
    @FocusState private var composerFocused: Bool

    private static var initialDraft: String {
#if DEBUG
        ProcessInfo.processInfo.arguments.contains("--show-mention-picker") ? "Ask @" : ""
#else
        ""
#endif
    }

    var body: some View {
        VStack(spacing: 0) {
            messages
            Divider()
            composer
        }
        .background(SlackTheme.conversationBackground.ignoresSafeArea())
        .navigationTitle(model.selectedChat?.title ?? "Conversation")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: model.selectedChat?.id) {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(2))
                await model.refreshSelectedChat()
            }
        }
    }

    private var messages: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 2) {
                    ForEach(visibleMessages) { message in
                        MessageRow(
                            author: message.authorLabel ?? message.authorID ?? "Agent",
                            messageBody: message.body,
                            timestamp: message.createdAt,
                            isUser: message.authorType == "user",
                            isStreaming: false
                        )
                        .id(message.id)
                    }
                    ForEach(model.streamingReplies) { reply in
                        MessageRow(
                            author: reply.agentLabel,
                            messageBody: reply.text,
                            timestamp: nil,
                            isUser: false,
                            isStreaming: true
                        )
                        .id("stream-\(reply.id)")
                    }
                }
                .padding(.vertical, 8)
            }
            .background(SlackTheme.conversationBackground)
            .onChange(of: visibleMessages.count + model.streamingReplies.count) { _, _ in
                if let target = model.streamingReplies.last.map({ "stream-\($0.id)" }) ?? visibleMessages.last?.id {
                    withAnimation(.easeOut(duration: 0.18)) { proxy.scrollTo(target, anchor: .bottom) }
                }
            }
        }
    }

    private var visibleMessages: [ChatMessage] {
        (model.selectedChat?.messages ?? []).filter { $0.authorType != "system" }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !mentionSuggestions.isEmpty {
                mentionPicker
            }

            if !mentionedAgents.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(mentionedAgents) { agent in
                            Label(agent.title, systemImage: "at")
                                .font(.caption.bold())
                                .foregroundStyle(SlackTheme.interactiveAccent)
                                .padding(.horizontal, 9)
                                .padding(.vertical, 5)
                                .background(SlackTheme.composerField, in: Capsule())
                                .overlay { Capsule().stroke(SlackTheme.divider) }
                                .accessibilityLabel("Selected agent \(agent.title)")
                        }
                    }
                }
            }

            HStack(alignment: .bottom, spacing: 10) {
                TextField("Message — use @ to address people", text: $draft, axis: .vertical)
                    .lineLimit(1...6)
                    .textFieldStyle(.plain)
                    .foregroundStyle(SlackTheme.conversationText)
                    .padding(.horizontal, 13)
                    .padding(.vertical, 10)
                    .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 12))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(SlackTheme.divider)
                    }
                    .focused($composerFocused)
                    .submitLabel(mentionSuggestions.isEmpty ? .send : .continue)
                    .onSubmit {
                        if let first = mentionSuggestions.first {
                            insertMention(first)
                        } else {
                            submit()
                        }
                    }

                Button(action: submit) {
                    Group {
                        if model.isSending { ProgressView().tint(.white) }
                        else { Image(systemName: "arrow.up").font(.headline.bold()) }
                    }
                    .frame(width: 42, height: 42)
                    .background(canSend ? SlackTheme.accent : Color.secondary.opacity(0.3), in: Circle())
                    .foregroundStyle(.white)
                }
                .disabled(!canSend)
                .accessibilityLabel(model.isSending ? "Agents are working" : "Send message")
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(SlackTheme.conversationBackground)
    }

    private var mentionPicker: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(mentionSuggestions) { agent in
                    Button {
                        insertMention(agent)
                    } label: {
                        HStack(spacing: 10) {
                            AvatarView(label: agent.title, color: SlackTheme.accent)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(agent.title)
                                    .font(.subheadline.bold())
                                    .foregroundStyle(SlackTheme.conversationText)
                                Text("@\(agent.id)")
                                    .font(.caption.monospaced())
                                    .foregroundStyle(SlackTheme.mutedText)
                            }
                            Spacer()
                            Image(systemName: "plus.circle")
                                .foregroundStyle(SlackTheme.interactiveAccent)
                        }
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Address \(agent.title), at \(agent.id)")

                    if agent.id != mentionSuggestions.last?.id {
                        Divider().padding(.leading, 52)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: 260)
        .background(SlackTheme.agentBubble, in: RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).stroke(SlackTheme.divider) }
    }

    private var mentionedAgentIDs: [String] {
        MentionSupport.resolveAgentIDs(in: draft, agents: model.agents)
    }

    private var mentionedAgents: [AgentProfile] {
        mentionedAgentIDs.compactMap { id in model.agents.first { $0.id == id } }
    }

    private var mentionSuggestions: [AgentProfile] {
        guard let context = MentionSupport.queryAtEnd(in: draft) else { return [] }
        let selected = Set(mentionedAgentIDs)
        return Array(
            MentionSupport.matchingAgents(model.agents, query: context.query)
                .filter { !selected.contains($0.id) }
                .prefix(6)
        )
    }

    private var canSend: Bool {
        !model.isSending && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func submit() {
        guard canSend else { return }
        let message = draft
        let targets = mentionedAgentIDs
        draft = ""
        Task { await model.send(message, mentionedAgentIDs: targets) }
    }

    private func insertMention(_ agent: AgentProfile) {
        guard let context = MentionSupport.queryAtEnd(in: draft) else { return }
        draft = MentionSupport.inserting(agentID: agent.id, into: draft, context: context)
        composerFocused = true
    }
}

struct MentionContext: Equatable {
    let query: String
    let range: NSRange
}

enum MentionSupport {
    private static let queryExpression = try! NSRegularExpression(
        pattern: #"(?:^|\s)@([A-Za-z0-9_.-]*)$"#
    )
    private static let tokenExpression = try! NSRegularExpression(
        pattern: #"(?:^|\s)@([A-Za-z0-9_.-]+)"#
    )

    static func queryAtEnd(in value: String) -> MentionContext? {
        let text = value as NSString
        let fullRange = NSRange(location: 0, length: text.length)
        guard let match = queryExpression.firstMatch(in: value, range: fullRange) else { return nil }
        let queryRange = match.range(at: 1)
        guard queryRange.location != NSNotFound else { return nil }
        return MentionContext(
            query: text.substring(with: queryRange),
            range: NSRange(location: queryRange.location - 1, length: queryRange.length + 1)
        )
    }

    static func matchingAgents(_ agents: [AgentProfile], query: String) -> [AgentProfile] {
        let needle = query.lowercased()
        guard !needle.isEmpty else { return agents }
        return agents.filter { agent in
            [agent.id, agent.name ?? "", agent.title]
                .joined(separator: " ")
                .lowercased()
                .contains(needle)
        }
    }

    static func inserting(agentID: String, into value: String, context: MentionContext) -> String {
        (value as NSString).replacingCharacters(in: context.range, with: "@\(agentID) ")
    }

    static func resolveAgentIDs(in value: String, agents: [AgentProfile]) -> [String] {
        let text = value as NSString
        let available = Set(agents.map(\.id))
        var resolved: [String] = []
        tokenExpression.enumerateMatches(
            in: value,
            range: NSRange(location: 0, length: text.length)
        ) { match, _, _ in
            guard let match else { return }
            let agentID = text.substring(with: match.range(at: 1))
            if available.contains(agentID), !resolved.contains(agentID) {
                resolved.append(agentID)
            }
        }
        return resolved
    }
}

private struct MessageRow: View {
    let author: String
    let messageBody: String
    let timestamp: String?
    let isUser: Bool
    let isStreaming: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if isUser { Spacer(minLength: 52) }
            if !isUser { AvatarView(label: author, color: SlackTheme.accent) }
            VStack(alignment: isUser ? .trailing : .leading, spacing: 5) {
                HStack(spacing: 7) {
                    Text(author)
                        .font(.subheadline.bold())
                        .foregroundStyle(SlackTheme.conversationText)
                    Text(isStreaming ? "working now" : formattedTime)
                        .font(.caption2)
                        .foregroundStyle(SlackTheme.mutedText)
                }
                if isStreaming && messageBody.isEmpty {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Thinking…")
                            .font(.subheadline)
                            .foregroundStyle(SlackTheme.mutedText)
                    }
                } else {
                    MessageContentView(text: messageBody)
                        .foregroundStyle(SlackTheme.conversationText)
                        .padding(11)
                        .background(
                            isUser ? SlackTheme.userBubble : SlackTheme.agentBubble,
                            in: RoundedRectangle(cornerRadius: 12)
                        )
                        .overlay {
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(SlackTheme.divider)
                        }
                }
            }
            .frame(maxWidth: 760, alignment: isUser ? .trailing : .leading)
            if isUser { AvatarView(label: author, color: SlackTheme.green) }
            if !isUser { Spacer(minLength: 30) }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(author): \(messageBody)")
    }

    private var formattedTime: String {
        guard let timestamp,
              let date = ISO8601DateFormatter.agentSlack.date(from: timestamp) else { return "" }
        return date.formatted(date: .omitted, time: .shortened)
    }
}

private extension ISO8601DateFormatter {
    static let agentSlack: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
