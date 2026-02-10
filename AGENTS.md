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

**NEVER push code changes without explicit user approval.**

- Create feature branches and make commits locally
- Stage changes with `git add` and commit with `git commit`
- **Wait for user review** before pushing: `git push`
- Only push when the user explicitly says to "commit and push" or "push the changes"
- This applies to all branches including feature branches
