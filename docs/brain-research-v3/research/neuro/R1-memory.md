# R1 — Memory: literature and implementation audit

**Scope.** This report audits the default V2 `hybrid` ranker and the selectable legacy `actr_v1` path, including the Rust SQLite kernel and the PostgreSQL function. It also records which memory-science mechanisms have no matching implementation. No code was changed. I read the V2 BrainBench baseline and the memory section of the problem register: baseline C reports hit@5 **0.438**, MRR **0.362**, and no abstentions for one persona/seed/month; the register still identifies obsolete facts outranking updates (M-5) and stale surfaced-memory carry-over (M-10) as open/proposed problems. These are useful product-level priorities, not human-memory validation data.

## 1. Models

### 1.1 ACT-R declarative memory

ACT-R activation is not simply a recency score. With subsymbolic retrieval enabled, ACT-R 7 defines chunk activation (Reference Manual §Declarative Memory, pp. 261–262) as:

\[
A_i = B_i + S_i + P_i + \epsilon_i^{perm} + \epsilon_i^{trans}
\]

`B_i` is base-level activation; `S_i` is spreading activation; `P_i` is partial-match activation; `ε_i^{perm}` is optional permanent chunk noise and `ε_i^{trans}` is transient retrieval noise. The chunk with the greatest activation among chunks matching the request is retrieved only if it exceeds threshold `τ`; otherwise retrieval fails. Activation also determines latency.

With full reference history (`:ol nil`), base-level learning is:

\[
B_i = \ln\left(\sum_{j=1}^{n} t_j^{-d}\right) + \beta
\]

Here `n` is the number of presentations/references; `t_j` is elapsed time since presentation `j` (ACT-R time units, typically model seconds); `d` is the decay parameter set by `:bll`; and `β` is the global base-level constant `:blc` (default `0`). ACT-R counts initial addition to declarative memory and each successful retrieval of a chunk as references; scoring a candidate that is not successfully retrieved does not count. A later re-encoding/merge can also update its reference history. Thus an app’s `recall_count` should include successful memory retrievals when a memory is actually returned for use, while avoiding increments for candidates merely considered or surfaced without a successful retrieval (ACT-R 7 manual, §Declarative Memory, pp. 262–263).

With optimized learning (`:ol t`, the ACT-R 7 default), ACT-R uses:

\[
B_i = \ln\left(\frac{n}{1-d}\right) - d\ln L + \beta
\]

`L` is chunk lifetime since creation. This approximation assumes presentations are approximately uniformly distributed over that lifetime; it keeps a count and lifetime signal but loses the actual schedule, so two memories with the same `n` and `L` are indistinguishable even if one was massed and the other spaced. Numeric `:ol=k` keeps `k` true recent timestamps and approximates the rest. ACT-R 7 manual §Declarative Memory, pp. 262–263, 268–270, [official manual](https://act-r.psy.cmu.edu/actr7.x/reference-manual.pdf). Its `:bll` default is disabled (`nil`); when enabled, the manual strongly recommends `d=0.5`. Time must use one consistent model scale; `β` and activation are dimensionless in the log equations.

Activation-based retrieval gives (manual p. 266):

\[
RT_i = F e^{-fA_i}, \qquad RT_{fail}=F e^{-f\tau}
\]

`RT` is seconds; `F` is latency factor (default `1.0`); `f` is latency exponent (default `1.0`); `A_i` and `τ` are activation and retrieval threshold. `τ` defaults to `0`. Because ACT-R activation noise is logistic with scale `s` (`:ans` transient-noise scale default `nil`/off; suggested `0.2–0.8`), the threshold-crossing probability for a *single* candidate, derived from that noise distribution, is:

\[
P(A_i^*+\epsilon_i^{trans}>\tau\mid\epsilon_i^{perm})=\frac{1}{1+\exp((\tau-A_i^*)/s)},\qquad A_i^*=B_i+S_i+P_i+\epsilon_i^{perm}.
\]

This is not a softmax over a candidate list: competing chunks, match constraints, and the maximum-activation rule affect which candidate wins. The manual specifies the threshold/noise mechanism rather than presenting this one-candidate probability as a separate canonical retrieval equation.

Spreading activation in ACT-R 7 (p. 264) is:

\[
S_i=\sum_k\sum_j W_{kj}S_{ji},\qquad S_{ji}=S-\ln(fan_{ji})
\]

`k` ranges over active source buffers, `j` over chunks in source-buffer slots; `W_{kj}` divides source activation among those source chunks; `S` is maximum associative strength (`:mas`, default `nil`/off); `fan` is the number of links from source `j`, with the chunk/slot adjustment specified in the manual. If the source chunk is not identical to or in a slot of candidate `i`, default `S_{ji}=0`. This is buffer-conditioned symbolic association, not cosine similarity. With partial matching enabled (`:mp` numeric; default `nil`/off), `P_i=\sum_k P M_{ki}`: `P` is the mismatch scale and `M` the slot-value similarity. `:md` defaults to `−1` and `:ms` to `0`; default mismatches penalize rather than positively boost a candidate (manual pp. 264–265, 271). The ACT-R framework is described in Anderson & Lebiere’s 1998 book and Anderson et al.’s 2004 integrated theory; neither makes arbitrary vector retrieval plus lexical BM25 the ACT-R partial-matching mechanism.

### 1.2 Spacing-aware learning

Pavlik & Anderson’s extension changes the decay of each practice trace according to activation at the time of that practice. In their notation, the accumulated activation is a power-trace sum,

\[
m_n=\ln\left(\sum_{i=1}^{n} t_i^{-d_i}\right), \qquad d_j=c e^{m_{j-1}}+a
\]

where `t_i` is elapsed time since practice `i`, `m_{j-1}` is activation just before practice `j`, `a` is the decay intercept, and `c` controls activation-dependent decay. Larger activation at a practice makes that practice’s added trace decay faster; a lower-activation, more difficult spaced practice produces a more durable increment. `a` and `c` are dimensionless if time is expressed in the model’s chosen units; they are fitted jointly with other task parameters, not universal constants. Across the published P&A model fits summarized in later model applications, the reported intercepts span approximately `a=.058–.300` and scales `c=.143–.495`; these are task fits, not an ACT-R default. The original paper presents the decay-rate account in §4.1, pp. 570–571. The experiment used Japanese–English paired associates. Spacing benefits increased with repetitions and with longer retention intervals. [Pavlik & Anderson 2005, *Cognitive Science* 29, 559–586, DOI](https://doi.org/10.1207/s15516709cog0000_14); [Pavlik & Anderson 2008, *Journal of Experimental Psychology: Applied* 14, 101–117, DOI](https://doi.org/10.1037/1076-898X.14.2.101).

The human spacing effect is established; one universal “optimal gap” is not. Cepeda et al.’s meta-analysis included **839 assessments from 317 experiments in 184 papers**. Across 271 massed-versus-spaced comparisons, mean final-test accuracy was **36.7% massed vs 47.3% spaced** (10.6 percentage-point advantage; study conditions varied). The optimal interstudy interval generally increased with the retention interval, while some very short retention intervals showed little or even reversed benefit. The authors also document sparse sampling of long retention intervals, so the average is not a companion-memory effect-size target without matching the task. [Cepeda et al. 2006, *Psychological Bulletin* 132, 354–380, DOI](https://doi.org/10.1037/0033-2909.132.3.354).

Cepeda et al. (2008) directly mapped gap by final-test delay: over **1,350 learners**, facts were reviewed after a gap as long as 3.5 months and tested after a further delay up to one year. At any fixed test delay, retention first rose then fell as the study gap lengthened. The maximizing gap lengthened with test delay, but the optimal gap/test-delay ratio declined from roughly **20–40% for a one-week delay** to **5–10% for a one-year delay**. This is a practical schedule result, not an invariant property of all autobiographical memories. [Cepeda et al. 2008, *Psychological Science* 19, 1095–1102, DOI](https://doi.org/10.1111/j.1467-9280.2008.02209.x).

### 1.3 Forgetting functions

Ebbinghaus’ original result is a savings/relearning curve from one trained observer learning nonsense syllables. His 1885 book reports retention as a function of delay and repetition; it does not license the popular universal claim that everyone forgets a fixed percentage after 24 hours. “Savings” means less relearning time than initial learning, not direct probability of spontaneous recall. The historical source is *Über das Gedächtnis* (1885); the full English translation is Ruger & Bussenius (1913), *Memory: A Contribution to Experimental Psychology*, Teachers College, Columbia University, Chapter 7, [York University facsimile/transcription](https://psychclassics.yorku.ca/Ebbinghaus/index.htm).

Wixted & Ebbesen fitted a power form, commonly written `y=a t^{−b}` (for positive retention/time values), against linear, exponential, exponential-power, hyperbolic, and logarithmic competitors. Their word-recall and face-recognition human experiments and pigeon delayed-matching experiment favored the power function; their reanalysis of Ebbinghaus savings also favored power. `a` is the fitted performance scale and `b` the fitted decay exponent; `a` follows the outcome scale (and the chosen time unit), while `b` is dimensionless. There is no universal fit. For scale only, Wixted & Ebbesen’s follow-up individual fits to word recall reported `a=.53–1.00` and `b=.03–.30` across eight subjects and short/long learning conditions; `a` estimates performance at a one-second interval because `t` was in seconds. These are task-specific observed ranges, not defaults. [Wixted & Ebbesen 1991, *Psychological Science* 2, 409–415, DOI](https://doi.org/10.1111/j.1467-9280.1991.tb00175.x); [Wixted & Ebbesen 1997, *Memory & Cognition* 25, 731–739, DOI](https://doi.org/10.3758/BF03211316).

That conclusion is contested at the level of the best universal functional form. Rubin & Wenzel assembled **210** published retention datasets, required at least five points and a correlation of `.90` to at least one candidate, then fit **105 two-parameter functions**. Their best-supported families were logarithmic, power, exponential in `√t`, and hyperbolic in `√t`; available data did not reliably separate those four. Examples include `y=a−b ln(t)`, `y=a exp(−b√t)`, `y=1/(a+b√t)`, and `y=a t^{−b}`; parameters are dataset fits with scale-dependent units, not universal rates. They also flag autobiographical retention as differing from the bulk of the corpus. A later individual-subject analysis by Wixted & Ebbesen (1997) argued power-like forgetting remains when individual curves are fit, addressing the alternative that aggregation of exponential individual curves creates a power group curve. For a companion, fit and validate on the intended material and delay distribution rather than hard-coding “the human forgetting curve.” [Rubin & Wenzel 1996, *Psychological Review* 103, 734–760, DOI](https://doi.org/10.1037/0033-295X.103.4.734); [Wixted & Ebbesen 1997, *Memory & Cognition* 25, 731–739, DOI](https://doi.org/10.3758/BF03211316).

### 1.4 Serial position, interference, and retrieval-induced forgetting

Murdock’s immediate free-recall curve is U-shaped: a steep (possibly exponential) primacy rise over the first **3–4** list positions, a relatively flat middle, and an S-shaped recency rise over approximately the last **8** positions. The study varied list length (10–40 words) and presentation rate (1 or 2 seconds/item); position effects therefore belong to an ordered-list retrieval design, not a general ranking rule for a database. Murdock discusses within-list proactive/retroactive inhibition as possible contributors. [Murdock 1962, “The serial position effect of free recall,” *Journal of Experimental Psychology* 64, 482–488, DOI](https://doi.org/10.1037/h0045106).

Retrieval practice can temporarily impair access to related, unpracticed items when final retrieval uses the same category cue. In Anderson, Bjork & Bjork’s first experiment, after three retrieval-practice trials and a 20-minute delay, practiced items (Rp+) were recalled at **73.6%**, related unpracticed items (Rp−) at **37.5%**, and unrelated baseline items (Nrp) at **48.4%**; Rp− was about **10.9 points below** baseline. This is a cue- and procedure-specific retrieval-induced-forgetting effect. Inhibition is not the only account: strength-dependent competition/output interference and cue dependence complicate the interpretation; evaluate same-cue and independent-cue probes separately. [Anderson, Bjork & Bjork 1994, *Journal of Experimental Psychology: Learning, Memory, and Cognition* 20, 1063–1087, DOI](https://doi.org/10.1037/0278-7393.20.5.1063).

### 1.5 Complementary learning systems, replay, and abstraction

CLS is a computational account rather than a single scalar forgetting equation. The hippocampal system rapidly records sparse, pattern-separated episodes; neocortical learning is slower and distributed, allowing repeated/interleaved reinstatement to integrate regularities without catastrophic interference. The 1995 connectionist account proposes that hippocampal reinstatement incrementally trains cortical structure; Kumaran, Hassabis & McClelland (2016) updates the theory: replay can prioritize task/reward-relevant experience, support planning, and sometimes generalize from a single episode. The systems-level prediction is a useful distinction: preserve specific episodic details while gradually extracting stable shared structure; do not replace a lived episode immediately with a lossy summary. [McClelland, McNaughton & O’Reilly 1995, *Psychological Review* 102, 419–457, DOI](https://doi.org/10.1037/0033-295X.102.3.419); [Kumaran, Hassabis & McClelland 2016, *Trends in Cognitive Sciences* 20, 512–534, DOI](https://doi.org/10.1016/j.tics.2016.05.004).

Sleep is one replay/consolidation condition, not an app timer that can be equated to biological sleep. Diekelmann & Born review evidence for coordinated hippocampal reactivation during slow-wave sleep (slow oscillations, spindles, ripples), with preferential consolidation of explicitly encoded and behaviorally relevant material; REM’s role in stabilizing transformed representations is presented as a proposed complementary role. The review does not give a single human pass-band or claim all memories benefit equally. [Diekelmann & Born 2010, *Nature Reviews Neuroscience* 11, 114–126, DOI](https://doi.org/10.1038/nrn2762).

### 1.6 Reconsolidation and prediction error

Reconsolidation findings constrain updating; they do not say every retrieval rewrites a trace. Nader, Schafe & LeDoux found in rats that post-reactivation amygdala protein-synthesis inhibition disrupted later auditory fear memory, while no reactivation or infusion six hours later did not produce that amnesia. The inference is a time-limited, retrieval-triggered lability condition in this fear-conditioning preparation. [Nader, Schafe & LeDoux 2000, *Nature* 406, 722–726, DOI](https://doi.org/10.1038/35021052).

Sevenster, Beckers & Kindt’s human differential-fear-conditioning experiments found pharmacologically induced amnesia depended on reminder prediction error; a reminder matching the learned contingency did not yield the same effect. In the primary study, there were three small groups (15 per group); it supports a specific boundary condition, not a general autobiographical-memory rewrite rule. [Sevenster, Beckers & Kindt 2013, *Science* 339, 830–833, DOI](https://doi.org/10.1126/science.1231357).

A competing account is latent-cause inference: when outcomes change, the learner can assign observations to a new hidden context/cause and retain the old association instead of overwriting it. Gershman, Blei & Niv’s Chinese-restaurant-process prior is `P(c_t=k|past)=N_k/(t−1+α)` for existing cause `k`, and `P(c_t=new|past)=α/(t−1+α)`. `N_k` is previous assignments to cause `k`; `t` is observation index; `α>0` is a concentration parameter controlling new-cause tendency. No universal fitted `α` is prescribed. The model predicts context-dependent renewal after extinction, whereas strong overwrite/reconsolidation predicts more direct replacement. These are competing explanations of conditioning data and should not be treated as established implementation guidance for autobiographical dialogue. [Gershman, Blei & Niv 2010, *Psychological Review* 117, 197–209, DOI](https://doi.org/10.1037/a0017808).

### 1.7 Emotional memory

Emotional arousal can selectively improve later memory for central/details of an event; amygdala-mediated modulation of consolidation is a prominent mechanistic account, not a mechanism measured by every behavioral study. This does not support a query-independent bonus on every distressed memory or exact valence matching. In Cahill & McGaugh’s story-slide experiments, matched emotional versus neutral narration was assessed after two weeks; enhancement was selective to the emotionally arousing narrative phase. In a post-learning stress study, 59 participants viewed 21 IAPS slides; a cold-pressor stressor after encoding enhanced one-week memory for emotionally arousing slides relative to control, while relatively neutral slides were not similarly enhanced. The result is selective and task-specific; it does not provide a portable numerical effect size in the accessible report. Sleep-related emotional selectivity is heterogeneous across conditions. A useful validation target is therefore *selective delayed enhancement of arousing event details while neutral details remain approximately unchanged*, with arousal at encoding and delay explicitly controlled, rather than a fixed global emotion coefficient. [Cahill & McGaugh 1995, *Consciousness and Cognition* 4, 410–421, DOI](https://doi.org/10.1006/ccog.1995.1048); [Cahill, Gorski & Le 2003, *Learning & Memory* 10, 270–274, DOI](https://doi.org/10.1101/lm.62403); [Diekelmann & Born 2010, DOI](https://doi.org/10.1038/nrn2762).

## 2. Human data for validation

These bands are first-pass targets for matched benchmark paradigms, not universal norms for personal-memory retrieval. Keep each dataset’s encoding, delay, cue, and outcome measure intact.

| Mechanism | Reproducible design and quantitative target | Candidate benchmark metric |
|---|---|---|
| Spacing | Equal total study time; compare massed vs spaced verbal recall. Cepeda et al. 2006 aggregate: 36.7% vs 47.3% correct (spaced +10.6 pp; 271 comparisons). Match retention delay; the meta-analysis found longer optimal ISI as RI increased. | Difference in delayed-cued-recall accuracy; require positive spacing effect except tiny RI designs. Estimate effect by RI stratum. |
| Optimal gap | Cepeda et al. 2008 factual learning; final RI up to a year. Optimal gap/RI about 20–40% at 1-week RI and 5–10% at 1-year RI. | Estimated argmax of gap sweep; test direction of optimal-gap increase and ratio decrease, not a single schedule across RIs. |
| Forgetting shape | Same item set tested at multiple delays. Compare power, exponential, log, exponential-root, hyperbolic-root out-of-sample by material and test type. Power beat five competitors in Wixted & Ebbesen’s tasks; Rubin & Wenzel found four families difficult to separate across 210 heterogeneous curves. | Held-out error/model weight by material and cue; report per-condition `a,b` or corresponding fitted parameters with time unit. Avoid a universal half-life. |
| Serial position | Murdock-style 10–40-word lists, 1- or 2-s presentation/item, immediate 90-s free recall. First 3–4 and last ~8 positions rise above mid-list. | Recall probability by serial position; primacy and recency contrasts vs middle; vary list length/rate. |
| Retrieval-induced forgetting | Category-exemplar study, retrieve selected exemplars three times, distractor, final category-cued test after 20 min. Rp+ 73.6%; Rp− 37.5%; Nrp 48.4%. | Rp− minus Nrp about −10.9 pp under same category cue; separate independent-cue condition to test cue dependence. |
| Emotional retention | Emotional/neutral story-slide versions; arousal manipulation at encoding; 2-week retention. Expect selective advantage in the arousing narrative phase, not a uniform gain across all slides. | Emotional-minus-neutral recall by event phase and delay; interaction, not main effect alone. Include neutral/arousing ratings. |
| Reconsolidation boundary | Fear conditioning followed next day by reminder with contingency-consistent vs surprising outcome; manipulate post-reminder intervention and include no-reminder control. Sevenster et al.: n=15/group, task-specific pharmacological effect conditional on prediction error. | Interaction between PE condition and post-reminder intervention; do not generalize beyond fear-learning task. |
| Consolidation/replay | Compare waking retention with post-learning sleep/replay or targeted reactivation, preserving matched study and test intervals. CLS predicts episodic retention plus gradual abstraction/interleaving. | Separate episode-detail recall from cross-episode inference; report sleep stage/replay manipulation, not a generic “consolidation score.” No universal numeric band from the cited theory. |

For current BrainBench, the **0.438 hit@5 / 0.362 MRR** baseline is an internal system baseline. It cannot be compared directly to human recall probabilities until probe construction, cueing, delay, and scoring are aligned.

## 3. Our code vs the literature

### 3.1 Default hybrid ranker

`backend/app/state/memory_ranking.py:61–65,140–146,149–186` defines the live default score:

\[
score=z(cosine)+1.5\frac{BM25}{\max(BM25)}+0.2z\bigl(B+1.5\,importance\bigr)
\]

with coded history term

\[
B_{code}=\ln(\max(1,n))-d\ln(h_{last}+1),
\]

where `n=max(1, recall_count)`, `h_last` is hours since `last_recalled_at` (floored at .001), and default `d=.5`. The pool has 60 vector-ranked candidates (`config.py:323–335`, consumed at `memory_store.py:4040–4065`); archive candidates can be added afterwards. Similarity z-scores and the activation z-score are pool-relative. BM25 uses `k1=1.2`, `b=.75`; `W_LEX=1.5`, `W_ACT=.2`, `W_IMP=1.5`.

**Verdict: defect in the ACT-R label, otherwise an intentional relevance-first hybrid.** The function docstring calls this “optimized-learning form”; it is not ACT-R’s optimized formula. It substitutes time since last use for `L` (lifetime since creation), omits `1/(1−d)`, omits `β`, and cannot use the trace timestamps that define the full equation. It does express two coarse signals—logged count and latest-use recency—but it cannot express the relative schedule of references. In particular, two candidates with equal count and the same last-use time get identical ACT-R-history values regardless of earlier massed/spaced practice. The .2 z-weight is an engineering prior, not an ACT-R parameter.

**Faithful correction:** persist reference/presentation timestamps and compute `B=ln(Σ_j (now−t_j)^(−d))+β`. If history storage must be compact, implement actual optimized ACT-R `ln(n/(1−d))−d ln(L)+β` and document that it cannot discriminate spacing schedules; do not add an invented spacing bonus and call it that equation. For Pavlik–Anderson spacing, persist practice activation/history and fit the separate `d_j=c e^{m_{j−1}}+a` mechanism. In the hybrid ranking, use the selected base-level as a history term and retain vector/BM25 as complementary retrieval evidence. A complete ACT-R retrieval probability/latency model would additionally need an ACT-R-like threshold, match constraints, noise, and latency calibration; the ranker does not currently model those.

**Expected ranking change:** repeated traces influence score through each past presentation’s decaying contribution, not only count and the most recent timestamp. Frequently rehearsed but currently old facts can remain accessible when the trace sum warrants it; recent uses remain strongest; schedule differences become visible only with full timestamps or a spacing-aware learning rule. Candidate omissions remain possible because the 60-item cosine pool gates later ranking.

### 3.2 Legacy `actr_v1` path: Python, SQL, Rust

The legacy scoring formula in `memory_store.py:882–916,2002–2053,2338–2391` and SQL `backend/db/schema.sql:216–250` is approximately:

\[
score=\ln n-d\ln(h_{last}+1)+1.5I+0.15(1-dist_{emo})+0.15\ln(\bar g+1)
+w_{spread}\,cosine\,(1+0.1v e-0.2ar\,cort)-0.5dist_{emo}
\]

where `I` is importance; `dist_emo=sqrt((v-v_q)^2+(e-ar_q)^2)`; `\bar g=(last_recall-created)/n` in hours; `v/e` are stored valence/emotional weight, `ar/cort` current arousal/cortisol, and default `d=.5`, `w_spread=1`, importance weight `1.5`, spacing coefficient `.15`, emotional proximity `.15`, and distance penalty `.5` (`config.py:319–321`; constants in `memory_store.py`). It is not the ACT-R activation equation: importance and mood distance are app offsets, and “spread” is query-vector similarity times a neurochemical gain, not the source-buffer associative formula.

* `memory_store.py:882–942` — **deliberate approximation** for the legacy path: `ln(n)−d ln(hours_since_last+1)` plus `0.15 ln((last−created)/n+1)`. The docstring correctly admits no per-reference timestamps and calls this an approximation. But dividing a two-timestamp span by count invents a mean interval and does not distinguish the many histories with the same endpoints. It is neither the ACT-R trace sum nor Pavlik–Anderson’s activation-dependent trace decay. Keep only as a declared app heuristic or replace with timestamp-based learning.
* `memory_store.py:3016–3071` — archive candidate scoring uses the same `_base_activation`, emotional-distance, and vector-gain terms as active legacy ranking. `memory_store.py:3331–3348` then rescales a promoted archive item with the incremented count and near-zero recency, plus direct-cue boost. **Verdict: same legacy heuristic, not ACT-R retrieval**; archived rows are not a distinct faithful model.
* `memory_store.py:2002–2053` (Qdrant) and `2338–2391` (row fallback) — **label-only for ACT-R spreading/partial matching**: both apply vector similarity, emotional gain, and emotional-distance subtraction; neither uses ACT-R buffer sources, slot fan, or slot-value mismatch. The vector cue is a sensible semantic-retrieval component in this product, but should be labeled cosine/semantic similarity rather than ACT-R spreading activation. Correct ACT-R comparison is `S_i=Σ_kΣ_j W_kjS_ji` if the system adopts symbolic buffer/association state; otherwise document cosine as the deliberate replacement. A true partial-match model would require structured slots and explicit similarity penalties, not just BM25.
* `backend/db/schema.sql:216–250` — duplicates the same legacy score, including the same timestamp-span bonus, for PostgreSQL. **Verdict: shared approximation**; SQL parity is good, scientific equivalence is not. The argument `p_emotion_weight` is present in the signature but the displayed score expression uses hardcoded `0.1` for the valence/emotion gain, so the named setting does not parameterize that term.
* `backend/crates/cognitive-rust/src/lib.rs:386–403,549–570` — Rust duplicates legacy SQLite scoring and the `.15 ln(span/n+1)` bonus. **Verdict: intentional implementation duplicate of the legacy approximation**, not ACT-R proper. Rust, Python and SQL should either retain and consistently name this as heuristic, or all consume the same persisted per-reference history and exact score definition.

### 3.3 Use counts, decay pruning, emotional updates

* `memory_store.py:835–880` — identical repeated writes call `_reinforce_memory`, increment `recall_count`, stamp `last_recalled_at`, and keep maximum importance. `memory_store.py:4443–4485` increments the same counter for returned search results when `refresh_on_recall=True`; cache hits can refresh too (`3688–3697`). **Verdict: plausible ACT-R reference bookkeeping only when the returned result represents a successful retrieval; too broad if candidates are surfaced without being retrieved for use.** Successful retrieval itself counts in ACT-R and does not require a separate re-encoding. The V2 config and ranker also treat missing/zero count as at least one, while the schema’s persisted count defaults to zero. Correct by recording creation as the initial presentation and each successful retrieval/re-encoding with timestamps; separately track candidate exposure if the product wants it, so exposure alone does not masquerade as a successful retrieval.
* `memory_store.py:4832–4885` computes `ln(n)−d ln(hours_since_creation+1)`, then archives below custom `−3.5/−4.5` thresholds and multiplies importance by `.8`. It uses creation age (not latest-use recency or full trace history) for the prune decision. **Verdict: deliberate lifecycle/pruning heuristic, not ACT-R decay.** ACT-R activation predicts relative access/latency; it does not specify these category thresholds or destructive archive behavior. Keep if useful operationally, but call it an inactivity-based archive policy and measure reversibility/recall consequences.
* `memory_store.py:4478–4485` also decays emotional weight by `.95` and importance by `.98` when negative-valence memories are retrieved in nonnegative context. **Verdict: label-only for reconsolidation/extinction.** This is a fixed retrieval-conditioned scalar attenuation; it lacks surprise/PE gating, a reconsolidation window, control condition, or competing latent-cause trace. It should not be described as human extinction or reconsolidation.

### 3.4 Consolidation, replay, and mechanisms not found

* `subconscious_agent.py:461–482,484–567` waits for five minutes of silence, takes up to ten unconsolidated episodes, sends paired messages to a reflection/fact-extraction task, marks episodes consolidated, and runs the archive heuristic. **Verdict: deliberate product simplification / CLS-inspired label, not CLS or sleep replay.** It has episodic inputs and creates extracted knowledge, but code does not implement a distinct fast hippocampal store plus slow distributed cortical learner, interleaved replay, or sleep-stage control. The 300-second inactivity threshold is not a human sleep/consolidation time constant. Retain original episodes alongside extracted facts and assess abstraction precision before deleting or weakening source traces.
* Search across `backend/app`, `backend/crates/cognitive-rust/src/lib.rs`, BrainBench, and lifesim found **no memory-level prediction-error-triggered reconsolidation, latent-cause assignment, serial-position model, or retrieval-induced-forgetting mechanism**. `cognitive/reappraisal.py` and pipeline prediction errors are affect/learning signals, not memory-trace update code. Lifelike “interference” probes/gates measure retrieval failures but do not implement a human interference model.
* There is **no sleep replay mechanism** in this track’s runtime code. `lexicon_store.py` comments describe Hebbian/ACT-R spreading principles, and V1 applies PPR to graph relations in `memory_store.py`; neither is ACT-R source-buffer spreading. The active hybrid path deliberately excludes that PPR feature as unmeasured, per `memory_ranking.py:22–36`.

### 3.5 Verdict summary by named mechanism

| Implemented name/location | What code actually does | Verdict |
|---|---|---|
| `memory_ranking.base_level` | `ln(count)−d ln(hours since last use+1)` | **Defect**: incorrectly called ACT-R optimized-learning form. |
| Legacy `_base_activation` / SQL / Rust | Last-use/count heuristic + app importance/emotion/vector terms + mean-span bonus | **Deliberate simplification** for legacy score; ACT-R “spacing/spreading” names overstate fidelity. |
| Search refresh count | Increments count for returned memory results, optionally | **Potential ACT-R analogue** when it means successful retrieval; overcounts if it includes only surfaced candidates. |
| `_compute_actr_decay` | Creation-age/count threshold for archive/prune and importance scale | **Label only** as ACT-R forgetting; it is a lifecycle policy. |
| Subconscious “consolidation” | Quiet-time batch reflection/fact extraction | **CLS-inspired simplification**, not sleep/replay/system consolidation. |
| Reconsolidation / latent causes / serial position / RIF | No memory implementation found | **Absent**; no equation should be inferred from names elsewhere. |

## 4. Recommended corrections, ranked by companion benefit

1. **Make new/updated facts outrank obsolete facts with temporal validity and explicit contradiction handling.** This is the highest direct product value and matches open M-5. Preserve versioned traces and apply `valid_from`, `valid_until`, and `contradicts_id` at retrieval. Metric: obsolete-wins rate (register currently reports 0.64/0.67 in older evals), temporal-question hit@k, and current-fact MRR. ACT-R fidelity alone does not solve supersession.
2. **Correct reference bookkeeping and use real trace timestamps.** Separate creation, successful retrieval, candidate exposure without retrieval, and summary/consolidation events; ACT-R counts successful retrieval without requiring later re-encoding. Persist reference times and compute full ACT-R BLL; use Pavlik–Anderson only as a separately fitted optional learning rule. Metric: matched human spacing/retention curves plus BrainBench hit@5/MRR split by count, age, and spacing schedule; add score-term observability. Expected benefit is less popularity feedback and more credible ranking of rehearsed/old facts.
3. **Stop calling creation-age archive pruning “ACT-R decay”; calibrate archive policy against recoverability.** Keep source episodes retrievable until extracted facts are validated. Metric: archive recall success after 1m/6m/1y, forgotten-obsolete rate, and storage/latency cost. This avoids silent loss of personally meaningful details while preserving bounded active storage.
4. **Add update uncertainty / PE only as a controlled memory-write policy.** On surprising contradiction, either keep competing versions and contextual evidence or create a new event; do not mutate old memory on every retrieval. Use latent causes as a candidate for context-sensitive conflicting beliefs, but first validate on human update/renewal data. Metric: false-overwrite and stale-fact rates by reminder PE condition; preserve original context.
5. **Add episodic-vs-semantic validation and replay only where it improves useful recall.** Preserve exact events and separately score extracted abstractions; do not simulate “sleep” with a wall-clock silence timer. Metric: episode-detail recall, cross-episode inference accuracy, and unsupported-generalization rate after consolidation.
6. **Defer serial-position and RIF mechanisms in production retrieval.** They are reliable laboratory phenomena but poorly matched to asynchronous conversational queries. Add them to mechanistic benchmark fixtures (ordered list and category retrieval) rather than adding ranker bonuses. Metric: the task-specific curves above; no expected lift to conversational hit@k without an independently demonstrated mapping.

## 5. Citation verification

The draft list was checked against the primary/publisher, PubMed, or author-hosted records linked here. All eleven are verified; item 3 is specifically a book, not a journal paper.

1. **Anderson & Lebiere (1998), VERIFIED.** *The Atomic Components of Thought*, Lawrence Erlbaum Associates. Correct authors/title/year; [publisher record](https://www.routledge.com/The-Atomic-Components-of-Thought/Anderson-Lebiere/p/book/9780805828177). Supports ACT-R architecture/component account; use the current ACT-R 7 manual for current declarative equations/defaults.
2. **Pavlik & Anderson (2005), VERIFIED.** “Practice and Forgetting Effects on Vocabulary Memory: An Activation-Based Model of the Spacing Effect,” *Cognitive Science* 29, 559–586. [DOI 10.1207/s15516709cog0000_14](https://doi.org/10.1207/s15516709cog0000_14). It reports Japanese–English paired-associate spacing/retention data and activation-dependent decay.
3. **Cepeda, Pashler, Vul, Wixted & Rohrer (2006), VERIFIED.** “Distributed Practice in Verbal Recall Tasks: A Review and Quantitative Synthesis,” *Psychological Bulletin* 132(3), 354–380. [DOI 10.1037/0033-2909.132.3.354](https://doi.org/10.1037/0033-2909.132.3.354). It is a review/meta-analysis, not one experiment.
4. **Wixted & Ebbesen (1991), VERIFIED.** “On the Form of Forgetting,” *Psychological Science* 2(6), 409–415. [DOI 10.1111/j.1467-9280.1991.tb00175.x](https://doi.org/10.1111/j.1467-9280.1991.tb00175.x). Correctly supports their power-function comparison, but not a universal settled functional form.
5. **Ebbinghaus (1885), VERIFIED.** *Über das Gedächtnis: Untersuchungen zur experimentellen Psychologie* (1885). The cited English title is Ruger & Bussenius’ 1913 translation, *Memory: A Contribution to Experimental Psychology*, Teachers College, Columbia University. [Original/translation record](https://psychclassics.yorku.ca/Ebbinghaus/index.htm). Use Chapter 7 for retention/obliviscence and describe the outcome as savings/relearning.
6. **Murdock (1962), VERIFIED.** “The Serial Position Effect of Free Recall,” *Journal of Experimental Psychology* 64(5), 482–488. [DOI 10.1037/h0045106](https://doi.org/10.1037/h0045106). Supports primacy, recency, and mid-list asymptote under free recall.
7. **McClelland, McNaughton & O’Reilly (1995), VERIFIED.** “Why There Are Complementary Learning Systems in the Hippocampus and Neocortex: Insights From the Successes and Failures of Connectionist Models of Learning and Memory,” *Psychological Review* 102(3), 419–457. [DOI 10.1037/0033-295X.102.3.419](https://doi.org/10.1037/0033-295X.102.3.419). Correctly supports CLS rationale and computational arguments.
8. **Kumaran, Hassabis & McClelland (2016), VERIFIED.** “What Learning Systems Do Intelligent Agents Need? Complementary Learning Systems Theory Updated,” *Trends in Cognitive Sciences* 20, 512–534. [DOI 10.1016/j.tics.2016.05.004](https://doi.org/10.1016/j.tics.2016.05.004). Supports the updated replay/dual-learning-system discussion.
9. **Diekelmann & Born (2010), VERIFIED.** “The Memory Function of Sleep,” *Nature Reviews Neuroscience* 11, 114–126. [DOI 10.1038/nrn2762](https://doi.org/10.1038/nrn2762). Supports sleep-stage/replay review claims; REM’s complementary role is framed as a hypothesis in the review.
10. **Nader, Schafe & LeDoux (2000), VERIFIED.** “Fear Memories Require Protein Synthesis in the Amygdala for Reconsolidation After Retrieval,” *Nature* 406(6797), 722–726. [DOI 10.1038/35021052](https://doi.org/10.1038/35021052). Supports a rat fear-conditioning reactivation result, not general memory rewrite.
11. **Sevenster, Beckers & Kindt (2013), VERIFIED.** “Prediction Error Governs Pharmacologically Induced Amnesia for Learned Fear,” *Science* 339(6121), 830–833. [DOI 10.1126/science.1231357](https://doi.org/10.1126/science.1231357). Correctly supports prediction-error gating in human associative fear memory; retain the task-specific boundary.

### Additional sources cited above

* ACT-R Research Group. *ACT-R 7.30+ Reference Manual*, §§Declarative Memory, pp. 261–274, [official PDF](https://act-r.psy.cmu.edu/actr7.x/reference-manual.pdf).
* Anderson et al. (2004). “An Integrated Theory of the Mind,” *Psychological Review* 111(4), 1036–1060. [DOI 10.1037/0033-295X.111.4.1036](https://doi.org/10.1037/0033-295X.111.4.1036).
* Cepeda et al. (2008). “Spacing Effects in Learning: A Temporal Ridgeline of Optimal Retention,” *Psychological Science* 19(11), 1095–1102. [DOI 10.1111/j.1467-9280.2008.02209.x](https://doi.org/10.1111/j.1467-9280.2008.02209.x).
* Pavlik & Anderson (2008). “Using a Model to Compute the Optimal Schedule of Practice,” *Journal of Experimental Psychology: Applied* 14(2), 101–117. [DOI 10.1037/1076-898X.14.2.101](https://doi.org/10.1037/1076-898X.14.2.101).
* Cahill & McGaugh (1995). “A Novel Demonstration of Enhanced Memory Associated with Emotional Arousal,” *Consciousness and Cognition* 4(4), 410–421. [DOI 10.1006/ccog.1995.1048](https://doi.org/10.1006/ccog.1995.1048).
* Cahill, Gorski & Le (2003). “Enhanced Human Memory Consolidation with Post-Learning Stress: Interaction with Degree of Arousal at Encoding,” *Learning & Memory* 10, 270–274. [DOI 10.1101/lm.62403](https://doi.org/10.1101/lm.62403).
* Anderson, Bjork & Bjork (1994). “Remembering Can Cause Forgetting: Retrieval Dynamics in Long-Term Memory,” *Journal of Experimental Psychology: Learning, Memory, and Cognition* 20(5), 1063–1087. [DOI 10.1037/0278-7393.20.5.1063](https://doi.org/10.1037/0278-7393.20.5.1063).
* Gershman, Blei & Niv (2010). “Context, Learning, and Extinction,” *Psychological Review* 117(1), 197–209. [DOI 10.1037/a0017808](https://doi.org/10.1037/a0017808).
* Rubin & Wenzel (1996). “One Hundred Years of Forgetting: A Quantitative Description of Retention,” *Psychological Review* 103(4), 734–760. [DOI 10.1037/0033-295X.103.4.734](https://doi.org/10.1037/0033-295X.103.4.734).
* Wixted & Ebbesen (1997). “Genuine Power Curves in Forgetting: A Quantitative Analysis of Individual Subject Forgetting Functions,” *Memory & Cognition* 25(5), 731–739. [DOI 10.3758/BF03211316](https://doi.org/10.3758/BF03211316).

**Verification boundary.** Primary/publisher records and author-hosted PDFs/pages above were opened for the cited claims. Human findings are mostly verbal-list, paired-associate, story-memory, or fear-conditioning tasks; they do not validate companion autobiographical-memory performance without a matched human study. No code, BrainBench run, heavy validation, or test suite was executed, per the task constraints.
