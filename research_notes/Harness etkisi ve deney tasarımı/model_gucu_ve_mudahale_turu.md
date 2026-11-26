# Model strength and intervention type: when do nudges and information interventions help?

Scope: evidence for (H1) text-only nudges, reminders and prompt layers give little or no benefit to strong models; (H2) interventions that supply new external information (execution or test feedback) help more, and harness effects grow as model strength falls and task length grows.
Research date: 2026-11-23. Setting tags: **[single-shot]** = non-agentic QA/benchmark prompting; **[iterative-code]** = multi-turn code generation with execution but not a full agent; **[agentic]** = tool-using agent loop (SWE-bench, Terminal-Bench, GAIA, METR).

## 1. Prompting techniques (CoT, personas, threats/tips, self-reflection prompts) vs model strength

### Takeaway
The best controlled evidence (Wharton GAIL reports, 2025) supports H1 for single-shot QA. Explicit CoT gives non-reasoning models a moderate average gain (about 12-14%) and reasoning models close to nothing (about -3% to +3%), with 20-80% more time spent. Threats, tips, politeness and expert personas give no reliable aggregate gain on any model. Intrinsic self-correction, where the model is prompted to "review your answer" with no new information, does not help reasoning and can hurt it. All of this evidence is [single-shot] or short multi-turn. None of it comes from an agentic coding loop.

### Cited Findings
- **Wharton Prompting Science Report 2 (Meincke, Mollick, Mollick, Shapiro; June 8, 2025), "The Decreasing Value of Chain of Thought in Prompting"** [single-shot, GPQA Diamond, multiple trials per condition]:
  - Explicit CoT gives reasoning models only marginal gains: o3-mini +2.9%, o4-mini +3.1%, Gemini Flash 2.5 -3.3%. CoT requests take about 20-80% longer. — [arXiv 2506.07142](https://arxiv.org/abs/2506.07142v1); [Wharton GAIL tech report](https://gail.wharton.upenn.edu/research-and-insights/tech-report-chain-of-thought/)
  - Non-reasoning models still gain on average, most for older or smaller ones: Gemini Flash 2.0 +13.5%, Sonnet 3.5 +11.7%. CoT also adds variability and can cause errors on questions the model would otherwise answer correctly. Many models already do CoT-like reasoning without being asked. — [arXiv 2506.07142](https://arxiv.org/pdf/2506.07142)
- **Wharton Prompting Science Report 3 (Aug 1, 2025)**: threatening or tipping the model gives no meaningful aggregate gain. Prompt variations do shift results on individual questions, unpredictably. The authors' advice is to test variants but expect noise. — [Wharton GAIL "threaten or tip"](https://gail.wharton.upenn.edu/research-and-insights/techreport-threaten-or-tip/); [arXiv 2508.00614](https://arxiv.org/abs/2508.00614v1)
- **Wharton Report 1 ("Prompt engineering is complicated and contingent", March 2025)**: politeness and similar wording changes affect individual answers but have minimal effect overall. — [Wharton GAIL](https://gail.wharton.upenn.edu/research-and-insights/tech-report-prompt-engineering-is-complicated-and-contingent/); [arXiv 2503.04818](https://arxiv.org/abs/2503.04818v1)
- **Wharton Report 4 (expert personas)**: "act as an expert" personas give no consistent accuracy boost and sometimes hurt. — [Penn Today summary](https://penntoday.upenn.edu/news/wharton-why-you-shouldnt-ask-chatbots-act-expert) (secondary source; the primary report was not opened)
- **Huang et al., "Large Language Models Cannot Self-Correct Reasoning Yet" (Google DeepMind/UIUC; ICLR 2024; arXiv Oct 2023)** [single-shot + self-correction rounds]:
  - Without external feedback, models generally fail to improve their reasoning by self-correcting. Performance can drop, and models sometimes change correct answers into wrong ones.
  - Earlier reported gains depended on oracle correctness labels. Without those labels, the methods reduce performance and can lose to simpler methods that use the same number of model calls.
  - Self-correction still helps style and appropriateness edits. The authors point to external feedback (humans, tools) as the way forward. — [arXiv 2310.01798](https://arxiv.org/abs/2310.01798); [ICLR 2024 poster](https://iclr.cc/virtual/2024/poster/18956)
- **Kamoi et al., "When Can LLMs Actually Correct Their Own Mistakes? A Critical Survey" (TACL; arXiv June 2024, v3 Dec 2024)**:
  - The bottleneck is feedback generation. Prompted self-feedback does not help on general tasks. Self-correction works well when reliable external feedback exists, and large-scale fine-tuning can enable it.
  - v3 adds exceptions for some tasks, including code generation and arithmetic, citing Gao et al. 2023 and Shinn et al. 2023. — [arXiv 2406.01297](https://arxiv.org/abs/2406.01297)
- **CRITIC (Gou et al., ICLR 2024)**: relying only on self-correction without external feedback "may yield modest improvements or even deteriorate performance". The authors note "the unreliability of all tested LLMs" at validating their own results. With tool-based checking (search engine, code interpreter), self-correction does work, on free-form QA, mathematical program synthesis and toxicity reduction. — [arXiv 2305.11738](https://arxiv.org/abs/2305.11738v4); [ICLR 2024 proceedings](https://proceedings.iclr.cc/paper_files/paper/2024/hash/fef126561bbf9d4467dbb8d27334b8fe-Abstract-Conference.html)

### Inferences
- In single-shot settings, H1 is well supported. An instruction that asks for behaviour the model already does by default (reasoning, "be careful", "double-check") adds tokens and variance and little accuracy. The marginal value of CoT fell as models internalised reasoning, so the same may well apply to agent-loop "reminder" layers. That extension has not been tested directly.
- Text nudges do not add zero variance. They redistribute per-question outcomes (Wharton Reports 1 and 3). A harness experiment therefore needs repeated trials and per-task paired analysis, or nudge "effects" will be indistinguishable from noise.
- The self-correction literature splits interventions along the line H1/H2 proposes. Rounds of "reflect again" without new information do not reliably help, and can hurt. The same rounds with tool or oracle signals do help. This is the strongest available theoretical anchor for the nudge-vs-information distinction.

### Gaps
- I found no controlled study that measures reminder or warning text inside an **agentic coding loop** (for example "remember to run tests" injected mid-trajectory) across model strengths. The H1 evidence is single-shot QA (GPQA) or short self-correction, extrapolated to agents.
- The Wharton studies use GPQA and similar multiple-choice QA. Whether the same pattern holds for code was not tested there.
- Reflexion (Shinn et al., NeurIPS 2023, [arXiv 2303.11366](https://arxiv.org/abs/2303.11366)) reports 91% HumanEval pass@1 against GPT-4's 80% (abstract figure, not re-fetched in this session). Its coding loop uses **self-generated unit tests**, which are executed, so it is a hybrid of nudge and information. I also recall that its appendix shows weaker models (StarChat-beta) gaining nothing from Reflexion, but I did not verify this in this session.

## 2. External execution feedback vs intrinsic self-correction in code (and effect by model size)

### Takeaway
External execution signals (unit-test results, error messages, runtime traces) consistently beat intrinsic self-critique for code. Reported gains are typically a few points up to about 12 points in [iterative-code] settings, and larger once the model is trained to use the feedback (RLEF). The benefit is not monotonic in model weakness, though. Turning feedback into a correct fix is itself a capability. In Olausson et al., self-repair is bottlenecked by the model's ability to understand the feedback, and prompting alone works poorly for small open models. H2's first half ("information > nudges") is supported. "Weaker benefits more" holds only above a capability floor.

### Cited Findings
- **Self-Debugging (Chen, Lin, Schärli, Zhou; Google DeepMind/UC Berkeley; ICLR 2024; arXiv Apr 2023)** [iterative-code]:
  - The model generates code, executes it and explains it in a loop.
  - With unit tests (TransCoder, MBPP), accuracy rises by up to 12%. With explanation only and no unit tests (Spider), the gain is about 2-3%.
  - The method matches or beats baselines that sample more than 10x as many candidates. — [arXiv 2304.05128](https://arxiv.org/abs/2304.05128v2); [ICLR 2024 paper](https://proceedings.iclr.cc/paper_files/paper/2024/file/2460396f2d0d421885997dd1612ac56b-Paper-Conference.pdf); [ACL workshop page](https://virtual2023.aclweb.org/paper_ACL_71.html)
  - The 12% (unit tests) vs 2-3% (no tests) contrast within one paper is direct evidence that information beats nudges.
- **Olausson et al., "Is Self-Repair a Silver Bullet for Code Generation?" (MIT/Microsoft; ICLR 2024)** [iterative-code; Code Llama, GPT-3.5, GPT-4; HumanEval, APPS]:
  - Once the cost of repair is counted, gains are often modest, vary across subsets, sometimes vanish, and can lose to i.i.d. sampling at small budgets.
  - Self-repair is bottlenecked by the model's ability to give feedback on its own code. Replacing a model's feedback with feedback from a stronger model (GPT-3.5 or GPT-4 critiquing Code Llama; GPT-4 critiquing GPT-3.5) beat both matched baselines every time.
  - Human feedback helped even GPT-4, which "lags far behind human-level debugging". — [arXiv 2306.09896](https://arxiv.org/abs/2306.09896); [ICLR 2024](https://proceedings.iclr.cc/paper_files/paper/2024/hash/9ddc141bdbf9d1db510cefff56c586ad-Abstract-Conference.html)
- **Jiang et al., "Training LLMs to Better Self-Debug and Explain Code" (2024)**: prompting alone yields poor self-debugging in small open-source models. The fix is SFT plus RL with a unit-test-pass reward. — [arXiv 2405.18649](https://arxiv.org/abs/2405.18649)
- **RLEF (Gehring et al., Meta FAIR; ICML 2025; arXiv Oct 2024)** [iterative-code, CodeContests]:
  - Llama 3.1 is trained with PPO to use test results and error messages across turns.
  - The authors report large gains at both 8B and 70B, with about an order of magnitude fewer samples, transferring to HumanEval+ and MBPP+. — [arXiv 2410.02089](https://arxiv.org/abs/2410.02089); [ICML 2025](https://proceedings.mlr.press/v267/gehring25a.html)
  - Secondary figures (Meta-CoT paper's table citing RLEF, not checked against the original): 8B RLEF 16.0 vs SFT 10.0; 70B RLEF 40.1 vs instruct 27.5. — [arXiv 2501.04682](https://arxiv.org/pdf/2501.04682)
- **LDB (Zhong, Wang, Shang; Findings of ACL 2024)**: splits the program into basic blocks, records intermediate variable values at runtime, and has the LLM check each block against the task. This supplies runtime-trace information beyond pass/fail. — [ACL Anthology 2024.findings-acl.49](https://aclanthology.org/2024.findings-acl.49); [arXiv 2402.16906](https://arxiv.org/abs/2402.16906)
- **CRITIC** (see section 1): critiques that include a code interpreter or tool fix outputs where self-critique alone does not. — [arXiv 2305.11738](https://arxiv.org/abs/2305.11738v4)
- **SWE-Gym (Pan et al., arXiv Dec 2024)** [agentic, SWE-bench]:
  - 2,438 Python tasks. Test-suite execution labels trajectories, and those labels train a verifier used for best-of-N selection at inference.
  - Reported self-improvement gains: 19.7% at 32B and 10.0% at 7B on SWE-bench Lite. The 7B figure appears in the v1 text and may have changed in v2. — [arXiv 2412.21139](https://arxiv.org/html/2412.21139v1)
- **R2E-Gym (2025)**: a 32B open model reaches 51% on SWE-bench Verified. Hybrid execution-based plus execution-free verifiers beat either alone. — [arXiv 2504.07164](https://arxiv.org/abs/2504.07164v1)

### Inferences
- Across the code literature, gains from "the model looks at its own code again" without execution are small (Spider-style +2-3%; Olausson's modest net gains). Gains with execution or test signals are larger (up to +12%), and larger still with training to use them (RLEF). This supports H2's claim that information beats nudges in [iterative-code] settings.
- On model size, the picture is more of an inverted U than "weaker benefits more":
  - Models that are too weak cannot interpret feedback (Olausson: feedback quality is the bottleneck; Jiang: prompting fails on small models).
  - Mid-capability models benefit most from external signals.
  - Frontier models may already do implicit testing on their own. This last point is my inference; I found no direct measurement.
- Where the feedback comes from matters as much as whether it exists. Feedback from a stronger model or a human helps weaker generators. This suggests a harness "information" layer is worth most when it supplies information the model cannot produce itself.

### Gaps
- I did not retrieve LDB's or CodeT's exact effect sizes in this session (CodeT: Chen et al. 2022, [arXiv 2207.10397](https://arxiv.org/abs/2207.10397)).
- I found no single study that crosses {no feedback, nudge "check your work", real test feedback} × {small, mid, frontier model} inside an **agentic** coding loop with the same harness. That design is the cleanest test of H1 and H2 together, and it appears to be open.

## 3. Scaffold sensitivity vs model capability (agentic benchmarks)

### Takeaway
Scaffolds clearly matter in agentic coding. Same-model gaps of 9-16 pp are common on SWE-bench and Terminal-Bench, and up to 34-48 pp on HAL's SWE-bench Verified Mini. The claim that **weaker models are more scaffold-sensitive** has mixed support:
- **For:** in Hu et al. (Sep 2026), complex-harness gains shrink as models get stronger on SWE-style repair. In Qian et al. (Aug 2026), weak targets gain most from harnesses written by stronger models.
- **Against:**
  - On GAIA (Starace, Jun 2026), the most capable model gained most from structured scaffolds at the harder level.
  - On open-ended repository tasks (Hu et al.), stronger models benefit from complex harnesses.
  - METR (Feb 2026) found no significant time-horizon difference between Claude Code and its ReAct scaffold for Opus 4.5.
  - In Liu & Han (Oct/Nov 2026), harness swaps change about as many outcomes as reruns (2-5 pp).

The stronger pattern is that harness effect depends on the model × task-type interaction, possibly with a capability floor.

### Cited Findings
- **"Stop Comparing LLM Agents Without Disclosing the Harness" (Zhang, Wang, Ge, Xu, Hamm, Reddy; arXiv May 7, 2026)** [agentic], collecting reported same-model gaps:
  - SWE-bench Pro: Claude Opus 4.5 scores 45.9% under SEAL and 55.4% under Claude Code (+9.5 pp). Adding the WarpGrep search subagent adds 2.1-2.2 pp, enough to flip leaderboard order.
  - SWE-bench Verified: Grok 4 scores 58.6% under SWE-agent and 72-75% under the xAI scaffold (+14-16 pp). In Epoch AI monitoring, scaffold-only variation reaches up to 11 pp (GPT-5) and 15 pp (Kimi K2 Thinking).
  - HAL SWE-bench Verified Mini: Claude Sonnet 4.5 goes from 68% to 34%, GPT-5 Medium from 46% to 12%, and o4-mini shows a gap of nearly 48 pp.
  - Terminal-Bench 2.0: a prompt + middleware + verification change raises a fixed model from 52.8% to 66.5% (+13.7 pp). AHE raises GPT-5.4 (high) from 69.7% to 77.0%.
  - Controlled factorial (100 SWE-bench Verified tasks, 3 harnesses × 3 models tightly clustered at about 45% on a leaderboard): harness H1→H3 adds +13.0 pp for GLM-5.1 and +8.5 pp for GPT-5.4 and Kimi K2.6. The model effect at a fixed harness is only 2.5-5.0 pp. Harness variance averages 7.8x model variance, and 6 of 9 model-pair comparisons reverse rank across harnesses.
  - The paper does **not** analyse sensitivity by capability tier. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)
- **Hu et al., "Beyond the Model: Demystifying Harness Effects in Software Engineering Agents" (arXiv Sep 26, 2026)** [agentic; 10 Qwen/DeepSeek models; mini-SWE-agent and OpenCode; SWE-bench Pro, ProgramBench, GitTaskBench]:
  - "Harness effectiveness depends jointly on model capability and task type." Complex harnesses show diminishing marginal gains on SWE-style issue repair as models get stronger, but can help stronger models on complex, open-ended repository-level tasks.
  - Components studied: tool registry, context compression, explicit planning, subagents, lazy skills. Structured tool use and task-specific subagents were most stable. Context compression and general subagents could **hurt** repository generation.
  - NanoHarness adds +7.37 pp over mini-SWE-agent on Qwen3.7-Max and +6.21 pp on DeepSeek-V4-Pro. — [arXiv 2609.32459](https://arxiv.org/abs/2609.32459)
  - A search-result summary attributes to this paper a "threshold effect", where models too weak cannot reliably use a complex harness. The abstract page I fetched did not show this, so verify it in the full text.
- **Liu & Han, "What Does a Harness Buy? Tokens, Mostly" (arXiv Oct 3, 2026; v2 Nov 18, 2026)** [agentic; 7 models × Claude Code, mini-SWE-agent, OpenCode; SWE-bench Verified + 2 non-SWE benchmarks; every model rerun unchanged]:
  - "No harness leads consistently across models." Where differences can be bounded, they are 2-5 pp, and a harness swap changes about as many task outcomes as a rerun.
  - There are model-specific exceptions: OpenCode trails by 8-9 pp on one model and leads by 14 pp on another.
  - Cost differences between harnesses range from under 10% to 3-4x. — [arXiv 2610.04433](https://arxiv.org/abs/2610.04433)
- **Starace, "Scaffold Effects on GAIA: A Controlled Comparison" (arXiv Jun 7, 2026)** [agentic, GAIA]:
  - Scaffold choice alone shifted accuracy by up to 28 pp within one model.
  - The pre-registered hypothesis that more capable models are less scaffold-sensitive was **rejected in direction**. The most capable Anthropic model gained most from structured scaffolds at Level 2, and tier scaling held only at Level 1.
  - The author argues model family, not capability tier, conditions the effect. The elicitation gap "is not guaranteed to shrink as models improve". — [arXiv 2606.08529](https://arxiv.org/abs/2606.08529)
- **Qian et al., "AI4AI at Test-Time: Strong-to-Weak Capability Transfer via Harnesses" (Salesforce/UIUC; arXiv Aug 12, 2026)** [non-coding, Theory-of-Mind benchmarks]:
  - Harnesses written by a stronger builder model nearly doubled average target performance (0.49 → 0.91). **Weaker targets gain most.**
  - The gains come mainly from moving unstable reasoning into deterministic code, benchmark-specific routing and strict answer-format enforcement, **not** from longer reasoning or more sampling. — [arXiv 2608.12307](https://arxiv.org/abs/2608.12307)
- **METR note (Feb 13, 2026), "Measuring time horizon using Claude Code and Codex"** [agentic, METR task suite]:
  - Neither the GPT-5 nor the Opus 4.5 time-horizon difference between specialised CLIs and METR's default scaffolds was statistically significant. Claude Code beat ReAct in about 50.7% of bootstrap samples for Opus 4.5.
  - A 4x token budget (32M) barely changed the result. Opus 4.5 under Claude Code sometimes over-anchored on a plan. — [METR note](https://metr.org/notes/2026-02-13-measuring-time-horizon-using-claude-code-and-codex/)
- **Run-to-run noise**: ten reruns of one model-scaffold pair on SWE-bench Verified spread by 2.2-6.0 pp, according to a search-result summary of the 2026 harness-disclosure and benchmark-convergence literature. — candidate sources: [arXiv 2609.17394 "Coding Agents Have Converged"](https://arxiv.org/pdf/2609.17394), [arXiv 2605.23950](https://arxiv.org/pdf/2605.23950). I did not confirm which paper gives this figure.
- **Small-model training work (SWE-Gym, R2E-Gym)**: open 7B-32B agents rely heavily on **execution-based verifiers** and test-labelled trajectories, an information intervention, rather than prompt changes. — [arXiv 2412.21139](https://arxiv.org/html/2412.21139v1); [arXiv 2504.07164](https://arxiv.org/abs/2504.07164v1)

### Inferences
- The broad claim "scaffold matters" is strong. "Scaffold matters more for weaker models" holds in some regimes, mainly well-specified SWE-style repair and with harness designs that offload work to code. It fails in others: hard open-ended tasks, GAIA Level 2, and frontier models on METR tasks.
- What the "big harness gains" have in common is that they **add capability or information**: search subagents, verification middleware, deterministic code offloading, routing, format enforcement. Pure prompt text is rarely the active ingredient. The Terminal-Bench +13.7 pp bundle mixes prompt, middleware and verification, so it does not isolate the nudge part.
- Liu & Han's result (harness swap ≈ rerun noise among mature harnesses) suggests that among already-competent harnesses, marginal differences are small for strong models. Much of the reported 10-48 pp spread probably reflects comparisons against weak or misconfigured baselines.

### Gaps
- No study found isolates **nudge-only** harness layers (reminder or warning text) from information layers (test execution, linters) in an agentic ablation across capability tiers.
- HAL's per-model scaffold gaps were not obtained from the primary HAL source in this session; they are cited via arXiv 2605.23950.
- Per-model tables from Hu et al. and Liu & Han, which would show whether the gain-vs-strength slope is monotonic, were not retrieved. Only the abstracts were read.

## 4. Task length / horizon and harness value

### Takeaway
Agent success falls steeply with human task length. METR's 50% horizon, about 50 minutes for Claude 3.7 Sonnet in early 2025, has doubled about every 7 months, and success decays roughly exponentially with length, as if errors occurred at a constant hazard rate. That makes per-step error recovery (verification, context management) plausibly more valuable on long tasks. I found **no study that directly measures harness benefit as a function of task length**. The closest evidence is indirect: GAIA Level 2 shows bigger structured-scaffold gains and more mid-trajectory recovery, and in Hu et al. stronger models benefit from complex harnesses only on complex, open-ended tasks.

### Cited Findings
- **METR, "Measuring AI Ability to Complete Long Tasks" (Kwa et al.; arXiv Mar 2025; NeurIPS 2025)**:
  - The 50% time horizon is the human task duration at which the agent succeeds 50% of the time. It grew exponentially from 2019 to 2025 with a doubling time of about 7 months, possibly faster since 2024. Claude 3.7 Sonnet sits at about 50 min.
  - 170 tasks, mostly SWE, ML and cybersecurity. — [arXiv 2503.14499](https://arxiv.org/abs/2503.14499v1); [METR blog](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)
  - Time Horizon 1.1 (Jan 29, 2026) tracks current models and also reports 80% horizons, with trends similar to the 50% ones. — [METR TH 1.1](https://metr.org/blog/2026-1-29-time-horizon-1-1); [METR time-horizons page](https://metr.org/time-horizons/)
- **Ord, "Is there a half-life for the success rates of AI agents?" (arXiv May 2025)**: models METR's data as a constant per-unit-time failure rate, so success decays exponentially with task length. — [arXiv 2505.05115](https://arxiv.org/pdf/2505.05115)
- **METR scaffold check (Feb 2026)**: on the long-task suite, specialised CLIs (Claude Code, Codex) did not significantly beat simple scaffolds for frontier models. This is evidence against large harness gains at the frontier even on long tasks. — [METR note](https://metr.org/notes/2026-02-13-measuring-time-horizon-using-claude-code-and-codex/)
- **Starace (GAIA)**: structured scaffolds made fewer tool calls but recovered more often from mid-trajectory errors at the harder Level 2. Scaffold gains were larger there for the most capable model. — [arXiv 2606.08529](https://arxiv.org/abs/2606.08529)
- **Hu et al.**: stronger models benefit from complex harnesses on complex, open-ended repository-level tasks, while gains shrink on SWE-style issue repair. Context compression could hurt repository generation, so context management is not uniformly positive on long tasks. — [arXiv 2609.32459](https://arxiv.org/abs/2609.32459)
- **Harness-disclosure paper**: scopes its thesis to long-horizon tasks. Its factorial stratifies SWE-bench Verified by difficulty (39 under 15 min, 52 at 15 min-1 h, 8 at 1-4 h, 1 over 4 h; 50-step budget) but does **not** break harness effects down by length. — [arXiv 2605.23950](https://arxiv.org/html/2605.23950v1)

### Inferences
- Under a constant-hazard model, any intervention that lowers the per-step error rate or catches errors before they compound has an effect that grows with task length. Verification and test feedback do this mechanically. A one-time nudge does not supply new information at each step. This gives a principled reason for H2's task-length claim, but it is a model-based argument, not a measured one.
- The relevant "difficulty" axis may be relative: task length compared with the model's own horizon. A strong model on a 30-minute task is like a weak model on a 2-minute task, so H2's "weaker model" and "longer task" claims may be one axis, the task's position on the model's success curve. Starace and Hu et al. show larger harness gains when the task is hard **for that model**, which fits this view.

### Gaps
- No paper found plots harness or verification benefit against METR-style task length with model fixed.
- METR's per-model current horizon values (late 2026) were not retrieved.
- Evidence on long-horizon degradation *mechanisms* (context rot, plan drift) and on which harness features fix them is anecdotal here: METR's "over-anchored on a plan" observation and Hu et al.'s finding that compression can hurt. Systematic ablations were not found.
