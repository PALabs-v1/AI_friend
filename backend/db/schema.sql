-- Enable the pgvector extension to work with embedding vectors
create extension if not exists vector;
create extension if not exists pgcrypto;

-- Create a table to store long-term memories
create table if not exists memories (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  raw_content text, -- Verbatim Truth
  wing text not null default 'personal',
  room text,
  embedding vector(768), -- Default local embedder dimension
  embedding_model text,
  embedding_dim integer,
  importance_score double precision not null default 0.5,
  emotional_weight double precision not null default 0.0,
  valence double precision not null default 0.0,
  certainty double precision not null default 1.0,
  source text not null default 'user',
  recall_count integer not null default 0,
  last_recalled_at timestamptz default now(),
  created_at timestamptz default now(),
  metadata jsonb default '{}'::jsonb,
  speaker text,
  record_type text not null default 'episode',
  valid_from timestamptz,
  valid_until timestamptz,
  contradicts_id uuid references memories(id) on delete set null,
  lifespan_stage varchar(100),
  crisis varchar(100),
  virtue varchar(100),
  relations varchar(255),
  relation_circles varchar(255),
  modality varchar(255)
);

-- Create a table to store cold archived decayed memories
create table if not exists archived_memories (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  raw_content text,
  wing text not null default 'personal',
  room text,
  importance_score double precision not null default 0.5,
  emotional_weight double precision not null default 0.0,
  valence double precision not null default 0.0,
  certainty double precision not null default 1.0,
  source text not null default 'user',
  recall_count integer not null default 0,
  last_recalled_at timestamptz default now(),
  created_at timestamptz default now(),
  metadata jsonb default '{}'::jsonb,
  speaker text,
  record_type text not null default 'episode',
  valid_from timestamptz,
  valid_until timestamptz,
  contradicts_id uuid,
  lifespan_stage varchar(100),
  crisis varchar(100),
  virtue varchar(100),
  relations varchar(255),
  relation_circles varchar(255),
  modality varchar(255),
  embedding halfvec(768),
  embedding_model text,
  embedding_dim integer
);

-- Schema migrations for existing tables
alter table memories add column if not exists lifespan_stage varchar(100);
alter table memories add column if not exists crisis varchar(100);
alter table memories add column if not exists virtue varchar(100);
alter table memories add column if not exists relations varchar(255);
alter table memories add column if not exists relation_circles varchar(255);
alter table memories add column if not exists modality varchar(255);
alter table memories add column if not exists speaker text;
alter table memories add column if not exists record_type text not null default 'episode';
alter table memories add column if not exists valid_from timestamptz;
alter table memories add column if not exists valid_until timestamptz;
alter table memories add column if not exists contradicts_id uuid;
alter table archived_memories add column if not exists speaker text;
alter table archived_memories add column if not exists record_type text not null default 'episode';
alter table archived_memories add column if not exists valid_from timestamptz;
alter table archived_memories add column if not exists valid_until timestamptz;
alter table archived_memories add column if not exists contradicts_id uuid;
alter table memories add column if not exists embedding_model text;
alter table memories add column if not exists embedding_dim integer;
alter table archived_memories add column if not exists embedding_model text;
alter table archived_memories add column if not exists embedding_dim integer;

alter table memories drop constraint if exists memories_contradicts_id_fkey;
alter table memories
  add constraint memories_contradicts_id_fkey
  foreign key (contradicts_id) references memories(id) on delete set null;

create index if not exists memories_contradicts_id_idx on memories (contradicts_id);

create index if not exists memories_embedding_idx
  on memories using hnsw (embedding vector_cosine_ops);

create index if not exists archived_memories_embedding_idx
  on archived_memories using hnsw (embedding halfvec_cosine_ops);

-- Screen-sourced salient visual episodes (P3-1). Screen captures are more
-- privacy-sensitive than camera captures -- a screen can show anything open
-- on the machine, not just the user's face -- so these get a hard TTL
-- (VISUAL_SCREEN_TRACE_TTL_H, pruned by MemoryStore.prune_expired_visual_screen_traces)
-- instead of the graded ACT-R fade `memories` rows get. Camera-sourced
-- visual traces go through `memories` instead (modality='visual') and do
-- follow that normal lifecycle.
create table if not exists visual_screen_traces (
  id uuid primary key default gen_random_uuid(),
  description text not null,
  valence double precision not null default 0.0,
  arousal double precision not null default 0.5,
  created_at timestamptz not null default now()
);

create index if not exists visual_screen_traces_created_at_idx
  on visual_screen_traces (created_at);

create table if not exists sessions (
  id uuid primary key,
  started_at timestamptz default now(),
  ended_at timestamptz,
  trust_benevolence double precision default 0.5,
  trust_competence double precision default 0.5,
  trust_integrity double precision default 0.5,
  metadata jsonb default '{}'::jsonb
);

create table if not exists messages (
  id uuid primary key,
  session_id uuid references sessions(id),
  role varchar(50) not null,
  content text not null,
  timestamp timestamptz default now(),
  consolidated boolean default false
);

create table if not exists agent_configs (
  id integer primary key,
  personality jsonb,
  background_history jsonb,
  evolved_learnings text,
  updated_at timestamptz default now()
);

-- Create a function to search for memories
-- usage: select * from match_memories(embedding, match_threshold, match_count)
create or replace function match_memories (
  query_embedding vector(768),
  match_threshold float,
  match_count int
)
returns table (
  id uuid,
  content text,
  similarity float
)
language sql stable
as $$
  select
    id,
    content,
    1 - (memories.embedding <=> query_embedding) as similarity
  from memories
  where 1 - (memories.embedding <=> query_embedding) > match_threshold
  order by memories.embedding <=> query_embedding
  limit match_count;
$$;

-- Create a high-performance ACT-R Based memory surfacing function
-- usage: select * from surface_actr_memories(query_embedding, wing, room, decay_rate, spread_weight, emotion_weight, current_valence, threshold, limit, simulated_time)
create or replace function surface_actr_memories (
  query_embedding vector(768),
  p_wing text,
  p_room text,
  p_decay_rate double precision,
  p_spread_weight double precision,
  p_emotion_weight double precision,
  p_current_valence double precision,
  p_current_arousal double precision,
  p_current_cortisol double precision,
  p_threshold double precision,
  p_limit int,
  p_simulated_time timestamptz default null
)
returns table (
  content text,
  raw_content text,
  wing text,
  room text,
  importance_score double precision,
  emotional_weight double precision,
  valence double precision,
  recall_count integer,
  last_recalled_at timestamptz,
  created_at timestamptz,
  metadata jsonb,
  similarity double precision,
  score double precision
)
language plpgsql stable
as $$
declare
  now_ts timestamptz := coalesce(p_simulated_time, clock_timestamp());
begin
  return query
  select
    m.content,
    m.raw_content,
    m.wing,
    m.room,
    m.importance_score,
    m.emotional_weight,
    m.valence,
    m.recall_count,
    m.last_recalled_at,
    m.created_at,
    m.metadata,
    (1 - (m.embedding <=> query_embedding))::double precision as similarity,
    (
      -- ACT-R Base-Level Activation (B_i)
      -- ln(greatest(1, recall_count)) - decay * ln(hours_since + 1) + 1.5 * importance_score + 0.15 * (1 - dist_emo)
      ln(greatest(1, m.recall_count))
      - p_decay_rate * ln(greatest(0.001, extract(epoch from (now_ts - coalesce(m.last_recalled_at, now_ts))) / 3600.0) + 1)
      + 1.5 * coalesce(m.importance_score, 0.5)
      + 0.15 * (1.0 - sqrt(power(coalesce(m.valence, 0.0) - p_current_valence, 2) + power(coalesce(m.emotional_weight, 0.0) - p_current_arousal, 2)))

      -- Bucket 9 (voice remediation Phase 3) spacing effect: rewards a
      -- memory whose repeat recalls were spread out over time (spaced
      -- practice) over one recalled the same number of times in a burst
      -- (massed practice). Mirrors `MemoryStore._spacing_hours`/
      -- `_base_activation` exactly (ACTR_SPACING_WEIGHT = 0.15) -- this SQL
      -- fast path must not silently diverge from the Python fallback path's
      -- scoring for the same memories.
      + case
          when m.recall_count >= 2
            and m.created_at is not null
            and m.last_recalled_at is not null
            and extract(epoch from (coalesce(m.last_recalled_at, now_ts) - m.created_at)) > 0
          then 0.15 * ln(
            (extract(epoch from (coalesce(m.last_recalled_at, now_ts) - m.created_at)) / 3600.0 / m.recall_count) + 1
          )
          else 0.0
        end

      -- Spreading Activation (Similarity with neuromodulatory gating)
      -- spread_weight * similarity * (1.0 + 0.1 * valence * emotional_weight - 0.2 * current_arousal * current_cortisol)
      + p_spread_weight * (1.0 - (m.embedding <=> query_embedding)) * (
        1.0
        + 0.1 * coalesce(m.valence, 0.0) * coalesce(m.emotional_weight, 0.0)
        - 0.2 * p_current_arousal * p_current_cortisol
      )

      -- Bower Emotional Proximity adjustment: -0.5 * dist_emo
      - 0.5 * sqrt(power(coalesce(m.valence, 0.0) - p_current_valence, 2) + power(coalesce(m.emotional_weight, 0.0) - p_current_arousal, 2))
    )::double precision as score
  from memories m
  where m.wing = p_wing
    and (p_room is null or m.room = p_room)
    and (
      (
        ln(greatest(1, m.recall_count))
        - p_decay_rate * ln(greatest(0.001, extract(epoch from (now_ts - coalesce(m.last_recalled_at, now_ts))) / 3600.0) + 1)
        + 1.5 * coalesce(m.importance_score, 0.5)
        + 0.15 * (1.0 - sqrt(power(coalesce(m.valence, 0.0) - p_current_valence, 2) + power(coalesce(m.emotional_weight, 0.0) - p_current_arousal, 2)))
        + case
            when m.recall_count >= 2
              and m.created_at is not null
              and m.last_recalled_at is not null
              and extract(epoch from (coalesce(m.last_recalled_at, now_ts) - m.created_at)) > 0
            then 0.15 * ln(
              (extract(epoch from (coalesce(m.last_recalled_at, now_ts) - m.created_at)) / 3600.0 / m.recall_count) + 1
            )
            else 0.0
          end
        + p_spread_weight * (1.0 - (m.embedding <=> query_embedding)) * (
          1.0
          + 0.1 * coalesce(m.valence, 0.0) * coalesce(m.emotional_weight, 0.0)
          - 0.2 * p_current_arousal * p_current_cortisol
        )
        - 0.5 * sqrt(power(coalesce(m.valence, 0.0) - p_current_valence, 2) + power(coalesce(m.emotional_weight, 0.0) - p_current_arousal, 2))
      ) > p_threshold
      or m.importance_score >= 0.7
    )
  order by score desc
  limit p_limit;
end;
$$;

-- Mental lexicon: the humanoid's learned vocabulary. It boots with a small
-- generic innate seed (source = 'innate') and acquires new words from lived
-- conversation. The embedding column is reserved for a future semantic-neighbor
-- augmentation and is left unpopulated by the current co-occurrence learner.
create table if not exists vocabulary (
  term text primary key,
  surface_forms text default '[]',
  embedding vector(768),
  embedding_model text,
  embedding_dim integer,
  times_seen integer default 1,
  source text default 'acquired',
  first_seen timestamptz default now(),
  last_seen timestamptz default now()
);
alter table vocabulary add column if not exists embedding_model text;
alter table vocabulary add column if not exists embedding_dim integer;

-- Learned word associations (distributional co-occurrence). Pairs are stored
-- once in canonical order (term_a < term_b); weight is reinforced each time the
-- two words are experienced together, so recall-time cue expansion reads what
-- the system has actually learned rather than a hardcoded thesaurus.
create table if not exists lexical_associations (
  term_a text not null,
  term_b text not null,
  weight double precision default 1.0,
  last_reinforced timestamptz default now(),
  primary key (term_a, term_b)
);

create index if not exists lexical_assoc_a_idx on lexical_associations(term_a);
create index if not exists lexical_assoc_b_idx on lexical_associations(term_b);

-- Self-knowledge gaps: specifics the agent tried to assert about its own life
-- and could not ground in anything the user has written or said. Recorded
-- rather than silently swallowed, because the gaps are the map of what the
-- biography is still missing -- and, later, of what the agent could ask about.
--
-- Keyed on the term so repeated hits accumulate: a name the user mentions
-- constantly but never explained climbs times_hit, while a one-off stays low.
-- There is deliberately no `resolved` flag; a gap the biography now covers
-- simply stops being hit, and last_seen already carries that.
create table if not exists self_knowledge_gaps (
  term text primary key,
  times_hit integer not null default 1,
  example_prompt text,
  first_seen timestamptz default now(),
  last_seen timestamptz default now(),
  asked_at timestamptz
);

create index if not exists self_gap_hits_idx on self_knowledge_gaps(times_hit desc);
