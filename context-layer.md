# context-layer.md — The Compounding Context Layer (LLM-Wiki-style memory)

Companion docs: `architecture.md` (build plan — §3.2 `redis.ts`, §3.4 `log.ts`, §5 `afterReply`), `spec.md` (§6 "Redis — context layer, not a crutch"), `sierra.md` (§1 Agent Data Platform + Experience Manager, §4 flywheel). This doc is the **research + design** for *expanding* our memory tier from flat key/value Redis into a self-maintaining, compounding knowledge artifact — Karpathy's "LLM Wiki" pattern adapted for an **autonomous A2A runtime** (no human curator, hard latency deadlines). We are not cloning the Sierra harness; we are out-building its ADP story.

Source for the borrow: Karpathy, *LLM Wiki* — https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

---

## 1. The gap we're closing

`architecture.md §3.2` / `spec.md §6` give us three flat Redis tiers: working memory (`convo:{ctx}` lists), long-term facts (`customer:{key}` hashes + semantic search), and a research cache / ticket trail. That is, in practice, **RAG over conversation logs**: every query re-retrieves raw fragments and re-derives the answer. The known failure modes of that pattern (https://vectorize.io/articles/agent-memory-vs-rag):

- **No knowledge evolution** — every chunk is treated as equally current. A customer's correction ("always refund, never replace") or a status change never *sticks*; it's just one more equally-weighted fragment.
- **No entity resolution** — "Dana", "Dana Cole", and the email on order #88231 don't link.
- **No temporal validity** — "last spring" is a keyword, not a time.
- **Symptoms:** users repeat themselves, corrections don't persist, the agent never gets better at a returning customer.

This matters for us specifically because **Track 1 is scored on `pass^k` reliability** (`sierra.md §3`) and **multi-turn coherence** (`spec.md §A`, same `contextId` reused). A memory layer that re-derives per query *is* the inconsistency surface the benchmark punishes. Vectorize names the exact two domains where maintained memory beats RAG: **customer support and personal assistants** — i.e. our trio.

---

## 2. The borrow — Karpathy's LLM Wiki

> "Instead of just retrieving from raw documents at query time, the LLM **incrementally builds and maintains a persistent wiki** … The knowledge is compiled once and then *kept current*, not re-derived on every query."

**Three layers:**

| Layer | Karpathy | Ownership |
|---|---|---|
| **Raw sources** | Curated articles/papers/data, **immutable** | Human supplies; LLM reads, never writes |
| **The wiki** | LLM-generated interlinked markdown: entity pages, concept pages, summaries, a synthesis | **LLM owns entirely** |
| **The schema** | A `CLAUDE.md`/`AGENTS.md` telling the LLM the conventions + ingest/query/maintain workflows | Co-evolved |

**Three operations:** **Ingest** (read a source → discuss → write a summary page → update the index → update 10–15 related entity/concept pages → append the log), **Query** (read the index → drill into relevant pages → synthesize with citations; *good answers get filed back as new pages so explorations compound*), **Lint** (health-check: find contradictions, stale claims newer sources superseded, orphan pages, missing cross-refs, gaps to fill).

**Two navigation files:** `index.md` (content-oriented catalog — page → one-line summary; the LLM reads this *first* on every query; "works surprisingly well … avoids the need for embedding-based RAG infrastructure" at hundreds-of-pages scale) and `log.md` (chronological append-only; consistent `## [date] op | title` prefix → greppable timeline).

**The key property:** the wiki is a **persistent, compounding artifact** — cross-references already there, contradictions already flagged, synthesis already reflects everything read.

---

## 3. The adaptation — human-curated → autonomous runtime

Karpathy's setup assumes a human with Obsidian on one screen, the LLM on the other, ingesting sources *one at a time with supervision*. Our runtime has **no human in the loop** and **hard deadlines** (research 25s / CS 40s / personal 50s, `architecture.md §3.3`). Every borrowed concept needs a runtime translation:

| Karpathy assumes | Our autonomous adaptation |
|---|---|
| Human curates sources into a raw folder | **Conversations and delegations *are* the sources** — auto-ingested from the JSONL transcript |
| Human asks questions, reads Obsidian | The **agent itself** reads `index.md` during its tool loop; no human reader |
| Ingest is slow, supervised, 10–15 pages | Ingest moves **off the hot path** — `afterReply` hook, fire-and-forget under a small budget; a single ingest touches the 1–3 pages it actually changed |
| Human resolves contradictions, prunes orphans | A **maintainer (lint) pass** runs async / between harness runs — the automated replacement for the curator |
| Obsidian markdown files on disk | **Redis-backed markdown pages** keyed by entity (or real files locally) — *enhancement-only*; correctness holds with `REDIS_URL` unset (`spec.md §6` degradation rule) |
| Schema co-evolved by hand | Schema = a fixed wiki-conventions block + the maintainer prompt, written tonight |

The autonomy gap is precisely where the research says explicit engineering is still required: *"conflict reconciliation and write-quality guardrails are where autonomous systems most often need explicit engineering rather than trusting emergent behavior."*

---

## 4. Mapping the three layers onto our stack

| Wiki layer | Our implementation | Already exists? |
|---|---|---|
| **Raw sources (immutable)** | `logs/{agent}.jsonl` — the per-event transcript | ✅ `architecture.md §3.4` `log.ts` |
| **The wiki (LLM-owned)** | Markdown **pages** in Redis: `wiki:customer:{id}`, `wiki:order:{id}`, `wiki:policy:{topic}`, `wiki:product:{sku}`, plus a `wiki:synthesis:{id}` per customer | 🔨 new (extends `redis.ts`) |
| **`index.md`** | `wiki:index` — a Redis hash `{pageKey → one-line summary}`; read first, cheaply, on every query | 🔨 new |
| **`log.md`** | The JSONL logger **is** `log.md` — already append-only and greppable; this is also our **Experience-Manager-lite** (`sierra.md §4`) | ✅ `log.ts` |
| **The schema** | A `WIKI.md` conventions block injected into the maintainer's system prompt | 🔨 new (tonight) |

The neat coincidence: `log.ts` already implements Karpathy's `log.md` *and* Sierra's Experience Manager — append-only chronological record, bad conversations promoted into `harness/scenarios/`. We're not adding a logger; we're recognizing the one we have plays both roles.

---

## 5. Latency-aware operation placement (the core design move)

Karpathy's Ingest/Query/Lint are all interactive and unbounded. We split them by hot-path cost. **This is the central engineering decision.**

| Op | Runs | Budget | What it does |
|---|---|---|---|
| **Query** | **ON** the hot path, inside the tool loop | capped reads | `memory_search` tool: read `wiki:index` (one cheap call) → drill into **≤2** named pages → return as context. This is Anthropic's **just-in-time retrieval** — hold lightweight identifiers (page keys in the index), load slices on demand, never pre-load everything (https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents). |
| **Ingest** | **OFF** the hot path, in `afterReply` | ≤2s, fire-and-forget, non-blocking | Structured-output extraction (reuse the `extract_requests` pattern, `architecture.md §6`) → **upsert** facts into the entity page (merge/supersede, §6 below) → update `wiki:index` → append the log. The reply already went out; ingest losing the race is harmless. |
| **Lint** | **OFF** the hot path — a maintainer sub-agent, run between harness runs / on idle | own deadline | Reconcile contradictions across pages, mark stale claims superseded, fix orphans, flag gaps. Runs in an **isolated context window** and returns a distilled summary (Anthropic sub-agent isolation) — the autonomous stand-in for the human curator. |

Result: the compounding-wiki benefit lands on the **read** side (richer, pre-synthesized context for free) without paying its cost on the **latency-critical** path. An excellent answer after the deadline scores zero (`spec.md §A`); we never let maintenance threaten that.

---

## 6. The write path is the whole game

RAG appends; **memory merges**. The research is blunt: maintained memory's value is entirely in a *"sophisticated write path — extract discrete facts, resolve entities, track temporal validity, and merge/reconcile rather than append."* `qmd` (or any search) indexes the compiled artifact but **does not produce it; the reconcile/update loop is ours to build.**

Concrete ingest write path (the `afterReply` job):

1. **Extract** — one structured-output LLM call over the turn: `[{entity, type, claim, confidence, supersedes?}]`. Same deterministic-placement trick we use for `extract_requests`.
2. **Resolve entity** — match on soft identifiers the PA already pulls (name, email, order IDs — `architecture.md §9.6`). Same person across `contextId`s → same page.
3. **Merge / supersede** — don't append a fact, **rewrite the page**: a new claim that contradicts an old one marks the old one `~~superseded [date]~~`, mirroring Karpathy's "noting where new data contradicts old claims." Temporal validity is explicit.
4. **Re-index** — refresh the page's one-liner in `wiki:index`.
5. **Log** — `## [date] ingest | customer:{id}` to the JSONL.

**Write-quality guardrails** (the part the research flags as needing real engineering, not emergent trust): the ingest job may only write to entity pages, never to policy/product pages (those are read-only knowledge, owned by Linkup/the knowledge engine); a confidence floor on what gets persisted; page size caps that force compaction rather than unbounded growth.

---

## 7. Retrieval: index-first, `qmd` as the stretch

- **Default (hack-scoped):** read `wiki:index` → the agent picks page keys → `get` them. No embeddings, no vector infra. Karpathy: this "works surprisingly well at moderate scale (~100 sources, ~hundreds of pages)" — comfortably above anything the eval will throw at us in 6.5 hours.
- **Stretch (only if scale demands):** `qmd` (https://github.com/tobi/qmd) — local hybrid **BM25 + vector + LLM re-rank** over the markdown, on-device. CLI (`qmd query --json --files --min-score`) so a tool can shell out, **and** an MCP server (`query`/`get`/`multi_get`) so it drops in as a native tool. Returns ranked *paths* above a score cutoff → agent `get`s only what it needs (just-in-time, big token savings). Keep behind the index; do not build day-of unless the index visibly strains.

---

## 8. Why this is good context engineering (not just a feature)

Every Karpathy/wiki mechanism maps cleanly onto an Anthropic context-engineering principle — the design is principled, not bolted-on:

| Anthropic principle (effective-context-engineering) | Our wiki mechanism |
|---|---|
| Context is a **finite attention budget**; "context rot" as tokens grow | Compiled entity pages = "the smallest set of high-signal tokens" — pre-synthesized, so we feed one tight page instead of 20 raw transcript turns |
| **Just-in-time retrieval** — hold identifiers, load at runtime | `wiki:index` holds page keys; load ≤2 pages on demand inside the loop |
| **Compaction** — summarize, reinitiate from summary, keep decisions/facts | An entity page **is** the compaction of that customer's raw transcript history; ingest is continuous compaction |
| **Structured note-taking / external memory** (the `NOTES.md` / memory-tool pattern, `memory_20250818`: `view/create/str_replace/insert`) | The wiki **is** the agent's external `NOTES.md`; ingest = `str_replace`/`insert` into entity pages |
| **Sub-agent context isolation** — clean window, return distilled summary | The lint/maintainer agent runs isolated, returns a digest — keeps the customer-facing agents lean |
| **Tool minimalism** | Two tools only: `memory_search` (query) + `memory_save` (enqueue ingest). No bloated surface. |

Anthropic API primitives we can name-drop / lean on if useful: server-side **compaction** (`compact_20260112`), **tool-result clearing** (`clear_tool_uses_20250919`), and the client-implemented **memory tool** (`memory_20250818`). Sources: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents · https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools

---

## 9. Fits our existing constraints

- **Degradation rule (`spec.md §6`, `architecture.md §3.2`):** the wiki is **enhancement-only**. Pages are a cache of synthesis; with `REDIS_URL` unset, `memory_search` returns `""` and the agent answers statelessly. Correctness *never* depends on the wiki. Unchanged.
- **Deadlines (`§3.3`):** query reads are capped (index + ≤2 pages); ingest and lint are off the hot path. The compounding benefit is free on read.
- **Concurrency (`§5`):** pages are keyed by entity/`contextId`; no module-level state. Ingest is idempotent (upsert), so parallel harness conversations can't corrupt a page.
- **Interface stability:** this lives *behind* the existing `mem.*` interface (`§3.2`). `getProfile`/`saveFact` become `getPage`/`ingestSource`; the agent-kit and tools don't change shape.

---

## 10. Minimal build (what we actually ship in the window)

Scoped to ~one focused block (`spec.md §7` timeline, the "Redis memory into PA/CS" slot), ~60 lines over `redis.ts`:

```ts
// redis.ts additions — all withTimeout(1000), no-throw, no-op without REDIS_URL
mem.getIndex(): Promise<Record<string,string>>            // HGETALL wiki:index
mem.getPage(key): Promise<string>                          // GET wiki:{key}  (markdown)
mem.ingestSource(contextId, turn): Promise<void>           // extract → resolve → merge → reindex → log
mem.queryWiki(query): Promise<string>                      // index → pick ≤2 pages → concat
mem.lint(): Promise<string>                                // maintainer pass, called off hot path
```

- **Tools** (`tools.ts`): `memory_search` → `queryWiki`; `memory_save` → enqueue `ingestSource`. Two tools, both no-op safely.
- **Wiring** (`agent-kit/runAgent.ts §5`): `buildContext()` calls `queryWiki`; `afterReply()` fire-and-forgets `ingestSource`. Hooks already exist.
- **Schema:** a `WIKI.md` conventions block (page types, supersede syntax, what's read-only) injected into the maintainer prompt — write tonight.
- **Harness tie-in (`§7`):** add a scenario where **turn 2 corrects turn 1** ("actually, always refund me, don't replace") and a later turn must honor it — this directly tests merge/supersede, the thing RAG-over-logs fails. Promote any failure into the scenario set (the flywheel).
- **Stretch only:** `qmd` MCP/CLI; the lint maintainer as a true background sub-agent; cross-`contextId` identity merge.

**Updated stage soundbite** (upgrades `sierra.md §4`): *"ADP-style shared memory on Redis — but instead of RAG-over-logs, our agents compile a self-maintaining wiki: every conversation is ingested into interlinked entity pages that merge corrections and flag contradictions, so the trio actually gets better at a returning customer instead of re-deriving them every turn."*

---

## Sources

- Karpathy, *LLM Wiki* (the borrow): https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Anthropic, *Effective context engineering for AI agents* (attention budget, just-in-time retrieval, compaction, note-taking, sub-agent isolation, tool minimalism): https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic cookbook, *Context-engineering tools* (compaction `compact_20260112`, tool-result clearing `clear_tool_uses_20250919`, memory tool `memory_20250818`): https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
- Vectorize, *Agent Memory vs RAG* (compiled-memory write path, RAG failure modes, CS/PA as the canonical case): https://vectorize.io/articles/agent-memory-vs-rag
- `qmd` — local hybrid markdown search (BM25 + vector + rerank, CLI + MCP): https://github.com/tobi/qmd
- Cross-refs: `architecture.md` §3.2/§3.4/§5/§6/§9.6 · `spec.md` §6/§A · `sierra.md` §1/§4
