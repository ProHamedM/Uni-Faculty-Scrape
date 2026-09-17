# Phase 2 — agent skill (planned)

Phase 1 is a scripted pipeline. Phase 2 packages it as a **uniform skill** that an LLM can use through an agent harness (for example the Claude Agent SDK or any tool-calling runtime), so a student can say *"find funded PhD positions in robot learning in Austria and Germany"* and the agent plans, runs, reads the logs and explains the results.

Phase 2 starts only after Phase 1 has been validated on real universities.

## Planned tools (thin wrappers over Phase 1)

| Tool | Wraps | Notes |
|---|---|---|
| `list_universities()` | `config.load_universities` | slugs, sections, seeds, last verification date |
| `check_robots(url)` | `robots.RobotsCache` | lets the agent pick allowed entry points |
| `draft_university_config(name, seeds)` | `config.UniversityConfig` validation | human reviews before it is saved |
| `run_university(slug, profile, max_pages, llm_budget)` | `pipeline.run_university` | limits capped by the same constants as the CLI |
| `read_run_summary(run_id)` | `summary.json` | totals, stopped hosts, top reject reasons |
| `explain_rejection(run_id, url)` | `events.jsonl`, `llm/*.json` | evidence and rule that dropped a page |

## Non-negotiables

* Guardrails live inside the tools. The skill has no parameter to disable robots.txt, delays, stop-on-block, scope or verification.
* The agent never sends emails or messages on the student's behalf.
* Every tool call is logged into the same run folder as Phase 1.

## Folder layout (to be added)

```
skills/
  uni-faculty-scout/
    SKILL.md          when to use, workflow, tool contracts, examples
    tools.py          the wrappers above
    examples/         sample sessions and expected summaries
```
