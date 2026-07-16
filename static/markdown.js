(function initMarkdown(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.AgentSlackMarkdown = api;
}(typeof window !== "undefined" ? window : globalThis, function createMarkdownRenderer() {
  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function safeUrl(value) {
    const url = String(value || "").trim();
    if (/^https?:\/\//i.test(url) || /^\/(?!\/)/.test(url) || /^#/.test(url)) {
      return escapeHtml(url);
    }
    return null;
  }

  function renderInline(value) {
    const codeTokens = [];
    let text = escapeHtml(value);
    text = text.replace(/`([^`\n]+)`/g, (_match, code) => {
      const token = `\u0000CODE${codeTokens.length}\u0000`;
      codeTokens.push(`<code>${code}</code>`);
      return token;
    });
    text = text.replace(/\[([^\]]+)]\(([^)\s]+)\)/g, (_match, label, url) => {
      const href = safeUrl(url);
      return href
        ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : `${label} (${escapeHtml(url)})`;
    });
    text = text
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
      .replace(/~~([^~\n]+)~~/g, "<del>$1</del>")
      .replace(/(^|[^\w])\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_([^_\n]+)_/g, "$1<em>$2</em>");
    codeTokens.forEach((code, index) => {
      text = text.replace(`\u0000CODE${index}\u0000`, code);
    });
    return text;
  }

  function splitTableRow(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
  }

  function isTableDivider(line) {
    const cells = splitTableRow(line);
    return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  }

  function tableAlignment(cell) {
    if (/^:-+:$/.test(cell)) return "center";
    if (/-+:$/.test(cell)) return "right";
    return "left";
  }

  function parseJsonDocument(value) {
    let source = String(value ?? "").trim();
    const fenced = source.match(/^```json\s*\n([\s\S]*?)\n```$/i);
    if (fenced) source = fenced[1].trim();
    if (!source || !/^[\[{]/.test(source)) return null;
    try {
      const parsed = JSON.parse(source);
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (_error) {
      return null;
    }
  }

  function jsonLabel(key) {
    return String(key)
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/[_-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function renderJsonValue(value) {
    if (value === null) return '<span class="json-empty">None</span>';
    if (Array.isArray(value)) {
      if (!value.length) return '<span class="json-empty">None</span>';
      return `<ul class="json-list">${value.map((item) => `<li>${renderJsonValue(item)}</li>`).join("")}</ul>`;
    }
    if (typeof value === "object") {
      if (!Object.keys(value).length) return '<span class="json-empty">No fields</span>';
      return `<div class="json-nested">${Object.entries(value).map(([key, item]) => (
        `<div><strong>${escapeHtml(jsonLabel(key))}</strong>${renderJsonValue(item)}</div>`
      )).join("")}</div>`;
    }
    if (typeof value === "boolean") return `<span class="json-scalar">${value ? "Yes" : "No"}</span>`;
    if (typeof value === "number") return `<span class="json-scalar">${escapeHtml(value)}</span>`;
    return `<span>${renderInline(String(value))}</span>`;
  }

  function renderJsonDocument(document) {
    const entries = Array.isArray(document)
      ? [["items", document]]
      : Object.entries(document).length ? Object.entries(document) : [["fields", null]];
    return `<section class="json-card">${entries.map(([key, value]) => {
      const classes = ["json-field"];
      if (key === "summary") classes.push("json-summary");
      if (key === "status") classes.push("json-status");
      return `<div class="${classes.join(" ")}"><div class="json-label">${escapeHtml(jsonLabel(key))}</div><div class="json-value">${renderJsonValue(value)}</div></div>`;
    }).join("")}</section>`;
  }

  function render(markdown) {
    const jsonDocument = parseJsonDocument(markdown);
    if (jsonDocument) return renderJsonDocument(jsonDocument);
    const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
    const html = [];
    let paragraph = [];

    const flushParagraph = () => {
      if (!paragraph.length) return;
      html.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
      paragraph = [];
    };

    for (let index = 0; index < lines.length;) {
      const line = lines[index];
      const trimmed = line.trim();
      if (!trimmed) {
        flushParagraph();
        index += 1;
        continue;
      }

      const fence = trimmed.match(/^```([\w+-]*)\s*$/);
      if (fence) {
        flushParagraph();
        const language = fence[1].replace(/[^\w+-]/g, "");
        const code = [];
        index += 1;
        while (index < lines.length && !/^```\s*$/.test(lines[index].trim())) {
          code.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        const languageClass = language ? ` class="language-${language}"` : "";
        html.push(`<pre><code${languageClass}>${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }

      const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        flushParagraph();
        const level = heading[1].length;
        html.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
        flushParagraph();
        html.push("<hr>");
        index += 1;
        continue;
      }

      if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
        flushParagraph();
        const headers = splitTableRow(line);
        const dividers = splitTableRow(lines[index + 1]);
        const alignments = dividers.map(tableAlignment);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        const head = headers.map((cell, cellIndex) => (
          `<th style="text-align:${alignments[cellIndex] || "left"}">${renderInline(cell)}</th>`
        )).join("");
        const body = rows.map((row) => `<tr>${headers.map((_header, cellIndex) => (
          `<td style="text-align:${alignments[cellIndex] || "left"}">${renderInline(row[cellIndex] || "")}</td>`
        )).join("")}</tr>`).join("");
        html.push(`<div class="markdown-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`);
        continue;
      }

      const listItem = line.match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/);
      if (listItem) {
        flushParagraph();
        const tag = listItem[2] ? "ol" : "ul";
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(/^\s*(?:([-+*])|(\d+)\.)\s+(.+)$/);
          if (!item || (item[2] ? "ol" : "ul") !== tag) break;
          items.push(`<li>${renderInline(item[3])}</li>`);
          index += 1;
        }
        html.push(`<${tag}>${items.join("")}</${tag}>`);
        continue;
      }

      if (/^>\s?/.test(line)) {
        flushParagraph();
        const quote = [];
        while (index < lines.length && /^>\s?/.test(lines[index])) {
          quote.push(lines[index].replace(/^>\s?/, ""));
          index += 1;
        }
        html.push(`<blockquote>${quote.map(renderInline).join("<br>")}</blockquote>`);
        continue;
      }

      paragraph.push(trimmed);
      index += 1;
    }
    flushParagraph();
    return html.join("");
  }

  function appendStream(currentText, delta) {
    const text = `${currentText || ""}${delta || ""}`;
    return { text, html: render(text) };
  }

  return { appendStream, escapeHtml, parseJsonDocument, render, renderInline, renderJsonDocument, safeUrl };
}));
