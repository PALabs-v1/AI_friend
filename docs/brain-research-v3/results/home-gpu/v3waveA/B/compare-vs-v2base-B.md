# BrainBench comparison: v2base-B vs v3waveA-B

Warnings: Git SHAs differ

## bargein / default / 1m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| completed_outcome_count | 1.2698 | [1.2650, 1.2752] | 4200 | 0.0000 | True | 0.9771 |
| completed_outcome_seen_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_char_length | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_rows_compared | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_unresolved_without_completion | -1.2731 | [-1.2793, -1.2676] | 4200 | 0.0000 | True | -0.9781 |
| replies_with_multiple_terminal_outcomes | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_with_zero_terminal_outcomes | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| replies_without_history_row | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_claimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_unclaimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| started_replies_without_terminal | -1.4164 | [-1.4226, -1.4107] | 4200 | 0.0000 | True | -0.9786 |
| started_reply_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcome_replies_eligible | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcomes_per_reply_claimed_violation | -0.0005 | [-0.0012, 0.0000] | 4200 | 0.2410 | False | -0.0005 |
| terminal_outcomes_per_reply_unclaimed_violation | -0.1429 | [-0.1429, -0.1429] | 4200 | 0.0000 | True | -0.1429 |
| terminal_outcomes_per_reply_violation | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| unattributed_assistant_rows | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## bargein / default / 1w

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| completed_outcome_count | 1.2698 | [1.2650, 1.2752] | 4200 | 0.0000 | True | 0.9771 |
| completed_outcome_seen_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_char_length | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_rows_compared | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_unresolved_without_completion | -1.2731 | [-1.2793, -1.2676] | 4200 | 0.0000 | True | -0.9781 |
| replies_with_multiple_terminal_outcomes | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_with_zero_terminal_outcomes | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| replies_without_history_row | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_claimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_unclaimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| started_replies_without_terminal | -1.4164 | [-1.4226, -1.4107] | 4200 | 0.0000 | True | -0.9786 |
| started_reply_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcome_replies_eligible | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcomes_per_reply_claimed_violation | -0.0005 | [-0.0012, 0.0000] | 4200 | 0.2410 | False | -0.0005 |
| terminal_outcomes_per_reply_unclaimed_violation | -0.1429 | [-0.1429, -0.1429] | 4200 | 0.0000 | True | -0.1429 |
| terminal_outcomes_per_reply_violation | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| unattributed_assistant_rows | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## bargein / default / 1y

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| completed_outcome_count | 1.2698 | [1.2650, 1.2752] | 4200 | 0.0000 | True | 0.9771 |
| completed_outcome_seen_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_char_length | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_rows_compared | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_unresolved_without_completion | -1.2731 | [-1.2793, -1.2676] | 4200 | 0.0000 | True | -0.9781 |
| replies_with_multiple_terminal_outcomes | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_with_zero_terminal_outcomes | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| replies_without_history_row | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_claimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_unclaimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| started_replies_without_terminal | -1.4164 | [-1.4226, -1.4107] | 4200 | 0.0000 | True | -0.9786 |
| started_reply_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcome_replies_eligible | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcomes_per_reply_claimed_violation | -0.0005 | [-0.0012, 0.0000] | 4200 | 0.2410 | False | -0.0005 |
| terminal_outcomes_per_reply_unclaimed_violation | -0.1429 | [-0.1429, -0.1429] | 4200 | 0.0000 | True | -0.1429 |
| terminal_outcomes_per_reply_violation | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| unattributed_assistant_rows | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## bargein / default / 6m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| completed_outcome_count | 1.2698 | [1.2650, 1.2752] | 4200 | 0.0000 | True | 0.9771 |
| completed_outcome_seen_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| completed_outcome_seen_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| current_turn_harmed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_matches_heard_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_char_length | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_mismatch_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| history_rows_compared | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| hung_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_unresolved_without_completion | -1.2731 | [-1.2793, -1.2676] | 4200 | 0.0000 | True | -0.9781 |
| replies_with_multiple_terminal_outcomes | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_with_zero_terminal_outcomes | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| replies_without_history_row | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_claimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| replies_without_history_row_unclaimed | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| stale_stop_applied_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| started_replies_without_terminal | -1.4164 | [-1.4226, -1.4107] | 4200 | 0.0000 | True | -0.9786 |
| started_reply_count | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcome_replies_eligible | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| terminal_outcomes_per_reply_claimed_violation | -0.0005 | [-0.0012, 0.0000] | 4200 | 0.2410 | False | -0.0005 |
| terminal_outcomes_per_reply_unclaimed_violation | -0.1429 | [-0.1429, -0.1429] | 4200 | 0.0000 | True | -0.1429 |
| terminal_outcomes_per_reply_violation | -0.1433 | [-0.1440, -0.1429] | 4200 | 0.0000 | True | -0.1433 |
| unattributed_assistant_rows | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_claimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_unclaimed_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |
| unattributed_assistant_rows_violation | 0.0000 | [0.0000, 0.0000] | 4200 | 1.0000 | False | 0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / brain_first/1m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -148.1172 | [-226.5736, -104.6699] | 2611 | 0.0000 | True | -0.1545 |
| capped | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| collision | -0.1723 | [-0.2200, -0.1434] | 2611 | 0.0000 | True | -0.1723 |
| cooldown_violations | -155.9414 | [-239.8882, -110.8930] | 2611 | 0.0000 | True | -0.1628 |
| eligible_ticks | -155.0597 | [-238.5960, -110.2806] | 2611 | 0.0000 | True | -0.0368 |
| first_initiation_after_hours | 9.1979 | [4.5416, 17.9954] | 39 | 0.0000 | True | 0.7436 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| initiations | -156.0437 | [-240.0098, -110.9866] | 2611 | 0.0000 | True | -0.1506 |
| initiations_per_idle_hour_after_threshold | -59.9668 | [-60.2126, -59.6509] | 426 | 0.0000 | True | -0.9953 |
| irrelevant_initiations | -148.1172 | [-226.5736, -104.6699] | 2611 | 0.0000 | True | -0.1545 |
| last_proactive_attempt_resets | -156.1046 | [-240.0880, -111.0316] | 2611 | 0.0000 | True | -0.1632 |
| min_seconds_between_initiations | 6813.6389 | [4441.9355, 11194.8373] | 36 | 0.0000 | True | 0.9491 |
| night_initiations | -55.5687 | [-82.7370, -40.8437] | 2611 | 0.0000 | True | -0.1003 |
| ticks | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| useful_initiations | -7.9265 | [-14.7290, -3.9970] | 2611 | 0.0000 | True | -0.0009 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / brain_first/1w

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -111.0028 | [-210.9566, -71.1698] | 703 | 0.0000 | True | -0.1173 |
| capped | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| collision | -0.1266 | [-0.1845, -0.1010] | 703 | 0.0000 | True | -0.1266 |
| cooldown_violations | -111.1252 | [-210.7940, -71.3268] | 703 | 0.0000 | True | -0.1223 |
| eligible_ticks | -110.5533 | [-209.3203, -71.1167] | 703 | 0.0000 | True | -0.0348 |
| first_initiation_after_hours | 6.3667 | [3.2250, 18.9333] | 5 | 0.0000 | True | 0.8000 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| initiations | -111.2361 | [-210.9613, -71.4169] | 703 | 0.0000 | True | -0.1161 |
| initiations_per_idle_hour_after_threshold | -60.1666 | [-60.3452, -60.0419] | 86 | 0.0000 | True | -1.0000 |
| irrelevant_initiations | -111.0028 | [-210.9566, -71.1698] | 703 | 0.0000 | True | -0.1173 |
| last_proactive_attempt_resets | -111.2475 | [-210.9613, -71.4336] | 703 | 0.0000 | True | -0.1223 |
| min_seconds_between_initiations | 12545.0000 | [7140.0000, 14346.6667] | 4 | 0.0000 | True | 1.0000 |
| night_initiations | -40.5220 | [-75.2445, -26.3826] | 703 | 0.0000 | True | -0.0755 |
| ticks | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.2333 | [-0.6407, 0.0000] | 703 | 0.6840 | False | -0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / brain_first/1y

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -157.6459 | [-243.9414, -112.3667] | 29910 | 0.0000 | True | -0.1460 |
| capped | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| collision | -0.1757 | [-0.2158, -0.1515] | 29910 | 0.0000 | True | -0.1757 |
| cooldown_violations | -176.5888 | [-274.6321, -125.7176] | 29910 | 0.0000 | True | -0.1638 |
| eligible_ticks | -175.2628 | [-272.4652, -124.8206] | 29910 | 0.0000 | True | -0.0311 |
| first_initiation_after_hours | 7.9337 | [5.7737, 10.9604] | 928 | 0.0000 | True | 0.6228 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| initiations | -176.5695 | [-274.5462, -125.7332] | 29910 | 0.0000 | True | -0.1380 |
| initiations_per_idle_hour_after_threshold | -60.3765 | [-60.9672, -59.8660] | 4907 | 0.0000 | True | -0.9996 |
| irrelevant_initiations | -157.6459 | [-243.9414, -112.3667] | 29910 | 0.0000 | True | -0.1460 |
| last_proactive_attempt_resets | -176.7528 | [-274.8179, -125.8683] | 29910 | 0.0000 | True | -0.1641 |
| min_seconds_between_initiations | 43813.9145 | [25730.5789, 67193.8043] | 924 | 0.0000 | True | 0.9980 |
| night_initiations | -62.3344 | [-93.7755, -46.1349] | 29910 | 0.0000 | True | -0.0968 |
| ticks | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| useful_initiations | -18.9235 | [-29.7157, -13.2973] | 29910 | 0.0000 | True | -0.0029 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / brain_first/6m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -159.8929 | [-249.5086, -114.7458] | 14904 | 0.0000 | True | -0.1485 |
| capped | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| collision | -0.1781 | [-0.2203, -0.1532] | 14904 | 0.0000 | True | -0.1781 |
| cooldown_violations | -177.1848 | [-277.1181, -126.9821] | 14904 | 0.0000 | True | -0.1643 |
| eligible_ticks | -175.9309 | [-275.1435, -126.1034] | 14904 | 0.0000 | True | -0.0319 |
| first_initiation_after_hours | 8.9089 | [6.2849, 12.7893] | 417 | 0.0000 | True | 0.6427 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| initiations | -177.1904 | [-277.0938, -127.0307] | 14904 | 0.0000 | True | -0.1412 |
| initiations_per_idle_hour_after_threshold | -60.7595 | [-61.9659, -59.8174] | 2453 | 0.0000 | True | -0.9992 |
| irrelevant_initiations | -159.8929 | [-249.5086, -114.7458] | 14904 | 0.0000 | True | -0.1485 |
| last_proactive_attempt_resets | -177.3494 | [-277.3092, -127.1299] | 14904 | 0.0000 | True | -0.1646 |
| min_seconds_between_initiations | 45236.1332 | [24924.9153, 69241.2695] | 413 | 0.0000 | True | 0.9955 |
| night_initiations | -62.4474 | [-94.8154, -46.0829] | 14904 | 0.0000 | True | -0.0973 |
| ticks | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| useful_initiations | -17.2975 | [-28.6212, -11.7684] | 14904 | 0.0000 | True | -0.0022 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / subconscious_first/1m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -148.1172 | [-226.5736, -104.6699] | 2611 | 0.0000 | True | -0.1545 |
| capped | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| collision | -0.1723 | [-0.2200, -0.1434] | 2611 | 0.0000 | True | -0.1723 |
| cooldown_violations | -155.9414 | [-239.8882, -110.8930] | 2611 | 0.0000 | True | -0.1628 |
| eligible_ticks | -155.0597 | [-238.5960, -110.2806] | 2611 | 0.0000 | True | -0.0368 |
| first_initiation_after_hours | 9.1979 | [4.5416, 17.9954] | 39 | 0.0000 | True | 0.7436 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| initiations | -156.0437 | [-240.0098, -110.9866] | 2611 | 0.0000 | True | -0.1506 |
| initiations_per_idle_hour_after_threshold | -59.9668 | [-60.2126, -59.6509] | 426 | 0.0000 | True | -0.9953 |
| irrelevant_initiations | -148.1172 | [-226.5736, -104.6699] | 2611 | 0.0000 | True | -0.1545 |
| last_proactive_attempt_resets | -156.1046 | [-240.0880, -111.0316] | 2611 | 0.0000 | True | -0.1632 |
| min_seconds_between_initiations | 6813.6389 | [4441.9355, 11194.8373] | 36 | 0.0000 | True | 0.9491 |
| night_initiations | -55.5687 | [-82.7370, -40.8437] | 2611 | 0.0000 | True | -0.1003 |
| ticks | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| useful_initiations | -7.9265 | [-14.7290, -3.9970] | 2611 | 0.0000 | True | -0.0009 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / subconscious_first/1w

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -111.0028 | [-210.9566, -71.1698] | 703 | 0.0000 | True | -0.1173 |
| capped | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| collision | -0.1266 | [-0.1845, -0.1010] | 703 | 0.0000 | True | -0.1266 |
| cooldown_violations | -111.1252 | [-210.7940, -71.3268] | 703 | 0.0000 | True | -0.1223 |
| eligible_ticks | -110.5533 | [-209.3203, -71.1167] | 703 | 0.0000 | True | -0.0348 |
| first_initiation_after_hours | 6.3667 | [3.2250, 18.9333] | 5 | 0.0000 | True | 0.8000 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| initiations | -111.2361 | [-210.9613, -71.4169] | 703 | 0.0000 | True | -0.1161 |
| initiations_per_idle_hour_after_threshold | -60.1666 | [-60.3452, -60.0419] | 86 | 0.0000 | True | -1.0000 |
| irrelevant_initiations | -111.0028 | [-210.9566, -71.1698] | 703 | 0.0000 | True | -0.1173 |
| last_proactive_attempt_resets | -111.2475 | [-210.9613, -71.4336] | 703 | 0.0000 | True | -0.1223 |
| min_seconds_between_initiations | 12545.0000 | [7140.0000, 14346.6667] | 4 | 0.0000 | True | 1.0000 |
| night_initiations | -40.5220 | [-75.2445, -26.3826] | 703 | 0.0000 | True | -0.0755 |
| ticks | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.2333 | [-0.6407, 0.0000] | 703 | 0.6840 | False | -0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / subconscious_first/1y

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -157.6459 | [-243.9414, -112.3667] | 29910 | 0.0000 | True | -0.1460 |
| capped | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| collision | -0.1757 | [-0.2158, -0.1515] | 29910 | 0.0000 | True | -0.1757 |
| cooldown_violations | -176.5888 | [-274.6321, -125.7176] | 29910 | 0.0000 | True | -0.1638 |
| eligible_ticks | -175.2628 | [-272.4652, -124.8206] | 29910 | 0.0000 | True | -0.0311 |
| first_initiation_after_hours | 7.9337 | [5.7737, 10.9604] | 928 | 0.0000 | True | 0.6228 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| initiations | -176.5695 | [-274.5462, -125.7332] | 29910 | 0.0000 | True | -0.1380 |
| initiations_per_idle_hour_after_threshold | -60.3765 | [-60.9672, -59.8660] | 4907 | 0.0000 | True | -0.9996 |
| irrelevant_initiations | -157.6459 | [-243.9414, -112.3667] | 29910 | 0.0000 | True | -0.1460 |
| last_proactive_attempt_resets | -176.7528 | [-274.8179, -125.8683] | 29910 | 0.0000 | True | -0.1641 |
| min_seconds_between_initiations | 43813.9145 | [25730.5789, 67193.8043] | 924 | 0.0000 | True | 0.9980 |
| night_initiations | -62.3344 | [-93.7755, -46.1349] | 29910 | 0.0000 | True | -0.0968 |
| ticks | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| useful_initiations | -18.9235 | [-29.7157, -13.2973] | 29910 | 0.0000 | True | -0.0029 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / broadcast / subconscious_first/6m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -159.8929 | [-249.5086, -114.7458] | 14904 | 0.0000 | True | -0.1485 |
| capped | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| collision | -0.1781 | [-0.2203, -0.1532] | 14904 | 0.0000 | True | -0.1781 |
| cooldown_violations | -177.1848 | [-277.1181, -126.9821] | 14904 | 0.0000 | True | -0.1643 |
| eligible_ticks | -175.9309 | [-275.1435, -126.1034] | 14904 | 0.0000 | True | -0.0319 |
| first_initiation_after_hours | 8.9089 | [6.2849, 12.7893] | 417 | 0.0000 | True | 0.6427 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| initiations | -177.1904 | [-277.0938, -127.0307] | 14904 | 0.0000 | True | -0.1412 |
| initiations_per_idle_hour_after_threshold | -60.7595 | [-61.9659, -59.8174] | 2453 | 0.0000 | True | -0.9992 |
| irrelevant_initiations | -159.8929 | [-249.5086, -114.7458] | 14904 | 0.0000 | True | -0.1485 |
| last_proactive_attempt_resets | -177.3494 | [-277.3092, -127.1299] | 14904 | 0.0000 | True | -0.1646 |
| min_seconds_between_initiations | 45236.1332 | [24924.9153, 69241.2695] | 413 | 0.0000 | True | 0.9955 |
| night_initiations | -62.4474 | [-94.8154, -46.0829] | 14904 | 0.0000 | True | -0.0973 |
| ticks | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| useful_initiations | -17.2975 | [-28.6212, -11.7684] | 14904 | 0.0000 | True | -0.0022 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / brain_first/1m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -2.5381 | [-3.8726, -1.8066] | 2611 | 0.0000 | True | -0.1544 |
| capped | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| collision | -0.0019 | [-0.0051, 0.0000] | 2611 | 0.0780 | False | -0.0019 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.6381 | [-2.5582, -1.1318] | 2611 | 0.0000 | True | -0.0238 |
| first_initiation_after_hours | 9.1979 | [4.5416, 17.9954] | 39 | 0.0000 | True | 0.7436 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| initiations | -2.6220 | [-4.0139, -1.8717] | 2611 | 0.0000 | True | -0.1497 |
| initiations_per_idle_hour_after_threshold | -1.2339 | [-1.3746, -1.1157] | 426 | 0.0000 | True | -0.9828 |
| irrelevant_initiations | -2.5381 | [-3.8726, -1.8066] | 2611 | 0.0000 | True | -0.1544 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 3125.6111 | [902.6514, 7423.9355] | 36 | 0.0000 | True | 0.2485 |
| night_initiations | -0.9307 | [-1.3849, -0.6844] | 2611 | 0.0000 | True | -0.0992 |
| ticks | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.0839 | [-0.1600, -0.0421] | 2611 | 0.0000 | True | -0.0005 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / brain_first/1w

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -1.9033 | [-3.5867, -1.2310] | 703 | 0.0000 | True | -0.1173 |
| capped | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| collision | -0.0028 | [-0.0126, 0.0000] | 703 | 0.6970 | False | -0.0028 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.2219 | [-2.1711, -0.8182] | 703 | 0.0000 | True | -0.0298 |
| first_initiation_after_hours | 6.3667 | [3.2250, 18.9333] | 5 | 0.0000 | True | 0.8000 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| initiations | -1.9047 | [-3.5889, -1.2325] | 703 | 0.0000 | True | -0.1160 |
| initiations_per_idle_hour_after_threshold | -1.2212 | [-1.4957, -1.0411] | 86 | 0.0000 | True | -0.9785 |
| irrelevant_initiations | -1.9033 | [-3.5867, -1.2310] | 703 | 0.0000 | True | -0.1173 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 7402.7500 | [3600.0000, 8670.3333] | 4 | 0.0000 | True | 0.7500 |
| night_initiations | -0.6799 | [-1.2556, -0.4418] | 703 | 0.0000 | True | -0.0755 |
| ticks | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.0014 | [-0.0039, 0.0000] | 703 | 0.6840 | False | -0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / brain_first/1y

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -2.6791 | [-4.1194, -1.9231] | 29910 | 0.0000 | True | -0.1455 |
| capped | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| collision | -0.0027 | [-0.0038, -0.0020] | 29910 | 0.0000 | True | -0.0027 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.5386 | [-2.3086, -1.1223] | 29910 | 0.0000 | True | -0.0155 |
| first_initiation_after_hours | 7.9337 | [5.7737, 10.9604] | 928 | 0.0000 | True | 0.6228 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| initiations | -2.8453 | [-4.3709, -2.0417] | 29910 | 0.0000 | True | -0.1357 |
| initiations_per_idle_hour_after_threshold | -1.7475 | [-2.3461, -1.2729] | 4907 | 0.0000 | True | -0.9695 |
| irrelevant_initiations | -2.6791 | [-4.1194, -1.9231] | 29910 | 0.0000 | True | -0.1455 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 39976.7814 | [21922.1143, 63399.1854] | 924 | 0.0000 | True | 0.1600 |
| night_initiations | -1.0324 | [-1.5490, -0.7661] | 29910 | 0.0000 | True | -0.0962 |
| ticks | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.1661 | [-0.2565, -0.1191] | 29910 | 0.0000 | True | -0.0018 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / brain_first/6m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -2.7195 | [-4.2161, -1.9641] | 14904 | 0.0000 | True | -0.1481 |
| capped | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| collision | -0.0025 | [-0.0038, -0.0017] | 14904 | 0.0000 | True | -0.0025 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.6201 | [-2.4924, -1.1634] | 14904 | 0.0000 | True | -0.0165 |
| first_initiation_after_hours | 8.9089 | [6.2849, 12.7893] | 417 | 0.0000 | True | 0.6427 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| initiations | -2.8796 | [-4.4884, -2.0835] | 14904 | 0.0000 | True | -0.1391 |
| initiations_per_idle_hour_after_threshold | -2.0876 | [-3.3596, -1.1828] | 2453 | 0.0000 | True | -0.9728 |
| irrelevant_initiations | -2.7195 | [-4.2161, -1.9641] | 14904 | 0.0000 | True | -0.1481 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 41431.5860 | [21159.9697, 65290.4448] | 413 | 0.0000 | True | 0.1670 |
| night_initiations | -1.0360 | [-1.5721, -0.7664] | 14904 | 0.0000 | True | -0.0966 |
| ticks | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.1602 | [-0.2634, -0.1078] | 14904 | 0.0000 | True | -0.0017 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / subconscious_first/1m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -2.5381 | [-3.8726, -1.8066] | 2611 | 0.0000 | True | -0.1544 |
| capped | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| collision | -0.0019 | [-0.0051, 0.0000] | 2611 | 0.0780 | False | -0.0019 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.6381 | [-2.5582, -1.1318] | 2611 | 0.0000 | True | -0.0238 |
| first_initiation_after_hours | 9.1979 | [4.5416, 17.9954] | 39 | 0.0000 | True | 0.7436 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| initiations | -2.6220 | [-4.0139, -1.8717] | 2611 | 0.0000 | True | -0.1497 |
| initiations_per_idle_hour_after_threshold | -1.2339 | [-1.3746, -1.1157] | 426 | 0.0000 | True | -0.9828 |
| irrelevant_initiations | -2.5381 | [-3.8726, -1.8066] | 2611 | 0.0000 | True | -0.1544 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 3125.6111 | [902.6514, 7423.9355] | 36 | 0.0000 | True | 0.2485 |
| night_initiations | -0.9307 | [-1.3849, -0.6844] | 2611 | 0.0000 | True | -0.0992 |
| ticks | 0.0000 | [0.0000, 0.0000] | 2611 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.0839 | [-0.1600, -0.0421] | 2611 | 0.0000 | True | -0.0005 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / subconscious_first/1w

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -1.9033 | [-3.5867, -1.2310] | 703 | 0.0000 | True | -0.1173 |
| capped | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| collision | -0.0028 | [-0.0126, 0.0000] | 703 | 0.6970 | False | -0.0028 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.2219 | [-2.1711, -0.8182] | 703 | 0.0000 | True | -0.0298 |
| first_initiation_after_hours | 6.3667 | [3.2250, 18.9333] | 5 | 0.0000 | True | 0.8000 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| initiations | -1.9047 | [-3.5889, -1.2325] | 703 | 0.0000 | True | -0.1160 |
| initiations_per_idle_hour_after_threshold | -1.2212 | [-1.4957, -1.0411] | 86 | 0.0000 | True | -0.9785 |
| irrelevant_initiations | -1.9033 | [-3.5867, -1.2310] | 703 | 0.0000 | True | -0.1173 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 7402.7500 | [3600.0000, 8670.3333] | 4 | 0.0000 | True | 0.7500 |
| night_initiations | -0.6799 | [-1.2556, -0.4418] | 703 | 0.0000 | True | -0.0755 |
| ticks | 0.0000 | [0.0000, 0.0000] | 703 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.0014 | [-0.0039, 0.0000] | 703 | 0.6840 | False | -0.0000 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / subconscious_first/1y

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -2.6791 | [-4.1194, -1.9231] | 29910 | 0.0000 | True | -0.1455 |
| capped | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| collision | -0.0027 | [-0.0038, -0.0020] | 29910 | 0.0000 | True | -0.0027 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.5386 | [-2.3086, -1.1223] | 29910 | 0.0000 | True | -0.0155 |
| first_initiation_after_hours | 7.9337 | [5.7737, 10.9604] | 928 | 0.0000 | True | 0.6228 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| initiations | -2.8453 | [-4.3709, -2.0417] | 29910 | 0.0000 | True | -0.1357 |
| initiations_per_idle_hour_after_threshold | -1.7475 | [-2.3461, -1.2729] | 4907 | 0.0000 | True | -0.9695 |
| irrelevant_initiations | -2.6791 | [-4.1194, -1.9231] | 29910 | 0.0000 | True | -0.1455 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 39976.7814 | [21922.1143, 63399.1854] | 924 | 0.0000 | True | 0.1600 |
| night_initiations | -1.0324 | [-1.5490, -0.7661] | 29910 | 0.0000 | True | -0.0962 |
| ticks | 0.0000 | [0.0000, 0.0000] | 29910 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.1661 | [-0.2565, -0.1191] | 29910 | 0.0000 | True | -0.0018 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |

## proactive / none / subconscious_first/6m

| Metric | Delta | 95% CI | n | p | Holm significant | Cliff's delta |
|---|---:|---:|---:|---:|---|---:|
| annoyance_count | -2.7195 | [-4.2161, -1.9641] | 14904 | 0.0000 | True | -0.1481 |
| capped | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| collision | -0.0025 | [-0.0038, -0.0017] | 14904 | 0.0000 | True | -0.0025 |
| cooldown_violations | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| eligible_ticks | -1.6201 | [-2.4924, -1.1634] | 14904 | 0.0000 | True | -0.0165 |
| first_initiation_after_hours | 8.9089 | [6.2849, 12.7893] | 417 | 0.0000 | True | 0.6427 |
| gap_hours | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| goal_resurfacing_reachable | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| initiations | -2.8796 | [-4.4884, -2.0835] | 14904 | 0.0000 | True | -0.1391 |
| initiations_per_idle_hour_after_threshold | -2.0876 | [-3.3596, -1.1828] | 2453 | 0.0000 | True | -0.9728 |
| irrelevant_initiations | -2.7195 | [-4.2161, -1.9641] | 14904 | 0.0000 | True | -0.1481 |
| last_proactive_attempt_resets | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| min_seconds_between_initiations | 41431.5860 | [21159.9697, 65290.4448] | 413 | 0.0000 | True | 0.1670 |
| night_initiations | -1.0360 | [-1.5721, -0.7664] | 14904 | 0.0000 | True | -0.0966 |
| ticks | 0.0000 | [0.0000, 0.0000] | 14904 | 1.0000 | False | 0.0000 |
| useful_initiations | -0.1602 | [-0.2634, -0.1078] | 14904 | 0.0000 | True | -0.0017 |

| Unpaired rows | Count |
|---|---:|
| Baseline-only cells | 0 |
| Arm-only cells | 0 |
| Baseline-only probes | 0 |
| Arm-only probes | 0 |
