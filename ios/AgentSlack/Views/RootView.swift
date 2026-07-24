import SwiftUI

struct RootView: View {
    @Environment(\.horizontalSizeClass) private var horizontalSizeClass
    @StateObject private var model = AppModel()
    @State private var showingAddServer = false
    @State private var showingNewChat = false
    @State private var section: DirectorySection = .chats
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @State private var preferredCompactColumn: NavigationSplitViewColumn = .sidebar

    var body: some View {
        Group {
            switch model.startupConnectionState {
            case .checking:
                StartupCheckingView(address: model.selectedConnection?.baseURL.absoluteString)
            case let .needsConnection(message):
                StartupConnectionView(model: model, message: message)
            case .connected:
                workspace
            }
        }
        .tint(SlackTheme.interactiveAccent)
        .task { await model.bootstrap() }
    }

    private var workspace: some View {
        NavigationSplitView(
            columnVisibility: $columnVisibility,
            preferredCompactColumn: $preferredCompactColumn
        ) {
            WorkspaceSidebarView(model: model, showingAddServer: $showingAddServer)
                .toolbar(removing: .sidebarToggle)
                .navigationSplitViewColumnWidth(min: 230, ideal: 280, max: 340)
                .simultaneousGesture(
                    DragGesture(minimumDistance: 24)
                        .onEnded { value in
                            guard horizontalSizeClass == .compact,
                                  model.selectedWorkspaceID != nil,
                                  CompactNavigation.shouldDismissSidebar(
                                    horizontalTranslation: value.translation.width,
                                    verticalTranslation: value.translation.height
                                  ) else { return }
                            preferredCompactColumn = .content
                        }
                )
        } content: {
            DirectoryView(model: model, section: $section, showingNewChat: $showingNewChat)
                .navigationSplitViewColumnWidth(min: 280, ideal: 330, max: 420)
        } detail: {
            if model.selectedChat != nil {
                ChatView(model: model)
                    .toolbar {
                        if horizontalSizeClass == .regular {
                            ToolbarItem(placement: .topBarLeading) {
                                Button {
                                    withAnimation(.easeInOut(duration: 0.2)) {
                                        columnVisibility = columnVisibility == .detailOnly ? .all : .detailOnly
                                    }
                                } label: {
                                    Image(systemName: columnVisibility == .detailOnly ? "sidebar.leading" : "arrow.up.left.and.arrow.down.right")
                                }
                                .accessibilityLabel(columnVisibility == .detailOnly ? "Show chat list" : "Show chat full screen")
                            }
                        }
                    }
            } else {
                ContentUnavailableView(
                    "Choose a conversation",
                    systemImage: "bubble.left.and.bubble.right",
                    description: Text("Open a chat or select a person to begin.")
                )
            }
        }
        .sheet(isPresented: $showingAddServer) {
            AddServerView(model: model)
        }
        .sheet(isPresented: $showingNewChat) {
            NewChatView(model: model)
        }
        .alert("Agent Slack", isPresented: Binding(
            get: { model.errorMessage != nil },
            set: { if !$0 { model.dismissError() } }
        )) {
            Button("OK") { model.dismissError() }
        } message: {
            Text(model.errorMessage ?? "Unknown error")
        }
        .onAppear {
            preferredCompactColumn = CompactNavigation.column(
                workspaceID: model.selectedWorkspaceID,
                chatID: model.selectedChat?.id
            )
        }
        .onChange(of: model.selectedWorkspaceID) { _, workspaceID in
            if workspaceID != nil {
                preferredCompactColumn = .content
            }
        }
        .onChange(of: model.selectedChat?.id) { _, chatID in
            if chatID != nil {
                preferredCompactColumn = .detail
            }
        }
    }
}

enum CompactNavigation {
    static func column(workspaceID: String?, chatID: String?) -> NavigationSplitViewColumn {
        if chatID != nil { return .detail }
        if workspaceID != nil { return .content }
        return .sidebar
    }

    static func shouldDismissSidebar(
        horizontalTranslation: CGFloat,
        verticalTranslation: CGFloat
    ) -> Bool {
        horizontalTranslation < -80 && abs(horizontalTranslation) > abs(verticalTranslation) * 1.25
    }
}

private struct StartupCheckingView: View {
    let address: String?

    var body: some View {
        ZStack {
            SlackTheme.sidebar.ignoresSafeArea()
            VStack(spacing: 18) {
                Image("AppLogo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 82, height: 82)
                    .clipShape(RoundedRectangle(cornerRadius: 18))
                Text("Agent Slack").font(.largeTitle.bold())
                ProgressView().tint(.white)
                Text("Checking backend server…")
                    .font(.headline)
                if let address {
                    Text(address)
                        .font(.caption.monospaced())
                        .foregroundStyle(.white.opacity(0.7))
                        .multilineTextAlignment(.center)
                }
            }
            .foregroundStyle(.white)
            .padding(32)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Checking Agent Slack backend server")
    }
}

private struct StartupConnectionView: View {
    @ObservedObject var model: AppModel
    let message: String?
    @State private var address = ""
    @State private var connecting = false
    @FocusState private var addressFocused: Bool

    var body: some View {
        ZStack {
            SlackTheme.sidebar.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 24) {
                    Image("AppLogo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 96, height: 96)
                        .clipShape(RoundedRectangle(cornerRadius: 22))
                    VStack(spacing: 7) {
                        Text("Connect to Agent Slack").font(.largeTitle.bold())
                        Text("Enter the backend URL from the Agent Slack app running on your Mac.")
                            .foregroundStyle(.white.opacity(0.78))
                            .multilineTextAlignment(.center)
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        Text("BACKEND SERVER URL")
                            .font(.caption.bold())
                            .foregroundStyle(.white.opacity(0.72))
                        TextField("http://192.168.1.10:8899", text: $address)
                            .textInputAutocapitalization(.never)
                            .keyboardType(.URL)
                            .autocorrectionDisabled()
                            .submitLabel(.go)
                            .focused($addressFocused)
                            .onSubmit { connect() }
                            .padding(14)
                            .foregroundStyle(Color(uiColor: .label))
                            .tint(SlackTheme.interactiveAccent)
                            .background(
                                Color(uiColor: .systemBackground),
                                in: RoundedRectangle(cornerRadius: 11)
                            )
                            .overlay {
                                RoundedRectangle(cornerRadius: 11)
                                    .stroke(
                                        addressFocused ? SlackTheme.selection : Color(uiColor: .separator),
                                        lineWidth: addressFocused ? 2 : 1
                                    )
                            }

                        if let message {
                            Label {
                                Text(message).fixedSize(horizontal: false, vertical: true)
                            } icon: {
                                Image(systemName: "exclamationmark.triangle.fill")
                            }
                            .font(.footnote)
                            .foregroundStyle(Color(red: 1, green: 0.82, blue: 0.45))
                            .accessibilityLabel("Connection failed. \(message)")
                        }

                        Button(action: connect) {
                            HStack {
                                if connecting { ProgressView().tint(SlackTheme.interactiveAccent) }
                                Text(connecting ? "Checking Server…" : "Connect")
                                    .fontWeight(.semibold)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 12)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.white)
                        .foregroundStyle(SlackTheme.interactiveAccent)
                        .disabled(address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || connecting)
                    }
                    .padding(20)
                    .background(.white.opacity(0.1), in: RoundedRectangle(cornerRadius: 16))

                    Text("The URL is stored on this device. Agent Slack verifies it whenever the app starts. Use a trusted LAN, VPN, or authenticated tunnel.")
                        .font(.footnote)
                        .foregroundStyle(.white.opacity(0.68))
                        .multilineTextAlignment(.center)
                }
                .foregroundStyle(.white)
                .frame(maxWidth: 520)
                .padding(.horizontal, 24)
                .padding(.vertical, 48)
                .frame(maxWidth: .infinity)
            }
        }
        .onAppear {
            if address.isEmpty {
                address = model.selectedConnection?.baseURL.absoluteString ?? ""
            }
            addressFocused = model.selectedConnection == nil
        }
    }

    private func connect() {
        guard !connecting,
              !address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        connecting = true
        Task {
            _ = await model.connectFromStartup(address: address)
            connecting = false
        }
    }
}

enum DirectorySection: String, CaseIterable, Identifiable {
    case chats = "Chats"
    case people = "People"
    var id: String { rawValue }
}

struct WorkspaceSidebarView: View {
    @ObservedObject var model: AppModel
    @Binding var showingAddServer: Bool

    var body: some View {
        ZStack {
            LinearGradient(colors: [SlackTheme.sidebarTop, SlackTheme.sidebar], startPoint: .top, endPoint: .bottom)
                .ignoresSafeArea()
            VStack(spacing: 0) {
                HStack {
                    if let workspace = model.selectedWorkspace {
                        ServerAvatar(workspace: workspace, api: model.api, size: 42)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(model.selectedWorkspace?.name ?? model.selectedConnection?.name ?? "Agent Slack")
                            .font(.headline.bold())
                            .lineLimit(1)
                        Text(model.selectedWorkspace?.projectName ?? "Local agent workspace")
                            .font(.caption)
                            .foregroundStyle(.white.opacity(0.7))
                            .lineLimit(1)
                    }
                    Spacer()
                    Button { showingAddServer = true } label: {
                        Image(systemName: "plus")
                            .frame(width: 34, height: 34)
                            .background(.white.opacity(0.12), in: Circle())
                    }
                    .accessibilityLabel("Add Agent Slack server")
                }
                .padding()

                List {
                    Section("Servers") {
                        ForEach(model.connections) { connection in
                            Button {
                                Task { await model.selectConnection(connection) }
                            } label: {
                                Label(connection.name, systemImage: connection.id == model.selectedConnectionID ? "server.rack" : "network")
                                    .fontWeight(connection.id == model.selectedConnectionID ? .bold : .regular)
                            }
                            .contextMenu {
                                Button(role: .destructive) { model.removeConnection(connection) } label: {
                                    Label("Remove Server", systemImage: "trash")
                                }
                            }
                            .listRowBackground(Color.clear)
                        }
                    }

                    if model.selectedConnection != nil {
                        Section("Workspaces") {
                            ForEach(model.workspaces) { workspace in
                                Button {
                                    Task { await model.selectWorkspace(workspace) }
                                } label: {
                                    HStack(spacing: 10) {
                                        ServerAvatar(workspace: workspace, api: model.api)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(workspace.name).fontWeight(workspace.id == model.selectedWorkspaceID ? .bold : .regular)
                                            if !workspace.available {
                                                Text("Unavailable").font(.caption2).foregroundStyle(.white.opacity(0.6))
                                            }
                                        }
                                    }
                                }
                                .disabled(!workspace.available)
                                .listRowBackground(Color.clear)
                            }
                        }
                    }
                }
                .scrollContentBackground(.hidden)
                .listStyle(.sidebar)

                if model.connections.isEmpty {
                    VStack(spacing: 12) {
                        Image(systemName: "network.badge.shield.half.filled").font(.largeTitle)
                        Text("Connect to the Agent Slack server running on your Mac.")
                            .font(.subheadline)
                            .multilineTextAlignment(.center)
                        Button("Add Server") { showingAddServer = true }
                            .buttonStyle(.borderedProminent)
                            .tint(.white.opacity(0.2))
                    }
                    .padding(24)
                }
            }
            .foregroundStyle(.white)
        }
    }
}

private struct ServerAvatar: View {
    let workspace: AgentServer
    let api: AgentSlackAPI?
    var size: CGFloat = 34

    var body: some View {
        Group {
            if let url = api?.imageURL(path: workspace.logoURL, revision: workspace.logoRevision) {
                AsyncImage(url: url) { image in image.resizable().scaledToFill() } placeholder: { fallback }
            } else {
                fallback
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: size * 0.26))
        .accessibilityLabel("\(workspace.name) server logo")
    }

    private var fallback: some View {
        ZStack {
            SlackTheme.selection
            Text(workspace.name.initials).font(.caption.bold()).foregroundStyle(.white)
        }
    }
}

struct DirectoryView: View {
    @ObservedObject var model: AppModel
    @Binding var section: DirectorySection
    @Binding var showingNewChat: Bool

    var body: some View {
        VStack(spacing: 0) {
            Picker("Directory", selection: $section) {
                ForEach(DirectorySection.allCases) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding()

            if model.selectedWorkspace == nil {
                ContentUnavailableView("Choose a workspace", systemImage: "square.grid.2x2")
            } else if model.isLoading && model.chats.isEmpty && model.agents.isEmpty {
                ProgressView("Loading workspace…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if section == .chats {
                chats
            } else {
                people
            }
        }
        .navigationTitle(model.selectedWorkspace?.name ?? "Agent Slack")
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { showingNewChat = true } label: { Image(systemName: "square.and.pencil") }
                    .disabled(model.agents.isEmpty)
                    .accessibilityLabel("New group chat")
            }
        }
    }

    private var chats: some View {
        Group {
            if model.chats.isEmpty {
                ContentUnavailableView(
                    "No conversations",
                    systemImage: "bubble.left",
                    description: Text("Open a person or create a group chat.")
                )
            } else {
                List(model.chats) { chat in
                    Button { Task { await model.openChat(chat) } } label: {
                        HStack(alignment: .top, spacing: 11) {
                            AvatarView(label: chat.title, color: chat.kind == "group" ? SlackTheme.accent : SlackTheme.selection)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(chat.title).font(.headline).foregroundStyle(.primary)
                                Text(chat.lastMessagePreview ?? "No messages yet")
                                    .font(.subheadline)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                        }
                    }
                    .listRowBackground(chat.id == model.selectedChat?.id ? SlackTheme.selection.opacity(0.12) : Color.clear)
                }
                .listStyle(.plain)
            }
        }
    }

    private var people: some View {
        Group {
            if model.agents.isEmpty {
                ContentUnavailableView("No people found", systemImage: "person.2.slash")
            } else {
                List(model.agents) { agent in
                    Button { Task { await model.openDirectMessage(with: agent) } } label: {
                        HStack(spacing: 11) {
                            AvatarView(label: agent.title, color: SlackTheme.selection)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(agent.title).font(.headline).foregroundStyle(.primary)
                                Text(agent.summary ?? agent.group ?? "Agent")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(2)
                            }
                            Spacer()
                            Circle().fill(SlackTheme.green).frame(width: 9, height: 9)
                        }
                    }
                }
                .listStyle(.plain)
            }
        }
    }
}

struct AvatarView: View {
    let label: String
    let color: Color

    var body: some View {
        Text(label.initials)
            .font(.caption.bold())
            .foregroundStyle(.white)
            .frame(width: 38, height: 38)
            .background(color.gradient, in: RoundedRectangle(cornerRadius: 9))
            .accessibilityHidden(true)
    }
}

struct AddServerView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var address = ""
    @State private var connecting = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Agent Slack server") {
                    TextField("Name (optional)", text: $name)
                    TextField("http://192.168.1.10:8899", text: $address)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                }
                Section {
                    Text("Use Copy API URL in the macOS menu-bar app. Connect only over a trusted LAN, VPN, or authenticated tunnel.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Add Server")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(connecting ? "Connecting…" : "Connect") {
                        connecting = true
                        Task {
                            if await model.addConnection(name: name, address: address) { dismiss() }
                            connecting = false
                        }
                    }
                    .disabled(address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || connecting)
                }
            }
        }
    }
}

struct NewChatView: View {
    @ObservedObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var selected: Set<String> = []

    var body: some View {
        NavigationStack {
            Form {
                TextField("Conversation name", text: $title)
                Section("People") {
                    ForEach(model.agents) { agent in
                        Button {
                            if selected.contains(agent.id) { selected.remove(agent.id) } else { selected.insert(agent.id) }
                        } label: {
                            HStack {
                                AvatarView(label: agent.title, color: SlackTheme.selection)
                                Text(agent.title).foregroundStyle(.primary)
                                Spacer()
                                if selected.contains(agent.id) { Image(systemName: "checkmark.circle.fill").foregroundStyle(SlackTheme.interactiveAccent) }
                            }
                        }
                    }
                }
            }
            .navigationTitle("New Conversation")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create") {
                        Task {
                            await model.createChat(title: title, memberIDs: Array(selected))
                            dismiss()
                        }
                    }
                    .disabled(selected.isEmpty)
                }
            }
        }
    }
}
