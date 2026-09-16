import XCTest
import UIKit
@testable import AgentSlack

final class ParserTests: XCTestCase {
    func testServerEndpointAddsSchemeAndRemovesVersionPath() throws {
        let url = try ServerEndpoint.normalize("192.168.1.20:8899/api/v1/")
        XCTAssertEqual(url.absoluteString, "http://192.168.1.20:8899")
    }

    func testMarkdownParserRecognizesStructuredBlocks() {
        let source = """
        # Verdict

        - First
        - Second

        | Name | Score |
        | --- | --- |
        | A | 9 |

        ```swift
        let value = 1
        ```
        """
        let blocks = MarkdownParser.parse(source)
        XCTAssertTrue(blocks.contains(.heading(level: 1, text: "Verdict")))
        XCTAssertTrue(blocks.contains(.list(ordered: false, items: ["First", "Second"])))
        XCTAssertTrue(blocks.contains(.table(headers: ["Name", "Score"], rows: [["A", "9"]])))
        XCTAssertTrue(blocks.contains(.code(language: "swift", value: "let value = 1")))
    }

    func testJSONParserAcceptsFencedObjects() {
        let value = JSONValue.parse("""
        ```json
        {"status":"pass","score":9}
        ```
        """)
        guard case let .object(entries) = value else {
            return XCTFail("Expected a JSON object")
        }
        XCTAssertEqual(entries.map(\.0), ["score", "status"])
    }

    func testJSONParserExtractsStructuredPayloadAfterAgentProse() {
        let source = """
        I now have all the evidence. Here is the structured output:

        {
          "agent": "market_regime",
          "status": "success",
          "evidence": [{"signal": "bullish"}]
        }
        """

        guard let embedded = JSONValue.extractEmbedded(from: source),
              case let .object(entries) = embedded.value else {
            return XCTFail("Expected embedded JSON object")
        }
        XCTAssertTrue(embedded.prefix.contains("structured output"))
        XCTAssertTrue(embedded.suffix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        XCTAssertEqual(entries.map(\.0), ["agent", "evidence", "status"])
    }

    func testJSONDisplayTextRemovesOnlyBoundaryWhitespace() {
        XCTAssertEqual(JSONValue.displayText("  first line\nsecond line  \n"), "first line\nsecond line")
    }

    func testVersionedServerPayloadDecodes() throws {
        let data = Data(#"{"servers":[{"server_id":"alpha","name":"Alpha","project_name":"Project","runner":"claude","model":"sonnet","available":true,"active":true,"logo_url":"/api/v1/servers/alpha/logo","logo_revision":null}],"active_server_id":"alpha"}"#.utf8)
        let payload = try JSONDecoder().decode(ServerListResponse.self, from: data)

        XCTAssertEqual(payload.activeServerID, "alpha")
        XCTAssertEqual(payload.servers.first?.id, "alpha")
        XCTAssertEqual(payload.servers.first?.runner, "claude")
    }

    func testWorkspaceHealthDecodesConfiguredHeadAgents() throws {
        let data = Data(#"{"ok":true,"architecture":{"orchestrator_ids":["coordinator","planner"]}}"#.utf8)
        let payload = try JSONDecoder().decode(HealthResponse.self, from: data)

        XCTAssertEqual(payload.architecture?.orchestratorIDs, ["coordinator", "planner"])
    }

    func testStreamingEventPayloadDecodes() throws {
        let data = Data(#"{"type":"delta","run_id":"run-1","task_id":"task-1","agent_id":"cio","agent_label":"Chief Investment Officer","text":"Working"}"#.utf8)
        let event = try JSONDecoder().decode(RunEvent.self, from: data)

        XCTAssertEqual(event.type, "delta")
        XCTAssertEqual(event.taskID, "task-1")
        XCTAssertEqual(event.text, "Working")
    }

    func testMeetingCreatedEventIdentifiesSeparateChat() throws {
        let data = Data(#"{"type":"meeting_created","chat_id":"meeting-2","source_chat_id":"direct-1","title":"Release meeting"}"#.utf8)
        let event = try JSONDecoder().decode(RunEvent.self, from: data)

        XCTAssertEqual(event.chatID, "meeting-2")
        XCTAssertEqual(event.sourceChatID, "direct-1")
        XCTAssertEqual(event.title, "Release meeting")
    }

    func testChatMessageDecodesDynamicChoiceInteraction() throws {
        let data = Data(
            #"""
            {
              "message_id": "agent-7",
              "author_type": "agent",
              "author_id": "coordinator",
              "author_label": "Coordinator",
              "body": "Choose how to proceed.",
              "created_at": "2026-07-27T16:00:00Z",
              "metadata": {
                "interaction": {
                  "kind": "choice",
                  "question_id": "q-1",
                  "prompt": "Approve the deployment scope?",
                  "options": [
                    {"id": "approve", "label": "Approve", "description": "Proceed with the current scope."},
                    {"id": "revise", "label": "Revise", "description": "Ask for a narrower plan."}
                  ],
                  "multiple": false,
                  "status": "pending",
                  "selected_option_ids": []
                }
              }
            }
            """#.utf8
        )
        let message = try JSONDecoder().decode(ChatMessage.self, from: data)

        XCTAssertEqual(message.metadata?.interaction?.questionID, "q-1")
        XCTAssertEqual(message.metadata?.interaction?.prompt, "Approve the deployment scope?")
        XCTAssertEqual(message.metadata?.interaction?.options.map(\.label), ["Approve", "Revise"])
        XCTAssertEqual(message.metadata?.interaction?.options.first?.description, "Proceed with the current scope.")
        XCTAssertEqual(message.metadata?.interaction?.isChoice, true)
        XCTAssertEqual(message.metadata?.interaction?.isPendingChoice, true)
    }

    func testAnsweredChoiceInteractionRemainsRenderableWithSelectedOptions() throws {
        let data = Data(
            #"""
            {
              "message_id": "agent-8",
              "author_type": "agent",
              "body": "Decision recorded.",
              "metadata": {
                "interaction": {
                  "kind": "choice",
                  "question_id": "q-answered",
                  "prompt": "Choose a path",
                  "options": [
                    {"id": "safe", "label": "Safe"},
                    {"id": "fast", "label": "Fast"}
                  ],
                  "multiple": false,
                  "status": "answered",
                  "selected_option_ids": ["safe"]
                }
              }
            }
            """#.utf8
        )
        let message = try JSONDecoder().decode(ChatMessage.self, from: data)
        let interaction = try XCTUnwrap(message.metadata?.interaction)

        XCTAssertTrue(interaction.isChoice)
        XCTAssertFalse(interaction.isPendingChoice)
        XCTAssertEqual(interaction.selectedOptionIDs, ["safe"])
    }

    func testChatMessageDecodesAttachmentMetadata() throws {
        let data = Data(
            #"""
            {
              "message_id": "user-attachment",
              "author_type": "user",
              "body": "See attached.",
              "metadata": {
                "attachments": [
                  {
                    "attachment_id": "att-1",
                    "filename": "diagram.png",
                    "path": "/uploads/diagram.png",
                    "content_type": "image/png",
                    "size": 2048
                  }
                ]
              }
            }
            """#.utf8
        )
        let message = try JSONDecoder().decode(ChatMessage.self, from: data)
        let attachment = try XCTUnwrap(message.metadata?.attachments?.first)

        XCTAssertEqual(attachment.id, "att-1")
        XCTAssertEqual(attachment.filename, "diagram.png")
        XCTAssertEqual(attachment.contentType, "image/png")
        XCTAssertEqual(attachment.size, 2048)
    }

    func testBackendLogoURLIncludesRevision() throws {
        let baseURL = try XCTUnwrap(URL(string: "http://192.168.1.20:8899"))
        let api = AgentSlackAPI(baseURL: baseURL)

        XCTAssertEqual(
            api.imageURL(path: "/api/servers/alpha/logo", revision: "rev 2")?.absoluteString,
            "http://192.168.1.20:8899/api/servers/alpha/logo?revision=rev%202"
        )
    }

    func testAgentStreamAllowsLongRunningTasks() {
        XCTAssertEqual(AgentSlackAPI.streamingTimeout, 6 * 60 * 60)
    }

    func testDarkModeInteractiveTextUsesCyanInsteadOfPurple() {
        let color = UIColor(SlackTheme.interactiveAccent).resolvedColor(
            with: UITraitCollection(userInterfaceStyle: .dark)
        )
        var red: CGFloat = 0
        var green: CGFloat = 0
        var blue: CGFloat = 0
        var alpha: CGFloat = 0

        XCTAssertTrue(color.getRed(&red, green: &green, blue: &blue, alpha: &alpha))
        XCTAssertGreaterThan(green, red)
        XCTAssertGreaterThan(blue, red)
        XCTAssertEqual(alpha, 1, accuracy: 0.001)
    }

    func testCompactNavigationAdvancesPastServerSelection() {
        XCTAssertEqual(CompactNavigation.column(workspaceID: nil, chatID: nil), .sidebar)
        XCTAssertEqual(CompactNavigation.column(workspaceID: "workspace", chatID: nil), .content)
        XCTAssertEqual(CompactNavigation.column(workspaceID: "workspace", chatID: "chat"), .detail)
    }

    func testCompactNavigationDismissesServerSelectorOnlyForDeliberateLeftSwipe() {
        XCTAssertTrue(CompactNavigation.shouldDismissSidebar(horizontalTranslation: -120, verticalTranslation: 20))
        XCTAssertFalse(CompactNavigation.shouldDismissSidebar(horizontalTranslation: -50, verticalTranslation: 5))
        XCTAssertFalse(CompactNavigation.shouldDismissSidebar(horizontalTranslation: -100, verticalTranslation: 120))
        XCTAssertFalse(CompactNavigation.shouldDismissSidebar(horizontalTranslation: 120, verticalTranslation: 0))
    }

    func testMentionPickerMatchesAndInsertsAnAgent() throws {
        let agents = mentionAgents
        let context = try XCTUnwrap(MentionSupport.queryAtEnd(in: "Please ask @risk"))

        XCTAssertEqual(context.query, "risk")
        XCTAssertEqual(MentionSupport.matchingAgents(agents, query: context.query).map(\.id), ["risk_gate"])
        XCTAssertEqual(
            MentionSupport.inserting(agentID: "risk_gate", into: "Please ask @risk", context: context),
            "Please ask @risk_gate "
        )
    }

    func testMentionResolverSupportsMultipleAgentsAndDeduplicatesThem() {
        XCTAssertEqual(
            MentionSupport.resolveAgentIDs(
                in: "@market_regime compare with @risk_gate then @market_regime",
                agents: mentionAgents
            ),
            ["market_regime", "risk_gate"]
        )
    }

    func testMultipleMentionsEncodeAsMeetingParticipants() throws {
        let request = RunRequest.meeting(
            agentIDs: ["market_regime", "risk_gate"],
            objective: "Compare the outlook"
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )

        XCTAssertEqual(object["mode"] as? String, "meeting")
        XCTAssertEqual(object["lead_agent_id"] as? String, "market_regime")
        XCTAssertEqual(object["participant_ids"] as? [String], ["market_regime", "risk_gate"])
        XCTAssertEqual(object["objective"] as? String, "Compare the outlook")
        XCTAssertNil(object["agent_ids"])
    }

    func testConfiguredHeadAgentDirectMessageRoutesToSeparateAutoMeeting() throws {
        let chat = Chat(
            chatID: "direct-1",
            title: "Coordinator",
            kind: "direct",
            memberIDs: ["coordinator"],
            messages: [],
            createdAt: nil,
            updatedAt: nil
        )
        let request = RunRouting.request(
            chat: chat,
            mentionedAgentIDs: [],
            orchestratorIDs: ["coordinator"],
            objective: "Schedule a release review"
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )

        XCTAssertEqual(object["mode"] as? String, "auto_meeting")
        XCTAssertEqual(object["lead_agent_id"] as? String, "coordinator")
        XCTAssertEqual(object["objective"] as? String, "Schedule a release review")
    }

    func testQuestionAnswerRequestEncodesOptionIDs() throws {
        let request = AnswerQuestionRequest(optionIDs: ["approve", "notify-team"])
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )

        XCTAssertEqual(object["option_ids"] as? [String], ["approve", "notify-team"])
    }

    func testPostMessageRequestEncodesAttachmentIDs() throws {
        let request = PostMessageRequest(body: "See files", attachmentIDs: ["att-1", "att-2"])
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )

        XCTAssertEqual(object["body"] as? String, "See files")
        XCTAssertEqual(object["attachment_ids"] as? [String], ["att-1", "att-2"])
    }

    func testUploadedAttachmentDecodesServerFields() throws {
        let data = Data(
            #"{"attachment_id":"att-1","filename":"report.pdf","path":"/uploads/report.pdf","content_type":"application/pdf","size":4096}"#.utf8
        )
        let attachment = try JSONDecoder().decode(UploadedAttachment.self, from: data)

        XCTAssertEqual(attachment.id, "att-1")
        XCTAssertEqual(attachment.filename, "report.pdf")
        XCTAssertEqual(attachment.path, "/uploads/report.pdf")
        XCTAssertEqual(attachment.contentType, "application/pdf")
        XCTAssertEqual(attachment.size, 4096)
    }

    func testInteractionFollowUpObjectiveIncludesPromptAndLabels() {
        XCTAssertEqual(
            InteractionFollowUp.objective(
                prompt: "Approve the deployment scope?",
                optionLabels: ["Approve", "Notify team"]
            ),
            #"The user answered the question "Approve the deployment scope?" by selecting: Approve, Notify team. Continue from that choice."#
        )
    }

    func testActiveRunPayloadDecodesRunningTasks() throws {
        let data = Data(#"{"runs":[{"run_id":"run-1","chat_id":"chat-1","mode":"respond","lead_agent_id":"coordinator","status":"running","started_at":"2026-07-27T12:00:00Z","tasks":[{"task_id":"task-1","agent_id":"reviewer","agent_label":"Reviewer","status":"running","text":"Checking"}]}]}"#.utf8)
        let payload = try JSONDecoder().decode(ActiveRunsResponse.self, from: data)

        XCTAssertEqual(payload.runs.first?.runID, "run-1")
        XCTAssertEqual(payload.runs.first?.tasks.first?.taskID, "task-1")
        XCTAssertEqual(payload.runs.first?.tasks.first?.text, "Checking")
    }

    private var mentionAgents: [AgentProfile] {
        [
            AgentProfile(
                agentID: "market_regime",
                name: "Macro",
                title: "Market Regime Agent",
                summary: nil,
                group: "Research",
                kind: nil
            ),
            AgentProfile(
                agentID: "risk_gate",
                name: "Risk",
                title: "Risk Gate Agent",
                summary: nil,
                group: "Review",
                kind: nil
            )
        ]
    }
}

@MainActor
final class AppModelStartupTests: XCTestCase {
    private var defaults: UserDefaults!
    private let suiteName = "AgentSlackStartupTests"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suiteName)!
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func testFirstLaunchRequiresBackendURL() {
        let model = AppModel(defaults: defaults)

        XCTAssertEqual(model.startupConnectionState, .needsConnection(message: nil))
        XCTAssertNil(model.selectedConnection)
    }

    func testSavedBackendStartsInReachabilityCheck() throws {
        let connection = SavedConnection(
            name: "Home Mac",
            baseURL: try XCTUnwrap(URL(string: "http://192.168.1.20:8899"))
        )
        defaults.set(
            try JSONEncoder().encode([connection]),
            forKey: "agent-slack-ios-connections-v1"
        )

        let model = AppModel(defaults: defaults)

        XCTAssertEqual(model.startupConnectionState, .checking)
        XCTAssertEqual(model.selectedConnection?.baseURL, connection.baseURL)
    }

    func testStreamingDeltasAppearIncrementally() {
        let model = AppModel(defaults: defaults)
        let started = RunEvent(
            type: "agent_started",
            runID: "run-1",
            taskID: "task-1",
            chatID: nil,
            sourceChatID: nil,
            agentID: "cio",
            agentLabel: "Chief Investment Officer",
            title: nil,
            text: nil,
            message: nil
        )
        let firstDelta = RunEvent(
            type: "delta",
            runID: "run-1",
            taskID: "task-1",
            chatID: nil,
            sourceChatID: nil,
            agentID: "cio",
            agentLabel: nil,
            title: nil,
            text: "Market ",
            message: nil
        )
        let secondDelta = RunEvent(
            type: "delta",
            runID: "run-1",
            taskID: "task-1",
            chatID: nil,
            sourceChatID: nil,
            agentID: "cio",
            agentLabel: nil,
            title: nil,
            text: "update",
            message: nil
        )

        model.applyRunEvent(started)
        model.applyRunEvent(firstDelta)
        XCTAssertEqual(model.streamingReplies.first?.text, "Market ")
        model.applyRunEvent(secondDelta)

        XCTAssertEqual(model.streamingReplies.first?.agentLabel, "Chief Investment Officer")
        XCTAssertEqual(model.streamingReplies.first?.text, "Market update")
    }

    func testBackgroundDetachesTransportWithoutDiscardingActiveRun() {
        let model = AppModel(defaults: defaults)
        model.isSending = true
        model.applyRunEvent(
            RunEvent(
                type: "run_started",
                runID: "run-1",
                taskID: nil,
                chatID: "chat-1",
                sourceChatID: nil,
                agentID: "coordinator",
                agentLabel: "Coordinator",
                title: nil,
                text: nil,
                message: nil
            )
        )

        model.prepareForBackground()

        XCTAssertFalse(model.isSending)
        XCTAssertEqual(model.activeRunID, "run-1")
        XCTAssertNil(model.errorMessage)
    }

    func testDetachedActiveRunStillBlocksASecondSend() {
        let model = AppModel(defaults: defaults)
        model.applyRunEvent(
            RunEvent(
                type: "run_started",
                runID: "run-1",
                taskID: nil,
                chatID: "chat-1",
                sourceChatID: nil,
                agentID: "coordinator",
                agentLabel: "Coordinator",
                title: nil,
                text: nil,
                message: nil
            )
        )
        model.prepareForBackground()

        XCTAssertFalse(model.canStartRun)
    }

    func testForegroundResumeClearsTheDetachedSendingState() async {
        let model = AppModel(defaults: defaults)
        model.isSending = true
        model.prepareForBackground()

        await model.resumeAfterForeground()

        XCTAssertFalse(model.isSending)
        XCTAssertNil(model.errorMessage)
    }
}
