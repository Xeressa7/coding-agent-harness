# Harness mechanisms that measurably change coding-agent outcomes (evidence as of 2026-11-23)

Notation: **[M]** = measured result (ablation / controlled comparison, numbers given by the source). **[R]** = vendor-reported number without a published protocol detailed enough to replicate. **[O]** = opinion / practitioner guidance / anecdote. Dates are the publication or revision date of the source.

## 1. Tool / interface design (agent-computer interface, tool descriptions, number of tools)

### Takeaway
The best controlled evidence is still SWE-agent (2024). Interface choices moved success by 2-8 points absolute on SWE-bench Lite at an 18% baseline, which is 15-45% relative. The largest effects came from having a dedicated edit command at all, from the file-viewer window size, and from the search output format. Later sources (Anthropic 2025) claim large gains from tool-description quality, but they publish almost no numbers.

### Cited Findings
- [M] SWE-agent (arXiv 2405.15793, v3 11 Nov 2024, NeurIPS 2024). GPT-4 Turbo scored 18.00% (54/300) on SWE-bench Lite with the full ACI, versus 11.00% for a Shell-only agent: a 64% relative increase. On the full test set it scored 12.47% (286/2,294). Average cost per resolved instance was $1.67 on Lite, versus $1.46 for Shell-only. Claude 3 Opus scored 13.00% on Lite. — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [M] SWE-agent Table 3, ablations on SWE-bench Lite with GPT-4 Turbo (baseline 18.0%) — [SWE-agent paper](https://arxiv.org/abs/2405.15793):
  - **Editor:** edit with linting 18.0; edit without linting 15.0 (-3.0); no edit command 10.3 (-7.7).
  - **Search:** summarized search (all matches listed at once) 18.0; iterative search (one result at a time, like an IDE) 12.0 (-6.0); no search tool 15.7 (-2.3). A bad search tool was worse than having none.
  - **File viewer:** 100-line window 18.0; 30 lines 14.3 (-3.7); full file 12.7 (-5.3). Both too little and too much context hurt.
  - **Context:** keeping only the last 5 observations 18.0; full history 15.0 (-3.0).
  - **Demonstrations:** removing the demonstration trajectory gave 16.3 (-1.7). For Shell-only, removing the demonstration dropped the score from 11.00 to 7.33.
- [M] SWE-agent hyperparameter sweep (Table 5, SWE-bench dev subset). GPT-4 Turbo at temperature 0 scored 15.1% with a 100-line window and 10.8% with a 200-line window. Claude 3 Opus scored 8.1% and 7.1% respectively. — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [M] SWE-agent trajectory statistics. 51.7% of GPT-4 Turbo trajectories had at least one failed edit (one that raised a lint error). Any edit attempt had a 90.5% chance of eventually succeeding, falling to 57.2% after one failed edit. Resolved runs had a median cost of $1.21 and 12 steps. Unresolved runs averaged $2.52 and 21 steps ("succeed quickly, fail slowly"). 93.0% of resolved runs were submitted before the $4 budget ran out, versus 69.0% of all runs. The authors conclude that a larger budget would not help much. — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [R] Anthropic, "Writing effective tools for agents" (11 Sep 2025):
  - Claude Sonnet 3.5 reached state of the art on SWE-bench Verified after refinements to its tool descriptions. No figures are given.
  - Claude-optimized Slack and Asana MCP servers beat human-written ones on held-out accuracy. These results appear only in charts, without exact percentages.
  - A concise response format used 72 tokens versus 206 for the detailed format, about 1/3.
  - Claude Code caps tool responses at 25,000 tokens by default.
  — [Anthropic: Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [O] The same post recommends a few consolidated tools over many overlapping ones, namespacing, unambiguous parameter names, pagination/filtering/truncation with sensible defaults, and actionable error messages. It calls tool descriptions one of the most effective levers. No ablation on the number of tools is reported. — [Anthropic: Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [R] Anthropic multi-agent research system (13 Jun 2025). A tool-testing agent rewrote flawed MCP tool descriptions, and later agents using the new descriptions finished tasks 40% faster. This was a research/search task, not coding. — [Anthropic: multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)

### Inferences
- The SWE-agent effects are measured with weak 2024 models: GPT-4 Turbo at 18%. The magnitudes probably shrink with frontier models, which handle raw bash better. Anthropic's Claude 4 SWE-bench runs used only bash plus a string-replacement editor (see section 5). A thesis should re-test window size, search format and lint-on-edit on a current model rather than assume the 2024 deltas hold.
- The window-size and search-format results share one mechanism: the amount and shape of observation tokens per step. A thesis could treat "observation formatting" as a single factor with levels: truncate, summarize, paginate.

### Gaps
- No controlled study was found that varies the number of tools for coding agents. The Anthropic guidance is qualitative.
- No independent replication of the tool-description-quality effect on a coding benchmark, with numbers, was found.
- The SWE-agent ablations are single-run on 300 instances with no confidence intervals. A 3-point delta is about 9 instances.

## 2. Edit formats (whole / diff / udiff / search-replace; apply_patch vs str_replace)

### Takeaway
Edit format can move success dramatically for weaker or "lazy" models. Aider's unified-diff change took GPT-4 Turbo from 20% to 61% on a refactoring benchmark. Most of the gain came from tolerant application ("flexible patching"), not the syntax itself. For current frontier models the evidence is mostly format-compliance rates, not success deltas.

### Cited Findings
- [M] Aider, "Unified diffs make GPT-4 Turbo 3X less lazy" (undated page; the content refers to gpt-4-1106-preview, so it is from about Dec 2023). On an 89-task Python refactoring "laziness" benchmark — [Aider: unified diffs](https://aider.chat/docs/unified-diffs.html):
  - gpt-4-1106-preview: SEARCH/REPLACE 20% (lazy comments on 12 tasks); unified diff 61% (lazy comments on 4 tasks).
  - gpt-4-0613: 26% to 59%. 28% of the tasks exceed its 8k context, capping it at 72%.
- [M] Same source, mechanism ablations. Disabling flexible patching gave a 9x increase in editing errors on the Exercism benchmark. Removing the prompt instruction to make "high level" diffs gave a 30-50% increase in editing errors. The page reports only these relative error increases, not absolute rates. — [Aider: unified diffs](https://aider.chat/docs/unified-diffs.html)
- [O] Aider's stated mechanisms: a familiar format (git diff output is common in training data); no line numbers (models handle them poorly); no JSON escaping of code; whole-block rewrites instead of surgical line edits; tolerant application (normalizing hunks, handling indentation shifts, splitting hunks). — [Aider: unified diffs](https://aider.chat/docs/unified-diffs.html)
- [M] The Aider leaderboard reports `percent_cases_well_formed` (share of responses that follow the edit format) separately from pass rate, so format failures are measured per model and format. — [Aider leaderboards](https://aider.chat/docs/leaderboards/); [Aider benchmark README](https://github.com/Aider-AI/aider/blob/main/benchmark/README.md)
  - A secondary 2026 mirror lists diff-format well-formedness of 91.6% for GPT-5 (high), 97.8% for o3-pro and 97.3% for DeepSeek-V3.2-Exp. This was not verified against aider.chat. — [Steel leaderboard mirror](https://leaderboard.steel.dev/leaderboards/aider/)
- [M] SWE-agent shows that the editing interface matters inside an agent loop too: no edit command gave 10.3% versus 18.0% with a lint-guarded line-range edit (see section 1). — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [O] OpenAI's GPT-5.3-Codex guide lists apply_patch as a default tool and advises using it for single-file edits, but not for generated or bulk changes where scripting is more efficient. No comparative numbers. — [OpenAI GPT-5.3 Codex guide](https://developers.openai.com/api/docs/guides/gpt-5.3-codex)

### Inferences
- The edit format works mainly through malformed-edit rate and retry turns. Tolerant application probably matters more than the format choice. A thesis should log "edit applied / edit rejected" per call and the number of retries, not only the final success rate.
- apply_patch is trained-in for OpenAI Codex models and str_replace for Claude. A format may therefore interact with the model family, so a crossed design (format × model) is warranted.

### Gaps
- No published controlled comparison of apply_patch versus str_replace (success, error rate or tokens) was found.
- Per-model success deltas (whole vs diff) from the current Aider leaderboard were not extracted, because the live page was not fetched. The report writer should not cite specific per-model deltas from these notes.

## 3. Context management (masking, summarization/compaction, truncation, sub-agents, prompt caching)

### Takeaway
The strongest controlled evidence (The Complexity Trap, NeurIPS 2025 DL4C workshop) is that simple observation masking roughly halves cost versus an unmanaged agent, with no loss in solve rate. It matches LLM summarization, which also lengthens trajectories. The effect on success rate is small and model-dependent. Sub-agents and prompt caching are justified mainly on cost and context-isolation grounds, and there are no coding-specific success ablations.

### Cited Findings
- [M] The Complexity Trap (arXiv 2508.21433; v1 29 Aug 2025, v3 27 Oct 2025). Setup: SWE-agent on SWE-bench Verified, n=500 per model, turn limit 250, paired bootstrap with 10,000 replicates. † marks p<0.05 versus the raw agent. — [Complexity Trap](https://arxiv.org/html/2508.21433v3)

  | Model | Raw: solve %, $/instance | Masking (M=10) | LLM summary (N=21, M=10) |
  |---|---|---|---|
  | Qwen3-32B | 17.0, $1.12 | 15.0, $0.55† | 16.0, $0.50† |
  | Qwen3-32B (thinking) | 23.0, $0.51 | 24.6, $0.46 | 24.8, $0.51 |
  | Qwen3-Coder 480B | 53.4, $1.29 | 54.8, $0.61† | 53.8, $0.64† |
  | Gemini 2.5 Flash | 32.8, $0.41 | 35.6, $0.18† | 36.0, $0.24† |
  | Gemini 2.5 Flash (thinking) | 40.4, $0.56 | 36.4†, $0.24† | 31.4†, $0.25† |

- [M] Same paper, other findings — [Complexity Trap](https://arxiv.org/html/2508.21433v3):
  - **Trajectory length:** LLM summarization lengthened trajectories. With Gemini 2.5 Flash the mean was 52 turns with summarization versus 44 with masking and 50 raw. With Qwen3-Coder, summarization runs were 15% longer than raw and 13% longer than masking.
  - **Summary cost:** the summarization calls themselves cost 0.65-7.2% of instance cost.
  - **Hybrid:** masking plus occasional summarization (N=43, M=10) cut cost by a further 7% versus masking and 11% versus summarization, with +2.6 points versus raw. This was tested only with Qwen3-Coder on a 50-instance subset. Naive hybrid parameters (N=21) made cost efficiency worse.
  - **OpenHands:** a 50-instance probe is offered only as "initial evidence" that the results generalize to OpenHands.
- [M] SWE-agent (2024) found a masking-like effect earlier: collapsing observations before the last 5 to one line gave 18.0% versus 15.0% with full history (GPT-4 Turbo, SWE-bench Lite). — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [R] Anthropic context management (29 Sep 2025):
  - In a 100-turn web-search evaluation, context editing (clearing stale tool results) let agents finish workflows that otherwise failed from context exhaustion, and cut token use by 84%.
  - In a separate internal agentic-search evaluation, memory tool plus context editing gave +39% over baseline; context editing alone gave +29%.
  - These are not coding tasks and the protocol is not published.
  — [Anthropic: context management](https://www.anthropic.com/news/context-management)
- [R] Anthropic multi-agent research system (13 Jun 2025) — [Anthropic: multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system):
  - An Opus 4 lead agent with Sonnet 4 sub-agents beat a single Opus 4 agent by 90.2% on an internal research evaluation.
  - On BrowseComp, token usage explained about 80% of performance variance; with tool-call count and model choice, 95%.
  - Agents use about 4x the tokens of chat; multi-agent systems about 15x.
  - [O] The authors say most coding tasks have fewer truly parallelizable sub-tasks than research, so multi-agent setups fit coding poorly.
- [R/O] Manus, "Context Engineering for AI Agents" (18 Jul 2025) — [Manus: context engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus):
  - Reported: an average input:output token ratio of about 100:1 and about 50 tool calls per task.
  - Claude Sonnet pricing: cached input $0.30/MTok versus uncached $3/MTok (10x).
  - [O] KV-cache hit rate is "the single most important metric". Mask tools rather than remove them, so the cache is not invalidated. Use the file system as external memory. Recite a todo.md to keep goals in recent attention. Keep errors in context. No ablations are reported.
- [O] Anthropic Claude Code caps tool output at 25,000 tokens by default (a truncation guardrail; see section 1). — [Anthropic: Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents)

### Inferences
- With roughly 100:1 input:output ratios, context strategy is mainly a cost lever. Any compaction that changes the prompt prefix also destroys prompt-cache hits. A thesis measuring "cost" should report cache-read tokens separately, or masking/summarization savings may be over- or under-stated.
- The success effects in the Complexity Trap are within ±3 points for most models. The significant drops for Gemini Flash thinking show that a context strategy can hurt some models. Per-model testing with paired designs and CIs is needed, as that paper did.
- Summarization lengthening trajectories looks like a "hidden stop signal" problem: once evidence of failure is summarized away, the agent keeps going. A thesis could measure steps-to-submit per strategy.

### Gaps
- No controlled coding-benchmark ablation was found for sub-agents (context isolation) versus a single agent.
- No published measurement was found of prompt-caching hit rate versus compaction strategy in coding agents.
- No coding-specific measurement was found of tool-output truncation thresholds beyond SWE-agent's window-size result.

## 4. Verification / feedback loops (tests, linters/LSP, self-review vs external feedback)

### Takeaway
External, execution-grounded signals measurably help:
- Lint-on-edit: +3.0 points in SWE-agent.
- Generated reproduction tests for patch selection: +5.0 points in Agentless (from 27.0% to 32.0%).
- Feedback quality is the bottleneck for self-repair. A stronger feedback source gave larger gains than self-feedback (ICLR 2024).

The evidence that pure self-reflection does not help comes from code-generation studies, not agent harnesses.

### Cited Findings
- [M] SWE-agent: lint-guarded edits scored 18.0% versus 15.0% without linting (SWE-bench Lite, GPT-4 Turbo). With linting, agents usually recovered from failed edits, though the chance of success fell from 90.5% to 57.2% after the first failure. — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [M] Agentless (arXiv 2407.01489; v1 1 Jul 2024, v2 29 Oct 2024). Patch validation ablation (Table 4) on SWE-bench Lite with GPT-4o — [Agentless](https://arxiv.org/html/2407.01489v2):
  - majority voting only: 25.67%
  - plus regression tests: 27.00% (+$0.01/issue)
  - plus generated reproduction tests: 32.00% (+$0.25/issue)
  - Total cost was $0.70/issue, with about 78k tokens.
  - The oracle upper bound over all 40 samples was 42.0%, so selection leaves about 10 points on the table.
- [M] Agentless repair sampling (Table 3): 4 location sets × 10 samples scored 32.00%, versus 29.33% for 40 samples from the greedy location set. Gains plateau at about 40 samples. — [Agentless](https://arxiv.org/html/2407.01489v2)
- [M] "Is Self-Repair a Silver Bullet for Code Generation?" (arXiv 2306.09896, ICLR 2024; revised 2 Feb 2024):
  - Once the cost of repair is counted, the gains from self-repair are "often modest", vary by subset, and are sometimes absent.
  - Feedback from a stronger model produced substantially larger gains.
  - Feedback from humans showed that the best models' self-repair still lags far behind human debugging.
  - Exact numbers were not extracted (abstract only).
  — [Self-repair paper](https://arxiv.org/abs/2306.09896)
- [O] Anthropic long-running harness (26 Nov 2025). Prompting Claude to verify features end-to-end through browser automation (Puppeteer MCP) "dramatically improved performance". Before that, the agent marked features done after only unit tests or curl. No numbers. — [Anthropic: effective harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

### Inferences
- The candidate interventions rank by evidence strength:
  1. External execution signals used for selection or gating: reproduction tests, lint gates.
  2. External signals injected as feedback: test output after an edit.
  3. Prompted self-review.
- A thesis can test (2) versus (3) directly at matched token budgets, which mirrors the self-repair paper's cost-adjusted framing.
- This is consistent with the project's own prior finding (memory note `decisions/dogrulama-katmanlari.md`) that warning-text layers did not move strong models.

### Gaps
- No controlled ablation was found of LSP diagnostics after edits (versus lint, versus none) in an agent harness.
- No numbers were extracted for "LLMs cannot self-correct reasoning yet" (Huang et al., 2023, arXiv 2310.01798) or similar self-correction critiques. The source was not fetched in this pass.
- No controlled study was found of automatically running tests after every edit versus agent-initiated testing.

## 5. Planning/todo tools, reminders, stop checks, retries, best-of-n with verifiers, long-running harness practices

### Takeaway
Best-of-n with a verifier or selector is the best-quantified intervention in this group:
- Claude 4 on SWE-bench Verified: about +7 points (vendor-reported).
- Agentless: about +5 points from test-based selection.
- SWE-Gym: open-weight models reach 32.0% on Verified with verifier scaling.

Todo/planning tools, reminders and "are you done?" checks are supported only by practitioner reports and observed failure modes. No ablations were found.

### Cited Findings
- [R] Claude 4 launch (22 May 2025), SWE-bench Verified with a minimal scaffold (bash plus a string-replace file editor). With parallel test-time compute (multiple attempts, then an internal scoring model picks one, with no hidden tests):
  - Opus 4: 72.5% → 79.4%
  - Sonnet 4: 72.7% → 80.2%
  - The scorer is not described in enough detail to replicate. These figures came via a search summary of the Anthropic page and secondary cards.
  — [Anthropic: Introducing Claude 4](https://www.anthropic.com/news/claude-4); [Langbase model card](https://langbase.com/models/anthropic/claude-opus-4/benchmarks)
- [M] SWE-Gym (arXiv 2412.21139; ICML 2025, revised 6 Jun 2025). Fine-tuning on 2,438 executable task instances gave up to +19 points absolute in resolve rate. Adding verifier-based inference-time scaling reached 32.0% on SWE-bench Verified and 26.0% on Lite, which the authors call state of the art for open-weight agents at the time. The per-k best-of-n curve was not extracted. — [SWE-Gym](https://arxiv.org/abs/2412.21139)
- [M] Agentless: sampling plus test-based selection, with the 42.0% oracle versus 32.0% achieved (section 4). — [Agentless](https://arxiv.org/html/2407.01489v2)
- [M] SWE-agent: pass@k over 6 runs rises well above pass@1. Per-instance resolution varies a lot between runs even when mean variance is low. Because "agents succeed quickly and fail slowly", a larger budget is unlikely to help much. — [SWE-agent paper](https://arxiv.org/abs/2405.15793)
- [O] Anthropic, "Effective harnesses for long-running agents" (26 Nov 2025) — [Anthropic: effective harnesses](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents):
  - **Design:** an initializer agent writes a JSON feature list (over 200 features, all marked failing), an init.sh, a claude-progress.txt file and an initial git commit. Later coding sessions work one feature at a time, commit, update progress, and flip only the `passes` field. Each session starts by reading pwd, git log, progress and the feature list, then runs an end-to-end smoke test.
  - **Observed failure modes:**
    - One-shotting too much and running out of context mid-feature.
    - Declaring victory early after seeing existing progress.
    - Marking features done without end-to-end tests.
    - Setup overhead.
  - JSON was "less likely to be inappropriately changed" than Markdown.
  - The post reports no quantitative results.
- [O] Manus: recite a todo.md to keep the goal in recent attention ("manipulate attention through recitation"). No measurement. — [Manus: context engineering](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [O] OpenAI, "Harness engineering: leveraging Codex in an agent-first world" (11 Feb 2026, R. Lopopolo). A team built a repository of about 1M lines largely with Codex. One large AGENTS.md failed. A short AGENTS.md of about 100 lines, working as a table of contents into a structured docs/ system of record, worked better. These are qualitative statements from search snippets; the full post was not fetched. — [OpenAI: harness engineering](https://openai.com/index/harness-engineering/)

### Inferences
- Best-of-n gains are bounded by selector quality: Agentless reached 32% against a 42% oracle. A thesis testing best-of-n should report pass@k (oracle), selector accuracy and cost per resolved task. Without that, the gain cannot be separated from added compute.
- The long-running failure modes (premature victory, untested completion) are exactly what stop-hooks / "are you done?" checks target. A natural experiment is a finish-check gate that requires a passing test or a verification artifact, against no gate. Measure premature-submit rate, success and extra steps. The project already has a `finish_check` layer (commit 0086ed4) to test this.

### Gaps
- No published controlled ablation was found of todo/planning tools (e.g., Claude Code TodoWrite) or periodic system reminders in coding agents.
- No controlled study was found of retry policies (re-prompt after failure) versus fresh attempts at matched cost in coding agents.
- The full OpenAI Codex harness post and "Unrolling the Codex agent loop" were not fetched, so their specifics (for example any numbers on compaction or apply_patch) remain unverified.
- Cognition ("Don't build multi-agents"), Sourcegraph/Amp and Cursor posts were not reviewed in this pass. They are expected to be mostly [O].
