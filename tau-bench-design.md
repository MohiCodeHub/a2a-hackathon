# τ-bench & τ²-bench → Agent Design Playbook for Track 1

Source papers (in this repo): `2406.12045v1.pdf` (τ-bench, Yao/Shinn/Razavi/Narasimhan, Sierra 2024) and `2506.07982v1.pdf` (τ²-bench, Barres/Dong/Ray/Si/Narasimhan, Sierra 2025). Sierra sponsors Track 1; the held-out test set is very likely built on this methodology. This doc extracts exactly how the harness will probe us and turns every measured failure mode into a design decision and a tool list per agent.

---

## 1. How a τ-style harness will test us

**The setup (τ-bench §3):** an LLM-simulated user gets a hidden *instruction* as its system prompt ("You are mia_li_2017, you want to change your reservation to SF... If change is not possible, you want to cancel and rebook. You are concise."). It converses turn-by-turn with the agent under test. The user LLM runs at **temperature 1.0** (deliberate stochasticity), the agent at 0.0. Episode ends when the user emits `###STOP###` or a step cap (30 actions) is hit.

**The scoring (this is the part to engineer for):** reward `r = r_action × r_output ∈ {0,1}` — binary per task:
- `r_action`: final database/world state equals the unique annotated goal state (every required action taken, **no extra write actions**).
- `r_output`: the agent's messages to the user **contain all required information as substrings** — e.g. the user asked what they'd save: the strings `"54.04"` and `"41.64"` must appear somewhere in the agent's replies.

τ²-bench adds three more criteria the harness can mix per-task (§3.3): **status assertions** (predicates on final state), **natural-language assertions** judged over the transcript (e.g. *"the agent diagnosed the cause of the issue"*), and **action matching** (specific calls appear in the trajectory).

**Task structure:** τ²-bench generates tasks **compositionally** — atomic subtasks (init function, solution functions, assertion functions) combined into composite tasks, with difficulty = number of subtasks/actions. Tasks include **personas** (None / Easy / technically-clueless Hard) and deliberately include cases where the correct behavior is to **refuse** (policy forbids the request) or **transfer to a human** — doing nothing can be the right answer.

**Adaptation to the hackathon:** we are not one agent with DB tools; we're a 3-agent pipeline fronted by the Personal Agent, judged externally. The harness almost certainly keeps: LLM-simulated customer with hidden goals + personas, multi-issue messages, repeated trials (pass^k), and scoring that checks **required facts appear in the customer-facing replies** plus possibly LLM-judged transcript assertions ("the issue was resolved", "the agent was helpful/accurate"). If they hand out a scenario pack (mock company, policy doc, product data) at kickoff, treat it exactly like a τ domain policy: hard rules, quoted verbatim, never contradicted.

---

## 2. The measured failure modes — and our countermeasures

τ-bench's manual breakdown of 36 failed gpt-4o function-calling trajectories (retail) plus τ²-bench's ablations. **This table is the core of the playbook** — each row is a known, quantified way agents lose points:

| # | Failure mode (share) | Evidence | Our countermeasure |
|---|---|---|---|
| 1 | **Wrong info / wrong argument (~55%)** — omits info the user asked for, miscalculates totals, gives wrong info that derails the user | τ-bench Fig. 5; "the user asks for a tracking ID but the agent does not provide it"; wrong price → user makes wrong decision | Every customer-facing reply ends by **explicitly restating all requested facts verbatim** (numbers with 2 decimals, IDs, dates, names). Give CS + Research agents a **`calculate` tool** — never let the LLM do arithmetic in its head (τ-bench itself ships `calculate` as a tool for exactly this reason). Personal Agent final-answer prompt: "list every question the customer asked; verify each is answered with a concrete value before sending." |
| 2 | **Wrong decision / rule-following (25%)** — ignores or misapplies domain policy | Removing the policy doc costs gpt-4o 22.4 pts on airline; agents often don't even use the policy when present | If a policy/scenario doc is provided: it goes in the CS agent's system prompt **verbatim**, restructured as per-intent **workflow checklists** — τ²-bench measured that workflow-style policies beat prose policies in normal operation (Fig. 4). Hard rules separated from soft guidance. Include refusal paths: "if policy forbids it, say so, explain why, offer the closest allowed alternative." |
| 3 | **Partial resolution of compound requests (19%)** — handles issue 1, forgets issue 2; or stops after one of N similar items | τ-bench Fig. 6: success degrades sharply with number of required writes; "exchange tools can only be called once — collect ALL items into one list first" | First step of Personal *and* CS agent: **enumerate every distinct request in the message into a numbered checklist** (LLM extraction, kept in working memory). Don't finish until every item is marked resolved-or-explicitly-addressed. Batch related sub-requests into one delegation rather than dribbling them. |
| 4 | **Reliability decay (pass^k)** — same task, re-run, different outcome | gpt-4o: 61% pass^1 → <25% pass^8 (retail); telecom decays even faster | Temperature **0** everywhere except customer-facing phrasing (≤0.3). Deterministic code (not LLM) for: routing, retries, timeouts, answer assembly, delegation formatting. Our own harness reruns every scenario ≥3× and **all** runs must pass before we ship a prompt change (poor-man's pass^k gate). |
| 5 | **Coordination/communication drop (~20 pts)** — agent fine alone, fails when it must work *through* another actor | τ²-bench No-User vs Default: gpt-4.1 52%→34%, o4-mini 67%→42% | This is exactly the cross-team half of our score: our agents working "through" foreign agents. Delegations must be **single, complete, self-contained instructions** (everything the downstream agent needs, in one message — no assumed context), and the response must be **verified against the checklist** before being trusted: if the downstream answer doesn't address an item, re-ask once with a sharper question, then degrade gracefully. |
| 6 | **Long-horizon collapse** — pass^1 → ~0 beyond ~7 required actions | τ²-bench Fig. 5 | **Minimize round-trips by design**: PA→CS = 1 call carrying everything; CS→Research = 1 call with 1–3 parallel questions; no speculative chatter. Short pipelines are not just faster — they're measurably more reliable. |
| 7 | **Hallucinated arguments/IDs** — weaker models invent order/user IDs | gpt-4o: 0.46 nonexistent-ID calls per task; gpt-3.5: 2.08–6.34 | Never invent identifiers. If the customer didn't give an ID, ask once or proceed describing the item in their words. Echo IDs exactly as received (copy, don't paraphrase). |
| 8 | **Acting without confirmation** — e.g. issuing a return before explicit user consent (policy violation even when state ends up right) | τ-bench §3 reward discussion | Any consequential/irreversible action the customer requests: **restate what will happen and get an explicit yes** before "doing" it; non-consequential info requests: never block on confirmation. |
| 9 | **Method choice** — text ReAct/Act underperform native function calling on every SOTA model | τ-bench Fig. 3 | Use **native function/tool calling** for all agent loops. Don't hand-roll "Thought/Action" text parsing. (Their "think tool" experiment didn't help FC models either — skip it.) |
| 10 | **Difficult users** — terse, emotional, non-technical, info-withholding personas; users who change their mind mid-task | τ-bench instructions ("You are in debt and sad today, but very brief"); τ²-bench Hard persona | Personal Agent prompt explicitly handles: vague openers (ask one targeted question OR proceed with stated assumption), mind-changes (latest intent wins — re-run the checklist), emotion (brief acknowledgment, then competence), withheld info (proceed with assumption, state it). Never mirror rudeness; never stall. |

---

## 3. Per-agent design with exact tool lists

All three: native tool-calling loop, temp 0 (phrasing pass ≤0.3), hard wall-clock deadline, never emit a failed task. Tool counts kept deliberately small — failure mode #1 shows argument-filling errors grow with tool complexity.

### 3.1 Personal Agent (the one the simulated customer talks to — most τ-bench-exposed)

The grader reads **this agent's replies**. `r_output` substring checks happen *here*.

| Tool | Purpose | Notes |
|---|---|---|
| `extract_requests` (or structured-output first step) | Numbered checklist of every distinct ask in the message | Counters failure #3 |
| `memory_search` / `memory_save` (Redis) | Customer profile + prior tickets, keyed by contextId | Degrades to no-op if Redis down |
| `ask_customer_service(brief)` | A2A client → CS agent (ours or foreign) | One call, self-contained brief, 45s deadline, answer-extraction waterfall |
| `calculate(expression)` | Exact arithmetic for anything numeric in the final reply | Counters failure #1 |

Loop: extract checklist → recall memory → (delegate unless trivially answerable from memory/greeting) → verify CS answer covers every checklist item (re-ask once max) → compose reply that *quotes concrete values* for every item → save memory.
Final-reply contract in prompt: *"For each numbered request, state the resolution with exact figures, IDs and dates. If something could not be confirmed, say exactly what and why. Close by asking if anything else is needed."* (clean episode ending helps the user sim emit STOP).

### 3.2 Customer Service Agent (the policy-and-resolution engine)

| Tool | Purpose | Notes |
|---|---|---|
| `triage` (structured first step) | Classify issues; decide per-issue: answer-from-policy / needs-research / refuse-per-policy / escalate | Mirrors τ²'s intent structure |
| `ask_research_agent(questions)` | A2A client → Research agent (ours or foreign); 1 call, 1–3 standalone questions | 25s deadline; on failure answer from own knowledge + flag unverified |
| `calculate(expression)` | Totals, refunds, differences | Failure #1: wrong-price errors derail users |
| `lookup_policy` (only if scenario pack provided) | Retrieval over the provided policy/product docs | Else policy lives verbatim in system prompt as workflow checklists |
| `log_ticket` (Redis) | Resolution summary for PA recall + sponsor story | Fire-and-forget |

Behavior contract: always returns a **resolution** (or a policy-grounded refusal with the rule cited and an alternative offered — refusal tasks exist in τ², see §1). States diagnosis explicitly ("the cause is X") — τ² natural-language assertions literally check *"the agent diagnosed the cause."* Confirms before consequential actions (failure #8). Never invents order/account data; states assumptions.

### 3.3 Research Agent (leaf — optimize for never-fail + facts-with-numbers)

| Tool | Purpose | Notes |
|---|---|---|
| `linkup_search(query, depth)` | Sponsor web search, `sourcedAnswer` output | Parallel calls for multi-question requests |
| `linkup_fetch(url)` | Read a specific page when the request names one | |
| `calculate(expression)` | Unit conversions, comparisons | |
| *(fallback, code not tool)* | Gemini grounded-search → pure LLM with dated caveat | Chain on search failure |

No clarifying questions ever; any text is a query. Output: direct answer first, then bullet facts **with exact figures**, then source URLs. Hard 25s budget: synthesize whatever is in hand rather than blow the caller's timeout (failure #6 compounds across the pipeline).

### 3.4 Model & config choices

- Both papers: function-calling-native frontier models dominate; claude-3.5/3.7-sonnet topped their leaderboards, gpt-4.1/o4-mini mid-pack on telecom. Today's equivalents: **Gemini 2.5 Pro** for CS triage+synthesis and Research synthesis (judges are Google; quality matters most here), **Gemini 2.5 Flash** for PA loop and extraction steps (latency), **Claude Sonnet as wired-in fallback** (their benchmarks consistently favored Claude for tool use — if Gemini misbehaves on tool-calling during testing, swap one env var).
- Agent temp 0. Cap internal steps (~10 tool calls) like τ's 30-action cap — runaway loops burn the harness clock.
- Keep system prompts tight: τ-bench measured 95.9% of agent cost is input tokens from long policy+tool prompts; bloat = latency = missed deadlines.

---

## 4. Build our own τ-style harness (Saturday, ~1h, highest-ROI testing investment)

Replicate their recipe exactly — it's simple and it's what we'll be graded with:

1. **Scenario file** per task: `{instruction, required_substrings[], nl_assertions[], persona}`. Instruction = hidden customer system prompt, τ-style: identity + goals + constraints + personality (*"You are Dana. Your espresso machine arrived broken and you also want to know the return window for a kettle bought 3 weeks ago. Mention both together. You are irritated and brief."*).
2. **User simulator**: Gemini Flash, temp **1.0**, instruction as system prompt, sees only the PA's replies, emits `###STOP###` when satisfied (τ's convention). Speaks to our deployed PA over real A2A — tests the wire format too.
3. **Scoring**: substring checks (`r_output`) + cheap LLM judge for nl_assertions ("was the issue resolved? was the cause stated?") + latency per turn.
4. **pass^k gate**: each scenario ×3, temp-1 user gives natural variation; **3/3 required** before any prompt/code change ships. (τ: a 61%-pass^1 agent is a 25%-pass^8 agent — single green runs mean nothing.)
5. **Scenario coverage** (≥10): single issue; **compound 2–3 issues in one message** (failure #3); needs web research; pure-memory follow-up turn (same contextId); vague one-liner ("my thing is broken"); mind-change mid-conversation; angry+terse persona; should-refuse case; foreign-agent drill — point PA at a stub CS that answers tersely/slowly/in v1.0 format (failure #5).

---

## 5. One-line summary

τ-bench grades **outcomes and exact facts in replies, repeatedly** — so we win with: checklist-driven request tracking, a `calculate` tool everywhere numbers appear, policy-as-workflow prompts with refusal paths, one-round-trip self-contained delegations, temp-0 determinism, and a pass^k-gated simulated-customer harness running from hour two.
