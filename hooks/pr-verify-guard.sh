#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# PreToolUse hook: find the installed pr-verify skill and let its guard decide. Reads the hook JSON on stdin.
# Skill locations come from Observal's harness registry (project scope first, then user scope).
for d in "$PR_VERIFY_SKILL_DIR" \
  .claude/skills/pr-verify "$HOME/.claude/skills/pr-verify" \
  .agents/skills/pr-verify "$HOME/.agents/skills/pr-verify" \
  .cursor/skills/pr-verify "$HOME/.cursor/skills/pr-verify" \
  .kiro/skills/pr-verify "$HOME/.kiro/skills/pr-verify" \
  .github/skills/pr-verify "$HOME/.copilot/skills/pr-verify" \
  .opencode/skills/pr-verify "$HOME/.config/opencode/skills/pr-verify" \
  "$HOME/.gemini/antigravity-cli/skills/pr-verify" \
  .pi/skills/pr-verify "$HOME/.pi/agent/skills/pr-verify"; do
  if [ -n "$d" ] && [ -f "$d/scripts/prv.py" ]; then
    exec python3 "$d/scripts/prv.py" guard
  fi
done
# A quality gate, not a security boundary: without the skill there is nothing to check against.
echo "pr-verify guard: skill not installed, push not checked" >&2
exit 0
