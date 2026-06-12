# A2A Hackathon

[![A2A Protocol](https://img.shields.io/badge/A2A-Protocol-blue)](https://a2a-protocol.org) [![Discord](https://img.shields.io/discord/1391916121589944320?color=7289da&label=Discord&logo=discord&logoColor=white)](https://discord.gg/674NGXpAjU)

Harness for the A2A Hackathon track, built on
[tau2-bench](https://github.com/sierra-research/tau2-bench). The `a2a-hack`
CLI runs simulated users against your A2A agent pair locally: it spins up the
environment API, plays each task's simulated user against your personal
agent, records every conversation leg and tool call, and scores the run with
tau2's evaluators.

Start from the template — all instructions live in its README:
**[a2anet/a2a-hackathon-template](https://github.com/a2anet/a2a-hackathon-template)**.

## CLI

```bash
# Smoke-test one task: prints both conversation legs, every env tool call,
# and the reward. Loud about contextId mistakes.
uv run a2a-hack smoke --personal-url http://localhost:9001 --cs-url http://localhost:9002

# Run a task split (train/test) or comma-separated task ids
uv run a2a-hack run --personal-url http://localhost:9001 --cs-url http://localhost:9002 \
    --tasks train --save-to results/dev --auto-resume

# Browse saved results
uv run tau2 view results/dev

# Combine three pairing runs into the 50/25/25 final score
uv run a2a-hack score --a DIR --b DIR --c DIR --out scores.json
```

## 🤖 Join A2A Net

[A2A Net](https://a2anet.com) is an open-source community for the [A2A protocol](https://a2a-protocol.org/latest/) and platform to build AI agents for [Slack](https://slack.com/intl/en-gb/), [Microsoft 365 Copilot](https://m365.cloud.microsoft/), [Microsoft Teams](https://www.microsoft.com/microsoft-teams/), and [Gemini Enterprise](https://cloud.google.com/gemini-enterprise).

[Join the Discord](https://discord.gg/674NGXpAjU) to share your project, ask questions, stay up-to-date with the latest news, be the first to hear about open-source releases, tutorials, and more!
