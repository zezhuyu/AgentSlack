import SwiftUI
import UIKit

enum SlackTheme {
    static let workspaceRail = Color(red: 34 / 255, green: 21 / 255, blue: 39 / 255)
    static let sidebar = Color(red: 63 / 255, green: 15 / 255, blue: 64 / 255)
    static let sidebarTop = Color(red: 71 / 255, green: 18 / 255, blue: 74 / 255)
    static let accent = Color(red: 97 / 255, green: 31 / 255, blue: 105 / 255)
    static let interactiveAccent = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? .systemCyan
            : UIColor(red: 97 / 255, green: 31 / 255, blue: 105 / 255, alpha: 1)
    })
    static let selection = Color(red: 18 / 255, green: 100 / 255, blue: 163 / 255)
    static let cyan = Color(red: 54 / 255, green: 197 / 255, blue: 240 / 255)
    static let green = Color(red: 46 / 255, green: 182 / 255, blue: 125 / 255)
    static let conversationBackground = Color(uiColor: .systemBackground)
    static let conversationText = Color(uiColor: .label)
    static let mutedText = Color(uiColor: .secondaryLabel)
    static let agentBubble = Color(uiColor: .secondarySystemBackground)
    static let userBubble = Color(uiColor: UIColor { traits in
        traits.userInterfaceStyle == .dark
            ? UIColor(red: 24 / 255, green: 66 / 255, blue: 51 / 255, alpha: 1)
            : UIColor(red: 234 / 255, green: 247 / 255, blue: 240 / 255, alpha: 1)
    })
    static let composerField = Color(uiColor: .tertiarySystemBackground)
    static let divider = Color(uiColor: .separator)
}

extension String {
    var initials: String {
        split(separator: " ")
            .prefix(2)
            .compactMap(\.first)
            .map(String.init)
            .joined()
            .uppercased()
    }
}
