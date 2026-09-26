# Brain V3 memory scale runner

This runner builds deterministic memory rows from the `lifesim` generator,
embeds them once, and measures production `MemoryStore` loading and retrieval.
It does not claim production scale results until those cells have been run with
the real Ollama model and the named live services.

Run from `backend/`:

```sh
python -m tools.scale --sizes 200 --backends sqlite --seeds 1000 \
  --horizon 10y --stub-embeddings --data-dir /tmp/aif-scale \
  --report-dir /tmp/aif-scale/reports
```

`--stub-embeddings` is for no-network tests and local pipeline checks only. It
is explicitly recorded in the JSON report and is not evidence about real
embedding quality. The production path uses `MemoryStore.get_embeddings`,
which calls Ollama `/api/embed` with `nomic-embed-text` and no text prefix.
Ollama's model digest is read from `/api/tags` and bound into the cache name and
manifest. Do not reuse a cache if the corpus or model digest differs.

## Home-gpu run

Keep every generated file and database under `/data`. Use the project name
`aifv3`, start only the backend being measured, and give its container a Docker
memory limit that leaves room for the OS and the runner (home-gpu has 15 GB in
total, so 8 GB per backend container). Run one backend at a time so RSS, CPU and on-disk growth remain
attributable. The pinned Qdrant image is `qdrant/qdrant:v1.9.0`; pass the
reviewer's resolved latest stable image explicitly through `SCALE_QDRANT_IMAGE`.
Use `SCALE_QDRANT_PINNED_HOST` / `SCALE_QDRANT_PINNED_PORT` or
`SCALE_QDRANT_LATEST_HOST` / `SCALE_QDRANT_LATEST_PORT` when both versions are
available.

Example SQLite run:

```sh
python -m tools.scale --sizes 1000,10000,100000,500000,1000000 \
  --backends sqlite --data-dir /data/aifv3/scale \
  --report-dir /data/aifv3/scale/reports
```

Postgres uses the production memory table schema with pgvector/HNSW already
initialized and receives its DSN through `--database-url`. The DSN must name a
dedicated disposable scale database: the production decay API is content based
and cannot scope its writes to a room. For multiple sizes, use a database-name
template with `{size}` and initialize each database with the production schema.
Qdrant variants use the same SQL metadata store plus the production Qdrant
client; each cell gets its own collection. Set `SCALE_QDRANT_IMAGE` for the
latest-stable run to the resolved image tag. Neo4j uses the production
`GraphDB` client and its graph query path; use a fresh dedicated container or
volume for each size so service disk size is attributable to one cell.
The JSON marks a selected cell `not-run` with a setup error if its service or
schema is absent; unselected cells are also explicit. For each backend, run
all target sizes and retain each JSON/Markdown pair. Combine pairs only after
the run IDs, image tags, model digest and hardware have been checked.

The writer uses `MemoryStore.add_memory(..., embedding=...)` because it is the
production entry point that persists metadata, vector rows, and optional
Qdrant payloads together. It does not call the embedder per row. Retrieval is
measured through `MemoryStore.search_memories`; raw backend candidate query
time is recorded separately. The SQLite row count and vector-index row count
are both reported, so the 5,000-row `MEMORY_SQLITE_SCAN_LIMIT` cap is visible.

The report includes retrieval P50/P95/P99, raw query P50/P95/P99, hit@5 on
lifesim probes with known support turns, load time, decay-pass time, process
CPU/RSS, database/directory bytes, and foreground P95 with and without a
concurrent decay pass. The configured retrieval ceiling defaults to 300 ms,
the conservative whole interactive lower bound from DR-024; it is not a claim
that retrieval may consume the entire turn budget. Choose and record a smaller
retrieval share before treating it as an operational target.
Decay runs use batches of 5,000 content keys so SQLite stays under its bind
parameter limit; each batch still calls production `apply_actr_decay`.
Foreground comparisons clear the L1 cache before each query so both arms
measure store retrieval, not repeated-query cache hits.

The builder's local run cannot satisfy the home-gpu acceptance matrix or write
`docs/brain-research-v3/11-scale.md`; that document must use the reviewer's real
service/model measurements. Reports written under `/tmp` are not raw research
artifacts. Put reviewed home-gpu JSON under
`docs/brain-research-v3/results/scale/`.

## Verification log

- Pass 1: checked the module CLI with `python -m tools.scale --help` from
  `backend/`; the documented flags and import path resolve.
- Pass 2: ran the N=200 SQLite command above end to end; it produced a JSON and
  Markdown report, one measured SQLite cell and explicit `not-run` rows for
  four unselected services.
- Pass 3: `CI=1 pytest -q tests/test_scale_tool.py tests/test_scale_services.py`
  exercises the same corpus/cache/store/report pipeline in a temporary
  directory and verifies determinism, cache resume/rejection and report shape.
- Not verified here: the home-gpu command and all real-service sizes. This
  builder worktree cannot reach home-gpu; the reviewer settles those cells by
  running each live backend with real `nomic-embed-text` and recording the
  service image IDs, model digest and hardware in `results/scale/`.
