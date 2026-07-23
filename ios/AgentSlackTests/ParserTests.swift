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

    func testStreamingEventPayloadDecodes() throws {
        let data = Data(#"{"type":"delta","run_id":"run-1","task_id":"task-1","agent_id":"cio","agent_label":"Chief Investment Officer","text":"Working"}"#.utf8)
        let event = try JSONDecoder().decode(RunEvent.self, from: data)

        XCTAssertEqual(event.type, "delta")
        XCTAssertEqual(event.taskID, "task-1")
        XCTAssertEqual(event.text, "Working")
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
            agentID: "cio",
            agentLabel: "Chief Investment Officer",
            text: nil,
            message: nil
        )
        let firstDelta = RunEvent(
            type: "delta",
            runID: "run-1",
            taskID: "task-1",
            agentID: "cio",
            agentLabel: nil,
            text: "Market ",
            message: nil
        )
        let secondDelta = RunEvent(
            type: "delta",
            runID: "run-1",
            taskID: "task-1",
            agentID: "cio",
            agentLabel: nil,
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
}
