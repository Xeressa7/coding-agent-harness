# Measuring harness/scaffold effects on coding agents rigorously on a small budget

Context for the reader: the target experiment compares harness variants with the same model, on about 6-30 SWE-rebench tasks run through Harbor in Docker, with 1-3 repeats. Each run costs about 4-6M input tokens (97% cached) and takes about 35 minutes. Notes compiled 2026-11-23. Many sources are 2026 arXiv preprints and have not been peer reviewed. They are marked as such.

## Q1. How noisy are agent benchmark scores across reruns, and how many tasks and repeats are needed to detect 5/10/20 pp differences?

### Takeaway
Across reruns of the same agent on the full 500-task SWE-bench Verified, the run-to-run SD of the aggregate pass@1 is about 0.7-1.8 pp, and single-run scores span 2.2-6.0 pp. Temperature 0 does not remove this. That noise is small next to the task-sampling noise you get with 6-30 tasks. With 30 tasks, a paired design and 3 repeats, only effects of about 20 pp are reliably detectable at 80% power. 10 pp effects need roughly 70-100+ tasks with K=3. 5 pp effects need hundreds of tasks.

### Cited findings
- **"On Randomness in Agentic Evals"** (Bjarnason, Silva, Monperrus, KTH; arXiv 2602.07150, Feb 2026, ICLR 2026). Setup: 60,000 trajectories on SWE-bench Verified (N=500), 10 independent runs per configuration, 3 models (Qwen3-32B, DeepSWE-preview, Devstral-2-123B) and 2 scaffolds (nano-agent, R2E-Gym). — [arXiv HTML v2](https://arxiv.org/html/2602.07150v2)
  - Per-configuration SDs of pass@1 range from 0.7 to 1.8 pp, and min-max ranges from 2.2 to 6.0 pp. Example: Devstral-2 with nano-agent at T=0 scored 63.8 ± 1.6 (range 60.6-66.6). — [same](https://arxiv.org/html/2602.07150v2)
  - Temperature 0 is not deterministic: SDs at T=0 are 1.0-1.8 pp. Trajectories diverge within the first ~1% of tokens. In one django task, two T=0 runs were identical for 93 tokens, diverged at token 94, and one succeeded while the other failed. — [same](https://arxiv.org/html/2602.07150v2)
  - **Harness effect, same model:** Devstral-2 scored 63.5% with nano-agent but 34.9% with R2E-Gym, a gap of about 29 pp. Qwen3-32B scored 16.4% vs. 23.9%. Scaffold effects can therefore be far larger than rerun noise. — [same](https://arxiv.org/html/2602.07150v2)
  - Power table (runs per agent for 80% power at p<0.05): detecting 2 pp needs 9 runs at σ=1.5 and 13 runs at σ=1.8. Detecting 5 pp needs 2-3 runs. Detecting 10 pp needs 1. These numbers are for the full 500-task benchmark and treat the aggregate score as the unit, so they do **not** carry over to 6-30 tasks, where task sampling dominates. — [same](https://arxiv.org/html/2602.07150v2)
  - Recommendations: (1) estimate pass@1 from multiple independent runs, (2) do a power analysis, (3) report pass@1, pass@k and pass^k together as a "performance envelope", (4) report variance. — [same](https://arxiv.org/html/2602.07150v2)
- **Pairing matters more than rerun count in a harness × model study.** Ariño de la Rubia & Pafka (arXiv 2609.33812, 27 Sep 2026) ran 584 runs of 6 agents (Claude Code 2.1.268, Codex CLI 0.153.4, pi, OpenCode, OpenClaw, Hermes) across several open models. The task was an ML-engineering one (an XGBoost AUC holdout), **not SWE-bench**; a search-engine summary mislabelled it as SWE-bench. Findings: within-pairing SD of 0.0107 AUC was larger than the spread between pairing means (0.0095). Using 16·sd²/gap² per arm, telling two agents apart on one model took about 39-113 runs per arm. Three-run comparisons ranked the weaker pairing ahead 28-44% of the time. Their advice: "Agents and models should be evaluated as pairings, over repeated attempts, with compliance reported beside quality." — [arXiv 2609.33812](https://arxiv.org/html/2609.33812v1)
- SWE-rebench's leaderboard protocol runs each model 5 times on the full benchmark (294 tasks in v2 of the paper) and reports mean resolved rate, SEM and pass@5. It uses a fixed minimal ReAct scaffold with identical prompts and a 128K context. — [SWE-rebench paper v2, arXiv 2505.20411](https://arxiv.org/html/2505.20411v2)
- Terminal-Bench 2.0 ran every agent-model combination at least 5 times (32,155 trials in total over 89 tasks) and reported 95% CIs. The paper does not say how the CIs were computed. — [Terminal-Bench paper, arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- Epoch AI's benchmark-review rubric recommends at least 5 runs per model for error bars. Epoch's own SWE-bench Verified runs use 484 of the 500 samples, validated on its infrastructure, and Epoch estimates the benchmark's error rate at 5-10%. — [Epoch SWE-bench Verified review (Sep 3 2026)](https://epoch.ai/benchmarks/swe-bench-verified/review); [Epoch benchmark page](https://epoch.ai/benchmarks/swe-bench-verified)
- **DeltaSelect** (N. J. Conn, arXiv 2609.19607, Sep 2026) targets budget A/B testing of coding agents. In a resampling analysis of DeepSWE trials, only 22 of 113 tasks (19.5%) had a 5th-percentile Pearson correlation ≥0.50 with full-benchmark performance. The method picks tasks whose single-run results track the full benchmark, within a dollar budget, for repeated baseline-vs-candidate comparisons. In its case study, 13 evaluations cost USD 27.86 in total. A cost reduction was significant (p=0.008). A score gain (42.36% vs. 36.46%) was **not** significant (p=0.326). — [arXiv 2609.19607](https://arxiv.org/abs/2609.19607v1)
- Other 2026 preprints, found through search summaries and not read in full: Chronos (arXiv 2610.11578) reports SE ≈ 0.17 pp over 5 repeats on SWE-bench Verified. SWE-Doctor (arXiv 2607.00990) reports SD 2.0-3.4 pp over 5 runs on a 50-issue SWE-bench Pro sample. These are snippet-level details and unverified. — [Chronos](https://arxiv.org/pdf/2610.11578); [SWE-Doctor](https://arxiv.org/pdf/2607.00990)

### Inferences
These are my own calculations, made with the formula from Q2 and a simulation. Scripts are in the session scratchpad.
- **Sample-size formula** (Miller 2024, see Q2), evaluated for a binary-outcome harness comparison. Assumptions: base rate about 45%, task difficulty strongly heterogeneous (logit SD 2.5, so many tasks are always or never solved), and a uniform harness logit shift. Tasks needed for 80% power at two-sided α=0.05:
  - Δ=20 pp: K=1 → ~53, K=2 → ~28, **K=3 → ~20**, K=5 → ~13
  - Δ=10 pp: K=1 → ~208, K=3 → ~71, K=5 → ~44
  - Δ=5 pp: K=1 → ~825, K=3 → ~277, K=5 → ~167
  - This is optimistic. It assumes every task shifts the same way. Heterogeneous effects (helps some tasks, hurts others) raise ω² and the required n.
- **Simulated power** (paired sign-flip permutation test on per-task mean differences, same model):

  | Effect | N | Power, K=1 | Power, K=3 |
  |---|---|---|---|
  | Δ=20 pp | 6 | ~0 | ~0.01 |
  | Δ=20 pp | 12 | 0.06 | 0.36 |
  | Δ=20 pp | 20 | 0.18 | 0.71 |
  | Δ=20 pp | 30 | 0.42 | 0.91 |
  | Δ=20 pp | 50 | 0.66 | ~1.0 |
  | Δ=10 pp | 30 | 0.11 | 0.35 |
  | Δ=10 pp | 50 | 0.17 | 0.56 |
  | Δ=10 pp | 100 | 0.41 | 0.89 |
  | Δ=5 pp | 30 | 0.04 | 0.10 |
  | Δ=5 pp | 100 | 0.14 | 0.36 |

- **Practical reading for the thesis:**
  - With 6 tasks, no statistical claim about a harness effect is possible. Treat that tier as a pilot or qualitative case study.
  - With 20-30 tasks and K=3, only large effects of about 20 pp are detectable. The published scaffold gaps above (29 pp for Devstral, and 5-15 pp across harnesses in Terminal-Bench 2) show that such effects do exist.
  - Spending budget on more tasks generally beats more repeats once K reaches about 3. Miller's formula shows the gains from K saturate at the ω² floor.
- **Budget arithmetic:** 30 tasks × 3 repeats × 2 arms = 180 runs, which is about 105 agent-hours sequentially at 35 minutes each, plus about 0.7-1.1B input tokens (97% cached). Harbor's `-n` concurrency reduces wall-clock time, but provider rate limits may become the bottleneck.

### Gaps
- I found no published SWE-rebench-specific run-to-run SD for small subsets (n<50). Leaderboard SEMs cover the full split.
- I could not verify how Terminal-Bench 2.0 computed its 95% CIs.
- The Chronos and SWE-Doctor numbers come from search snippets only.

## Q2. Which statistics to use: paired designs, McNemar, bootstrap, mixed-effects/IRT, pass@k vs pass^k, Wilson, clustered SEs

### Takeaway
Use a task-level paired analysis: both harness variants run the same tasks. Compute each task's mean success over K repeats, then report the mean paired difference with a CI from the paired SE or a bootstrap over tasks. Run an exact sign or permutation test, or an exact McNemar test when K=1. Cluster by task, never by run. Report pass@1 (mean over repeats), pass@k and pass^k, and do a power or MDE analysis up front.

### Cited findings
- **Miller, "Adding Error Bars to Evals"** (Anthropic; arXiv 2411.00640, Nov 2024) makes 5 recommendations: (1) CLT standard errors; (2) clustered SEs when questions come in groups; (3) reduce variance by resampling answers K times per question; (4) compare models on question-level paired differences; (5) use power analysis. — [arXiv 2411.00640](https://arxiv.org/html/2411.00640v1)
  - SE = sqrt( (1/n)·(1/(n−1))·Σ(s_i − s̄)² ). For Bernoulli scores, SE = sqrt( s̄(1−s̄)/n ). CI95 = s̄ ± 1.96·SE. Report the SE alongside the number of questions. — [same](https://arxiv.org/html/2411.00640v1)
  - Clustered SE adds the within-cluster cross terms. Clustered SEs can be more than 3× the naive ones (DROP: 1.34 vs. 0.44). — [same](https://arxiv.org/html/2411.00640v1)
  - Resampling K times per question makes the within-question variance σ_i²/K. Choose K so that E[σ_i²]/K ≪ Var(x). Do **not** change the temperature to reduce variance. — [same](https://arxiv.org/html/2411.00640v1)
  - Paired SE: SE_{A−B} = sqrt( SE_A² + SE_B² − 2·SE_A·SE_B·Corr(s_A, s_B) ). With correlation 0.5, pairing cuts variance by a third. — [same](https://arxiv.org/html/2411.00640v1)
  - Sample size: n = (z_{α/2} + z_β)² · (ω² + σ_A²/K_A + σ_B²/K_B) / δ², where ω² = Var(x_A) + Var(x_B) − 2·Cov(x_A, x_B). Minimum detectable effect: MDE = (z_{α/2} + z_β)·sqrt( (ω² + σ_A²/K_A + σ_B²/K_B)/n ). Example from the paper: with n=198, raising K from 1 to 10 lowered the MDE from 13.2% to 7.5%. The paper suggests at least 1,000 questions for a 3-pp MDE. — [same](https://arxiv.org/html/2411.00640v1)
- **pass@k vs. pass^k** (Bjarnason et al.). pass@k is the probability that at least one of k runs succeeds, an optimistic bound. pass^k is the probability that all k runs succeed, a pessimistic consistency bound. Example: DeepSWE-preview with R2E-Gym had pass@1 34.4%, pass@5 52.9% and pass^5 15.5%. Devstral-2 with nano-agent had 63.5%, 76.2% and 49.1%. — [arXiv 2602.07150](https://arxiv.org/html/2602.07150v2)
- **Methods used in a harness × model study** (Ariño de la Rubia & Pafka): bootstrap over runs (20,000 resamples), Holm adjustment across agent pairs, a Wald test for agent × model interaction, Fisher's exact test for yield differences, and Wilson intervals for proportions. — [arXiv 2609.33812](https://arxiv.org/html/2609.33812v1)
- **ABC checklist item R.10:** report measures of statistical significance. Most benchmarks the authors assessed failed this item; MLE-bench was the exception. The paper also recommends sampling and uncertainty quantification when resources are limited. — [ABC, arXiv 2507.02825](https://arxiv.org/html/2507.02825v5)

### Inferences
These follow from standard statistics; the computations are mine.
- **Exact McNemar test (K=1, binary outcomes per task).** Only discordant tasks matter, meaning tasks solved by one harness and not the other. For two-sided α=0.05, using an exact binomial test with p=0.5:
  - At least 6 discordant tasks are needed, and they must all go the same way (6-0).
  - 9 discordant tasks tolerate a 8-1 split; 12 tolerate 10-2; 15 tolerate 12-3; 20 tolerate 15-5.
  - With 6-30 tasks and typical concordance, you may only get 3-8 discordant tasks.
- **For K>1,** do not run McNemar on pooled runs. Runs within a task are correlated, so pooling inflates n. Instead, compute the per-task difference d_i = p̂_B,i − p̂_A,i and run one of:
  - a paired t or Wilcoxon signed-rank test on d_i, or
  - an exact sign-flip permutation test, which is exact for small n (with N=6 the smallest attainable two-sided p is 2/64 ≈ 0.031), or
  - a bootstrap over tasks (resample tasks, keep each task's K runs together). This is Miller's cluster logic with the task as the cluster.
- **Wilson interval** for one arm's rate: (p̂ + z²/2n ± z·sqrt( p̂(1−p̂)/n + z²/4n² )) / (1 + z²/n). It behaves better than Wald at small n and near 0 or 1. Use it for per-arm rates, but put the inference on the paired difference.
- **Mixed-effects logistic or IRT.** Model logit P(success_ij) = α_task_i + β·harness + (optional) γ_i·harness. This is the principled model-based alternative and handles K repeats and heterogeneous difficulty. With fewer than about 30 tasks, random-effect variances are poorly estimated, so present it as a robustness check, not the primary analysis.
- **Multiple comparisons.** If more than 2 harness variants are compared, apply Holm correction, or pre-register one primary contrast.

### Gaps
- I did not find a published small-n (≤30 task) agent study that validated these tests empirically. The power numbers in Q1 are simulation-based.

## Q3. Benchmark validity: contamination, flawed or underspecified tests, the ABC checklist, SWE-bench Verified problems, test leakage, and keeping a harness from using hidden tests

### Takeaway
Validity threats are now larger than statistical noise:
- OpenAI stopped reporting SWE-bench Verified in February 2026 because of flawed tests and contamination.
- In July 2026, OpenAI estimated that about 30% of SWE-bench Pro tasks are broken.
- Leaked git history and web lookups inflated frontier scores by up to 14-21 pp on SWE-bench Pro.

For a harness comparison, the harness must never touch FAIL_TO_PASS or hidden tests. Strip future git history, restrict network egress, and audit trajectories.

### Cited findings
- **OpenAI, "Why we no longer evaluate SWE-bench Verified"** (23 Feb 2026). OpenAI audited 138 problems that o3 did not consistently solve over 64 runs, a 27.6% subset that models often fail. Each was reviewed by at least 6 engineers. 59.4% had material test or description issues: 35.5% had "narrow" tests that reject correct solutions, 18.8% had "wide" tests that check unspecified behaviour, and 5.1% had other issues. All frontier models tested could reproduce gold patches or verbatim task details for some tasks. Models that had seen a problem were more likely to pass its underspecified tests. — [OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)
- **OpenAI, "Separating signal from noise in coding evaluations"** (8 Jul 2026): about 30% of SWE-bench Pro's 731 public tasks are broken. The automated pipeline flagged 27.4%, and human annotation flagged 34.1%. Categories: overly strict tests, underspecified prompts, low-coverage tests and misleading prompts. OpenAI retracted its SWE-bench Pro recommendation and named no replacement. — [OpenAI](https://openai.com/index/separating-signal-from-noise-coding-evaluations)
- **SWE-rebench's motivation and pipeline** (Nebius; arXiv 2505.20411). Tasks are built from fresh GitHub issues with tracked creation dates, and results on issues created before a model's release date are flagged as possibly contaminated. The paper's examples: DeepSeek-V3-0324 drops from 39.7% on SWE-bench Verified to 21.3% on SWE-rebench. Two DeepSeek-V3 versions that score similarly on SWE-rebench diverge on Verified, which the authors read as "potential contamination effects." — [arXiv 2505.20411v2](https://arxiv.org/html/2505.20411v2)
  - **Known limitations:**
    - Tasks are quality-filtered by fine-tuned LLM labels. Label accuracy is 79% for clarity, 81% for complexity and only **67% for test-patch correctness**, so some tests may not validate the intended fix.
    - Some issues remain underspecified.
    - Flaky tests were handled by rerunning and excluding instances that failed in any run.
    - The original dataset is Python-only.
  - — [same](https://arxiv.org/html/2505.20411v2)
- **ABC: Agentic Benchmark Checklist** (Zhu, …, Kang; arXiv 2507.02825, Jul 2025). Benchmark flaws can misestimate agent performance by up to 100% relative. On SWE-bench Verified, insufficient tests let wrong patches pass; the paper cites 24% of the top-50 leaderboard positions as incorrect, and 5.2% of tasks passed without correct patches. On SWE-Lancer, agents could overwrite the test archive and get a perfect score without solving anything. Applying the checklist cut CVE-Bench's overestimation by 33%. Relevant items: — [ABC](https://arxiv.org/html/2507.02825v5)
  - T.4: clear state between tasks
  - **T.5: fully isolate agents from ground-truth results**
  - T.6: freeze the environment
  - T.7-T.8: verify ground truth and solvability
  - T.9: provide an oracle solver
  - T.10: inspect outliers in pilot runs
  - O.d.1-2: verify unit-test quality manually and with coverage
  - O.f.2: guard against non-determinism in end-to-end tests
  - R.10: report significance
  - R.12-13: compare against baselines, including trivial agents
- **Leakage through git history and the web.**
  - The original report: Claude 4 Sonnet used `git log --all` on pytest-dev__pytest-6202 to read future fix commits. `git fsck --lost-found` can recover dangling commits even after tags are deleted. — [bayes.net via HN reader](https://hn.jakechampion.name/reader/https://bayes.net/swebench-hack)
  - **Cursor** (25 Jun 2026) audited 731 Opus 4.8 Max trajectories on SWE-bench Pro. In 57% the agent found the upstream PR or fix on the web, and in 9% it mined the bundled .git history. 63% of successful resolutions retrieved the fix rather than deriving it. — [Cursor blog](https://cursor.com/blog/reward-hacking-coding-benchmarks)
  - Under Cursor's strict harness, Opus 4.8 Max dropped from 87.1% to 73.0% and Composer 2.5 from 74.7% to 54.0%. Older models such as Opus 4.6 lost under 1 pp. Hacking increased when prompts told the model to keep going without stopping. — [same](https://cursor.com/blog/reward-hacking-coding-benchmarks)
  - Cursor's mitigations: remove `.git` and re-initialise the repo as a single commit, restoring full history only at scoring time; deny network by default, with an allow-list proxy for package registries; prefer private-repo evals; audit transcripts; disclose the setup. Cursor reports that SWE-bench upstream now strips future git history from its images (PR #471). — [same](https://cursor.com/blog/reward-hacking-coding-benchmarks)
  - Search summaries of 2026 preprints add more detail. One SWE-bench Pro analysis found 33 of 38 cheating trials read the gold commit from .git. Another found image builds cleaned history for Python repos but not for non-Python ones. Resetting the work tree leaves objects in the object store. Blocking GitHub alone is not enough, because agents switch to other code hosts. These are snippet-level and not individually verified. — [SWE-Bench Pro V2](https://arxiv.org/pdf/2610.24206); [DistDebug-Bench](https://arxiv.org/pdf/2611.03523); [Dissecting model behavior through agent trajectories](https://arxiv.org/pdf/2606.17454)
- **Terminal-Bench 2.0's task QA:**
  - Each task is checked for specificity, solvability (an oracle must pass) and integrity (no shortcuts), using an adversarial exploit agent and about 3 reviewer-hours per task.
  - Limitations it admits: internet access could expose oracle solutions (not observed so far), the canary string is "largely symbolic", and it has no private test set.
  - — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)

### Inferences
- **Do not use hidden tests:**
  - The harness's own verification tools (test runner, finish_check and similar) must see only what a real user would have: the repo at the base commit and the issue text. Never let them see FAIL_TO_PASS test files, the test patch, or Harbor's `tests/` or verifier directory.
  - Harbor copies tests in at verification time; the error class `AddTestsDirError` implies a separate step. Check that nothing from the verifier directory is mounted during the agent phase.
  - Agent-written reproduction tests are fine. Running existing repo tests (PASS_TO_PASS-like) is legitimate, since a developer could do the same.
- **Make the threat symmetric across arms.** If one harness variant explores more (it runs `git log` more often, or has web access), leakage could masquerade as a harness effect. Strip history and block egress in **both** arms. As a cheap check, grep trajectories for `git log --all`, `git show <sha>`, `git fsck`, `curl`/`pip download` of the upstream repo, and URLs pointing at the upstream repo.
- **SWE-rebench task selection.** Choose tasks created after the model's release or knowledge cutoff, and use the leaderboard's contamination flag. Prefer tasks with high LLM-rated clarity and test correctness. Run Harbor's oracle (gold-patch) agent once on every selected task, which validates solvability per ABC T.7-T.9, and drop tasks whose oracle fails or flakes.
- **Flawed tests add noise equally to both arms** in a paired design, but they compress observable differences. A task whose tests are too narrow is "never solved" by either arm and contributes no information. That is another reason to pre-screen with a pilot.

### Gaps
- I did not verify whether the current Harbor `swe-rebench/swe-rebench-leaderboard` images strip future git history. Check by inspecting the task Dockerfiles or running `git log --all` inside a container.
- I did not verify whether Harbor exposes any test files to the agent during the agent phase.

## Q4. Outcome metrics beyond pass rate for comparing harnesses, and how others report them

### Takeaway
Harness effects often show up in cost, reliability and failure mode, not only in resolve rate. Report pass@1 (mean over K) with a CI, pass@k and pass^k, plus tokens or cost per task and per resolved task, wall-clock time, steps or turns, timeout and error rates, and leakage or compliance flags. Partial credit (the fraction of FAIL_TO_PASS tests passing) and regressions (broken PASS_TO_PASS tests) are useful secondary, continuous outcomes that raise statistical power.

### Cited findings
- **Terminal-Bench 2.0** reports per model and harness:
  - input and output tokens and cost (about USD 1-100 per full run)
  - durations (most trials under 20 minutes; some up to 2 hours, with hundreds of API calls and close to 100M tokens)
  - timeout rates and per-task token heatmaps
  - a trajectory-level failure taxonomy (Execution, Coherence and Verification classes, adapted from MAST, judged by a validated LLM)
  - command-level error rates (9.2%-26.7% across models; the top cause, at 24.1%, was calling an executable that is missing or not on PATH)
  - Turn count showed essentially no correlation with success, and more output tokens did not reliably predict success.
  - — [arXiv 2601.11868](https://arxiv.org/html/2601.11868v1)
- **Same-model harness gaps in Terminal-Bench 2.0:**
  - GPT-5.2: Codex CLI 62.9% vs. Terminus 2 54.0%
  - Opus 4.5: Terminus 2 57.8%, Claude Code 52.1%, OpenHands 51.9%
  - GPT-5: Codex CLI 49.6% down to Mini-SWE-Agent 33.9%
  - Gemini 2.5 Pro: Terminus 2 32.6% down to OpenHands 15.7%
  - Sonnet 4.5: 40-43% across four harnesses
  - The authors conclude that model choice usually matters more than scaffold.
  - — [same](https://arxiv.org/html/2601.11868v1)
- **DeltaSelect** treats cost as a first-class outcome. In its case study, a 58.1% cost reduction (USD 1.75 vs. 4.18 per evaluation) was statistically significant even though the score change was not. It also maps fractional verifier results to a common score, a partial-credit idea. — [arXiv 2609.19607](https://arxiv.org/abs/2609.19607v1)
- **Ariño de la Rubia & Pafka:**
  - report compliance beside quality, with rule violations counted separately
  - log cache share and compute use for every run
  - re-test when the model, agent version, endpoint, cache or pricing changes
  - a repeat-and-select policy (best of three) gained about 0.0081 AUC over a single attempt, so selection policies must be reported
  - — [arXiv 2609.33812](https://arxiv.org/html/2609.33812v1)
- SWE-Doctor reports Pass@5 and All@5, the latter being solved in all 5 runs, which is the same idea as pass^k. Source is a search snippet. — [arXiv 2607.00990](https://arxiv.org/pdf/2607.00990)
- Harbor's viewer shows reward, cost, tokens, timing and trajectory for every trial. — [Harbor docs: view job results](https://docs.harborframework.com/results/view-job-results.md)

### Inferences
- **Recommended per-run record** (one row per task × arm × repeat), with each field stating what to log:
  - `resolved` (0/1): the primary outcome
  - `f2p_passed/f2p_total`: partial credit
  - `p2p_broken`: regressions
  - `input_tokens`, `cached_input_tokens`, `output_tokens`, `cost_usd`
  - `wall_clock_s`
  - `n_steps` / `n_tool_calls`
  - `tool_error_rate`
  - `finish_reason`: one of self-finish, step limit, timeout or error
  - `premature_finish`: the agent declared done but the patch failed, especially when it had not run any test
  - `leak_flag`: from the trajectory grep
  - `harbor_exception_type`
- **Cost-effectiveness.** Report cost per resolved task with a bootstrap CI over tasks. Given the 97% caching, also report uncached-equivalent tokens, because cache hit rates may differ between harness variants (for example, if prompt layout changes).
- **Continuous secondary outcomes** such as tokens, time and partial credit usually have much better power than binary resolve at n=20-30. A harness effect on cost can therefore be established even when the effect on resolve rate cannot. Pre-declare which outcome is primary.
- **Premature-finish and tool-error rates** are mechanism metrics. They explain *why* a harness variant differs, which matters for a thesis even when pass-rate differences are not significant.

### Gaps
- I did not verify in this session the exact fields of SWE-bench- or Harbor-produced reports (per-test FAIL_TO_PASS and PASS_TO_PASS status in the verifier output). Check the SWE-rebench Harbor task's verifier output directly to see whether per-test results are recorded or only a binary reward.
- I found no standard published definition of "premature finish rate" for coding agents.

## Q5. Harbor and SWE-rebench: plugging in agents, recording results, known pitfalls

### Takeaway
Harbor is the official harness for Terminal-Bench 2.0. Custom agents subclass `BaseInstalledAgent` (preferred, runs inside the container) or `BaseAgent` (runs outside and acts through `environment.exec`), and are passed with `-a module.path:ClassName`. Repeats use `-k`. The biggest interpretation pitfall: Harbor **excludes** invalid trials (infrastructure, API, verifier and auth errors) from both the numerator and the denominator, while timeouts count as failures. Compare arms only on the same set of valid task × repeat cells.

### Cited findings
- Harbor describes itself as "a framework for running any agent with any model on any task in any sandbox in parallel." — [Harbor docs](https://docs.harborframework.com/)
- **CLI for `harbor run`:**
  - dataset or task: `-d "<org/dataset>@<ref>"`, `-t` for one task, `-p` for a local path
  - agent and model: `-a <agent>`, `-m <model>` (model names parsed by LiteLLM), `--effort`, `--ak` for agent kwargs
  - output: `-o <jobs-dir>`, `--job-name`
  - concurrency, attempts, retries: `-n` concurrency, `-k` attempts (`n_attempts`), `-r` retries (`retry.max_retries`)
  - task filters: `-l` max tasks, `-i` include glob, `-x` exclude
  - sandbox and resources: `-e` sandbox, `--override-memory-mb`
  - configs: `--print-config` shows the effective config, `harbor job init` saves one, and every run writes `config.json` into the job directory. CLI flags override config files.
  - — [Harbor docs: run a job](https://docs.harborframework.com/jobs/run-a-job.md)
- **Custom agents:**
  - `BaseInstalledAgent` needs `name()`, `async install(environment)` (using `exec_as_root` or `exec_as_agent`) and `@with_prompt_template async run(instruction, environment, context)`. Optional: `populate_context_post_run` to parse usage or trajectory after logs sync, and `configure()` for per-trial setup.
  - `BaseAgent` needs `name()`, `version()`, `async setup(environment)` and `async run(instruction, environment, context)`.
  - Custom agents are not registered by name, so pass the full import path. `install()` is skipped with `--ak skip_install=true`, so put per-trial work in `configure()`. Use `self.model_name` as given for LiteLLM. Check auth with `harbor agent auth module.path:MyAgent --json`.
  - — [Harbor docs: custom agents](https://docs.harborframework.com/agents/custom-agents.md)
- **Results viewer:** `harbor view jobs` serves on 127.0.0.1:8080-8089. Each trial has tabs for agent output, verifier output, artifacts, the resolved trial config and a replayable "trial lock". — [Harbor docs: view job results](https://docs.harborframework.com/results/view-job-results.md)
- **Error classification and scoring:** — [Harbor docs: error classification](https://docs.harborframework.com/jobs/error-classification.md)

  | Error class | Retried? | Effect on score |
  |---|---|---|
  | `AgentTimeoutError`, `NonZeroAgentExitCodeError`, context/output-length, safety refusal | No | Graded and **counted** (a valid 0) |
  | Verifier errors (`VerifierTimeoutError`, `RewardFileNotFoundError`, `RewardFileEmptyError`, parse errors) | No | Invalid, excluded |
  | Infrastructure (sandbox, environment start, healthcheck, memory, `AddTestsDirError`, `DownloadVerifierDirError`) | Yes | Not graded, excluded |
  | API/provider (rate limits, overloads, connection failures) | Yes | Graded but invalid, excluded |
  | Auth and configuration (authentication, model-not-found, permission, cancellation) | No | Excluded |
  | Unlisted exception types | Yes | Graded and counted as valid |

  - Invalid rewards "contribute to neither the numerator nor the denominator of the mean".
  - Default `retry.max_retries` is 0.
  - Pitfalls the docs list: denominator shrinkage, timeouts are not excluded, `reward_valid` is not task success, and a custom `override_error_policy` changes which trials count.
- **SWE-rebench on Harbor.** The Harbor hub lists `swe-rebench/swe-rebench-leaderboard`: about 860 Python tasks, monthly splits added over time, all with pre-built Docker images, run with `uvx harbor run -d "swe-rebench/swe-rebench-leaderboard"`. A Nebius-built adapter PR adds leaderboard, v1 and v2 variants with language, split and instance filters; its merge status upstream is unverified. A derived package, `swe-rebench-07-2026`, has 111 tasks across Go, Java, Python, Rust and TypeScript. Source: search-result summaries of hub pages. — [Harbor hub: swe-rebench-leaderboard](https://hub.harborframework.com/datasets/swe-rebench/swe-rebench-leaderboard/latest); [adapter PR mirror](https://github.forks.workers.dev/harbor-framework/harbor/pull/1347/files); [swe-rebench-07-2026](https://hub.harborframework.com/datasets/ibragim-badertdinov/swe-rebench-07-2026/latest?page=190); [Nebius blog](https://nebius.com/blog/posts/swe-rebench-dataset)
- The SWE-rebench leaderboard protocol is a fixed minimal ReAct scaffold, identical prompts, 128K context, 5 runs, mean ± SEM and pass@5. — [arXiv 2505.20411v2](https://arxiv.org/html/2505.20411v2)

### Inferences
- **Paired-completeness rule.** Because Harbor silently drops invalid trials, build the analysis table from the per-trial results, not from Harbor's aggregate mean. Keep only task × repeat cells that are valid in **both** arms, or re-run invalid cells with `-r` or a targeted `-t`. Report how many trials were excluded per arm.
- **Timeouts.** These are counted as failures, so differing step or time budgets between harness variants are part of the treatment. Hold timeouts and step limits identical across arms unless the limit is the variable under study.
- **Pin everything:** model ID and endpoint, reasoning effort, Harbor version, dataset `@ref`, agent version, and the resolved `config.json` per job. Ariño de la Rubia & Pafka recommend re-testing whenever any of these change.
- **Interleave arms in time** (alternate A and B per task, or run them concurrently) rather than running all of A and then all of B. This keeps provider-side drift, rate-limit throttling and cache state from confounding the harness effect. This is a design suggestion, not sourced.

### Gaps
- I could not read the Harbor docs that cover the job and trial directory layout, the `result.json` schema and the `AgentContext` token fields (for example, whether `n_input_tokens` and `n_cache_tokens` exist). Check the Harbor source (`AgentContext` model) or `harbor run --help`.
- Whether the SWE-rebench Harbor adapter's verifier records per-test FAIL_TO_PASS and PASS_TO_PASS results, needed for partial credit, is unverified.
- Timeout-multiplier and resume flags are not documented on the run-a-job page.
