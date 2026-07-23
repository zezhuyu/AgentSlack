import SwiftUI

@main
struct AgentSlackApp: App {
    var body: some Scene {
        WindowGroup {
#if DEBUG
            if ProcessInfo.processInfo.arguments.contains("--chat-visual-fixture") {
                ChatVisualFixture()
            } else {
                RootView()
            }
#else
            RootView()
#endif
        }
    }
}

#if DEBUG
private struct ChatVisualFixture: View {
    @StateObject private var model: AppModel

    init() {
        let suiteName = "AgentSlackChatVisualFixture"
        let defaults = UserDefaults(suiteName: suiteName) ?? .standard
        defaults.removePersistentDomain(forName: suiteName)
        let model = AppModel(defaults: defaults)
        model.selectedChat = Chat(
            chatID: "visual-fixture",
            title: "Portfolio review",
            kind: "direct",
            memberIDs: ["cio"],
            messages: [
                ChatMessage(
                    messageID: "user-1",
                    authorType: "user",
                    authorID: "user",
                    authorLabel: "You",
                    body: "How should I split this month’s contribution?",
                    createdAt: "2026-07-22T02:20:00.000Z"
                ),
                ChatMessage(
                    messageID: "agent-1",
                    authorType: "agent",
                    authorID: "cio",
                    authorLabel: "Chief Investment Officer",
                    body: "## Suggested allocation\n\n- **60%** broad market\n- **25%** defensive assets\n- **15%** cash reserve\n\nKeep the contribution diversified and review it monthly.",
                    createdAt: "2026-07-22T02:20:08.000Z"
                ),
                ChatMessage(
                    messageID: "agent-2",
                    authorType: "agent",
                    authorID: "data_quality",
                    authorLabel: "Data Quality Agent",
                    body: """
                    Here is the structured output:

                    {"status":"pass","confidence":0.91,"note":"\\n\\n Inputs are current \\n\\n"}
                    """,
                    createdAt: "2026-07-22T02:20:12.000Z"
                )
            ],
            createdAt: nil,
            updatedAt: nil
        )
        _model = StateObject(wrappedValue: model)
    }

    var body: some View {
        NavigationStack {
            ChatView(model: model)
        }
    }
}
#endif
