# Agent Instructions (Backend)

## Shared Rules

Use shared assistant rules from:
`/Users/Andrew/Developer/vivian-workspace/assistant-rules`

Primary shared rule files:
- `/Users/Andrew/Developer/vivian-workspace/assistant-rules/rules/github-issue-workflow.mdc`
- `/Users/Andrew/Developer/vivian-workspace/assistant-rules/rules/docker-compose.mdc`

## Repo-Specific Notes

- Prefer backend-focused changes in `apps/api`, `apps/mcp-server`, and `packages/shared`.
- Keep API behavior changes covered by tests where practical.

## Code Review Policy

Agents may push to feature branches and open pull requests without explicit approval when all of the following are true:

- The agent is confident the change is correct.
- Relevant tests/checks pass (or the best available checks pass when full test execution is unavailable).
- Behavior has been verified to the best available degree (manual/API validation as appropriate).

If confidence is low, validation is incomplete, or important checks fail, pause and ask for user guidance before pushing.
