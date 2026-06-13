# Sierra AI — How They Build Customer Service Agents (and What We Steal for Track 1)

Researched 12 June 2026. Sierra is a Track 1 sponsor. Founded by Bret Taylor (ex-Salesforce co-CEO, OpenAI board chair) and Clay Bavor (ex-Google). Production CS agents for GAP, Next, Asos, Ocado, Sonos, Wayfair, WeightWatchers, SiriusXM, Rocket Mortgage — millions of conversations/month. They are the closest thing to ground truth for "what a production customer service agent looks like," and their research team likely shaped how our held-out test set will be judged (see §4).

---

## 1. The Sierra platform (Agent OS)

| Component | What it is |
|---|---|
| **Agent SDK** | Code-first, *declarative* agent programming model. You declare **goals** ("help the customer return an order") + **deterministic guardrails** ("orders can only be returned within 30 days") + **composable skills** (triage, respond, confirm) assembled into workflows. Per-workflow dial between creativity and determinism. Abstracted from the underlying LLM, so model upgrades need no code changes. |
| **Agent Studio** | No-code counterpart (2.0 launched Nov 2025). **Journeys** = natural-language workflow definitions with the same engine as the SDK; **Ghostwriter** = describe behavior, it builds the agent; 40+ prebuilt integrations; versioned snapshots with QA→staging→prod promotion and instant rollback. |
| **Knowledge engine** | RAG over help centers, policy docs, custom articles (JSON ingestable). |
| **Agent Data Platform (ADP)** | Nov 2025. The memory/intelligence layer: unifies unstructured conversation history with structured data (orders, billing, inventory) so agents greet by name, remember prior conversations, anticipate needs. *Their version of what Redis wants us to demo.* |
| **Experience Manager** | QA surface: CX experts annotate samples of live conversations daily (right decision? right tone? missing knowledge?). |
| **Live Assist / Voice / channels** | Build once, deploy to chat, voice, email, SMS, contact center, ChatGPT. |

**SDK availability — important:** the Sierra Agent SDK is **not public**. No pip/npm package, no public docs; it's accessed through an enterprise engagement (their pitch: "develop in your existing programming environment"). For the hackathon we cannot build *on* Sierra — we borrow their *patterns* and name-drop accurately.

---

## 2. Their engineering doctrine (Agent Development Life Cycle)

From their canonical blog post (Taylor & Bavor, "The Agent Development Life Cycle") — the five practices that make their agents production-grade:

1. **Declarative goals + deterministic guardrails.** LLMs are "90% magical, 10% haywire" (they cite the Air Canada chatbot inventing airfares). Creative reasoning is allowed everywhere *except* the moments that matter — actions like processing a return are gated by hard, deterministically-enforced business rules, not prompt vibes.
2. **Immutable agent snapshots.** A release = code + prompts + model versions + a frozen knowledge snapshot, atomically. Enables instant rollback and A/B testing of agent behaviors.
3. **Continuous structured human feedback.** Daily expert annotation of real conversations; every annotation traces to the agent's reasoning trace, so failures are diagnosable.
4. **Conversation regression tests.** Every annotated conversation becomes a replayable simulation against **mock APIs**. Thousands run in parallel before every release — "your agent never makes the same mistake twice." TDD for agents.
5. **Model-agnostic abstraction.** Behavior is declared above the LLM layer, so foundation-model upgrades are absorbed without rewriting the agent.

The repeated theme in everything they publish: **reliability beats brilliance**. A demo-impressive agent that's inconsistent is worthless in production — and (see next section) inconsistency is precisely what their benchmark punishes.

---

## 3. Sierra research: τ-bench family

Sierra's research team built the de-facto standard benchmarks for customer service agents:

- **τ-bench** (June 2024, arXiv 2406.12045): tool-agent-user benchmark. An **LLM-simulated user** converses with the agent; the agent has domain API tools (retail/airline) and a **policy document** it must follow. Scoring is **objective and stateful**: compare the final database state against the annotated goal state, plus check that the agent's replies **contain required information (substring match)** — e.g., the user must actually be told "54.04".
- **pass^k metric**: probability that *all k* i.i.d. runs of the same task succeed — reliability, not just capability. Headline finding: GPT-4o passed <50% of retail tasks once, and only ~25% when the same task was run 8 times. Anthropic now reports pass^k in model cards — this metric won.
- **τ²-bench** (2025): dual-control (user also acts); **τ³-bench**: adds banking domain + voice; **τ-voice** (May 2026): 278 voice CS tasks.
- Repos: `github.com/sierra-research/tau-bench`, `tau2-bench` (now τ³).

### Why this matters for Saturday

Track 1 judging = **simulated customer messages + held-out test set + automated scoring**. That is *literally the τ-bench methodology*, and Sierra is in the room. Expect the harness to look like: an LLM-simulated customer with a hidden goal, your pipeline's responses scored on (a) whether the issue got resolved and (b) whether **specific facts appear in the final answer**.

Concrete implications:
- **State concrete facts explicitly** in customer-facing answers — numbers, dates, names, amounts, order IDs echoed back. If the grader substring-matches (τ-bench does), a vague-but-friendly answer scores zero.
- **Consistency > peak performance.** Low temperature on routing/delegation decisions, deterministic code paths for the chain mechanics, creativity confined to phrasing. The test set runs many cases; one flaky failure mode repeats across all of them.
- **The simulated user may withhold info, change their mind, or be vague** (τ-bench users do). Agents should ask-or-assume gracefully, never stall.
- **Policy adherence may be scored** — if the organizers hand out any policy/FAQ document for the scenario, treat it as hard guardrails, quote it, never contradict it.

---

## 4. Patterns to copy into our three agents (maps to spec.md)

| Sierra pattern | Our implementation |
|---|---|
| Goals + guardrails, declared separately | System prompts structured as: GOAL / HARD RULES (never break) / STYLE — not one prose blob. Hard rules include: never claim an action was taken that wasn't; never invent order data; always confirm before destructive intent. |
| Composable skills: **triage → resolve → confirm** | CS agent runs exactly this loop; triage decides "answer / research-then-answer"; confirm step restates what was done + key facts (substring-friendly). |
| Deterministic where it matters | Delegation routing, timeout/fallback logic, answer assembly = plain code. LLM only generates language and decisions inside guardrails. |
| Conversation regression tests vs mock APIs | Our `harness/` simulated-customer suite (τ-bench style): fixed scenarios with hidden goals + required facts, run repeatedly (poor-man's pass^k: run each scenario 3×, all must pass) before every redeploy on Saturday. |
| Immutable releases | Tag a git release per deployed version; redeploys are rollbackable; freeze at 17:00. |
| ADP memory layer | Our Redis two-tier memory (working + long-term) is the same story Sierra tells with ADP — say so on stage: "ADP-style memory, built on the Redis sponsor stack." |
| Experience Manager (annotate → fix → test) | Lightweight: log every conversation JSON; when a test convo goes wrong, snapshot it into the regression suite before fixing. |

**Stage soundbite (Sierra judges in the audience):** "We built our agents the way Sierra says to — declarative goals with deterministic guardrails, scored ourselves with a τ-bench-style simulated customer and a pass^k-style repeat-run gate, and gave the trio an ADP-style shared memory on Redis."

---

## Sources

- Agent SDK product page: https://sierra.ai/product/agent-sdk
- Agent Studio: https://sierra.ai/product/agent-studio · Studio 2.0: https://sierra.ai/blog/agent-studio-2-0
- The Agent Development Life Cycle (core engineering doctrine): https://sierra.ai/blog/agent-development-life-cycle
- Agent OS 2.0 + Agent Data Platform: https://sierra.ai/blog/agent-os-2-0
- τ-bench paper/blog: https://sierra.ai/blog/benchmarking-ai-agents · https://arxiv.org/abs/2406.12045 · code: https://github.com/sierra-research/tau-bench
- τ-bench retrospective (industry influence, pass^k adoption): https://sierra.ai/blog/tau-bench-shaping-development-evaluation-agents
- τ-voice (May 2026): https://sierra.ai/blog/tau-voice-benchmarking-real-time-voice-agents-on-real-world-tasks
- Agents as a service (outcome-based pricing): https://sierra.ai/blog/agents-as-a-service
