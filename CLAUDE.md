# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL: Ask Before Pushing to Main

**ALWAYS ask the user before pushing changes.** This is non-negotiable.

Before committing and pushing, ask:

> "Should I push this directly to main, or create a feature branch and PR?"

**Default to creating a branch and PR** unless the user explicitly says to push to main. PRs enable:

- Claude PR Review to catch issues
- Code review before deployment
- Discussion and iteration on changes
- Clean git history with context

Pushing directly to main bypasses all review mechanisms and should only be done when the user explicitly requests it.

## CRITICAL: Never Merge Without Permission

**NEVER merge a PR without explicit user approval.** This is non-negotiable.

After pushing changes to a PR:

1. Wait for CI to pass
2. Wait for Claude PR Review to complete
3. Report the results to the user
4. **ASK the user** if they want to merge
5. Only merge if the user explicitly says yes

Merging without permission bypasses:

- The user's ability to review changes
- Claude's automated review findings
- CI checks that may catch issues
- Any other stakeholders who need to approve

## CRITICAL: Git Commit Rules

1. **Commit Author**: Claude is the SOLE author. Do NOT include:
   - Co-Authored-By lines
   - Any reference to the user's name
   - Any "Generated with Claude Code" footer
   - Use `--author="Claude <claude@anthropic.com>"` on every commit
2. **Commit Messages**: ALWAYS include both a good subject AND description - neither should EVER be blank
3. **Pre-commit Must Pass**: NEVER commit if the pre-commit hook is failing. Loop until you fix all issues.
4. **No Suppression**: NEVER suppress warnings, disable linting rules, or skip checks without explicitly asking the user first
5. **No --no-verify**: NEVER use `--no-verify` or any flag to skip pre-commit hooks

## CRITICAL: Pre-commit Hook Behavior

**NEVER bypass pre-commit hooks.** This is non-negotiable.

The pre-commit hooks run these checks on commit:
- `ruff check .` - Linting
- `ruff format --check` - Code formatting
- `mypy .` - Type checking
- `pyright` - Type checking
- `lint-imports` - Architecture validation

On push, additional checks run:
- `bandit` - Security scanning
- `pip-audit` - Supply chain security
- `deptry` - Dependency hygiene
- `pytest` - Tests with 65% coverage threshold

**If any hook fails:**
1. Read the error message carefully
2. Fix the underlying issue (don't suppress or disable)
3. Stage the fix
4. Commit again
5. Repeat until all hooks pass

**NEVER use these flags:**
- `--no-verify` - Skips all hooks
- `--no-pre-commit` - Same effect
- Any environment variable that disables hooks

## Things to Remember Before Writing Any Code

1. State how you will verify this change works (ex. tests, bash commands, browser checks, etc)
2. Write the test orchestration step first
3. Then implement the code
4. Run verification and iterate until it passes

## Project Overview

bt-servant-message-broker is a message broker service for the bt-servant ecosystem. It provides per-user message queueing, FIFO ordering guarantees, and session management between client applications and the AI compute engine.

## Technology Stack

- Python 3.12+ with FastAPI
- Redis (Upstash) for message queues
- Fly.io for deployment

## Development Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run the server
uvicorn bt_servant_message_broker.main:app --reload

# Run tests
pytest --cov=bt_servant_message_broker --cov-report=term-missing

# Linting and formatting
ruff check .
ruff format .

# Type checking
mypy .
pyright

# Architecture validation
lint-imports

# Security checks
bandit -r src/bt_servant_message_broker
```

## What to Do After a Push

After every `git push`, you MUST invoke the ci-watcher subagent to verify CI passes:

1. Invoke the ci-watcher agent using the Task tool with `subagent_type: "ci-watcher"`
2. Wait for it to report CI status
3. If CI fails, fix the issues and push again
4. Repeat until CI passes

## Responding to PR Review Comments

When Claude PR Review (or any automated/human reviewer) comments on a PR:

### Priority Levels and Required Actions

| Priority            | Action Required                                            |
| ------------------- | ---------------------------------------------------------- |
| **Critical**        | MUST fix before merge. No exceptions.                      |
| **High**            | MUST fix before merge.                                     |
| **Medium**          | MUST fix before merge. These are real issues.              |
| **Low**             | Either fix now OR add a TODO comment with issue reference. |
| **Optional/Polish** | Address if time permits, otherwise note for future.        |

### Process

1. **Read the full review** - Don't skim. Understand each issue.
2. **Assess each issue honestly** - Don't dismiss valid concerns. If unsure, err on the side of fixing.
3. **Fix all Critical/High/Medium issues** - No shortcuts. These affect security, reliability, or maintainability.
4. **For Low priority issues**, choose one:
   - Fix them (preferred if quick)
   - Add `// TODO(review): <description>` comment in the code
   - Create a GitHub issue to track it
5. **Push fixes and wait for re-review** - The reviewer may find new issues or confirm fixes.
6. **Iterate until approved** - Don't merge with unresolved medium+ issues.
