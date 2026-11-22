---
name: vcdeslop
description: >-
  On-demand prompt and dispatch guide for running an independent, same-model
  code review or AI-slop cleanup (代码审查/Review/去AI味). Pure prompt-driven
  guidance with no programmatic gates, hooks, or numeric quotas.
license: MIT
metadata:
  version: "3.0.0"
  compatibility: "Claude Code, OpenAI Codex, DeepSeek Harness, Google Antigravity, Cursor"
---

# VCDeslop — Review Agent Prompt & Dispatch Guide

A lightweight, opt-in skill for running a fresh-eyes code review or surgical deslop pass without restricting how code is written.

## Core Idea

- **Opt-in only**: Use when explicitly reviewing a diff, PR, branch, or cleaning up slop. Never impose always-on coding-time gates or mechanical quotas.
- **Fresh eyes, same intelligence**: When your harness supports subagents, spawn a fresh isolated reviewer (`spawn`, not `fork`) on the **same model tier** (`inherit`, or omit model override—avoid downgrading review to lightweight presets).
- **Facts, not hints**: Hand the reviewer the repository path, scope (`git diff` base, commit range, or file list), and mode (`Review` read-only by default, or `Clean / Deslop` if edits were requested). Avoid passing persuasive summaries or leading suspicions that bias the pass.

## How to Dispatch

- **Harnesses with built-in `vcdeslop-reviewer` agent** (Claude Code, Antigravity):
  - Invoke `vcdeslop-reviewer` directly with `model: inherit` (already configured with the built-in review prompt).
- **Generic subagent tools** (Codex `spawn_agent`, DSH `subagent spawn`, Antigravity `self`, or custom review agents):
  - Pass the built-in prompt from [review-agent.md](./references/review-agent.md) along with the repository path and target scope, omitting model downgrades so it inherits the active tier.
- **Single-thread environments** (Cursor or no subagents):
  - Re-read the raw `git diff` and full changed files cold using the prompt in [review-agent.md](./references/review-agent.md).

If the repository already has native tests or linters, run them as supporting evidence when useful—never invent external scanner gates.

## Optional Language Taste Notes

For language-specific pitfalls (Java, JS/TS, SQL, Python, Go, Rust, Shell), consult [language-tastes.md](./references/language-tastes.md) only when relevant to the files in scope.
