# SPEC — Track 1 (A2A) Winning Architecture

Hackathon: The Generative UI/A2A Hackathon — Google London, Sat 13 June 2026.
Team task: build **three A2A agents** for customer service — a **Personal Agent**, a **Customer Service (CS) Agent**, and a **Research Agent** — chained as:

```
simulated customer → Personal Agent → CS Agent → Research Agent
```

Judged on a **held-out test set**. Scoring:

- **50%** — your three agents working together end-to-end.
- **50%** — *each* of your agents working in a pipeline where the **other two roles are filled by foreign agents** (other teams' / reference agents).

This scoring model dictates everything below. The single most important design conclusion:

> **Interoperability is the score.** Every agent must be a fully standalone, spec-compliant A2A server that assumes *nothing* about who is upstream or downstream of it. Private conventions between your own agents are worth at most 50% of the score; robustness to strangers is worth the other 50% — and most teams will fail there.

---

## 1. The one thing to confirm at kickoff (11:30)

**Which A2A protocol version does the judging harness speak?** The ecosystem is mid-migration and fragmented (verified 12 June 2026):

| Component | Protocol version | Wire format |
|---|---|---|
| A2A spec (a2a-protocol.org) | **v1.0.1** | JSON-RPC methods `SendMessage`/`GetTask`; **no `kind` discriminator** on Parts; enums `TASK_STATE_COMPLETED`, `ROLE_USER` |
| `a2a-sdk` (PyPI, latest = 1.1.0) | v1.0 | new format (fresh `pip install` gets this) |
| `@a2a-js/sdk` (npm, latest = 0.3.13) | **v0.3.0** | JSON-RPC methods `message/send`, `tasks/get`; Parts use `{"kind":"text",...}`; states `completed`, `input-required`; card at `/.well-known/agent-card.json` |
| `@ag-ui/a2a-middleware` (CopilotKit) | pins `@a2a-js/sdk ^0.2.2` | **v0.2/0.3** legacy format; 0.2 card path was `/.well-known/agent.json` |

The breaking change between dialects is real: v1.0 removed the `kind` discriminator on `Part` objects and renamed every JSON-RPC method, so v0.3 clients and v1.0 servers **cannot talk to each other at all**.

**Questions to ask the organizers verbatim:**
1. Which SDK/protocol version does the test harness use to call our agents? (Bet: **v0.3.x**, because CopilotKit's tooling and most public tutorials are on it.)
2. How do we register agents — three public URLs with agent cards? Any required card fields/skill IDs?
3. Does the harness send `message/send` (blocking) or `message/stream` (SSE)? What's the per-message timeout?
4. In the cross-team configuration, how does our Personal Agent discover the foreign CS Agent's URL (passed in the message? env var? registry)?
5. Does the simulated customer hold multi-turn conversations (same `contextId` reused), and does it react to `input-required`?
6. Is there a required output format the grader parses (text in artifacts vs status message)?

**Default plan if no answer:** build on **v0.3 wire format** (`@a2a-js/sdk` 0.3.13) and add the compat shims in §4. v0.3 is what every other team copying tutorials will speak, and the cross-team half of the score is measured against *them*.

---

## 2. Architecture overview

```
                        ┌─────────────────────────────────────────────┐
                        │           Shared (enhancement only)         │
                        │   Redis: Agent Memory Server + raw Redis    │
                        └──────▲──────────────▲──────────────▲────────┘
                               │              │              │
   simulated        ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   customer ──A2A──▶│ PERSONAL     │─▶│ CUSTOMER     │─▶│ RESEARCH     │
   (test harness)   │ AGENT :9001  │  │ SERVICE :9002│  │ AGENT :9003  │
                    │ customer     │  │ triage,      │  │ Linkup web   │
                    │ advocate,    │  │ resolve,     │  │ search +     │
                    │ memory,      │  │ delegate     │  │ synthesis    │
                    │ delegation   │  │ research     │  │              │
                    └──────────────┘  └──────────────┘  └──────────────┘
                      A2A server +      A2A server +      A2A server
                      A2A client        A2A client        (leaf)
```

Three independent processes, each:
- a complete **A2A server** (agent card + JSON-RPC endpoint + SSE streaming),
- deployable standalone with one env var file,
- functional **even if Redis is down and the downstream agent is unreachable** (degraded but never erroring).

Downstream URLs are **runtime-configurable** (env var `CS_AGENT_URL`, `RESEARCH_AGENT_URL`, *and* overridable per-request via message metadata if the harness passes URLs) so the organizers can swap foreign agents into the chain instantly.

**Stack:** TypeScript + Bun + `@a2a-js/sdk@0.3.13` (server + client), Express (the SDK's supported server binding), LLM = **Gemini 2.5 Flash** for routine turns / **Gemini 2.5 Pro** for synthesis (Google judges, Google Cloud credits; keep an Anthropic key as fallback). Deploy: Cloud Run (or local + cloudflared/ngrok tunnels as instant fallback — get public URLs working in the first hour, not the last).

Why one stack and not "show off three frameworks": the score is automated, not vibes. Uniform stack = shared compat/robustness code across all three agents = three hardened agents instead of one.

---

## 3. Scoring-model analysis → design rules

Decompose what the harness can actually measure, and what each implies:

**A. Your trio end-to-end (50%).** Quality of final answers to simulated customer messages. Implies: good prompts, real research capability, memory of prior turns, and *fast* responses (a harness always has timeouts; an excellent answer after the timeout scores zero).

**B. Your Personal Agent + foreign CS/Research (16.7%).** Your PA must produce requests a *stranger* CS agent understands. Implies: delegate in **plain natural language**, self-contained (include all relevant customer context *in the text* of the message, since the foreign agent can't see your Redis), parse whatever comes back (task or message, artifacts or status text).

**C. Your CS Agent + foreign PA/Research (16.7%).** Your CS agent receives arbitrary phrasings from foreign PAs and must query a foreign research agent. Implies: zero assumptions about input structure; research queries phrased as standalone natural-language questions; tolerate research agents that are slow, return junk, or are unreachable (answer from own knowledge and say so).

**D. Your Research Agent + foreign PA/CS (16.7%).** Foreign CS agents will send anything from a keyword to a paragraph. Implies: treat any text as a research query, never ask clarifying questions (a foreign caller likely won't handle `input-required`), always return *something* useful, fast.

**Universal rules derived (apply to all three agents):**

1. **Never return a failed task and never throw.** Catch everything; worst case return a completed task with a best-effort text answer. A `failed` state or HTTP 500 is a guaranteed zero for that test case.
2. **Always respond with a `completed` Task** (not a bare Message) — task-shaped responses are what both polling and streaming clients of every SDK handle. Exception only if organizers say otherwise.
3. **Put the answer text in three places**: the final `TaskStatusUpdate`'s `status.message`, a `TextPart` artifact, and task `history`. Different client implementations extract text from different fields; redundancy costs nothing and rescues points.
4. **Support `message/send` AND `message/stream`.** The SDK gives both; declare `capabilities.streaming: true`.
5. **Avoid `input-required`.** Make a reasonable assumption, state it in the answer, and complete. A simulated customer or foreign agent that doesn't implement the follow-up loop will leave your task hanging forever. (If the organizers confirm the harness handles multi-turn, relax this for the Personal Agent only.)
6. **No auth.** Open endpoints, permissive CORS, HTTP fine. `securitySchemes` empty.
7. **Plain text is the lingua franca.** Emit `TextPart` always; optionally attach a parallel `DataPart` with structured data (ticket id, sources) as a bonus for smart consumers — never *require* it to be read.
8. **Be lenient in what you accept**: extract text from *any* part shape (see §4), accept missing `contextId`/`messageId`, generate them if absent.
9. **Respond fast.** Budget: Research ≤ 25s, CS ≤ 40s, PA ≤ 50s end-to-end. Enforce with internal deadlines — if downstream hasn't answered by the deadline, return the best available partial answer.
10. **Don't depend on shared state for correctness.** Redis memory is an enhancer for the 50% trio score and the sponsor narrative; agents B/C/D configurations must score full marks without it.

---

## 4. Interop/compat layer (the secret weapon)

A small shared `packages/compat` module used by all three agents. This is the highest-leverage code of the day, because cross-team pipelines mostly die on wire-format trivia:

**Server-side (inbound leniency):**
- JSON-RPC **method aliasing**: accept `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`, `tasks/resubscribe` (v0.2/0.3) *and* `SendMessage`, `SendStreamingMessage`, `GetTask`, `CancelTask`, `SubscribeToTask` (v1.0) — middleware rewrites v1.0 method names + part shapes into v0.3 before the SDK handler sees them, and mirrors the translation on the way out (member-name-discriminated parts, `TASK_STATE_*` enums) when the request came in as v1.0.
- **Part normalization** — extract text from any of: `{kind:"text", text}` (v0.3), `{type:"text", text}` (v0.2 variants), `{text}` (v1.0), `{kind:"data", data}` / `{data}` (JSON-stringify it), file parts (note filename, ignore bytes). Concatenate everything textual; never crash on unknown shapes.
- **Agent card at every historical path**: `/.well-known/agent-card.json` (v0.3+), `/.well-known/agent.json` (v0.2), and `/agent-card` for good measure — same card, `protocolVersion: "0.3.0"`.
- Accept absent `params.message.messageId` / `contextId` / malformed `configuration` — default everything.

**Client-side (outbound robustness)** — used by PA→CS and CS→Research:
- Fetch the agent card from both well-known paths; detect dialect from `protocolVersion`; speak v0.3 by default, v1.0 if the card demands it.
- Prefer `message/send` (blocking, simplest); if the response is a non-terminal task, **poll `tasks/get`** every 2s up to the deadline; fall back to `message/stream` if blocking is rejected.
- **Answer extraction waterfall** from any response: artifacts' text parts → `status.message` text → last `agent`-role message in `history` → stringified data parts → raw JSON as last resort.
- Timeout + 1 retry; on total failure return `null` so the calling agent degrades gracefully instead of failing.

**Validation:** run `a2aproject/a2a-inspector` (updated yesterday — actively maintained) against all three agents, plus the `a2a-tck` compliance suite if time permits. Also cross-test: point your PA at a teammate-built *deliberately weird* CS stub (slow, terse, v1.0-formatted, junk-returning) — that's a dress rehearsal for the cross-team half of the score.

---

## 5. Agent designs

### 5.1 Personal Agent (port 9001) — "the customer's advocate"

Receives raw simulated-customer messages. Responsibilities:
1. Understand the customer message (intent, urgency, what they actually need).
2. Recall customer context from memory (Redis, keyed by `contextId` + any customer identifiers found in messages).
3. Decide: answer directly (greetings, thanks, pure-memory questions) vs **delegate to CS Agent** (anything involving orders, products, policies, complaints, account issues — default to delegating; the chain exists to be used and the grader is probably checking that it is).
4. When delegating, compose a **self-contained natural-language brief**: "Customer (name/account if known) reports X. Relevant history: Y. They need Z." — readable by *any* CS agent, no schemas required.
5. Translate the CS reply back into a warm, first/second-person customer-facing answer. Never leak agent-to-agent mechanics ("I asked the CS agent...") — just resolve the issue, plus a natural acknowledgment of history when relevant ("since this is about the order you mentioned earlier...").

Agent card skills: `handle-customer-message` ("Receive any customer message — questions, complaints, requests — and resolve it end-to-end on the customer's behalf, coordinating with customer service"), `customer-context` ("Remember and apply customer history and preferences across the conversation"). Rich `examples` in the skill (foreign orchestrators and LLM routers read these to decide what to send you).

Prompt notes: persona = capable personal assistant employed *by the customer*. Multi-turn aware: full conversation history for the `contextId` goes into context (working memory). Handles multiple issues in one message (enumerate, delegate as one combined brief). If CS is unreachable: answer from general knowledge + memory, honestly noting what it could and couldn't confirm — never "sorry, system error".

### 5.2 Customer Service Agent (port 9002) — "the resolver"

Receives requests from any personal agent (or any client). Responsibilities:
1. Triage the inbound text: what is the issue, what facts are needed to resolve it.
2. Resolve from its own competence first (policy reasoning, troubleshooting steps, empathetic complaint handling, plausible process guidance — order lookups etc. handled with reasonable simulated/process answers since there's no real backend; state assumptions).
3. **Delegate to the Research Agent** whenever current/external/specific facts would materially improve the answer (product specs, comparisons, current policies/prices, news, anything verifiable on the web). Phrase as 1–3 *standalone* natural-language research questions — a foreign research agent gets zero context.
4. Synthesize: research findings + service judgment → a complete, actionable resolution addressed to whoever asked. Include sources when research provided them.
5. Log resolution summary to Redis (ticket trail — sponsor visibility + lets the PA recall past tickets in the trio configuration).

Agent card skills: `resolve-customer-issue` ("Handle any customer service request: questions, complaints, orders, troubleshooting, policy queries; researches facts as needed and returns a complete resolution"), with concrete `examples`.

Prompt notes: professional CS rep persona; always returns a *resolution*, not a referral; if research fails/times out, answer from own knowledge and flag unverified facts. Cap at one research round-trip (latency budget).

### 5.3 Research Agent (port 9003) — "the librarian"

Leaf node, no downstream. Likely the *easiest to score points with* in cross-team mode — make it bulletproof.
1. Treat the entire inbound text as a research request — keywords, questions, or paragraphs all work. Never asks for clarification.
2. **Linkup API** (sponsor; `linkup-sdk`, `depth: "standard"` for speed, `output_type: "sourcedAnswer"`) as primary search; if multiple distinct questions detected, run searches in parallel.
3. Synthesize with Gemini into a dense, factual answer: direct answer first, key facts as bullets, **source URLs listed** (cheap credibility points with both grader-LLMs and humans).
4. Fallback chain: Linkup → Gemini with Google-Search grounding → pure LLM knowledge with an explicit "based on general knowledge as of..." caveat. Never empty, never failed.
5. Cache query→result in Redis (TTL 1h) — repeat test-set queries return instantly.

Agent card skills: `web-research` ("Answer any factual or research question using live web search; returns a synthesized, sourced answer"). `defaultOutputModes: ["text/plain"]`.

Hard deadline 25s: return whatever search results are in hand, synthesized quickly, rather than blow a caller's timeout.

---

## 6. Redis (sponsor) — context layer, not a crutch

Run **Redis Agent Memory Server** (`redis/agent-memory-server`, REST API) + raw Redis, one Redis Cloud instance (free tier) so deployed agents share it.

- **Working memory** (session-scoped, key = `contextId`): full conversation per context — powers multi-turn coherence for PA and CS.
- **Long-term memory** (semantic search): customer facts the PA extracts ("prefers email", "order #4521 was late"), CS resolution summaries. Retrieved via semantic search on each new message.
- **Raw Redis**: research cache, ticket trail (`ticket:{contextId}` list).

Namespacing per agent (`personal`, `cs`) with a shared `customer:{id}` namespace both can read — this is the "agents that actually learn, remember, and collaborate" story Redis judges asked for, and it genuinely lifts trio-mode multi-turn scores.

**Degradation rule:** every Redis call wrapped in try/catch + 1s timeout → on failure, agents run stateless within the SDK's in-process task store. Correctness never depends on Redis.

If the Memory Server is friction on the day, fall back to plain Redis (LPUSH/LRANGE conversation lists + a `HSET` customer profile) — 30 minutes of work, still a full sponsor story.

---

## 7. Repo layout & build order

```
a2a/
├── packages/
│   ├── compat/        # §4: dialect shims, lenient parsing, answer extraction, A2AClientPlus
│   ├── core/          # shared: LLM wrapper (Gemini+fallback), Redis helpers, logging, deadline utils
│   ├── personal-agent/
│   ├── cs-agent/
│   └── research-agent/
├── harness/           # local test harness: simulated customer + weird-agent stubs + eval script
└── spec.md
```

Bun workspaces; each agent = `index.ts` (Express + A2A SDK app), `executor.ts` (AgentExecutor), `card.ts`, `prompt.ts`. **One agent template written once, instantiated three times** — only card + prompt + delegation target differ.

**Build order on the day (hacking 11:30–18:00, ~6.5h):**

| When | What |
|---|---|
| 11:30–12:00 | Confirm §1 questions. Scaffold repo, deploy a hello-world A2A agent to a public URL (prove the deployment path immediately) |
| 12:00–13:30 | `compat` + `core` + agent template; all three agents running with naive prompts; chain works end-to-end locally |
| 13:30–14:30 | Deploy all three publicly; run a2a-inspector on each; register with organizers early |
| 14:30–16:00 | Linkup into Research; Redis memory into PA/CS; prompt quality pass on all three |
| 16:00–17:00 | **Adversarial testing**: weird-agent stubs, v1.0-dialect caller, timeout storms, garbage inputs; fix everything that isn't a graceful completed task. Cross-test with a neighboring team if possible (best proxy for the real cross-team eval) |
| 17:00–18:00 | Freeze features. Latency tuning, redeploy, smoke-test the registered URLs, write the 1-slide architecture summary for the stage |

---

## 8. Risk register

| Risk | Mitigation |
|---|---|
| Harness speaks v1.0, we built v0.3 | Compat layer accepts both inbound (§4); outbound dialect auto-detected from agent card; worst case flip one constant |
| Organizers provide a mandatory starter template at kickoff | Architecture is template-agnostic: executors, prompts, compat and memory code port into any A2A server skeleton in <1h |
| Foreign agents hang/crash in cross-team mode | Deadlines + fallback answers everywhere (§3 rules 1, 9; §4 client) |
| Wi-Fi/deploy issues at venue | Deploy in hour one; tunnel fallback; everything also runs fully local |
| Grader parses answers from a field we didn't fill | Answer text in artifacts + status message + history (§3 rule 3) |
| `input-required` deadlock with foreign callers | Avoid the state entirely; assume-and-state (§3 rule 5) |
| LLM rate limits mid-eval | Gemini Flash default (high quota), Anthropic fallback wired into `core` LLM wrapper |
| Multiple issues / adversarial customer messages in test set | Prompts explicitly handle multi-intent, vague, angry, and out-of-scope messages — always a constructive completed answer |

---

## 9. Stage-worthiness (tiebreaker)

Top scorers present prompts/architecture/learnings. Our three headlines, pre-packaged:
1. **"We treated interop as the product"** — dialect-compat layer + lenient parsing + answer redundancy, born from the v0.3/v1.0 ecosystem split (show the SDK version table from §1).
2. **"Degrade, never fail"** — every agent answers something useful even with both neighbors and Redis dead; demo by killing the research agent live.
3. **"Shared memory, zero coupling"** — Redis two-tier memory lifts multi-turn quality without ever being a correctness dependency.

---

## Appendix: verified reference links

- A2A spec v1.0.1: https://a2a-protocol.org/latest/specification/ (method mapping §5.3; kind-discriminator breaking change in Appendix A.2.1)
- Life of a Task (message-vs-task, contextId semantics): https://a2a-protocol.org/latest/topics/life-of-a-task/
- JS SDK (v0.3.0 protocol, card at `/.well-known/agent-card.json`): https://github.com/a2aproject/a2a-js — npm `@a2a-js/sdk@0.3.13` (`next` = 1.0.0-alpha.0)
- Python SDK: PyPI `a2a-sdk` 1.1.0 = protocol v1.x; pin `a2a-sdk<1` for v0.x dialect
- Samples: https://github.com/a2aproject/a2a-samples · Inspector: https://github.com/a2aproject/a2a-inspector · TCK: https://github.com/a2aproject/a2a-tck
- CopilotKit A2A middleware (`@ag-ui/a2a-middleware@0.0.2`, pins `@a2a-js/sdk ^0.2.2`): https://docs.copilotkit.ai + `npx copilotkit@latest create -f a2a`
- Redis Agent Memory Server (working/long-term memory, REST+MCP): https://redis.github.io/agent-memory-server/ · https://github.com/redis/agent-memory-server · redis.io/iris
- Linkup (sponsor search API; $5 free credit, `linkup-sdk` on npm/PyPI): https://docs.linkup.so
- Event page: https://luma.com/a2a-and-generative-ui-hackathon
