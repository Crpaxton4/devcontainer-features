"use strict";
// parse-payload.js - the one reader of the PreToolUse payload, used by every hook.
// It reads stdin once and exports the fields the hooks judge, plus deny(), which
// prints the deny envelope on stdout and ends the process with exit 0.
// payload is null when stdin is not a JSON object; raw keeps the text for the
// hooks that fail closed on an unreadable payload that still names their agent.
const fs = require("fs");

let raw = "";
try { raw = fs.readFileSync(0, "utf8"); } catch { raw = ""; }
let payload = null;
try {
  const j = JSON.parse(raw);
  if (j && typeof j === "object" && !Array.isArray(j)) payload = j;
} catch { payload = null; }

const p = payload || {};
const toolInput = p.tool_input && typeof p.tool_input === "object" ? p.tool_input : {};

const deny = (reason) => {
  fs.writeSync(1, JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: reason,
    },
  }));
  process.exit(0);
};

module.exports = {
  raw,
  payload,
  toolName: typeof p.tool_name === "string" ? p.tool_name : "",
  toolInput,
  agentType: typeof p.agent_type === "string" ? p.agent_type : "",
  command: typeof toolInput.command === "string" ? toolInput.command : null,
  cwd: typeof p.cwd === "string" && p.cwd ? p.cwd : process.cwd(),
  deny,
};
