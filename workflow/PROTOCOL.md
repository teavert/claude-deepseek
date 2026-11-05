# Persistent workflow for claude-deepseek

Use for substantive debugging, implementation, source review and authorized
artifact/security analysis. Skip trivial edits, greetings and status questions.
This protocol complements the profile's standing GPT planning and validation
instructions. It does not replace them or recursively delegate from reviewers.

## Invocation

The SessionStart hook supplies a CLI prefix containing the installed Python,
workflow.py and the opaque `--session` key. Use that exact prefix and append the
subcommand below. It is unique to the Claude session and current project. When
running outside the project, add `--cwd "ABSOLUTE PROJECT PATH"` before the
subcommand. Do not use the launcher run ID in place of the session key.

Use tools to write JSON input files under the project's work directory, then
pass their filenames. Do not interpolate user text into shell command strings.
All evidence/subject paths are relative to the project and must remain within it.
State is local in workflow/state/workflow.sqlite3; it is not put in the repository.
Only explicitly supplied notes and artifact paths/hashes enter the ledger.
Keep secrets, personal data and raw transcripts out of saved notes and reports.

## Start and resume

1. Establish the existing user-authorized scope and absolute project directory.
   Reuse authorization already provided. Identify success criteria and exclusions.
2. Obtain the actual frontier-reviewer planning result as the profile requires.
   Save its relevant sanitized result in the project; don't invent a review.
3. `status` checks for an existing workflow. Resume relevant unfinished work;
   read its latest checkpoint, tasks, reviews and evidence. Check changed subject
   files and dependencies before reusing conclusions. Do not replay mutations.
   In a new Claude session, `list` shows saved runs for this project. Use
   `resume --run ID` to attach the intended run explicitly. `--replace` is needed
   to switch when this session already has another active run. Avoid editing the
   same run from two live sessions; resume does not stop the original session.
4. For a new objective, write a plan and run `start --file work/plan.json`.
   Use `--new` only when intentionally replacing the active objective; the old
   run remains in `history`. Normally use 3–7 task descriptions.

```json
{
  "objective": "Concrete user-requested result and success criteria",
  "scope": "Actual project/artifacts and permitted operations; explicit exclusions",
  "tasks": ["Inspect relevant behavior", "Implement the change", "Verify and review"]
}
```

5. Save the planning review using a checkpoint. After each meaningful phase,
   checkpoint observed progress and the next concrete action. The support hooks
   remind after 20 tool calls but do not save semantic progress for you.

## Checkpoints

Run `checkpoint --file work/checkpoint.json` with:

```json
{
  "phase": "implementation",
  "summary": "What changed, what was observed, and any rejected approach",
  "next_action": "One concrete next step",
  "tasks": [
    {"id": "1", "status": "completed", "verification": "How it was verified", "evidence": ["work/inspection.txt"]},
    {"id": "2", "status": "running"}
  ]
}
```

Phases: planning, analysis, implementation, validation, reporting, complete.
Task states: pending, running, completed, skipped, blocked. A completed task
needs a verification summary and saved evidence. Skipping requires a reason.
Unmentioned tasks/reviews are preserved. Checkpoint updates are transactional.

The optional `reviews` object accepts `planning` and `validation`. Each completed
review requires `status: "completed"`, `summary`, `reviewer`, `model`, `auth`,
`reasoning_effort`, and `evidence` (array of saved result paths). Record metadata
from the actual terminal bridge result, not the Claude wrapper's model. For an
unavailable review use `status: "unavailable"` and a factual `summary`; this is
shown as a limitation, never as a successful review. A refusal remains a refusal.

## Findings

Run `finding --file work/finding.json`. Begin with `status: "candidate"`; required
fields are `title`, `asset`, and `root_cause`. Use stable asset/root-cause wording:
identical pairs are rejected as duplicates even when the title differs. This is
exact deduplication, not semantic similarity matching. Use `replace: true` to
update a known finding; previous versions remain in its history. Don't change
asset/root cause just to circumvent duplicate detection.

Only promote a finding to `validated` after a bounded local reproduction and
actual independent validation. Required fields for a validated record:

- `title`, `asset`, `root_cause`, `status`, `severity` (critical/high/medium/low/info)
- `reproduction`: exact safe reproduction steps and preconditions
- `expected`, `observed`, `impact`, `remediation`: observations and bounded claims
- `subjects`: relative paths to the source/APK/binary/config under investigation
- `evidence`: relative paths to saved reproduction results; no credentials
- `validation`: completed review metadata using the checkpoint review shape
- `verdict: "confirmed"`: only if the reviewer actually confirmed the conclusion

Other states are `rejected` and `inconclusive`; verdicts include `disputed` and
`inconclusive`. Keep untested hypotheses separate. Static references do not
prove runtime execution. Local reproduction is sufficient; never expand scope
to a live target just to satisfy this ledger. If the subject is external and
cannot be represented by local versioned evidence, keep the result inconclusive
here and explain the limitation.

Validation records are assertions made by the caller, not cryptographic proof
that a model reviewed something. The runtime checks required fields and file
integrity; it cannot decide whether evidence proves the claim. Hash source
dependencies and config that materially affect the conclusion as subjects.
Save final evidence before recording it. If source changes during remediation,
reproduce/review the new version before updating a validated record.

## Recovery and completion

After repeated failures, inspect the actual error and change the hypothesis or
inputs. Keep failed approaches in a short checkpoint. Do not blindly replay
side effects. Explicit wait/status/result polling is exempt from loop reminders.
Hooks are advisory; they neither impose execution timeouts nor switch providers.

Before final claims, obtain the real finding-validator review and resolve
disagreements with evidence. Record its result. Set `phase: "complete"` only
after all tasks are completed or explicitly skipped, with intact task evidence
and planning/validation outcomes recorded. Unavailable reviews remain visible.
Completion freezes edits to that run; `start --new` begins subsequent work.

`report --format markdown` or `report --format json` writes to stdout. Save to a
requested report path using normal file tools/redirection. Reports separate
validated findings from candidates, rejected and inconclusive findings. Missing
or changed subject, reproduction or review files downgrade a validated finding
to inconclusive in the exported report. They do not alter the recorded history.
Incomplete workflows export progress reports, not completion claims. The report
contains local paths and explicit notes: review it before sharing externally.

This workflow never executes test evidence, scans targets, installs tooling,
changes tool permissions, launches agents or contacts model providers itself.
