(function initAgentSlackGenerativeUI(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.AgentSlackGenerativeUI = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createAgentSlackGenerativeUI() {
  const MAX_NODES = 120;
  const MAX_DEPTH = 8;
  const known = new Set([
    "Stack", "Row", "Grid", "Card", "Divider", "Tabs", "Text", "Heading", "Badge",
    "Progress", "Statistic", "Spinner", "Table", "LineChart", "BarChart", "PieChart", "Button",
    "TextInput", "Select", "Checkbox", "Slider", "Form", "AgentStatus", "TaskProgress",
    "ToolResult", "ApprovalCard", "ArtifactPreview",
  ]);
  const actions = new Set(["run_analysis", "submit_form", "answer_question", "approve", "reject"]);
  const propKeys = {
    Stack: ["gap"], Row: ["gap"], Grid: ["columns"], Card: ["title"], Divider: [], Tabs: ["tabs"],
    Text: ["text"], Heading: ["text", "level"], Badge: ["label", "status"], Progress: ["label", "value"], Spinner: ["label"],
    Statistic: ["label", "value", "change"], Table: ["columns", "rows"], LineChart: ["title", "data", "animation"],
    BarChart: ["title", "data", "animation"], PieChart: ["title", "data", "animation"], Button: ["id", "label", "action"],
    TextInput: ["label", "name", "placeholder", "value", "maxLength"], Select: ["label", "name", "options", "value"],
    Checkbox: ["label", "name", "checked"], Slider: ["label", "min", "max", "value", "name"],
    Form: ["id", "action", "submitLabel"], AgentStatus: ["label", "name", "status", "progress", "summary"],
    TaskProgress: ["label", "name", "title", "status", "progress", "summary"],
    ToolResult: ["title", "tool", "result", "summary"],
    ApprovalCard: ["title", "summary", "impact", "action", "approvalId", "status"],
    ArtifactPreview: ["title", "filename", "summary", "artifactId"],
  };

  function validate(spec) {
    if (!spec || spec.schemaVersion !== "1" || !spec.root || typeof spec.root !== "object") {
      throw new Error("Unsupported or malformed UI specification");
    }
    if (JSON.stringify(spec).length > 65536) throw new Error("UI specification is too large");
    let count = 0;
    function visit(node, depth) {
      count += 1;
      if (count > MAX_NODES || depth > MAX_DEPTH) throw new Error("UI specification exceeds render limits");
      if (!node || typeof node !== "object" || typeof node.type !== "string") throw new Error("Malformed UI component");
      if (!node.props || typeof node.props !== "object" || Array.isArray(node.props)) throw new Error("Malformed component props");
      if (node.children !== undefined && !Array.isArray(node.children)) throw new Error("Malformed component children");
      if (propKeys[node.type] && Object.keys(node.props).some((key) => !propKeys[node.type].includes(key))) {
        throw new Error("Component contains unsupported properties");
      }
      const expectedAnimation = { LineChart: "draw", BarChart: "grow", PieChart: "reveal" }[node.type];
      if (Object.hasOwn(node.props, "animation") &&
          node.props.animation !== "none" && node.props.animation !== expectedAnimation) {
        throw new Error("Unsupported chart animation");
      }
      if (node.props.action && !actions.has(node.props.action.type)) throw new Error("Unsupported UI action");
      (node.children || []).forEach((child) => visit(child, depth + 1));
    }
    visit(spec.root, 1);
    return spec;
  }

  function element(tag, className, text) {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== undefined && text !== null) result.textContent = String(text);
    return result;
  }

  function renderNode(node, onAction, isPending, getActionStatus) {
    const props = node.props || {};
    const children = node.children || [];
    const actionStatus = typeof getActionStatus === "function" ? getActionStatus(props.action, props.id) : null;
    const pending = (typeof isPending === "function" && isPending(props.action, props.id)) || actionStatus === "requested";
    if (!known.has(node.type)) return element("div", "agent-ui-unsupported", `Unsupported component: ${node.type}`);
    let output;
    switch (node.type) {
      case "Stack": output = element("div", "agent-ui-stack"); break;
      case "Row": output = element("div", "agent-ui-row"); break;
      case "Grid": output = element("div", "agent-ui-grid"); break;
      case "Card": output = element("section", "agent-ui-card"); break;
      case "Divider": return element("hr", "agent-ui-divider");
      case "Text": return element("p", "agent-ui-text", props.text);
      case "Heading": {
        const level = Math.max(2, Math.min(4, Number(props.level) || 3));
        return element(`h${level}`, "agent-ui-heading", props.text);
      }
      case "Badge": return element("span", "agent-ui-badge", props.label);
      case "Progress": {
        const wrap = element("div", "agent-ui-progress");
        const bar = element("progress", "", null);
        bar.max = 100;
        bar.value = Math.max(0, Math.min(100, Number(props.value) || 0));
        bar.setAttribute("aria-label", String(props.label || "Progress"));
        wrap.append(bar);
        if (props.label) wrap.append(element("span", "", props.label));
        return wrap;
      }
      case "Spinner": {
        const wrap = element("div", "agent-ui-spinner");
        const label = String(props.label || "Loading").slice(0, 120);
        wrap.setAttribute("role", "status");
        wrap.setAttribute("aria-label", label);
        const mark = element("span", "agent-ui-spinner-mark");
        mark.setAttribute("aria-hidden", "true");
        wrap.append(mark, element("span", "agent-ui-spinner-label", label));
        return wrap;
      }
      case "Statistic": {
        output = element("div", "agent-ui-statistic");
        output.append(element("span", "agent-ui-statistic-label", props.label));
        output.append(element("strong", "agent-ui-statistic-value", props.value));
        if (props.change !== undefined) output.append(element("small", "", props.change));
        return output;
      }
      case "Table": return renderTable(props);
      case "LineChart":
      case "BarChart":
      case "PieChart": return renderChart(node.type, props);
      case "Button": {
        const label = props.label || "Action";
        const button = element("button", "agent-ui-button", pending ? `${label} · Working…`
          : actionStatus === "completed" ? `${label} · Run again`
            : actionStatus === "failed" ? `${label} · Retry` : label);
        button.type = "button";
        button.disabled = pending || typeof onAction !== "function" || !props.action || typeof props.action.type !== "string";
        if (pending) button.title = "This action is already in progress";
        else if (button.disabled) button.title = "This action is unavailable";
        else button.addEventListener("click", () => onAction(props.action, {}, props.id, button));
        const wrap = element("div", "agent-ui-action");
        wrap.append(button);
        appendActionStatus(wrap, actionStatus || (pending ? "requested" : null));
        return wrap;
      }
      case "TextInput": {
        const wrap = labeledControl(props.label || props.name || "Text", "text");
        wrap.control.name = safeName(props.name);
        wrap.control.placeholder = String(props.placeholder || "").slice(0, 120);
        wrap.control.value = String(props.value || "").slice(0, 4000);
        wrap.control.maxLength = Math.max(1, Math.min(4000, Number(props.maxLength) || 500));
        return wrap.container;
      }
      case "Select": {
        const wrap = labeledControl(props.label || props.name || "Choose", "select");
        wrap.control.name = safeName(props.name);
        const options = Array.isArray(props.options) ? props.options.slice(0, 50) : [];
        options.forEach((option) => {
          const item = element("option", "", option?.label);
          item.value = String(option?.value || "").slice(0, 200);
          wrap.control.append(item);
        });
        return wrap.container;
      }
      case "Checkbox": {
        const wrap = element("label", "agent-ui-checkbox");
        const input = element("input");
        input.type = "checkbox";
        input.name = safeName(props.name);
        input.checked = Boolean(props.checked);
        wrap.append(input, element("span", "", props.label || props.name));
        return wrap;
      }
      case "Slider": {
        const wrap = element("label", "agent-ui-slider");
        wrap.append(element("span", "", props.label || "Value"));
        const input = element("input");
        input.type = "range";
        input.min = Number.isFinite(Number(props.min)) ? Number(props.min) : 0;
        input.max = Number.isFinite(Number(props.max)) ? Number(props.max) : 100;
        input.value = Math.max(input.min, Math.min(input.max, Number(props.value) || 0));
        wrap.append(input);
        return wrap;
      }
      case "Tabs": {
        output = element("div", "agent-ui-tabs");
        const tabs = Array.isArray(props.tabs) ? props.tabs.slice(0, children.length) : [];
        const controls = element("div", "agent-ui-tab-list");
        const panels = children.map((child, index) => renderNode(child, onAction, isPending, getActionStatus));
        panels.forEach((panel, index) => {
          const button = element("button", "agent-ui-tab", tabs[index]?.label || `Tab ${index + 1}`);
          button.type = "button";
          button.setAttribute("role", "tab");
          button.setAttribute("aria-selected", index === 0 ? "true" : "false");
          panel.setAttribute("role", "tabpanel");
          panel.hidden = index !== 0;
          button.addEventListener("click", () => {
            panels.forEach((item, itemIndex) => {
              item.hidden = itemIndex !== index;
              controls.children[itemIndex].setAttribute("aria-selected", itemIndex === index ? "true" : "false");
            });
          });
          controls.append(button);
        });
        output.append(controls, ...panels);
        return output;
      }
      case "Form": {
        output = element("form", "agent-ui-form");
        output.noValidate = false;
        output.addEventListener("submit", (event) => {
          event.preventDefault();
          if (pending || !output.reportValidity() || typeof onAction !== "function" || !props.action) return;
          const args = Object.fromEntries(new FormData(output).entries());
          onAction(props.action, args, props.id, output);
        });
        break;
      }
      case "AgentStatus": return statusCard("agent", props);
      case "TaskProgress": return statusCard("task", props);
      case "ToolResult": {
        output = element("details", "agent-ui-tool-result");
        output.append(element("summary", "", props.title || props.tool || "Tool result"));
        output.append(element("pre", "", props.result || props.summary || "No result"));
        return output;
      }
      case "ApprovalCard": {
        output = element("section", "agent-ui-approval");
        output.append(element("strong", "", props.title || "Approval required"));
        output.append(element("p", "", props.summary || "Review this proposed operation."));
        if (props.impact) output.append(element("p", "agent-ui-impact", props.impact));
        if (props.status && props.status !== "pending") {
          output.append(element("span", "agent-ui-approval-status", props.status));
          return output;
        }
        const controls = element("div", "agent-ui-row");
        ["approve", "reject"].forEach((decision) => {
          const button = element("button", "agent-ui-button", decision === "approve" ? "Approve" : "Reject");
          button.type = "button";
          const pending = typeof isPending === "function" && isPending({ type: decision }, props.approvalId);
          button.disabled = pending || typeof onAction !== "function" || !props.approvalId;
          if (pending) button.title = "This approval is already being processed";
          if (!button.disabled) button.addEventListener("click", () => onAction({ type: decision }, {}, props.approvalId, button));
          controls.append(button);
        });
        output.append(controls);
        return output;
      }
      case "ArtifactPreview": return renderArtifact(props);
      default: return element("div", "agent-ui-unsupported", `Unsupported component: ${node.type}`);
    }
    for (const child of children) output.append(renderNode(child, onAction, isPending, getActionStatus));
    if (node.type === "Form") {
      const label = props.submitLabel || "Submit";
      const submit = element("button", "agent-ui-button", pending ? `${label} · Working…`
        : actionStatus === "completed" ? `${label} · Submit again`
          : actionStatus === "failed" ? `${label} · Retry` : label);
      submit.type = "submit";
      submit.disabled = pending || typeof onAction !== "function" || !props.action;
      output.append(submit);
      appendActionStatus(output, actionStatus || (pending ? "requested" : null));
    }
    return output;
  }

  function safeName(value) {
    return /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(String(value || "")) ? String(value) : "field";
  }

  function appendActionStatus(container, status) {
    const labels = {
      requested: "Action in progress",
      completed: "Completed · select to run again",
      failed: "Failed · select to retry",
    };
    const label = labels[status];
    if (!label) return;
    const note = element("span", "agent-ui-action-status", label);
    note.setAttribute("role", "status");
    container.append(note);
  }

  function labeledControl(label, type) {
    const container = element("label", "agent-ui-field");
    container.append(element("span", "", label));
    const control = element(type === "select" ? "select" : "input");
    if (type !== "select") control.type = type;
    container.append(control);
    return { container, control };
  }

  function statusCard(kind, props) {
    const node = element("section", `agent-ui-status agent-ui-${kind}-status`);
    node.append(element("strong", "", props.label || props.name || props.title || kind));
    node.append(element("span", "agent-ui-badge", props.status || "unknown"));
    if (props.progress !== undefined) {
      const progress = element("progress");
      progress.max = 100;
      progress.value = Math.max(0, Math.min(100, Number(props.progress) || 0));
      node.append(progress);
    }
    if (props.summary) node.append(element("p", "", props.summary));
    return node;
  }

  function renderArtifact(props) {
    const card = element("section", "agent-ui-artifact");
    card.append(element("strong", "", props.title || props.filename || "Artifact"));
    if (props.summary) card.append(element("p", "", props.summary));
    if (/^[a-f0-9]{32}$/.test(String(props.artifactId || ""))) {
      const link = element("a", "", "Open artifact");
      link.href = `/api/artifacts/${encodeURIComponent(props.artifactId)}`;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      card.append(link);
    }
    return card;
  }

  function renderChart(kind, props) {
    const data = Array.isArray(props.data) ? props.data.slice(0, 100) : [];
    const animation = props.animation || "none";
    const chart = element("figure", `agent-ui-chart agent-ui-${kind.toLowerCase()}${animation !== "none" ? ` agent-ui-animated agent-ui-animation-${animation}` : ""}`);
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", String(props.title || kind));
    chart.append(element("figcaption", "", props.title || kind));
    if (!data.length) {
      chart.append(element("p", "agent-ui-unsupported", "No chart data"));
      return chart;
    }
    if (kind === "PieChart") {
      const total = data.reduce((sum, item) => sum + Math.max(0, Number(item?.value) || 0), 0) || 1;
      const legend = element("ul", "agent-ui-chart-legend");
      const colors = ["#5969d8", "#1a9a85", "#da8b35", "#c95070", "#7b55a5", "#527b91"];
      let start = 0;
      data.forEach((item, index) => {
        const value = Math.max(0, Number(item?.value) || 0);
        const end = start + value / total * 100;
        const row = element("li", "");
        const swatch = element("span", "agent-ui-swatch");
        swatch.style.backgroundColor = colors[index % colors.length];
        row.append(swatch, element("span", "", `${item?.label || `Segment ${index + 1}`}: ${formatValue(value)}`));
        legend.append(row);
        start = end;
      });
      const segments = data.map((item, index) => `${colors[index % colors.length]} ${data.slice(0, index).reduce((sum, prior) => sum + Math.max(0, Number(prior?.value) || 0), 0) / total * 100}% ${(data.slice(0, index + 1).reduce((sum, prior) => sum + Math.max(0, Number(prior?.value) || 0), 0) / total * 100)}%`);
      const pie = element("div", "agent-ui-pie");
      pie.style.background = `conic-gradient(${segments.join(",")})`;
      pie.setAttribute("aria-hidden", "true");
      chart.append(pie, legend);
      return chart;
    }
    const max = Math.max(1, ...data.map((item) => Number(item?.value) || 0));
    if (kind === "LineChart") {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 600 180");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", String(props.title || "Line chart"));
      const points = data.map((item, index) => {
        const x = data.length < 2 ? 300 : 24 + index * 552 / (data.length - 1);
        const value = Math.max(0, Number(item?.value) || 0);
        const y = 150 - value / max * 126;
        return `${x},${y}`;
      }).join(" ");
      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("points", points);
      line.setAttribute("fill", "none");
      line.setAttribute("stroke", "currentColor");
      line.setAttribute("stroke-width", "3");
      line.setAttribute("class", "agent-ui-line-path");
      line.setAttribute("pathLength", "1");
      svg.append(line);
      chart.append(svg);
      const labels = element("ol", "agent-ui-chart-labels");
      data.forEach((item) => labels.append(element("li", "", `${item?.label || ""}: ${formatValue(item?.value)}`)));
      chart.append(labels);
      return chart;
    }
    const rows = element("div", "agent-ui-chart-rows");
    data.forEach((item) => {
      const row = element("div", "agent-ui-chart-row");
      row.append(element("span", "", item?.label || ""));
      const track = element("span", "agent-ui-chart-track");
      const mark = element("span", kind === "LineChart" ? "agent-ui-line-mark" : "agent-ui-bar-mark");
      mark.style.width = `${Math.max(0, Math.min(100, (Number(item?.value) || 0) / max * 100))}%`;
      track.append(mark);
      row.append(track, element("strong", "", formatValue(item?.value)));
      rows.append(row);
    });
    chart.append(rows);
    return chart;
  }

  function formatValue(value) {
    const number = Number(value);
    return Number.isFinite(number) ? String(number) : String(value ?? "");
  }

  function renderTable(props) {
    const table = element("table", "agent-ui-table");
    const columns = Array.isArray(props.columns) ? props.columns.slice(0, 20) : [];
    const rows = Array.isArray(props.rows) ? props.rows.slice(0, 100) : [];
    if (!columns.length) return element("p", "agent-ui-unsupported", "Table has no columns");
    const head = element("thead");
    const header = element("tr");
    columns.forEach((column) => header.append(element("th", "", column)));
    head.append(header);
    const body = element("tbody");
    rows.forEach((row) => {
      const tr = element("tr");
      columns.forEach((column) => tr.append(element("td", "", row && typeof row === "object" ? row[column] : "")));
      body.append(tr);
    });
    table.append(head, body);
    return table;
  }

  function append(container, spec, options = {}) {
    const startedAt = globalThis.performance?.now?.() || 0;
    try {
      validate(spec);
      const region = element("section", "agent-ui-renderer");
      region.setAttribute("aria-label", "Agent generated interface");
      region.append(renderNode(spec.root, options.onAction, options.isActionPending, options.getActionStatus));
      container.append(region);
      if (globalThis.console?.debug) {
        const counts = {};
        (function count(node) {
          counts[node.type] = (counts[node.type] || 0) + 1;
          (node.children || []).forEach(count);
        })(spec.root);
        console.debug("Agent UI rendered", { components: counts, durationMs: Math.max(0, (globalThis.performance?.now?.() || startedAt) - startedAt) });
      }
      return true;
    } catch (_error) {
      const fallback = element("p", "agent-ui-error", "This interactive content could not be displayed.");
      fallback.setAttribute("role", "status");
      container.append(fallback);
      if (globalThis.console?.warn) console.warn("Agent UI render failed");
      return false;
    }
  }

  return { validate, append };
});
