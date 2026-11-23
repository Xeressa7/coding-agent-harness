# Same model, different harness: how much does the scaffold change coding-agent results?

Compiled 2026-11-23. All numbers are dated. "Second-hand" means I could not open the primary source and the number comes through another paper, blog or search summary. Some sites (tbench.ai, epoch.ai, the swe-rebench teletype post) failed to load during this session (DNS errors or HTTP 502), so their own pages could not be checked directly. Nearly all controlled evidence is from **late 2025 to mid-2026**. I found no controlled same-model study from Aug–Nov 2026.

## Q1. On the main leaderboards, how far apart are different harnesses for the same model?

### Takeaway
The most rigorous same-model grid is the Terminal-Bench 2.0 paper (89 tasks, at least 5 trials per cell, ~Nov 2025 models). For a fixed model, the spread between harnesses runs from **0.3 to 16.9 pp** (median ≈5 pp over 16 models). On public leaderboards that include harnesses tuned for the benchmark, one model (Opus 4.6, TB 2.0, 2026) spans **about 22 pp** (58.0% to 79.8%). On SWE-bench Verified the spread is smaller among competent harnesses (about 4–8 pp). It is huge (about 34–48 pp) only against a non-coding generalist scaffold (HAL).

### Cited Findings

**Terminal-Bench 2.0 paper, Appendix A Table 2 (arXiv 2601.11868, submitted 2026-01-17; models from about Aug–Dec 2025). Each model-agent pair was run at least 5 times; 32,155 trials in total; 95% CIs are about ±2.5–3 pp.** — [Terminal-Bench paper (arXiv 2601.11868)](https://arxiv.org/html/2601.11868v1)

| Model | Agents and scores (TB 2.0 resolution rate) | Spread (pp) |
|---|---|---|
| GPT-5.2 | Codex CLI 62.9 · Terminus 2 54.0 | 8.9 |
| Claude Opus 4.5 | Terminus 2 57.8 · Claude Code 52.1 · OpenHands 51.9 | 5.9 |
| GPT-5 | Codex CLI 49.6 · OpenHands 41.5 · Terminus 2 35.2 · mini-SWE-agent 33.9 | 15.7 |
| Claude Sonnet 4.5 | Terminus 2 42.8 · mini-SWE-agent 42.5 · OpenHands 40.3 · Claude Code 40.1 | 2.7 (inside the CI) |
| Claude Opus 4.1 | Terminus 2 38.0 · mini 35.1 · OpenHands 34.9 · Claude Code 34.8 | 3.2 |
| GPT-5-mini | Codex CLI 31.9 · OpenHands 27.7 · Terminus 2 24.0 · mini 22.2 | 9.7 |
| Claude Haiku 4.5 | mini 29.8 · Terminus 2 28.3 · Claude Code 27.5 · OpenHands 13.3 | 16.5 (2.3 without OpenHands) |
| Grok 4 | mini 29.0 · Terminus 2 23.4 · OpenHands 19.6 | 9.4 |
| Gemini 2.5 Pro | Terminus 2 32.6 · mini 26.1 · Gemini CLI 19.6 · OpenHands 15.7 | 16.9 |
| Gemini 2.5 Flash | mini 17.1 · Terminus 2 16.9 · OpenHands 15.5 · Gemini CLI 15.4 | 1.7 |
| Grok Code Fast 1 | mini 24.5 · Terminus 2 14.5 | 10.0 |
| GPT-OSS-120B | Terminus 2 18.7 · mini 14.2 | 4.5 |
| GPT-5-nano | Codex CLI 11.5 · OpenHands 9.5 · Terminus 2 7.9 · mini 7.0 | 4.5 |
| GPT-OSS-20B | mini 3.4 · Terminus 2 3.1 | 0.3 |
| Kimi K2 Instruct | Terminus 2 27.8 · OpenHands 25.6 | 2.2 |
| Qwen3 Coder 480B | OpenHands 24.3 · Terminus 2 23.9 | 0.4 |

- The paper's own conclusion: "model selection is usually more important than agent scaffold when optimizing for performance." The text also notes a "17%" gain for Gemini 2.5 Pro with Terminus 2 over OpenHands, but the table shows 16.9 pp. Some token notes in the paper say "74 tasks" while the benchmark has 89, a small internal inconsistency. — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)

**Terminal-Bench 2.0 / 2.1 public leaderboard (2026). Mostly second-hand:**
- Claude Opus 4.6 on TB 2.0:
  - Meta-Harness 76.4%, Terminus-KIRA 74.7%, Terminus 2 62.9%, Claude Code 58.0%. The first three come from a survey table (arXiv 2606.20683, June 2026) and the Claude Code figure from the TB 2.1 release notes. The same survey gives Opus 4.6 input tokens of 755.0K, 618.6K and 79.4K and timeout rates of 7.9%, 18.2% and 18.2% for the three survey rows. — [dev.to (Anhaia), second-hand](https://dev.to/gabrielanhaia/stop-comparing-models-same-claude-58-or-76-on-terminal-bench-depending-on-the-harness-16d1)
  - ForgeCode 79.8% and Capy 75.3% (tbench.ai snapshot of 2026-04-30). — [Bustamante, "Model-Harness-Fit", 2026-05-03, second-hand](https://nicolasbustamante.com/blog/model-harness-fit)
  - Taken together, Opus 4.6 ranges about **58.0 to 79.8, roughly 22 pp**, across harnesses on TB 2.0. This is my combination of the two sources above.
- **TB 2.1 rescoring:** TB 2.1 fixed 28 of the 89 tasks (dependencies that had changed, resource budgets too small for valid approaches, instructions that didn't match their tests). Claude Code + Opus 4.6 went from 58.0% to **70.1%**. Terminus 2 + Opus 4.6 scores 63.8% on 2.1. — [dev.to, citing the tbench.ai 2.1 release notes, second-hand](https://dev.to/gabrielanhaia/stop-comparing-models-same-claude-58-or-76-on-terminal-bench-depending-on-the-harness-16d1)
  - Implication: part of the measured "harness gap" was really harness × broken-task interaction, so the order flipped from Claude Code < Terminus 2 to Claude Code > Terminus 2.
- GPT-5.5 + Codex CLI: 82.2% ± 2.2 (2026-04-23, rank 4 on the TB 2.0 board). This comes from a search snippet of tbench.ai, and the page itself would not load. Another blog gives 82.0%. — [tbench.ai TB 2.0 leaderboard](https://www.tbench.ai/leaderboard/terminal-bench/2.0); [Bustamante](https://nicolasbustamante.com/blog/model-harness-fit)
- Cursor reports moving "from Top 30 to Top 5" on TB 2.0 by changing only its harness, with the model fixed (Cursor post of 2026-04-30). — [Bustamante, second-hand](https://nicolasbustamante.com/blog/model-harness-fit)
- **Unverified, treat with caution:**
  - Opus in Cursor at 93%, a Claude Code "Mythos" configuration at 92.1%, and default Claude Code at 77% on TB 2.0 (Jozefiak, 2026-04-15) — [D. Vaughan blog](https://codex.danielvaughan.com/2026/04/19/the-harness-effect-same-model-different-tool-different-score/)
  - Opus 4.7 at 90.2% in a "vix" harness — [search summary only](https://codex.danielvaughan.com/2026/04/09/harness-performance-terminal-bench/)
  - None of these could be matched to the official board.

**SWE-bench Verified**
- **Official bash-only track** (mini-SWE-agent v2.2.1 prompts, 500 Verified tasks, one shared system prompt, run by the SWE-bench team and not self-reported; 2026-02-19):
  - Scores: Opus 4.5 76.8, Gemini 3 Flash 75.8, MiniMax M2.5 75.8, Opus 4.6 75.6, GLM-5 72.8, GPT-5.2 72.8, Sonnet 4.5 72.8, Kimi K2.5 71.4, DeepSeek V3.2 70.8, Haiku 4.5 70.0. — [Simon Willison, 2026-02-19](https://simonwillison.net/2026/Feb/19/swe-bench/)
  - Vals.ai: Opus 4.5 (Thinking) + mini-SWE-agent at 76.40%. — [Vals.ai](https://www.vals.ai/benchmarks/swebench)
- **Vendor-reported:** Opus 4.5 at **80.9%** with Anthropic's own scaffold (2025-11-24). — [TechCrunch](https://techcrunch.com/2025/11/24/anthropic-releases-opus-4-5-with-new-chrome-and-excel-integrations); [ITPro](https://www.itpro.com/technology/artificial-intelligence/anthropic-announces-claude-opus-4-5-the-new-ai-coding-frontrunner)
  - That is **about 4.1 pp** above the standardized bash-only score (my calculation; the runs differ in time and in thinking settings).
- **Claude Sonnet 4 (2025):** mini-SWE-agent 64.9, OpenHands 70.4, SWE-agent with tools 72.4, vendor scaffold 72.7, a **7.8 pp spread**. This comes second-hand from a survey table (arXiv 2606.20683) through a search summary and is not verified. — [search result referencing the survey](https://arxiv.org/pdf/2606.20683)
- **Epoch AI:** changing only the scaffold moved GPT-5 by up to **11 pp** and Kimi K2 Thinking by up to **15 pp** on SWE-bench Verified. These figures reach me second-hand, cited in arXiv 2605.23950, because epoch.ai would not load. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
  - Epoch's review says results are "highly dependent on the scaffold used." Epoch upgraded its own scaffold (v2.0.0) in February 2026 and re-evaluated key models, which led to "significantly improved performance." Epoch scores from before and after Feb 2026 are not comparable. — [Epoch SWE-bench Verified page (search snippet)](https://epoch.ai/benchmarks/swe-bench-verified); [Epoch review (search snippet)](https://epoch.ai/benchmarks/swe-bench-verified/review)
- **Grok 4 (2025):** SWE-agent 58.6% vs xAI's own scaffold 72–75%, a **+14–16 pp** gap. — [arXiv 2605.23950, citing Vals AI and vendor reports](https://arxiv.org/html/2605.23950v1)

**SWE-bench Pro**
- Claude Opus 4.5: Scale's SEAL standardized scaffold 45.9% vs Claude Code 55.4%, a **+9.5 pp** gap.
- Under SEAL, the six leading models span only **4.9 pp**, so the harness gap for one model is about twice the spread across frontier models.
- Adding a WarpGrep search subagent on the same infrastructure gave **+2.1–2.2 pp** for Opus 4.5, Opus 4.6 and MiniMax 2.5.
- Source for all three points: [arXiv 2605.23950, citing the Morph leaderboard](https://arxiv.org/html/2605.23950v1)

**HAL: SWE-bench Verified Mini** (50 tasks; Kapoor, Stroebl et al., Princeton, arXiv 2510.11977, 2025-10-13, ICLR 2026)
- The task-specific SWE-agent scaffold beat the HAL Generalist (smolagents-based) scaffold in **11 of 12** model comparisons. — [HAL paper](https://arxiv.org/html/2510.11977)
- Reported gaps: Claude Sonnet 4.5 68% vs 34% (**34 pp**), GPT-5 Medium 46% vs 12% (**34 pp**), o4-mini nearly **48 pp**. — [arXiv 2605.23950 citing HAL](https://arxiv.org/html/2605.23950v1)
- A later reanalysis argues that a large HAL scaffold gap on TAU-bench Airline (not a coding benchmark) came from silent grader errors. Many Generalist episodes were never graded, and regrading closes the pooled gap to a tie. I found no equivalent reanalysis for SWE-bench, but this is a warning against reading extreme scaffold gaps at face value. — [arXiv 2611.04399](https://arxiv.org/pdf/2611.04399)

**SWE-rebench**
- Claude Code was ranked #1 at 62.1% resolved in an older update (Opus 4.5 / Sonnet 4.5 era) and used about 2M tokens per task, the most in the top ten. Web search was explicitly disabled for Claude Code and Codex. — [habr (search summary)](https://habr.com/ru/post/977902); [swe-rebench](https://swe-rebench.pages.dev/)
- The July 2026 update reportedly covers 111 tasks in 5 languages, 13 models in a unified SWE-agent-based scaffold, and 4 agents (Claude Code, Codex, Cursor, Junie). The page returned HTTP 502, so the per-agent numbers were not retrieved. — [swe-rebench 2026-07 post](https://teletype.in/@ibragim_bad/swe-rebench-2026-07)

### Inferences
- Among competent harnesses for the same model, typical gaps are about **2–10 pp**. Gaps of **15–22 pp** appear when one harness is a poor fit for the model (GPT-5 without Codex CLI, Gemini 2.5 Pro in OpenHands, Haiku 4.5 in OpenHands) or when benchmark-optimized harnesses are compared with default vendor CLIs. Gaps of **30–48 pp** appear only against a non-coding generalist scaffold (HAL).
- More harnesses tested per model mechanically widens the reported range, so ranges should be normalized for the number of harnesses.
- Gaps smaller than about ±3 pp on TB 2.0 (89 tasks) sit inside the 95% CI. Sonnet 4.5, Opus 4.1, Gemini 2.5 Flash, Kimi K2 and Qwen3 Coder show no reliable harness effect there.
- Benchmark defects interact with harnesses: TB 2.1 moved Claude Code + Opus 4.6 by 12.1 pp while Terminus 2 moved by only 0.9 pp. A "harness gap" can partly be a benchmark artifact.

### Gaps
- tbench.ai and epoch.ai pages could not be fetched (DNS failure), so the full current TB 2.0 and 2.1 tables and Epoch's per-scaffold table are unverified.
- No same-model harness comparison found for SWE-bench Multilingual or for the Aider Polyglot leaderboard (Aider varies edit format, not harness). Not researched in depth.
- SWE-rebench per-agent numbers for July 2026 were not retrieved (HTTP 502).
- Vendor-reported numbers for Opus 4.6 and GPT-5.2 on SWE-bench Verified were not verified in this session, so their bash-only vs vendor gap is not computed.
- A search summary claimed Vals.ai TB 2.1 shows "Claude Opus 4.8 + Claude Code 69.66%" and "GPT 5.5 + Codex 57.30%". This is unverified and mixes harnesses, so it is not usable as same-model evidence. — [Vals.ai TB 2.1](https://vals.ai/benchmarks/terminal-bench-2-1)

## Q2. How large is the harness effect relative to the model-generation effect?

### Takeaway
Under a fixed harness, one model generation adds **about 13–20 pp** on TB 2.0. That is larger than the typical harness spread for one model (median about 5 pp) and roughly equal to the worst-case spread (about 16–17 pp). Among **comparably capable frontier models**, however, the harness can dominate: one controlled study found harness variance **7.8×** model variance.

### Cited Findings
- **Model-generation effect at a fixed harness, TB 2.0** (my calculation from Table 2 of [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)):
  - Terminus 2: Opus 4.1 → Opus 4.5 is 38.0 → 57.8 (**+19.8 pp**); GPT-5 → GPT-5.2 is 35.2 → 54.0 (**+18.8 pp**); Sonnet 4.5 → Opus 4.5 is 42.8 → 57.8 (+15.0 pp).
  - Codex CLI: GPT-5 → GPT-5.2 is 49.6 → 62.9 (**+13.3 pp**).
  - Model-size effect within Codex CLI: GPT-5.2 vs GPT-5-nano is 62.9 vs 11.5, about 51 pp.
- **Controlled factorial**, the cleanest variance decomposition I found (Zhang, Wang, Ge, Xu, Hamm, Reddy, arXiv 2605.23950, 2026-05-07). Setup: 100-task SWE-bench Verified subset, 2 runs per cell, three harness levels (H1 minimal, H2 improved, H3 full). — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
  - GLM-5.1: 52.5 / 56.5 / 65.5 (+13.0 from H1 to H3).
  - GPT-5.4: 55.0 / 58.5 / 63.5 (+8.5).
  - Kimi K2.6: 52.0 / 59.0 / 60.5 (+8.5).
  - Mean harness variance was 18.48 pp² vs mean model variance 2.37 pp², a **7.80×** ratio (8.72× and 6.76× per run).
  - Swapping the model at a fixed harness moved scores by 2.5–5.0 pp; changing the harness at a fixed model moved them by 8.5–13.0 pp.
  - There were **6 ranking reversals in 9** pairwise comparisons. The authors explicitly say 7.8× is not universal.
- **Public SWE-bench Pro:** six frontier models under SEAL span 4.9 pp, while one model (Opus 4.5) varies 9.5 pp across harnesses. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
- **Bash-only SWE-bench Verified (2026-02):** the top 10 models span only 70.0 to 76.8, about **6.8 pp**, under one harness. Opus 4.5 beat Opus 4.6 by about 1 pp, so a generation step was not visible here. — [Simon Willison](https://simonwillison.net/2026/Feb/19/swe-bench/)
- **Harness optimization at a fixed model on TB 2:**
  - GPT-5.4 (high) went from 69.7 to 77.0 (+7.3 pp) through automated harness evolution (AHE).
  - An unnamed fixed model went from 52.8 to 66.5 (+13.7 pp) through prompt, middleware and verification changes (LangChain/Trivedy).
  - Both are second-hand. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)

### Inferences
- The answer depends on where you are on the capability curve. Across generations and sizes, the model effect (13–50 pp) dominates. Among same-tier frontier models, which differ by about 2–7 pp, harness choice (5–13 pp in controlled settings) is the bigger lever and can reverse rankings.
- A thesis can phrase it as: *"one good harness change ≈ one model generation"*. That is only true for well-optimized harness changes. The typical difference between two off-the-shelf harnesses is about a third of a generation.

### Gaps
- The 2605.23950 factorial uses only 3 models, 100 tasks and 2 runs. The CIs on 100 tasks are wide (about ±10 pp), so the 7.8× ratio is fragile.
- No variance decomposition exists for Terminal-Bench across many models and harnesses. Table 2 could be used for one, but its harness coverage per model is unbalanced.

## Q3. What do HAL, Epoch AI, METR and independent evaluators say about scaffold sensitivity, including cost and tokens?

### Takeaway
HAL and Epoch find large scaffold sensitivity in accuracy and cost. METR found that vendor CLIs (Claude Code, Codex) did **not** beat its generic scaffolds on time horizon. Token use per harness differs by up to **about 66×** for the same model with similar accuracy.

### Cited Findings
- **HAL** (Princeton, 2025-10-13; ICLR 2026). Source for all points: [HAL paper](https://arxiv.org/html/2510.11977)
  - 21,730 rollouts, 9 models, 9 benchmarks, about **$40,000** in total.
  - Task-specific scaffolds won on accuracy: 11 of 12 on SWE-bench Verified Mini and 9 of 12 on CORE-Bench Hard. Generalist scaffolds were **cheaper in 20 of 24** comparisons.
  - Higher reasoning effort gave equal or lower accuracy in **21 of 36** model × scaffold × benchmark combinations.
  - Token use correlated positively with accuracy on 6 of 9 benchmarks.
  - Model × scaffold interaction (web-agent domain, not coding): Claude did better with BrowserUse and OpenAI models with SeeAct. In one example, SeeAct + GPT-5 Medium cost $171 vs Browser-Use + Claude Sonnet 4 at $1,577, about 9× more for about 2 pp accuracy difference.
  - Agents on SciCode and CORE-Bench almost never finished a run without at least one tool-calling failure.
- **Epoch AI:**
  - The scaffold has "the single biggest impact on the overall performance" in their SWE-bench setup. — [Epoch, "Why benchmarking is hard" (search snippet; page did not load)](https://epoch.ai/gradient-updates/why-benchmarking-is-hard)
  - OpenAI reports up to 3% improvement on SWE-bench Verified from using its Responses API, so the API layer itself is a factor. — [same Epoch source, search snippet](https://epoch.ai/gradient-updates/why-benchmarking-is-hard)
  - Scaffold-only swings of up to 11 pp (GPT-5) and 15 pp (Kimi K2 Thinking). — [second-hand via arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
  - The Feb 2026 scaffold upgrade (v2.0.0) "significantly improved performance." — [Epoch SWE-bench Verified page (search snippet)](https://epoch.ai/benchmarks/swe-bench-verified)
- **METR** (note dated 2026-02-13). Source for the first three points: [METR note](https://metr.org/notes/2026-02-13-measuring-time-horizon-using-claude-code-and-codex/)
  - Setup: Opus 4.5 run in Claude Code vs METR's ReAct scaffold, and GPT-5 in Codex vs METR's Triframe scaffold.
  - Result: **neither vendor harness outperformed** METR's default scaffolds on 50% time horizon, and neither difference was statistically significant. Claude Code beat ReAct in 50.7% of bootstrap samples; Codex beat Triframe in only 14.5%.
  - Token budgets were fixed at 8M (Opus) and 16M (GPT-5). A follow-up gave Opus in Claude Code 32M, and the measurement barely changed. METR notes the wrappers were rough, and that prompting the agents not to give up early might help "moderately."
  - A METR staff comment says Opus 4.6's horizon exceeds what the Time Horizon 1.1 suite can measure. — [LessWrong thread](https://www.lesswrong.com/posts/3SywPAjGQWCtQFafb/you-re-gonna-need-a-bigger-boat-benchmark-metr)
- **Terminal-Bench 2.0 costs and tokens:**
  - For Opus 4.5 across all tasks, **Claude Code used about 256.9M input tokens vs Terminus 2 about 3.9M** (about 66×), while Terminus 2 scored *higher* (57.8 vs 52.1).
  - The paper finds that "higher token count does not necessarily correlate with better performance" and "essentially no correlation" between average turns and success.
  - A full TB 2.0 run costs between $1 and $100 depending on the model.
  - Source for all three points: [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- **Codex CLI vs Claude Code tokens:** Codex CLI reportedly uses about 3–4× fewer tokens than Claude Code on equivalent tasks. This is secondary and not a same-model comparison. — [D. Vaughan blog citing Jozefiak](https://codex.danielvaughan.com/2026/04/19/the-harness-effect-same-model-different-tool-different-score/)
- **SWE-rebench:** Claude Code used about 2M tokens per task, the most in the top ten (older update). — [habr (search summary)](https://habr.com/ru/post/977902)

### Inferences
- Cost per solved task may vary more across harnesses than accuracy does: about 66× tokens for a 5.7 pp gap in the wrong direction on TB 2.0. A thesis should report tokens or cost next to accuracy as a primary outcome.
- METR's null result and the TB 2.0 result (Claude Code not best for Claude models) both contradict the popular claim that vendor harnesses always win. The exception is Codex CLI with OpenAI models (see Q4).

### Gaps
- Could not read Epoch's own per-scaffold table or its token and cost data.
- METR's exact time-horizon point estimates (in minutes) per scaffold were not extracted.
- HAL's per-model cost on SWE-bench Verified Mini, generalist vs SWE-agent, was not extracted (the second half of the paper was not read).

## Q4. Is there a "home-field advantage" (Claude in Claude Code, GPT/Codex models in Codex CLI with apply_patch)?

### Takeaway
The evidence is **asymmetric**. OpenAI GPT-5-family models do best in Codex CLI in every TB 2.0 measurement, by 2–9 pp over the next-best harness. Claude models were *never* best in Claude Code on TB 2.0 (late-2025 data), and Gemini 2.5 was near the bottom in Gemini CLI. Tool-format fit is real and measurable: patch-style edits hurt models not trained on them by up to about 60 pp, while Codex models barely gain from alternatives.

### Cited Findings
- **TB 2.0, OpenAI models in Codex CLI**, margin over the next-best harness (my calculation from Table 2): GPT-5.2 +8.9 (vs Terminus 2), GPT-5 +8.1 (vs OpenHands), GPT-5-mini +4.2, GPT-5-nano +2.0. Against mini-SWE-agent the gaps are 15.7 pp (GPT-5), 9.7 (mini) and 4.5 (nano). — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- **TB 2.0, Anthropic models in Claude Code:**
  - Opus 4.5 scores 52.1 vs Terminus 2 at 57.8 (**−5.7 pp**). Sonnet 4.5 and Opus 4.1 have Claude Code last (−2.7 and −3.2, inside or near the CI). Haiku 4.5: Claude Code −2.3 vs mini. — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
  - TB 2.1 later lifted Claude Code + Opus 4.6 from 58.0 to 70.1, above Terminus 2 at 63.8. Part of the "away advantage" was due to broken tasks. — [dev.to citing the TB 2.1 notes, second-hand](https://dev.to/gabrielanhaia/stop-comparing-models-same-claude-58-or-76-on-terminal-bench-depending-on-the-harness-16d1)
- **TB 2.0, Google models in Gemini CLI:** Gemini 2.5 Pro scores 19.6 vs Terminus 2 at 32.6 (**−13.0 pp**). Gemini 2.5 Flash scores 15.4, last of four. — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- **Vendor harnesses on other benchmarks:**
  - SWE-bench Pro: Opus 4.5 gains +9.5 pp in Claude Code over SEAL.
  - SWE-bench Verified: Grok 4 gains +14–16 pp in the xAI scaffold over SWE-agent.
  - The paper does not analyze this as home-field advantage, and both are vendor-favorable comparisons. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
- **METR:** Claude Code (Opus 4.5) and Codex (GPT-5) did not significantly beat generic scaffolds. METR acknowledges that both CLIs are "explicitly optimized for their respective model families." — [METR 2026-02-13](https://metr.org/notes/2026-02-13-measuring-time-horizon-using-claude-code-and-codex/)
- **Tool-format swap experiment** (Can Bölük, 2026-02-12). Setup: 180 synthetic React bug-fix tasks × 3 runs, 16 models, and three edit formats: patch (apply_patch-style), str_replace and "hashline". Source for all points below: [Bölük, "The Harness Problem"](https://stencil.so/blog/the-harness-problem)
  - Hashline beat patch for 14 of 16 models, with an average gain of **+15 pp over patch**.
  - Grok Code Fast 1 went from 6.7% to 68.3% (+64.6 pp) with only the edit tool changed. Other gains: MiniMax M2.1 +41.7, Devstral Medium +40.5, Claude Sonnet 4.5 +14.4, Claude Haiku 4.5 +13.0.
  - **GPT-5.2 Codex gained only +4.6 over patch** and was −0.4 vs replace while using +26% tokens. This fits the model being trained on patch format.
  - Patch failure rates for non-Codex models: Grok 4 50.7%, GLM-4.7 46.2%.
  - Output tokens fell by up to 61% with the better format.
  - The benchmark is small, synthetic and run by the author. It is not peer-reviewed.
- **Cursor (2026-04-30):** OpenAI models are trained on patch-based edits and Anthropic models on string replacement. Giving a model the unfamiliar tool "costs extra reasoning tokens and produces more mistakes." No magnitude is published. Copilot CLI exposes apply_patch only to Codex-family models. — [Bustamante, citing Cursor, second-hand](https://nicolasbustamante.com/blog/model-harness-fit)
- **Codex in third-party harnesses:** Codex-tuned GPT models are trained on the Codex CLI loop, its apply_patch tool and its system prompt, and lose some performance in harnesses with generic tool names. This is a secondary, qualitative claim. — [D. Vaughan blog](https://codex.danielvaughan.com/2026/04/28/codex-models-third-party-harnesses-apply-patch-v4a-portable-agent/)

### Inferences
- Home-field advantage is clearest at the **tool-format level** (edit tool schema) and for **OpenAI/Codex models**. It is not a general "native CLI wins" law: Claude Code and Gemini CLI were not best for their own models on TB 2.0 (late 2025).
- Possible confounders: vendor CLIs carry product features (safety prompts, permission flows, early stopping) that hurt unattended benchmark runs, and benchmark harness adapters (Harbor) may wrap CLIs imperfectly, as METR noted.

### Gaps
- I found no controlled study that swaps only the tool format inside the same real harness on SWE-bench or TB. For example: Claude in Codex CLI with apply_patch vs with str_replace, or GPT-5.x-Codex in Claude Code.
- No 2026 TB grid exists of Claude Opus 4.6/4.7 vs GPT-5.4/5.5 crossed with Claude Code and Codex CLI. Leaderboard entries are not crossed.

## Q5. How close does a minimal harness (mini-SWE-agent, Terminus) get to elaborate harnesses, and is the gap larger with weaker models?

### Takeaway
For **Claude models**, minimal harnesses (mini-SWE-agent, Terminus 2) match or beat elaborate ones on TB 2.0 (within 0–3 pp). On SWE-bench Verified, bash-only mini-SWE-agent is about 4 pp below vendor scaffolds at the frontier. For **OpenAI GPT-5-family models** the minimal harness loses 5–16 pp to Codex CLI. Weaker models gain the most from better tooling in *relative* terms and in edit-format experiments, but not uniformly in absolute pp.

### Cited Findings
- **mini-SWE-agent vs the best harness on TB 2.0** (from Table 2):
  - Claude: Sonnet 4.5 −0.3, Opus 4.1 −2.9, Haiku 4.5 best.
  - Grok 4 and Grok Code Fast 1: mini is best (+5.6 and +10.0).
  - Gemini 2.5 Pro: −6.5.
  - GPT-5: −15.7. GPT-5-mini: −9.7. GPT-5-nano: −4.5 (39% relative).
  - Source: [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- **Terminus 2** (a minimal tmux-only harness) was best of all tested agents for Opus 4.5, Sonnet 4.5, Opus 4.1, Gemini 2.5 Pro, GPT-OSS-120B and Kimi K2. — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- **2026 frontier on TB 2.0, Opus 4.6:** Terminus 2 at 62.9 vs benchmark-tuned harnesses at 74.7–79.8 (Terminus-KIRA, ForgeCode, Meta-Harness), a 12–17 pp gap. On TB 2.1, Terminus 2 at 63.8 vs Claude Code at 70.1 (−6.3). — [dev.to, second-hand](https://dev.to/gabrielanhaia/stop-comparing-models-same-claude-58-or-76-on-terminal-bench-depending-on-the-harness-16d1); [Bustamante, second-hand](https://nicolasbustamante.com/blog/model-harness-fit)
- **SWE-bench Verified:** bash-only Opus 4.5 at 76.8 (2026-02) vs vendor 80.9 (2025-11), about **−4 pp**. — [Simon Willison](https://simonwillison.net/2026/Feb/19/swe-bench/); [TechCrunch](https://techcrunch.com/2025/11/24/anthropic-releases-opus-4-5-with-new-chrome-and-excel-integrations)
  - Sonnet 4 (2025): mini 64.9 vs vendor 72.7, about −8 pp. This is second-hand and unverified. — [survey arXiv 2606.20683 via search](https://arxiv.org/pdf/2606.20683)
  - Read together, the bash-only gap may be shrinking as models get stronger (−8 for Sonnet 4 to −4 for Opus 4.5). This is my inference from two non-comparable sources.
- **Weaker vs stronger models:**
  - In the edit-format experiment, the weakest models gained most (Grok Code Fast 1 +64.6, MiniMax M2.1 +41.7, Devstral Medium +40.5), while strong or format-native models gained least (GPT-5.2 Codex +4.6, Gemini 3 Flash +8). — [Bölük](https://stencil.so/blog/the-harness-problem)
  - In the 2605.23950 factorial, the largest harness gain went to GLM-5.1 (+13.0), the lowest H1 starter but similar to the others. GPT-5.4 and Kimi K2.6 both gained +8.5. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
  - On TB 2.0, absolute spreads are smallest for the very weakest models (GPT-OSS-20B 0.3 pp, Gemini 2.5 Flash 1.7 pp, near the floor) and large for mid-to-strong models (GPT-5 15.7, Gemini 2.5 Pro 16.9). The pattern is not monotonic. — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
  - HAL: generalist-vs-specialist gaps on SWE-bench Verified Mini were very large for mid models (o4-mini about 48 pp, GPT-5 Medium 34 pp) and also 34 pp for Sonnet 4.5. — [arXiv 2605.23950 citing HAL](https://arxiv.org/html/2605.23950v1)
- **Small-model harness lift:** one blog post looks at how far a better harness takes a small coding model, but I did not read it. — [dev.to (Barakhov)](https://dev.to/dmitry_barakhov/how-far-can-a-small-coding-model-go-with-a-better-harness-18lb)

### Inferences
- "Minimal is enough" holds for Anthropic models and general bash or tmux tasks (TB 2.0 in late 2025, about 4 pp on SWE-bench Verified). It does not hold for OpenAI models detached from Codex CLI, or against harnesses tuned specifically for the benchmark (+12–17 pp on TB 2.0 in 2026).
- The "weak models need more harness" claim is supported for **brittle interfaces** (edit formats, tool-call parsing). Absolute pp gaps can be small near the floor (the model can't solve the task anyway), so relative effects should be reported too.

### Gaps
- No single controlled study varies model strength systematically (same family, several sizes) across a minimal and an elaborate harness. The GPT-5 / mini / nano rows in TB 2.0 are the closest: Codex − mini-SWE-agent gaps of 15.7 / 9.7 / 4.5 pp, relative 46% / 44% / 64%.
- No 2026 (post-Feb) bash-only vs vendor comparison for GPT-5.4/5.5 or Opus 4.6/4.7 was verified.
