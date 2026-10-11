// SPDX-License-Identifier: Apache-2.0
// pi guard for pr-verify: blocks `git push` / `gh pr create` until the commit has a passing verification stamp.
// The decision lives in prv.py so pi, Claude Code-style hooks and the git pre-push hook all behave the same.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { isToolCallEventType } from "@earendil-works/pi-coding-agent";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const PRV = join(dirname(fileURLToPath(import.meta.url)), "..", "skills", "pr-verify", "scripts", "prv.py");
const PUSH = /(^|[;&|(]\s*|\s)(git(\s+-C\s+\S+)?\s+push\b|gh\s+pr\s+create\b)/;

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event, ctx) => {
		if (!isToolCallEventType("bash", event)) return;
		const command = event.input.command ?? "";
		if (!PUSH.test(command)) return; // cheap pre-filter; prv.py makes the real decision
		const res = await pi.exec("python3", [PRV, "guard", "--command", command, "--cwd", ctx.cwd], { timeout: 30_000 });
		if (res.code === 2) return { block: true, reason: res.stderr.trim() };
		// A quality gate, not a security boundary: if the guard itself breaks, say so and let the push through.
		if (res.code !== 0 && ctx.hasUI) ctx.ui.notify(`pr-verify guard error: ${res.stderr.trim().slice(0, 200)}`, "warning");
	});
}
