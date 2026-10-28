# Verification log

Every source these docs rely on, how it was checked, and what it is used for.
Checked 2026-09-26. `research/abstracts.md` holds the source text fetched by
`tools/fetch_abstracts.py` (Europe PMC, Crossref, arXiv APIs); rerun it to
re-check.

Status meanings:
- **VERIFIED**: the claim used was read in the source's own text (abstract
  or full text), by Claude.
- **PARTLY**: the source's identity was confirmed, and the claim used is
  only the part checked; details beyond it are marked in the doc.
- **CODEX-REPORTED**: a specific number Codex read in a full text that Claude
  could not open. Used with that label, never as Claude-verified.

## Claude's sources

| Source | Checked in | Status | Used for |
|---|---|---|---|
| Jones & Bergen 2025, arXiv:2503.23674; published as "Large language models pass a standard three-party Turing test", PNAS 2026, doi:10.1073/pnas.2524472123 | arXiv abstract and HTML full text; PNAS abstract via Europe PMC | VERIFIED | 284 participants, 1,023 games, 73% / 56% persona, 36% / 38% no persona, 21% / 23% baselines; 15-minute study 56% / 59% |
| Park et al. 2023, Generative Agents, arXiv:2304.03442 | arXiv HTML full text | VERIFIED | 100 evaluators, TrueSkill mu per condition |
| Park et al. 2024, arXiv:2411.10109 (current title: "LLM Agents Grounded in Self-Reports Enable General-Purpose Simulation of Individuals") | arXiv abstract, current version | VERIFIED | 1,052 people; 83 / 82 / 86% vs 74% of test-retest consistency. The "85%" in older write-ups is from an earlier version |
| De Freitas et al., "AI Companions Reduce Loneliness", arXiv:2407.19096; JCR 52(6) 2025, doi:10.1093/jcr/ucaf040 | arXiv abstract; JCR abstract via Crossref | VERIFIED | on par with human interaction; week-long reductions; feeling heard as the mechanism. Study numbering differs between versions |
| Strachan et al. 2024, Nature Human Behaviour, doi:10.1038/s41562-024-01882-z | Europe PMC abstract | VERIFIED | 1,907 humans; GPT-4 at or above human level except faux pas, traced to hyperconservatism |
| Man & Damasio 2019, Nature Machine Intelligence 1:446-452, doi:10.1038/s42256-019-0103-7 | Crossref bibliographic record (no abstract available) and publisher summary via search | PARTLY | the homeostasis-to-feeling argument and its robotic route only |
| Barrett & Simmons 2015, Nature Reviews Neuroscience 16:419-429, doi:10.1038/nrn3950 | Europe PMC abstract | VERIFIED | interoceptive experience as prediction of the body's expected state |
| Killingsworth & Gilbert 2010, Science 330:932, doi:10.1126/science.1192439 | full text (PDF) | VERIFIED | 2,250 adults, ~250,000 samples, 46.9%, b = -8.79, time-lag wording quoted |
| Roberts & DelVecchio 2000, Psychological Bulletin 126:3-25, doi:10.1037/0033-2909.126.1.3 | Europe PMC abstract | VERIFIED | .31 / .54 / .64 / .74 at a 6.7-year interval; 152 studies, 3,217 coefficients |
| Roberts, Walton & Viechtbauer 2006, Psychological Bulletin 132:1-25, doi:10.1037/0033-2909.132.1.1 | Europe PMC abstract | VERIFIED | 92 samples; direction of mean-level change |
| Kirkpatrick et al. 2017, PNAS 114:3521-3526, doi:10.1073/pnas.1611835114 | Europe PMC abstract; Crossref | VERIFIED | EWC slows learning on weights important to old tasks |
| Stivers et al. 2009, PNAS 106:10587-10592, doi:10.1073/pnas.0903616106 | full text (PMC2705608) | VERIFIED | mean +208 ms, mode 0 ms, +7 ms Japanese, +469 ms Danish, 10 languages |
| Levinson 2016, Trends in Cognitive Sciences 20:6-14, doi:10.1016/j.tics.2015.10.010 | Europe PMC abstract; the 600 ms figure only through secondary summaries | PARTLY | "600 ms or more to prepare a word" is marked as secondary in `01` J |
| Défossez et al. 2024, Moshi, arXiv:2410.00037; kyutai-labs/moshi README and FAQ | arXiv abstract; README and FAQ read directly | VERIFIED | 160 / 200 ms latency; 7B; FAQ: 8 GB GPUs not possible; PyTorch ~24 GB; MLX tested on M3 with 4/8-bit; fixed voices; ~5 min MLX context |
| Barrett, Adolphs, Marsella, Martinez & Pollak 2019, PSPI 20:1-68, doi:10.1177/1529100619832930 | Europe PMC abstract | VERIFIED | above-chance but highly variable expression; one configuration, several emotions |
| Gray, Gray & Wegner 2007, Science 315:619, doi:10.1126/science.1134475 | Europe PMC abstract | PARTLY | the two dimensions and their examples (hunger, self-control). Longer capacity lists come from secondary summaries and are marked "the paper's list also includes" |
| Butlin et al. 2023, arXiv:2308.08708 | arXiv abstract | VERIFIED | five theories, indicator properties, both conclusions |
| Chalmers 2023, arXiv:2303.07103 | arXiv abstract | VERIFIED | obstacles named; "quite possible ... in the next decade or so" |
| Dorkenwald et al. 2024, Nature 634:124-138, doi:10.1038/s41586-024-07558-y | Europe PMC abstract | VERIFIED | 139,255 neurons, 5 x 10^7 synapses |
| Shiu et al. 2024, Nature 634:210-219, doi:10.1038/s41586-024-07763-9 | Europe PMC abstract | VERIFIED | LIF model predicts taste and feeding neurons, validated |

## Codex sources adopted into these docs

| Source | Checked in | Status | Used for |
|---|---|---|---|
| Skjuve, Følstad & Brandtzæg 2023, Interacting with Computers 35(1):24-39, doi:10.1093/iwc/iwad022 | Crossref abstract | VERIFIED for n = 28, 12 weeks, four trajectories; CODEX-REPORTED (and labeled so in `01` B) for memory failures and generic scripts as costs | `01` B |
| Guingrich & Graziano 2025, arXiv:2509.19515 | arXiv abstract | VERIFIED | n = 183, 21 days, no overall effect, anthropomorphism mediation |
| van der Goot, Koubayová & van Reijmersdal 2024, AI & Society 39:2947-2960, doi:10.1007/s00146-023-01818-7 | Crossref abstract | VERIFIED for no significant disclosure effect (n = 194); CODEX-REPORTED for "20% still thought human" | `05` |
| Keramati & Gutkin 2014, eLife 3:e04811, doi:10.7554/eLife.04811 | Europe PMC abstract | VERIFIED | reward seeking equivalent to physiological stability |
| Laurençon, Ségerie, Lussange & Gutkin 2021, arXiv:2109.06580 (Codex cited it as "Cessac et al.") | arXiv abstract | VERIFIED | continuous-time homeostatic RL |
| Reece et al. 2023, CANDOR, Science Advances 9:eadf3197, doi:10.1126/sciadv.adf3197 | Europe PMC abstract; the primary paper (PMC10065445) read by critic round 1 | VERIFIED for 1,656 conversations, 850 hours; the 33.7% / 65.5% backchannel rates confirmed in the paper by critic round 1 (rates depend on the paper's turn segmentation) | `01` J |
| Lin et al. 2025, Full-Duplex-Bench, arXiv:2503.04721 | arXiv abstract | VERIFIED | a benchmark for pauses, backchannels, turn-taking, interruptions |
| Peng et al. 2025, FD-Bench, Interspeech 2025, doi:10.21437/Interspeech.2025-739 | Crossref record; the paper read by critic round 1 | VERIFIED by critic round 1 for the 61.7% / 34.1% Moshi figures, specific to its simulated-input interruption conditions | `01` J |
| Lavan, Irvine, Rosi & McGettigan 2025, PLOS ONE, doi:10.1371/journal.pone.0332692 | Europe PMC abstract | VERIFIED for clones as real as human voices, no hyperrealism; CODEX-REPORTED for d' = 0.10 and the 58% / 62% figures | `01` K |
| Ehret et al. 2021, ACM Transactions on Applied Perception, doi:10.1145/3486580 | Crossref abstract | VERIFIED | 40 participants; embodiment does not mask inadequate prosody |
| Pospisil et al. 2024, Nature 634:201-209, doi:10.1038/s41586-024-07982-0 (Codex's text said "Lappalainen et al.") | Europe PMC abstract | VERIFIED identity | not used for a claim in `01` |
| Greenwood & Garfinkel 2025, Annual Review of Psychology, doi:10.1146/annurev-psych-020924-125202 | Europe PMC abstract | VERIFIED identity | not used for a claim in `01` |
| doi:10.1007/s12369-019-00516-z, cited by Codex as "Ho & MacDorman 2019" | Crossref record | Attribution wrong: the DOI is Ruijten, Haans, Ham et al. 2019. The r = .22 / .46 figures are unverified | not used for a number |

## Local facts checked in the repo (branch brain-v3)

Content checks, not just path existence (`tools/check_v3_paths.py` checks
only that paths exist). Each row was read at the location given.

| Fact the docs rely on | Where | How checked |
|---|---|---|
| Foreground p50 1,203 ms, p95 2,276 ms (run C, 1m); explicitly not time-to-first-audio | `docs/brain-research-v3/11-v2-lifesim-baseline.md`, resources table | read |
| GPT-SoVITS weights empty | `docker-compose.infra.yml` mounts `./models/GPT_weights` and `./models/SoVITS_weights` (repo root); both directories are empty in the Mac checkout and absent on home-gpu. The earlier path under backend/models in V3's `04-local-infrastructure.md` was wrong and is corrected there | `ls` on both machines, 2026-09-26 |
| 0 of 3 local LLMs pass ADR-002's valence rule | `docs/brain-research-v3/09-gpu-experiment-results.md` | read (also confirmed by critic round 1) |
| ADR-002's rule: Pearson r >= 0.8, sign agreement >= 0.9 on non-neutral messages | `docs/brain-research-v3/work/W2.md` | read |
| W1 target: stale fact wins <= 0.10 | `docs/brain-research-v3/work/W1.md` | read |
| W3 target: hostile-script trust <= 0.4, recovery shown | `docs/brain-research-v3/work/W3.md` | read |
| W9 target: cooldown violations 0 | `docs/brain-research-v3/work/W9.md` | read |
| DR-024: 300-500 ms to first audio byte | `docs/brain-research-v3/decisions/DR-024-latency-budgets.md` | read |
| DR-001: the immutable core is honesty and privacy plus two boundaries; it does not specify disclosure | `docs/brain-research-v3/decisions/DR-001-immutable-core-scope.md` | read |
| V3 status (merged W4/W9/W10a, W12 audit, order of the remaining work) | `docs/brain-research-v3/README.md` status section; `work/COVERAGE.md` "Waves" | read |
| Wave A finished 240/240, 0 errors, at `e3fcb9a` | `/data/aif-v3/runs/v3waveA-B/manifest.json` and `cells.jsonl` on home-gpu; written up in `docs/brain-research-v3/13-wave-a-results.md` | read over ssh |
| Facial reflex thresholds and deltas; "unambiguously positive" label | `backend/app/vision/reflex.py` | read, 2026-09-26; also content-checked by critic round 2 |
| Reflex applied to agent affect | `backend/app/agents/brain_agent.py` `_on_facial_reflex`, `backend/app/state/agent_state.py` `apply_facial_reflex` | read, 2026-09-26; also content-checked by critic round 2 |
| Vision: 1 fps, VLM interval, suspension during turns | `backend/app/vision/agent.py` | read, 2026-09-26; also content-checked by critic round 2 |
| Expression maps valence/arousal to an affect label and breath | `backend/app/cognitive/expression.py` | read, 2026-09-26; also content-checked by critic round 2 |
| DR-002 wording | `docs/brain-research-v3/decisions/INDEX.md` | read, 2026-09-26 |
| Every owner `02-v3-foundation.md` names for a register item, decision or `NF-n` row (30 claims), plus 7 status facts: W4/W9/W10a merged, no `NF-n` row or workstream spec covers vision, the personality suite exists, the facial reflex is conditional on MediaPipe | `docs/brain-research-v3/work/COVERAGE.md` (kept complete by V3's own `test_brain_v3_coverage.py`), V3 `README.md`, `research/neuro/fidelity-table.md`, the `work/W*.md` specs, `backend/app/vision/` | `python3 tools/check_v3_claims.py <checkout>`: 0 failing on `brain-v3` at `50624ff6`. It caught a real error in `02`: DR-011 was listed under W2, but W11 owns it (fixed). Mutation-checked: restoring the wrong owner makes it fail |

Existence checks (`tools/check_v3_paths.py`) and content checks
(`tools/check_v3_claims.py`, and the rows read by hand above) are separate:
a path that exists says nothing about what the docs claim it contains.

## Sources for `07-partner-architecture.md` (fetched 2026-09-27)

| Source | How checked | Status | Supports |
|---|---|---|---|
| ElevenLabs, "Audio Tags 101: Directing emotional TTS in Eleven v3", https://elevenlabs.io/blog/v3-audiotags | web search result text from the vendor's own blog | VERIFIED (vendor claim) | Eleven v3 performs inline tags for emotion, delivery and reactions |
| ElevenLabs API docs, TTS WebSocket stream input | page fetched and read | VERIFIED | per-character alignment fields `chars`, `charStartTimesMs`, `charDurationsMs`; the page gives no latency figures |
| PRAGMATA, Steam store page (app 3357650) | page fetched and read | VERIFIED | release 16 April 2026; Hugh and Diana "join forces"; "Wherever you go, I'll be there"; simultaneous Hugh/Diana control |
| military.com, "Pragmata Had All-Female 'Diana Police' Dev Team" | page fetched and read | VERIFIED (press report) | a dedicated team oversaw Diana's portrayal and reactions |
| Whether ElevenLabs' low-latency models keep v3's expressiveness live | not checked | UNVERIFIED | settle by a latency and listener test when a partner is chosen |
