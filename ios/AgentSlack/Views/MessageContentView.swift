import SwiftUI

indirect enum JSONValue {
    case object([(String, JSONValue)])
    case array([JSONValue])
    case string(String)
    case number(String)
    case bool(Bool)
    case null

    static func parse(_ text: String) -> JSONValue? {
        var candidate = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if candidate.hasPrefix("```json"), candidate.hasSuffix("```") {
            candidate.removeFirst("```json".count)
            candidate.removeLast(3)
            candidate = candidate.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard let data = candidate.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else {
            return nil
        }
        return from(object)
    }

    static func displayText(_ text: String) -> String {
        text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func extractEmbedded(from text: String) -> (prefix: String, value: JSONValue, suffix: String)? {
        for start in text.indices where text[start] == "{" || text[start] == "[" {
            var stack: [Character] = []
            var inString = false
            var escaped = false
            var index = start

            while index < text.endIndex {
                let character = text[index]
                if inString {
                    if escaped {
                        escaped = false
                    } else if character == "\\" {
                        escaped = true
                    } else if character == "\"" {
                        inString = false
                    }
                } else {
                    switch character {
                    case "\"":
                        inString = true
                    case "{", "[":
                        stack.append(character)
                    case "}", "]":
                        guard let opening = stack.last,
                              (opening == "{" && character == "}") || (opening == "[" && character == "]") else {
                            break
                        }
                        stack.removeLast()
                        if stack.isEmpty {
                            let candidate = String(text[start...index])
                            if let value = parse(candidate) {
                                return (
                                    prefix: String(text[..<start]),
                                    value: value,
                                    suffix: String(text[text.index(after: index)...])
                                )
                            }
                        }
                    default:
                        break
                    }
                }
                index = text.index(after: index)
            }
        }
        return nil
    }

    private static func from(_ value: Any) -> JSONValue {
        if value is NSNull { return .null }
        if let value = value as? Bool { return .bool(value) }
        if let value = value as? NSNumber { return .number(value.stringValue) }
        if let value = value as? String { return .string(value) }
        if let value = value as? [Any] { return .array(value.map(from)) }
        if let value = value as? [String: Any] {
            return .object(value.keys.sorted().map { ($0, from(value[$0]!)) })
        }
        return .string(String(describing: value))
    }
}

enum MarkdownBlock: Equatable {
    case heading(level: Int, text: String)
    case paragraph(String)
    case list(ordered: Bool, items: [String])
    case quote(String)
    case code(language: String?, value: String)
    case table(headers: [String], rows: [[String]])
    case divider

}

enum MarkdownParser {
    static func parse(_ markdown: String) -> [MarkdownBlock] {
        let lines = markdown.replacingOccurrences(of: "\r\n", with: "\n").components(separatedBy: "\n")
        var blocks: [MarkdownBlock] = []
        var index = 0

        while index < lines.count {
            let line = lines[index]
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { index += 1; continue }

            if trimmed.hasPrefix("```") {
                let language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                index += 1
                var code: [String] = []
                while index < lines.count, !lines[index].trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                    code.append(lines[index])
                    index += 1
                }
                if index < lines.count { index += 1 }
                blocks.append(.code(language: language.isEmpty ? nil : language, value: code.joined(separator: "\n")))
                continue
            }

            if trimmed == "---" || trimmed == "***" {
                blocks.append(.divider)
                index += 1
                continue
            }

            if let heading = heading(from: trimmed) {
                blocks.append(heading)
                index += 1
                continue
            }

            if index + 1 < lines.count,
               trimmed.contains("|"),
               isTableSeparator(lines[index + 1]) {
                let headers = cells(from: trimmed)
                index += 2
                var rows: [[String]] = []
                while index < lines.count, lines[index].contains("|"), !lines[index].trimmingCharacters(in: .whitespaces).isEmpty {
                    rows.append(cells(from: lines[index]))
                    index += 1
                }
                blocks.append(.table(headers: headers, rows: rows))
                continue
            }

            if trimmed.hasPrefix(">") {
                var quoted: [String] = []
                while index < lines.count {
                    let value = lines[index].trimmingCharacters(in: .whitespaces)
                    guard value.hasPrefix(">") else { break }
                    quoted.append(String(value.dropFirst()).trimmingCharacters(in: .whitespaces))
                    index += 1
                }
                blocks.append(.quote(quoted.joined(separator: "\n")))
                continue
            }

            if let ordered = listKind(trimmed) {
                var items: [String] = []
                while index < lines.count {
                    let value = lines[index].trimmingCharacters(in: .whitespaces)
                    guard listKind(value) == ordered else { break }
                    items.append(stripListMarker(value, ordered: ordered))
                    index += 1
                }
                blocks.append(.list(ordered: ordered, items: items))
                continue
            }

            var paragraph = [trimmed]
            index += 1
            while index < lines.count {
                let next = lines[index].trimmingCharacters(in: .whitespaces)
                if next.isEmpty || next.hasPrefix("```") || heading(from: next) != nil || next.hasPrefix(">") || listKind(next) != nil {
                    break
                }
                paragraph.append(next)
                index += 1
            }
            blocks.append(.paragraph(paragraph.joined(separator: "\n")))
        }
        return blocks
    }

    private static func heading(from line: String) -> MarkdownBlock? {
        let hashes = line.prefix { $0 == "#" }.count
        guard (1...4).contains(hashes), line.dropFirst(hashes).first == " " else { return nil }
        return .heading(level: hashes, text: String(line.dropFirst(hashes + 1)))
    }

    private static func listKind(_ line: String) -> Bool? {
        if line.hasPrefix("- ") || line.hasPrefix("* ") { return false }
        if line.range(of: #"^\d+\.\s+"#, options: .regularExpression) != nil { return true }
        return nil
    }

    private static func stripListMarker(_ line: String, ordered: Bool) -> String {
        if !ordered { return String(line.dropFirst(2)) }
        guard let range = line.range(of: #"^\d+\.\s+"#, options: .regularExpression) else { return line }
        return String(line[range.upperBound...])
    }

    private static func isTableSeparator(_ line: String) -> Bool {
        let pieces = cells(from: line)
        return !pieces.isEmpty && pieces.allSatisfy {
            $0.replacingOccurrences(of: ":", with: "").allSatisfy { $0 == "-" }
        }
    }

    private static func cells(from line: String) -> [String] {
        line.trimmingCharacters(in: CharacterSet(charactersIn: " |"))
            .split(separator: "|", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
    }
}

struct MessageContentView: View {
    let text: String

    var body: some View {
        Group {
            if let json = JSONValue.parse(text) {
                JSONCardView(value: json)
            } else if let embedded = JSONValue.extractEmbedded(from: text) {
                VStack(alignment: .leading, spacing: 10) {
                    if !embedded.prefix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        MarkdownView(markdown: embedded.prefix)
                    }
                    JSONCardView(value: embedded.value)
                    if !embedded.suffix.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                        MarkdownView(markdown: embedded.suffix)
                    }
                }
            } else {
                MarkdownView(markdown: text)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }
}

struct MarkdownView: View {
    let markdown: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(MarkdownParser.parse(markdown).enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case let .heading(level, text):
            inline(text)
                .font(level == 1 ? .title2.bold() : level == 2 ? .headline : .subheadline.bold())
        case let .paragraph(text):
            inline(text).font(.body)
        case let .list(ordered, items):
            VStack(alignment: .leading, spacing: 5) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Text(ordered ? "\(index + 1)." : "•").fontWeight(.semibold)
                        inline(item)
                    }
                }
            }
        case let .quote(text):
            HStack(alignment: .top, spacing: 8) {
                Rectangle().fill(SlackTheme.interactiveAccent.opacity(0.65)).frame(width: 3)
                inline(text).foregroundStyle(.secondary)
            }
            .padding(.vertical, 3)
        case let .code(language, value):
            VStack(alignment: .leading, spacing: 5) {
                if let language { Text(language.uppercased()).font(.caption2.bold()).foregroundStyle(.secondary) }
                ScrollView(.horizontal) {
                    Text(value).font(.system(.callout, design: .monospaced)).textSelection(.enabled)
                }
            }
            .padding(12)
            .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 9))
        case let .table(headers, rows):
            ScrollView(.horizontal) {
                VStack(alignment: .leading, spacing: 0) {
                    tableRow(headers, bold: true)
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in tableRow(row, bold: false) }
                }
                .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(SlackTheme.divider))
            }
        case .divider:
            Divider()
        }
    }

    private func inline(_ value: String) -> Text {
        let attributed = (try? AttributedString(markdown: value, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(value)
        return Text(attributed)
    }

    private func tableRow(_ cells: [String], bold: Bool) -> some View {
        HStack(spacing: 0) {
            ForEach(Array(cells.enumerated()), id: \.offset) { _, cell in
                inline(cell)
                    .font(bold ? .caption.bold() : .caption)
                    .frame(width: 150, alignment: .leading)
                    .padding(8)
                    .overlay(alignment: .trailing) { Divider() }
            }
        }
        .overlay(alignment: .bottom) { Divider() }
    }
}

struct JSONCardView: View {
    let value: JSONValue

    var body: some View {
        JSONValueView(value: value, depth: 0)
            .padding(10)
            .fixedSize(horizontal: false, vertical: true)
            .background(Color.primary.opacity(0.035), in: RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(SlackTheme.divider))
    }
}

private struct JSONValueView: View {
    let value: JSONValue
    let depth: Int

    var body: some View {
        switch value {
        case let .object(entries):
            VStack(alignment: .leading, spacing: 7) {
                ForEach(Array(entries.enumerated()), id: \.offset) { _, entry in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.0.replacingOccurrences(of: "_", with: " ").uppercased())
                            .font(.caption2.bold())
                            .foregroundStyle(SlackTheme.interactiveAccent)
                        JSONValueView(value: entry.1, depth: depth + 1)
                    }
                    if entry.0 != entries.last?.0 { Divider() }
                }
            }
        case let .array(items):
            VStack(alignment: .leading, spacing: 6) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .top, spacing: 6) {
                        Text("\(index + 1)").font(.caption2.bold()).foregroundStyle(.secondary)
                        JSONValueView(value: item, depth: depth + 1)
                    }
                }
            }
        case let .string(value):
            Text(JSONValue.displayText(value)).font(.body).textSelection(.enabled)
        case let .number(value):
            Text(value).font(.system(.body, design: .monospaced)).foregroundStyle(.blue)
        case let .bool(value):
            Text(value ? "true" : "false").font(.system(.body, design: .monospaced)).foregroundStyle(value ? .green : .orange)
        case .null:
            Text("null").font(.system(.body, design: .monospaced)).foregroundStyle(.secondary)
        }
    }
}
