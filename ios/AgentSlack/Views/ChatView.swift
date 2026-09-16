import Foundation
import AVFoundation
import AVKit
import PDFKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct ChatView: View {
    @ObservedObject var model: AppModel
    @State private var draft = ChatView.initialDraft
    @State private var pendingAttachments: [PendingAttachmentUpload] = []
    @State private var selectedPhotoItems: [PhotosPickerItem] = []
    @State private var isPresentingFileImporter = false
    @FocusState private var composerFocused: Bool
    @StateObject private var voiceInput = VoiceInputController()
    @StateObject private var voicePlayback = VoicePlaybackController()
    @State private var voiceChatID: String?
    @State private var voiceSourceChatID: String?
    private static let bottomID = "conversation-bottom"
    private static let maxAttachmentCount = 10
    private static let maxAttachmentBytes = 50 * 1024 * 1024

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
                        VStack(alignment: .leading, spacing: 10) {
                            MessageRow(
                                author: message.authorLabel ?? message.authorID ?? "Agent",
                                messageBody: message.body,
                                attachments: message.metadata?.attachments ?? [],
                                timestamp: message.createdAt,
                                isUser: message.authorType == "user",
                                isStreaming: false,
                                isStopping: false,
                                stopAction: nil,
                                fileURL: { model.api?.fileURL(for: $0) }
                            )
                            if let interaction = choiceInteraction(for: message) {
                                QuestionInteractionCard(
                                    chatID: model.selectedChat?.id ?? "",
                                    interaction: interaction,
                                    isSubmitting: model.isSubmittingQuestion(
                                        chatID: model.selectedChat?.id ?? "",
                                        questionID: interaction.questionID
                                    ),
                                    onSubmit: { optionIDs in
                                        Task { await model.answerQuestion(interaction, optionIDs: optionIDs) }
                                    }
                                )
                                .frame(maxWidth: 560)
                                .frame(maxWidth: .infinity, alignment: .center)
                                .padding(.horizontal, 14)
                            }
                        }
                        .id(message.id)
                    }
                    ForEach(model.streamingReplies) { reply in
                        let isRunning = model.isRunningTask(reply.id)
                        MessageRow(
                            author: reply.agentLabel,
                            messageBody: reply.text,
                            attachments: [],
                            timestamp: nil,
                            isUser: false,
                            isStreaming: isRunning,
                            isStopping: model.isStoppingTask(reply.id),
                            stopAction: isRunning ? {
                                Task { await model.stopAgent(taskID: reply.id) }
                            } : nil,
                            fileURL: { model.api?.fileURL(for: $0) }
                        )
                        .id("stream-\(reply.id)")
                    }
                    Color.clear
                        .frame(height: 1)
                        .id(Self.bottomID)
                }
                .padding(.vertical, 8)
            }
            .background(SlackTheme.conversationBackground)
            .task(id: model.selectedChat?.id) {
                await Task.yield()
                proxy.scrollTo(Self.bottomID, anchor: .bottom)
            }
            .onChange(of: visibleMessages.count + model.streamingReplies.count) { _, _ in
                withAnimation(.easeOut(duration: 0.18)) {
                    proxy.scrollTo(Self.bottomID, anchor: .bottom)
                }
            }
        }
    }

    private var visibleMessages: [ChatMessage] {
        (model.selectedChat?.messages ?? []).filter { $0.authorType != "system" }
    }

    private func choiceInteraction(for message: ChatMessage) -> ChatInteraction? {
        guard message.authorType != "user",
              let interaction = message.metadata?.interaction,
              interaction.isChoice else {
            return nil
        }
        return interaction
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

            if !pendingAttachments.isEmpty {
                pendingAttachmentChips
            }

            HStack(alignment: .bottom, spacing: 10) {
                attachmentActions
                Button {
                    if voiceInput.isListening {
                        guard let audioURL = voiceInput.stop() else { return }
                        Task { @MainActor in
                            guard let api = model.api,
                                  let workspaceID = model.selectedWorkspaceID,
                                  let sourceChatID = model.selectedChat?.id else { return }
                            do {
                                if voiceChatID == nil || voiceSourceChatID != sourceChatID {
                                    let voiceChat = try await api.createVoiceSession(
                                        sourceChatID: sourceChatID,
                                        workspaceID: workspaceID
                                    )
                                    voiceChatID = voiceChat.id
                                    voiceSourceChatID = sourceChatID
                                }
                                guard let voiceChatID else { return }
                                for try await event in api.streamVoiceTurn(
                                    try Data(contentsOf: audioURL),
                                    chatID: voiceChatID,
                                    workspaceID: workspaceID
                                ) {
                                    if event.type == "speech_ready", let path = event.url {
                                        let speech = try await api.speechData(path: path)
                                        voicePlayback.play(speech)
                                    }
                                    if event.type == "error" || event.type == "agent_failed" {
                                        throw APIError.server(status: 502, message: event.message ?? event.text ?? "Voice turn failed")
                                    }
                                }
                                model.selectedChat = try await api.chat(voiceChatID, workspaceID: workspaceID)
                            } catch {
                                model.presentAttachmentError("Backend voice turn failed. \(error.localizedDescription)")
                            }
                        }
                    }
                    else { voiceInput.start() }
                } label: {
                    Image(systemName: voiceInput.isListening ? "stop.circle.fill" : "mic.fill")
                        .font(.headline)
                        .foregroundStyle(voiceInput.isListening ? .red : SlackTheme.interactiveAccent)
                        .frame(width: 40, height: 40)
                        .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 12))
                        .overlay { RoundedRectangle(cornerRadius: 12).stroke(SlackTheme.divider) }
                }
                .accessibilityLabel(voiceInput.isListening ? "Stop voice input" : "Start voice input")
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

                if model.activeRunID != nil {
                    Button(role: .destructive) {
                        Task { await model.stopCurrentRun() }
                    } label: {
                        Group {
                            if model.isStoppingRun {
                                ProgressView().tint(.white)
                            } else {
                                Image(systemName: "stop.fill").font(.headline.bold())
                            }
                        }
                        .frame(width: 42, height: 42)
                        .background(Color.red, in: Circle())
                        .foregroundStyle(.white)
                    }
                    .disabled(model.isStoppingRun)
                    .accessibilityLabel(model.isStoppingRun ? "Stopping agent run" : "Stop all running agents")
                } else {
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
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(SlackTheme.conversationBackground)
        .fileImporter(
            isPresented: $isPresentingFileImporter,
            allowedContentTypes: [.item],
            allowsMultipleSelection: true
        ) { result in
            handleImportedFiles(result)
        }
        .onChange(of: selectedPhotoItems) { _, items in
            guard !items.isEmpty else { return }
            Task { @MainActor in
                await loadSelectedPhotos(items)
            }
        }
    }

    private var attachmentActions: some View {
        HStack(spacing: 8) {
            PhotosPicker(
                selection: $selectedPhotoItems,
                maxSelectionCount: 10,
                matching: .images
            ) {
                Image(systemName: "photo.on.rectangle")
                    .font(.headline)
                    .foregroundStyle(SlackTheme.interactiveAccent)
                    .frame(width: 40, height: 40)
                    .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 12))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(SlackTheme.divider)
                    }
            }
            .accessibilityLabel("Add photos")

            Button {
                isPresentingFileImporter = true
            } label: {
                Image(systemName: "paperclip")
                    .font(.headline)
                    .foregroundStyle(SlackTheme.interactiveAccent)
                    .frame(width: 40, height: 40)
                    .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 12))
                    .overlay {
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(SlackTheme.divider)
                    }
            }
            .accessibilityLabel("Add files")
        }
    }

    private var pendingAttachmentChips: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(pendingAttachments) { attachment in
                    HStack(spacing: 8) {
                        Image(systemName: attachmentSymbol(for: attachment))
                            .foregroundStyle(SlackTheme.interactiveAccent)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(attachment.filename)
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(SlackTheme.conversationText)
                                .lineLimit(1)
                            Text(ByteCountFormatter.string(fromByteCount: Int64(attachment.size), countStyle: .file))
                                .font(.caption2)
                                .foregroundStyle(SlackTheme.mutedText)
                        }
                        Button {
                            pendingAttachments.removeAll { $0.id == attachment.id }
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(SlackTheme.mutedText)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Remove \(attachment.filename)")
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(SlackTheme.composerField, in: Capsule())
                    .overlay { Capsule().stroke(SlackTheme.divider) }
                }
            }
        }
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
        .frame(maxWidth: 560, maxHeight: 260)
        .background(SlackTheme.agentBubble, in: RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).stroke(SlackTheme.divider) }
        .frame(maxWidth: .infinity, alignment: .center)
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
        model.canStartRun && (
            !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ||
            !pendingAttachments.isEmpty
        )
    }

    private func submit() {
        guard canSend else { return }
        let message = draft
        let attachments = pendingAttachments
        let targets = mentionedAgentIDs
        Task { @MainActor in
            let sent = await model.send(
                message,
                attachments: attachments,
                mentionedAgentIDs: targets
            )
            guard sent else { return }
            draft = ""
            pendingAttachments = []
            selectedPhotoItems = []
        }
    }

    private func insertMention(_ agent: AgentProfile) {
        guard let context = MentionSupport.queryAtEnd(in: draft) else { return }
        draft = MentionSupport.inserting(agentID: agent.id, into: draft, context: context)
        composerFocused = true
    }

    private func handleImportedFiles(_ result: Result<[URL], Error>) {
        do {
            let urls = try result.get()
            for url in urls {
                let attachment = try makeAttachment(from: url)
                appendAttachmentIfNeeded(attachment)
            }
        } catch {
            model.presentAttachmentError("Could not import the selected file. \(error.localizedDescription)")
        }
    }

    @MainActor
    private func loadSelectedPhotos(_ items: [PhotosPickerItem]) async {
        var loaded: [PendingAttachmentUpload] = []
        do {
            for (index, item) in items.enumerated() {
                guard let data = try await item.loadTransferable(type: Data.self) else {
                    throw APIError.invalidResponse
                }
                let type = item.supportedContentTypes.first ?? .image
                let filename = photoFilename(for: type, index: index + 1)
                loaded.append(
                    PendingAttachmentUpload(
                        filename: filename,
                        contentType: type.preferredMIMEType ?? "application/octet-stream",
                        data: data
                    )
                )
            }
            for attachment in loaded {
                appendAttachmentIfNeeded(attachment)
            }
        } catch {
            model.presentAttachmentError("Could not load the selected photo. \(error.localizedDescription)")
        }
    }

    private func makeAttachment(from url: URL) throws -> PendingAttachmentUpload {
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing {
                url.stopAccessingSecurityScopedResource()
            }
        }

        let values = try? url.resourceValues(forKeys: [.contentTypeKey, .fileSizeKey])
        if let fileSize = values?.fileSize, fileSize > Self.maxAttachmentBytes {
            throw AttachmentValidationError.tooLarge
        }
        let data = try Data(contentsOf: url)
        let type = values?.contentType ?? UTType(filenameExtension: url.pathExtension) ?? .data
        return PendingAttachmentUpload(
            filename: url.lastPathComponent,
            contentType: type.preferredMIMEType ?? "application/octet-stream",
            data: data
        )
    }

    private func appendAttachmentIfNeeded(_ attachment: PendingAttachmentUpload) {
        guard attachment.size <= Self.maxAttachmentBytes else {
            model.presentAttachmentError("Attachments must be 50 MB or smaller.")
            return
        }
        guard !pendingAttachments.contains(where: {
            $0.filename == attachment.filename &&
            $0.size == attachment.size &&
            $0.contentType == attachment.contentType
        }) else { return }
        guard pendingAttachments.count < Self.maxAttachmentCount else {
            model.presentAttachmentError("You can attach up to 10 items per message.")
            return
        }
        pendingAttachments.append(attachment)
    }

    private func photoFilename(for type: UTType, index: Int) -> String {
        let stem = selectedPhotoItems.count > 1 ? "photo-\(index)" : "photo"
        if let ext = type.preferredFilenameExtension {
            return "\(stem).\(ext)"
        }
        return stem
    }

    private func attachmentSymbol(for attachment: PendingAttachmentUpload) -> String {
        if attachment.contentType.hasPrefix("image/") {
            return "photo"
        }
        if attachment.contentType == "application/pdf" {
            return "doc.richtext"
        }
        return "paperclip"
    }
}

private enum AttachmentValidationError: LocalizedError {
    case tooLarge

    var errorDescription: String? {
        switch self {
        case .tooLarge:
            return "Attachments must be 50 MB or smaller."
        }
    }
}

private struct QuestionInteractionCard: View {
    let chatID: String
    let interaction: ChatInteraction
    let isSubmitting: Bool
    let onSubmit: ([String]) -> Void

    @State private var selectedOptionIDs: Set<String>

    init(
        chatID: String,
        interaction: ChatInteraction,
        isSubmitting: Bool,
        onSubmit: @escaping ([String]) -> Void
    ) {
        self.chatID = chatID
        self.interaction = interaction
        self.isSubmitting = isSubmitting
        self.onSubmit = onSubmit
        _selectedOptionIDs = State(initialValue: Set(interaction.selectedOptionIDs ?? []))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(interaction.prompt)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(SlackTheme.conversationText)

            ForEach(interaction.options) { option in
                Button {
                    toggle(optionID: option.id)
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: selectedOptionIDs.contains(option.id) ? "checkmark.circle.fill" : "circle")
                            .font(.headline)
                            .foregroundStyle(selectedOptionIDs.contains(option.id) ? SlackTheme.interactiveAccent : SlackTheme.mutedText)
                            .padding(.top, 1)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(option.label)
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(SlackTheme.conversationText)
                            if let description = option.description, !description.isEmpty {
                                Text(description)
                                    .font(.caption)
                                    .foregroundStyle(SlackTheme.mutedText)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 10))
                    .overlay {
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(selectedOptionIDs.contains(option.id) ? SlackTheme.interactiveAccent : SlackTheme.divider, lineWidth: 1)
                    }
                }
                .buttonStyle(.plain)
                .disabled(isSubmitting || interaction.status != "pending")
                .accessibilityLabel(option.label)
                .accessibilityHint(interaction.multiple ? "Toggles this option before submitting" : "Submits this answer")
                .accessibilityValue(selectedOptionIDs.contains(option.id) ? "Selected" : "Not selected")
            }

            if interaction.multiple && interaction.status == "pending" {
                Button {
                    onSubmit(Array(selectedOptionIDs))
                } label: {
                    HStack(spacing: 8) {
                        if isSubmitting {
                            ProgressView().tint(.white)
                        }
                        Text(isSubmitting ? "Submitting…" : "Submit")
                            .font(.subheadline.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                }
                .buttonStyle(.borderedProminent)
                .tint(SlackTheme.accent)
                .disabled(selectedOptionIDs.isEmpty || isSubmitting)
                .accessibilityLabel(isSubmitting ? "Submitting selected options" : "Submit selected options")
            } else if interaction.status == "answered" {
                Label("Answered", systemImage: "checkmark.circle.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(SlackTheme.mutedText)
            }
        }
        .padding(12)
        .background(SlackTheme.agentBubble, in: RoundedRectangle(cornerRadius: 14))
        .overlay {
            RoundedRectangle(cornerRadius: 14)
                .stroke(SlackTheme.divider)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("dynamic-question-\(chatID)-\(interaction.questionID)")
    }

    private func toggle(optionID: String) {
        guard interaction.status == "pending" else { return }
        if interaction.multiple {
            if selectedOptionIDs.contains(optionID) {
                selectedOptionIDs.remove(optionID)
            } else {
                selectedOptionIDs.insert(optionID)
            }
            return
        }

        selectedOptionIDs = [optionID]
        onSubmit([optionID])
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
    let attachments: [UploadedAttachment]
    let timestamp: String?
    let isUser: Bool
    let isStreaming: Bool
    let isStopping: Bool
    let stopAction: (() -> Void)?
    let fileURL: (UploadedAttachment) -> URL?

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
                    if let stopAction {
                        Button(role: .destructive, action: stopAction) {
                            Text(isStopping ? "Stopping…" : "Stop")
                                .font(.caption.bold())
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(isStopping)
                        .accessibilityLabel(isStopping ? "Stopping \(author)" : "Stop \(author)")
                    }
                }
                if isStreaming && messageBody.isEmpty {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Thinking…")
                            .font(.subheadline)
                            .foregroundStyle(SlackTheme.mutedText)
                    }
                } else {
                    VStack(alignment: .leading, spacing: 8) {
                        MessageContentView(text: messageBody)
                            .foregroundStyle(SlackTheme.conversationText)

                        if !attachments.isEmpty {
                            attachmentCards
                        }
                    }
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

    private var attachmentCards: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(attachments) { attachment in
                VStack(alignment: .leading, spacing: 6) {
                    if let url = fileURL(attachment) {
                        Link(destination: url) {
                            attachmentCard(attachment)
                        }
                        AttachmentPreview(attachment: attachment, url: url)
                    } else {
                        attachmentCard(attachment)
                    }
                }
            }
        }
    }

    private func attachmentCard(_ attachment: UploadedAttachment) -> some View {
        HStack(spacing: 10) {
                    Image(systemName: attachmentIcon(for: attachment))
                        .font(.headline)
                        .foregroundStyle(SlackTheme.interactiveAccent)
                        .frame(width: 28, height: 28)
                        .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text(attachment.filename)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(SlackTheme.conversationText)
                            .lineLimit(2)
                        Text(ByteCountFormatter.string(fromByteCount: Int64(attachment.size), countStyle: .file))
                            .font(.caption2)
                            .foregroundStyle(SlackTheme.mutedText)
                    }
                    Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .stroke(SlackTheme.divider)
        }
    }

    private func attachmentIcon(for attachment: UploadedAttachment) -> String {
        if attachment.contentType.hasPrefix("image/") {
            return "photo"
        }
        if attachment.contentType == "application/pdf" {
            return "doc.richtext"
        }
        return "paperclip"
    }
}

private struct AttachmentPreview: View {
    let attachment: UploadedAttachment
    let url: URL
    @State private var isPresentingFullScreen = false

    var body: some View {
        Group {
            if attachment.contentType.hasPrefix("image/") {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFit()
                    } else {
                        Color.clear
                    }
                }
                .frame(maxWidth: 280, maxHeight: 220)
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .onTapGesture { isPresentingFullScreen = true }
                .fullScreenCover(isPresented: $isPresentingFullScreen) {
                    ImageLightbox(url: url) { isPresentingFullScreen = false }
                }
            } else if attachment.contentType.hasPrefix("video/") || attachment.contentType.hasPrefix("audio/") {
                VideoPlayer(player: AVPlayer(url: url))
                    .frame(maxWidth: 320, minHeight: attachment.contentType.hasPrefix("audio/") ? 60 : 200)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
            } else if attachment.contentType == "application/pdf" {
                PDFKitView(url: url)
                    .frame(maxWidth: 320, minHeight: 240)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .overlay {
                        RoundedRectangle(cornerRadius: 10).stroke(SlackTheme.divider)
                    }
            } else if attachment.contentType.hasPrefix("text/") || attachment.contentType == "application/json" {
                TextFilePreview(url: url, maxBytes: attachment.size)
            } else {
                EmptyView()
            }
        }
    }
}

private struct PDFKitView: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.document = PDFDocument(url: url)
        return view
    }

    func updateUIView(_ uiView: PDFView, context: Context) {}
}

private struct ImageLightbox: View {
    let url: URL
    let dismiss: () -> Void

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            AsyncImage(url: url) { phase in
                if let image = phase.image {
                    image.resizable().scaledToFit()
                } else {
                    ProgressView().tint(.white)
                }
            }
        }
        .onTapGesture(perform: dismiss)
    }
}

private struct TextFilePreview: View {
    let url: URL
    let maxBytes: Int
    @State private var content: String?

    var body: some View {
        Group {
            if let content {
                Text(content)
                    .font(.caption.monospaced())
                    .frame(maxWidth: 320, alignment: .leading)
                    .padding(8)
                    .background(SlackTheme.composerField, in: RoundedRectangle(cornerRadius: 8))
                    .frame(maxHeight: 200)
            } else {
                EmptyView()
            }
        }
        .task {
            guard maxBytes <= 200 * 1024 else { return }
            guard let (data, _) = try? await URLSession.shared.data(from: url) else { return }
            content = String(decoding: data.prefix(4000), as: UTF8.self)
        }
    }
}

@MainActor
private final class VoiceInputController: NSObject, ObservableObject {
    @Published var isListening = false
    private var recorder: AVAudioRecorder?
    private var recordingURL: URL?

    func start() {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: [.duckOthers])
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            let url = FileManager.default.temporaryDirectory.appendingPathComponent("agent-slack-voice-\(UUID().uuidString).wav")
            let recorder = try AVAudioRecorder(url: url, settings: [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: 16_000,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false
            ])
            recorder.record()
            self.recorder = recorder
            recordingURL = url
            isListening = true
        } catch {
            stop()
        }
    }

    func stop() -> URL? {
        recorder?.stop()
        recorder = nil
        isListening = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        let url = recordingURL
        recordingURL = nil
        return url
    }
}

@MainActor
private final class VoicePlaybackController: NSObject, ObservableObject, AVAudioPlayerDelegate {
    private var player: AVAudioPlayer?

    func play(_ data: Data) {
        do {
            player?.stop()
            player = try AVAudioPlayer(data: data)
            player?.delegate = self
            player?.prepareToPlay()
            player?.play()
        } catch {
            player = nil
        }
    }
}

private extension ISO8601DateFormatter {
    static let agentSlack: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}
