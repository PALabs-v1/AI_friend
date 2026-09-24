# Interview Round 8 — Voice and Turn-Taking

Gates W4 (voice lifecycle contract) and W5 (barge-in end-to-end). F-002 (this session's finding) already established that the "finished playing" signal is dead code with no producer — this round settles the *semantics* W4 needs to implement once the plumbing exists.

## Q1. When a reply is interrupted mid-sentence, what counts as "what the user heard" for history?

**What exists**: for a confirmed speculative barge-in, the reply is truncated to what was heard by character offset (`_truncate_interrupted_reply`) — word-level granularity, from the last progress marker. This is currently the *only* case that cuts history at all.

## Q2. Should an ordinary (non-speculative) barge-in cut history the same way?

**What exists**: no — confirmed in `00-current-architecture.md`. Only the speculative-then-confirmed path truncates history; a plain interruption doesn't touch the stored reply text at all today.

## Q3. Should a self-correction retry (register V-2) always be allowed to finish, or is it interruptible too?

**What exists**: today it isn't reliably either — an unscoped `audio.stop` from the self-correction path can cancel its own retry, which is a bug, not a designed behavior. The question is what the *intended* behavior should be once V-2's `flush` semantics are built (W4).

## Q4. Priority when a proactive turn and an incoming user turn collide — does the user always win?

**What exists**: they compete for one active-generation slot; whichever confirms first currently wins by virtue of ordering, not an explicit priority rule. This is a different collision from DR-026 (which was about a *self-initiated thought* interrupting an already-in-progress user turn) — this is about an ordinary proactive turn already speaking when the user starts talking.
