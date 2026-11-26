#!/usr/bin/env node
// Forward a Claude PermissionRequest to Agent Slack. Silence means use Claude's prompt.
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
let input;
try { input = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { process.exit(0); }

async function main() {
  try {
    const dataRoot = process.env.AGENT_SLACK_DATA_ROOT || join(homedir(), "Library", "Application Support", "agent-slack-desktop", "data");
    const token = process.env.AGENT_SLACK_HOOK_TOKEN || (await readFile(join(dataRoot, "hook-token"), "utf8")).trim();
    if (!token) return;
    const wait = Math.max(0, Math.min(110, Number(process.env.AGENT_SLACK_HOOK_WAIT ?? 100) || 0));
    const response = await fetch(`${process.env.AGENT_SLACK_URL || "http://127.0.0.1:8899"}/api/hooks/permission?wait=${wait}`, {
      method: "POST", headers: { "content-type": "application/json", "X-Agent-Slack-Hook-Token": token },
      body: JSON.stringify({ ...input, source: "claude" }), signal: AbortSignal.timeout((wait + 5) * 1000),
    });
    if (!response.ok) return;
    const result = await response.json();
    if (result.decision !== "allow" && result.decision !== "deny") return;
    const decision = { behavior: result.decision };
    if (result.decision === "deny" && result.reason) decision.message = String(result.reason).slice(0, 1000);
    if (result.decision === "allow" && result.answers && typeof result.answers === "object") {
      // AskUserQuestion: hand the answers chosen in Agent Slack back to Claude instead of its terminal prompt.
      decision.updatedInput = { ...(input.tool_input || {}), answers: result.answers };
    }
    process.stdout.write(JSON.stringify({ hookSpecificOutput: { hookEventName: "PermissionRequest", decision } }));
  } catch { /* Fail open to Claude's own prompt; never approve on bridge failure. */ }
}

await main();
