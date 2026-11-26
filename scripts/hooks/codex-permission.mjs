#!/usr/bin/env node
// Forward a Codex PermissionRequest to Agent Slack. Silence means use Codex's prompt.
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
let input;
try { input = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { process.exit(0); }

async function main() {
  // Codex questions (request_user_input) can only be answered in Codex itself; never show them as approve/deny.
  if (/^(request_user_input|ask_user_question)$/i.test(String(input.tool_name || input.toolName || ""))) return;
  try {
    const dataRoot = process.env.AGENT_SLACK_DATA_ROOT || join(homedir(), "Library", "Application Support", "agent-slack-desktop", "data");
    const token = process.env.AGENT_SLACK_HOOK_TOKEN || (await readFile(join(dataRoot, "hook-token"), "utf8")).trim();
    if (!token) return;
    const wait = Math.max(0, Math.min(110, Number(process.env.AGENT_SLACK_HOOK_WAIT ?? 100) || 0));
    const response = await fetch(`${process.env.AGENT_SLACK_URL || "http://127.0.0.1:8899"}/api/hooks/permission?wait=${wait}`, {
      method: "POST", headers: { "content-type": "application/json", "X-Agent-Slack-Hook-Token": token },
      body: JSON.stringify({ ...input, source: "codex" }), signal: AbortSignal.timeout((wait + 5) * 1000),
    });
    if (!response.ok) return;
    const result = await response.json();
    if (result.decision !== "allow" && result.decision !== "deny") return;
    const decision = { behavior: result.decision };
    if (result.decision === "deny" && result.reason) decision.message = String(result.reason).slice(0, 1000);
    process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: "PermissionRequest", decision } }));
  } catch { /* Fail open to Codex's own prompt; never approve on bridge failure. */ }
}

await main();
