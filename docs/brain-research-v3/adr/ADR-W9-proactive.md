# ADR-W9: Proactive attention and cooldown ownership

**Status:** implemented; architecture-only panel measured
**Scope:** V-4 / F-011, DR-016 through DR-019, DR-024 starvation guarantee

## Decisions

1. **Merge the proactive watermark monotonically.** `last_proactive_attempt`
   is a cooldown watermark, not an affect value. `apply_external_state` keeps
   the maximum of its local and incoming timestamps, even when the rest of a
   broadcast is stale. Redis keeps a separate atomic max watermark so a stale
   writer cannot erase the attempt before a restart; the brain also marks a
   subconscious-sourced turn when it accepts one. None of these updates idle
   time. Tests exercise both tick orders with two real `StateService`
   instances and the real brain tick handler, plus stale Redis persistence
   across restart.
2. **Score the generated thought once.** `SubconsciousEngine` asks its existing
   generation call for structured thought text, importance, category, and an
   optional stable goal id. The prompt receives bounded active goals,
   disclosed commitment details and deadlines, inferred goals, and unresolved
   thought history. Scores are finite and clamped to `[0, 1]`; absent or
   malformed scores use a deterministic estimate. General activity signals
   stay below the useful threshold; a goal or commitment due within 24 hours
   supplies the urgency boost. Without a model, the deterministic check-in
   selects that near-term context. Useful thoughts require `0.45`;
   self-directed thoughts require `0.75`. The bounded score and category
   travel on `chat.input` and `chat.output` for W5's interruption and
   grace-window decisions.
3. **Use bounded quiet-hour and resurfacing heuristics.** Before eight observed
   user turns, quiet hours default to 22:00–06:00. Thereafter the recent 168
   interaction-hour observations can override that default. User times are
   captured by the brain and broadcast; no lifesim rhythm is read by runtime
   code. A thought with no user outcome is reviewed after 24 hours. Each ignore
   reduces its re-raise probability by one third; the third ignore sets a real
   zero. Explicit lexical overlap marks an acted-on outcome, and clear
   dismissal wording marks dismissed. Neither outcome is resurfaced.
4. **Delete `BackgroundScheduler` and its pipeline hooks.** Inspection found no
   producer calling `enqueue`; the existing consolidation path is already
   dispatched as a retained background task by `SubconsciousAgent`, in a
   different service from `CognitivePipeline`. The scheduler therefore never
   controlled real work and could not enforce DR-024. Keep the established
   dispatch path and test a foreground BrainBench turn while that consolidation
   task is pending, with a measured 165 ms elapsed time against the 500 ms
   upper budget.

## Acceptance measurement

The fixed architecture-only dev panel completed 48/48 cells (12 personas,
seeds 1000–1011, 1-week and 1-month horizons, both broadcast tick orders)
with zero cell errors. Across both horizons and orders: cooldown violations
0; marked-attempt resets 0; median post-threshold initiations per idle hour
0; annoyance 0.088 per simulated day versus the V2 control's 21.8/day;
useful rate 45.7% versus V2's 0%; and night fraction 17.1% versus V2's 36.5%.
The one-week useful rate was 0%; the one-month rate was 50%. The scorer keeps
its existing 24-hour still-planned commitment definition.

Importance was non-degenerate (mean 0.305, range 0.20–0.90). All 70 observed
initiations were `useful_to_user`; none were `self_directed`. The useful
category's useful rate was 45.7%; the self-directed rate was undefined because
that category had no initiations. Quiet-hour initiations were 0/70. Re-raise
decay averaged 1.788 raises per ignored thought in the panel; the regression
test separately confirms that the third ignore sets probability to exactly
zero. The importance distribution and category split are architecture-only
results; the `llm_augmented` structured-output path still requires the
home-gpu reference run. For the fixed V2 gate, an empty set of initiations
maps to a 0 night share; the separate initiation count remains explicit and
the night-share upper band is still checked.

The completed 48-cell run predates the final tick-time refresh of disclosed
commitment deadlines. That refresh has a focused regression asserting that a
known plan advances from six hours to three hours remaining as simulated time
passes. The panel values above are preserved as measured; the reviewer command
should rerun the panel against the final tree.

## Limits

The heuristic can only recognize direct dismissal phrases and lexical overlap;
it does not claim semantic outcome detection. A locally available Ollama model
is required to measure the structured scoring distribution in `llm_augmented`;
architecture-only uses the deterministic score. Safety and boundary handling
remain on the foreground response path and are not gated by proactive scores.
