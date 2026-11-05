---
description: Start, resume, checkpoint or report an evidence-based workflow
argument-hint: "[start <objective> | status | checkpoint | report]"
---

Use the persistent claude-deepseek workflow for: $ARGUMENTS

Read the `workflow/PROTOCOL.md` path identified by the profile CLAUDE.md.
Use the session-specific CLI prefix supplied by the SessionStart hook. For status,
read-only resume inspection or report export, do not start another GPT planning
job. For a new substantive task, follow the existing frontier-reviewer planning
and finding-validator completion requirements. Reuse the user's established
scope, preserve ongoing work, and don't claim a review that did not complete.

If no argument was given, show current workflow status and its next action;
when no workflow exists, explain that `/workflow start <objective>` starts one.
If the session-specific prefix is absent, report that the session needs to be
reopened to load the installed SessionStart hook; do not guess a session key.
