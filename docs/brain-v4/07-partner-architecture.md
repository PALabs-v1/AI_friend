# 07. Partner architecture: our brain, partner voice and vision

Status: direction set by Aniket, 2026-09-27. Not built. Decisions D5-D8 are
in `06-decisions.md`.

## Where it came from

Aniket, 2026-09-27 (verbatim):

> "for voice maybe if this system works, we can colab with eleven labs for
> example, whuch will look after our voice part, similarly vision from some
> other tech giants, where brain stand out as our novelty, and let's take an
> example of pragmata game by capcom, they had a charcater called diana,
> which felt like human, so we are sort of creating diana type of 3 teir
> architecture, braiun, voice, vision, I dont mean diana literally, because
> the personality of this system/brain will be authored by user opnce and for
> all at the start, so if we look t that angle"

## The three tiers

| Tier | Who builds it | What it does | What it must never do |
|---|---|---|---|
| Brain | us (V3, then V4): the product and the novelty | memory truth, feelings the user moves, drives, inner life, identity, shared goals, and every conversational decision: what to say, which emotion to voice, when to yield, when to interrupt | depend on one partner's internals |
| Voice | a partner (ElevenLabs is the example) | renders the brain's text and emotion as speech; recognizes the user's speech | decide turn-taking (D6) |
| Vision | a partner (a frontier vision model) | describes what the camera sees | decide what is remembered or what it means |

Partners sit behind contracts, so any one can be swapped without touching
the brain. The voice contract already exists: V3's W4 playback lifecycle
(`STARTED`, `PLAYING`, `COMPLETED`, `INTERRUPTED`, `FAILED`, keyed by
utterance and turn, with the heard offset). The current Rust voice path
becomes one adapter; a partner becomes another.

## Brain to voice

- **Brain sends**: text chunks, utterance and turn ids, and emotion
  directions derived from the brain's actual affect state (mood, arousal,
  drives), never chosen by the voice tier.
- **ElevenLabs can take that direction.** Eleven v3 accepts inline audio
  tags that the model performs rather than reads: emotions such as
  `[excited]`, `[nervous]`, `[sorrowful]`, delivery such as `[whispers]`,
  and reactions such as `[laughs]`, `[sighs]` (ElevenLabs, "Audio Tags 101").
  The brain-to-tag mapping is V4-8's job, and it only means something once
  W2 makes affect respond to the user.
- **Voice returns**: lifecycle events with the heard offset. ElevenLabs'
  streaming WebSocket returns per-character timing (`chars`,
  `charStartTimesMs`, `charDurationsMs`), which is the input DR-027's
  word-level truncation of heard text needs.
- **Speech recognition returns**: transcripts, plus early speech-onset
  signals, so the brain can make W5's barge-in decisions itself.

## Turn-taking stays in the brain (D6)

When to stop if interrupted, the proactive grace window (DR-030), and when
a significant thought may interrupt (DR-026) are the brain's decisions. A
partner's all-in-one conversational agent would make them instead, and the
behavior that makes the mind feel alive would belong to the partner. So the
partner is used as a renderer and a recognizer, not as the conversational
agent.

## Vision

A partner model describes scenes. The brain decides what becomes memory
(under W1's truth maintenance) and what it means (V4-9). Reading the user's
emotion from their face stays weak evidence whoever provides the model:
that limit is science (Barrett et al. 2019), not resources, and no partner
removes it (capability M in `01-what-is-possible.md`).

## What changes in the capability table

| Capability (`01`) | Before | With partners |
|---|---|---|
| K. Expressive voice | RESOURCE + ENGINEERING | mostly solved by the partner; the brain supplies the emotion |
| J. Human timing in voice | latency RESOURCE; turn-taking unsolved | latency mostly the partner's; turn-taking still ENGINEERING, in the brain (W5, V4-7) |
| L. Seeing | ENGINEERING | scene understanding is the partner's; what matters stays the brain's |
| M. Emotion from faces | SCIENCE | unchanged: no partner fixes it |
| B-H, P | ENGINEERING / SCIENCE | unchanged: this is the novelty, and nobody sells it |

## The Diana reference

PRAGMATA (Capcom, April 2026) pairs Hugh with Diana, a young android. Its
store page describes the pair as having to "join forces to find a way off
the moon", with the line "Wherever you go, I'll be there", and play in
which you control Hugh while Diana hacks at the same time (Steam store
page). Capcom had a dedicated "Diana Police" team overseeing how she
behaves and reacts (military.com).

What that teaches, as a design reference (not evidence about long-term
realism; a game is authored, scripted and bounded to hours):
1. Her realism comes from a **partnership**: a shared goal, mutual
   dependence, doing things together, and an arc. Conversation is only part
   of it. That is new capability **P** (`01-what-is-possible.md`) and
   workstream **V4-12**.
2. Her consistency is **enforced** by a team with a character bible. Here
   the user writes the character bible once, at the start (D5), and the
   brain is the enforcement, with no team and no script, for months:
   DR-001's immutable core, W11's identity bands, W1's memory truth.

## Persona authored once (D5)

The user authors the persona once at the start and cannot edit it later.
The authored core never moves. Everything else grows slowly on its own,
within human ranges (DR-020's self-authored evolution, W11, and V4-5's
human-band identity probe). Diana changes through her story and stays Diana.

## Open before integration

- **Privacy (D8, decided: decide at integration).** Mic audio and camera
  frames going to a third party must be settled against DR-001's privacy
  core when a partner is chosen: what is sent, what is retained, and
  whether memories or the persona ever leave the device.
- **Latency.** Network round trips count against DR-024's 300-500 ms to
  first audio. To measure, not assume.
- **Expressiveness at low latency.** Eleven v3 is positioned for long-form
  content. Whether ElevenLabs' low-latency models keep its expressiveness
  in live conversation is unverified.
- **Cost and dependence**: per-minute pricing, and the risk of a provider
  changing terms. The adapter contract is the mitigation.

## Sources (fetched 2026-09-27)

- ElevenLabs, "Audio Tags 101: Directing emotional TTS in Eleven v3":
  https://elevenlabs.io/blog/v3-audiotags
- ElevenLabs API docs, text-to-speech WebSocket stream input (alignment
  fields `chars`, `charStartTimesMs`, `charDurationsMs`):
  https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-stream-input
- PRAGMATA on Steam (store description, release 16 April 2026):
  https://store.steampowered.com/app/3357650/PRAGMATA/
- "Pragmata Had All-Female 'Diana Police' Dev Team":
  https://www.military.com/pragmata-had-all-female-diana-police-dev-team
