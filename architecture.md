# architecture.md — Implementation Plan (Track 1: A2A)

Companion docs: `spec.md` (strategy/scoring analysis), `sierra.md` (sponsor doctrine), `tau-bench-design.md` (failure modes → design rules, tool tables). This doc is the **build plan**: repo structure, code skeletons, tools, the eval harness, deployment, and the runbook. Target: everything in §2–§8 buildable in the 6.5h hacking window because the skeleton is rehearsed tonight.

---

## 1. Stack (pinned)

| Thing | Choice | Why |
|---|---|---|
| Runtime / PM | Bun + workspaces | Team default; fast |
| Language | TypeScript | Shared types across agents/compat/harness |
| A2A | `@a2a-js/sdk@0.3.13` (server + client) | Protocol v0.3.0 = the dialect other teams & CopilotKit tooling speak (see spec.md §1) |
| HTTP | Express 4 (SDK's supported binding) + `cors` | |
| LLM | `@google/genai` → Gemini 2.5 Flash (loops) / Pro (synthesis); `@anthropic-ai/sdk` Claude Sonnet as env-swappable fallback | τ-bench: native function calling only |
| Math | `mathjs` (scoped `evaluate`) | the `calculate` tool — never LLM arithmetic |
| Search | `linkup-sdk` | Sponsor; sourcedAnswer output |
| Memory | `redis` client (+ Agent Memory Server REST if smooth, else raw Redis) | Sponsor; enhancement-only |
| Deploy | Cloud Run (Dockerfile per agent) + `cloudflared` tunnel fallback | Public URLs in hour one |
| Validation | `a2a-inspector` (run against each deployed agent) | |

---

## 2. Repo layout

```
a2a/
├── package.json                 # bun workspaces
├── packages/
│   ├── core/
│   │   ├── llm.ts               # tool-calling loop, Gemini↔Claude behind one interface
│   │   ├── tools.ts             # calculate, linkup, redis-memory, a2a-delegate factories
│   │   ├── redis.ts             # guarded client (1s timeout, no-throw)
│   │   ├── deadline.ts          # Deadline class, withTimeout()
│   │   └── log.ts               # JSONL transcript logger
│   ├── compat/
│   │   ├── inbound.ts           # Express middleware: v1.0→v0.3 request rewrite
│   │   ├── parts.ts             # partsToText(): any Part shape → string
│   │   ├── client.ts            # A2AClientPlus: card discovery, send+poll, extractAnswer()
│   │   └── card.ts              # serveCard(app, card) at all well-known paths
│   ├── agent-kit/
│   │   └── runAgent.ts          # the ONE server template all three agents instantiate
│   ├── personal-agent/          # index.ts, card.ts, prompt.ts, tools.ts
│   ├── cs-agent/                #   "        "        "        "
│   └── research-agent/          #   "        "        "
├── harness/
│   ├── scenarios/*.json         # τ-style task files
│   ├── simulator.ts             # LLM user sim (temp 1.0, ###STOP###)
│   ├── score.ts                 # substring + LLM-judge + latency
│   ├── run.ts                   # CLI: bun harness/run.ts --k 3 [--agent-url ...]
│   └── stubs/weird-cs.ts        # hostile foreign-agent stub (terse/slow/v1.0)
├── deploy/
│   ├── Dockerfile               # shared, ARG AGENT=personal-agent
│   └── deploy.sh                # gcloud run deploy ×3
└── *.md                         # spec, sierra, tau-bench-design, architecture
```

---

## 3. `packages/core`

### 3.1 `llm.ts` — one tool-loop to rule them all

```ts
export interface Tool { name: string; description: string; parameters: object;
                        run(args: any, ctx: AgentCtx): Promise<string> }
export interface LlmOpts { system: string; tools: Tool[]; maxSteps?: number;  // default 10
                           model?: "flash"|"pro"; temperature?: number }      // default 0

export async function runToolLoop(messages: Msg[], opts: LlmOpts, dl: Deadline): Promise<string>
```

- Native function calling (Gemini `tools:[{functionDeclarations}]`); loop: call model → if functionCall, run tool, append result, repeat; stop on text-only response, `maxSteps`, or `dl.nearlyDone()` (then force a final no-tools "answer now with what you have" call).
- Every tool `run` is wrapped: errors become `"TOOL_ERROR: <msg>"` strings fed back to the model — the loop never throws.
- Provider fallback: if Gemini errors/429s twice, transparently re-run the turn on Claude (`LLM_FALLBACK=anthropic` also forces it). Same `Tool` interface mapped to Anthropic `tool_use`.
- Temp 0 everywhere; the optional customer-facing rephrase pass uses 0.3.

### 3.2 `redis.ts` — guarded, optional

```ts
export const mem = {
  getConversation(contextId): Promise<Msg[]>,        // LRANGE convo:{ctx}
  appendConversation(contextId, msgs): Promise<void>,
  getProfile(key): Promise<Record<string,string>>,   // HGETALL customer:{key}
  saveFact(key, fact): Promise<void>,
  cacheGet/cacheSet(queryHash, ttl=3600),            // research cache
  logTicket(contextId, summary),
}
```
Every call: `withTimeout(1000)`, catch-all → return empty/no-op. If the Agent Memory Server spins up cleanly on the day, swap `getProfile/saveFact` internals for its `/v1/long-term-memory` semantic search — interface unchanged. **Correctness must hold with `REDIS_URL` unset.**

### 3.3 `deadline.ts`

```ts
new Deadline(ms)  // .remaining(), .nearlyDone(reserveMs=4000), .signal (AbortSignal)
```
Budgets (env-overridable): research 25s, CS 40s, personal 50s. Passed down call chains so inner work always finishes before the outer caller's timeout.

### 3.4 `log.ts`
Append one JSONL line per event (`{ts, agent, contextId, taskId, type: inbound|llm|tool|delegate|outbound, payload}`) to `logs/{agent}.jsonl` + stdout. This is our Experience-Manager-lite: any bad conversation gets copied into `harness/scenarios/` before fixing (sierra.md §4 flywheel).

---

## 4. `packages/compat` (build FIRST — see spec.md §4 for rationale)

- **`inbound.ts`**: Express middleware before the SDK's JSON-RPC handler. Maps method names (`SendMessage`→`message/send`, `GetTask`→`tasks/get`, `SendStreamingMessage`→`message/stream`, `CancelTask`→`tasks/cancel`, `SubscribeToTask`→`tasks/resubscribe`); normalizes v1.0 parts (`{text}`→`{kind:"text",text}`, `{raw,filename,mediaType}`→file part, bare `{data}`→data part); defaults missing `messageId`/`contextId` (uuid); strips unknown config fields. Tags the request so the response serializer mirrors back v1.0 shapes (member-name parts, `TASK_STATE_*`, `ROLE_*`) when the caller spoke v1.0.
- **`parts.ts`**: `partsToText(parts: unknown): string` — the lenient waterfall (kind/type/text/data/file). Total function, never throws, returns "" worst case.
- **`card.ts`**: serve identical card at `/.well-known/agent-card.json`, `/.well-known/agent.json`, `/agent-card`; plus `GET /healthz`.
- **`client.ts` — `A2AClientPlus`** (used by PA→CS, CS→Research, and the harness):

```ts
const client = await A2AClientPlus.discover(url);     // tries both card paths, detects dialect
const answer = await client.ask(text, { contextId, deadline });
// ask(): message/send (blocking config) → if non-terminal task: poll tasks/get @2s
//        → if method rejected: retry as v1.0 SendMessage → extractAnswer(result)
// extractAnswer(): artifacts text → status.message text → last agent msg in history
//                  → stringified data parts → raw JSON. Returns null only on hard failure.
```

---

## 5. `packages/agent-kit/runAgent.ts` — the single agent template

```ts
export interface AgentDef {
  card: AgentCard;
  systemPrompt: string;
  tools: Tool[];
  buildContext?(contextId: string): Promise<Msg[]>;  // e.g. memory recall
  afterReply?(ctx, reply): Promise<void>;            // e.g. memory save, ticket log
}
export function runAgent(def: AgentDef, port: number): void
```

Internals (one `AgentExecutor` impl for all three):
1. `execute(requestContext, eventBus)`: publish initial Task (`submitted`) → `working` status with a short "on it" message (streams keep clients alive) → `partsToText()` the inbound message → `buildContext()` → `runToolLoop()` under the agent's `Deadline`.
2. Publish answer **redundantly**: artifact (`TextPart`) → final `completed` status whose `status.message` carries the same text → SDK task history covers the third location. (spec.md rule 3.)
3. **Top-level catch**: any throw → still publish `completed` with best-effort text ("Here's what I can tell you so far…"). `failed` is never emitted. A watchdog at `deadline - 2s` force-completes with partial output.
4. `cancelTask`: mark canceled, no-op gracefully.
5. Server wiring: `cors()` (allow `*`), compat inbound middleware, `DefaultRequestHandler` + `InMemoryTaskStore`, card serving, `/healthz`.

**Concurrency note (easy to miss):** the harness will likely run many test conversations in parallel. No module-level mutable state; everything keyed by `contextId`/`taskId`. `InMemoryTaskStore` is fine **only at exactly one instance** — set Cloud Run `--min-instances 1 --max-instances 1`. If we ever need >1 instance, swap in a 30-line Redis-backed TaskStore (load/save JSON by taskId) — interface is two methods.

Agent cards (per `card.ts` in each agent): `protocolVersion: "0.3.0"`, `capabilities: {streaming: true, pushNotifications: false}`, `defaultInput/OutputModes: ["text/plain"]`, no `securitySchemes`, and **rich skill `examples`** (foreign LLM routers read these to decide what to send us — write 3–4 realistic example utterances per skill).

---

## 6. Tools (`core/tools.ts` factories + per-agent wiring)

Per-agent assignments and rationale live in `tau-bench-design.md` §3. Implementation notes:

| Tool | Impl | Gotchas |
|---|---|---|
| `calculate` | `mathjs.evaluate(expr)` in try/catch, result formatted `toFixed(2)` when money-like | Description tells the model: "ALWAYS use this for any arithmetic, totals, differences, conversions." |
| `linkup_search` | `linkupClient.search({query, depth:"standard", outputType:"sourcedAnswer"})`; return answer + `sources[].url` list | `withTimeout(12_000)`; on failure return `"SEARCH_UNAVAILABLE"` so the prompt's fallback instruction kicks in. Parallelize multi-question requests with `Promise.allSettled`. Cache via `mem.cacheGet/Set`. |
| `linkup_fetch` | `outputType:"structured"`? no — use linkup fetch endpoint; fallback plain `fetch()`+strip HTML | Only when a URL appears in the request. |
| `ask_customer_service` / `ask_research_agent` | `A2AClientPlus.discover(env URL).ask(brief, {contextId, deadline: dl.child(...)})` | Returns `"DOWNSTREAM_UNAVAILABLE"` on null → prompts instruct degrade-gracefully behavior. URL override order: request metadata → env var. Re-ask at most once (τ² rule). |
| `memory_search`/`memory_save` | `mem.*` wrappers | No-ops without Redis; never block >1s. |
| `log_ticket` | `mem.logTicket` fire-and-forget | |
| `extract_requests` / `triage` | Not real tools — **structured-output first LLM call** (JSON schema: `[{id, request, route, needs_research}]`) before the main loop; checklist is injected into the system prompt of the loop | Deterministic placement beats hoping the model self-organizes (τ failure #3). |

---

## 7. The harness (`harness/`) — our τ-bench clone

**Build this in parallel with the agents, not after.** It's also our demo evidence.

### 7.1 Scenario schema (`scenarios/*.json`)

```json
{
  "id": "compound-return-and-window",
  "instruction": "You are Dana Cole. Your espresso machine (order #88231, £249.00) arrived broken and you want a replacement or refund. You ALSO want to know the return window for a kettle bought 3 weeks ago. Mention both together. If only one is possible you prefer the refund. You are irritated and brief.",
  "persona": "hard",
  "required_substrings": ["88231", "249"],
  "nl_assertions": [
    "the agent addressed BOTH the espresso machine and the kettle question",
    "the agent stated a concrete next step or resolution for the broken machine"
  ],
  "max_turns": 8
}
```

### 7.2 `simulator.ts`
- Gemini Flash, **temp 1.0**, system prompt = generic user-sim rules (τ's: stay in character, reveal info only when asked, one intent at a time unless instructed, emit `###STOP###` when goals met or clearly impossible) + scenario `instruction`.
- Talks to the target PA over **real A2A via `A2AClientPlus`** (same `contextId` across turns → also tests multi-turn/memory and our wire format end-to-end).
- Turn cap `max_turns`; record per-turn latency.

### 7.3 `score.ts`
`r = substrings_ok && nl_ok && !timeout`. Substrings checked across all agent replies (normalize whitespace; for money also accept `249.00`). NL assertions: one cheap judge call (Flash, temp 0) per assertion over the transcript → strict `true/false`. Output per run: `{scenario, trial, pass, fail_reasons[], turns, p95_turn_latency}`.

### 7.4 `run.ts`
- `--k 3` (default): every scenario ×k, **all-pass gate**; prints pass^1 and pass^k table + aggregated fail reasons.
- `--agent-url`: point at deployed PA (default localhost) — same command smoke-tests production after each deploy.
- `--stub-cs`: starts `stubs/weird-cs.ts` and points PA at it → the cross-team drill. The stub cycles behaviors per request: 15s delay; one-word answers; v1.0-dialect responses; refuses with a JSON error body; returns a data-part-only response. PA must still produce passing customer answers (degraded ok, failed never).
- Failed transcripts auto-saved to `harness/failures/{scenario}-{trial}.json` → triage → either fix or promote to a new scenario.

### 7.5 Scenario set (≥10, from tau-bench-design.md §4)
single-issue · compound×2 · compound×3 · needs-web-research · memory follow-up (turn 2 references turn 1) · vague one-liner · mind-change mid-convo · angry+terse · should-refuse (impossible/forbidden ask) · ID-echo (obscure order ID must reappear verbatim) · numeric (forces `calculate`).

---

## 8. Deployment

### 8.1 Dockerfile (shared)
```dockerfile
FROM oven/bun:1 AS base
WORKDIR /app
COPY . .
RUN bun install --frozen-lockfile
ARG AGENT
ENV AGENT=${AGENT}
CMD ["sh", "-c", "bun packages/${AGENT}/index.ts"]
```

### 8.2 `deploy.sh`
```bash
for A in personal-agent cs-agent research-agent; do
  gcloud run deploy $A --source . --region europe-west2 \
    --set-build-env-vars AGENT=$A \
    --min-instances 1 --max-instances 1 --timeout 300 --allow-unauthenticated \
    --set-env-vars "$(cat .env.deploy | tr '\n' ',')"
done
```
- `--min-instances 1`: kills cold starts (a cold start during the eval = blown deadline) **and** makes `InMemoryTaskStore` safe.
- Deploy order: research → cs (gets research URL) → personal (gets cs URL). The **card `url` field must be the public URL** (env `PUBLIC_URL`), not localhost — clients re-derive the endpoint from the card; this is a classic silent killer.
- Fallback: `cloudflared tunnel --url http://localhost:900X` ×3 — rehearse tonight, write the three commands in the README.
- After every deploy: `bun harness/run.ts --agent-url <PA-url> --k 1` smoke + a2a-inspector pass on all three.

### 8.3 Env vars
```
GEMINI_API_KEY=            ANTHROPIC_API_KEY=        LLM_FALLBACK=
LINKUP_API_KEY=            REDIS_URL=                 PUBLIC_URL=
CS_AGENT_URL=              RESEARCH_AGENT_URL=
DEADLINE_MS=               LOG_DIR=
```

---

## 9. Gaps caught / judgment calls (things not yet in the other docs)

1. **Blocking-send semantics**: many simple clients call `message/send` once and read the response — they never poll. Our executor therefore completes the task *within* the send handler (we're fast enough under our deadlines), so the immediate JSON-RPC response already contains the final `completed` task. Streaming and polling still work; blocking-only clients lose nothing.
2. **Parallel-harness safety**: no global state; one instance per agent; everything contextId-keyed (see §5). Test it: `run.ts --parallel 5`.
3. **"On it" early status message**: first `working` event carries a brief text — keeps SSE clients and impatient pollers from declaring us dead, costs nothing.
4. **Python contingency**: if organizers mandate a Python starter at kickoff, the port is mechanical: `pip install "a2a-sdk<1"` (v0.x dialect), same executor shape (`AgentExecutor.execute(ctx, event_queue)`), prompts/tools/harness logic translate 1:1. Decide by 12:00, don't dither.
5. **Organizer-provided scenario pack**: if a policy doc / mock company / product data drops at kickoff, it slots into: CS system prompt (workflow-checklist rewrite, 20 min), `lookup_policy` tool if it's big, and 3–4 new harness scenarios derived from it. Assign one person immediately.
6. **Customer identity across contexts**: cross-team mode may reuse a customer name with a fresh `contextId`. PA extracts soft identifiers (name, email, order IDs) from messages and merges Redis profiles on them — bonus continuity in trio mode, harmless if foreign agents are upstream.
7. **Rate limits under the eval burst**: the test set may hit all teams simultaneously. Flash-first keeps us in high-quota tiers; the Claude fallback also covers 429 storms. No client-side queueing — parallel conversations must not serialize.
8. **Optional stage UI (only if hours remain)**: `npx copilotkit@latest create -f a2a` pointed at our three live agents = a visual mesh demo for the presentation. Zero score impact — strictly post-freeze (after 17:00) work.
9. **What we deliberately do NOT build**: push notifications (declare `false`), auth, extended agent card, gRPC/REST bindings, multi-region, fine-tuning. All cost, no score.

---

## 10. Tonight (pre-hackathon checklist) & team split

**Tonight (~2h):**
- [ ] Accounts + keys in `.env`: Gemini (AI Studio), Anthropic, Linkup, Redis Cloud free tier, GCP project with Cloud Run + billing, `gcloud` authed
- [ ] Scaffold repo per §2; `bun install` all pinned deps; commit
- [ ] Hello-world agent on `@a2a-js/sdk` running locally; deploy it to Cloud Run once (warm the path: source builds, org policies, region quota); hit its card from a phone
- [ ] `cloudflared` installed + tunnel tested
- [ ] Skim SDK express sample + `a2a-inspector` README
- [ ] Print/save offline: spec.md, tau-bench-design.md (venue Wi-Fi insurance)

**Team split (3 people, after kickoff):**
- **A — Platform**: compat layer, agent-kit, deploys, inspector runs, §9.1–9.3 correctness
- **B — Agents**: prompts (goals/guardrails per sierra.md), tools wiring, the three cards, scenario-pack ingestion if provided
- **C — Eval**: harness + scenarios + stubs from minute one; runs the pass^k gate continuously; owns the failure→scenario flywheel and latency tracking

Hour-by-hour schedule: `spec.md` §7. Freeze 17:00, then smoke-test registered URLs ×3 and prep the 3-headline stage summary (`sierra.md` §4 soundbite).
