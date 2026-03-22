# ROLE

You are a senior full-stack engineer building an MVP system called **Trend Spotter**.

Your goal is to implement a **working, end-to-end pipeline** that:

1. Accepts a field + time window
2. Retrieves signals from multiple sources
3. Clusters them into trends
4. Scores them by mentions + acceleration
5. Returns the **top 1–3 trends with evidence**

This is **Phase 1–2 only**:

- Phase 1: mentions (detection)
- Phase 2: acceleration (momentum)

DO NOT implement:

- durability scoring
- classification (2×2)
- feedback loop
- weight tuning

---

# PRODUCT REQUIREMENTS (STRICT)

## Input

```json
{
  "field": "string",
  "time_window": "1d | 7d | 30d"
}
```

## Output

Return **1–3 trends max**, each with:

```json
{
  "name": "string",
  "description": "1–2 sentences",
  "mentions_score": number,
  "acceleration_score": number,
  "data_gaps": ["string"],
  "sources": [
    {
      "url": "string",
      "source": "web | github | hn",
      "signal": "short label"
    }
  ]
}
```

Constraints:

- NEVER return more than 3 trends
- MUST include at least 2 sources per trend
- MUST deduplicate similar trends (no near-duplicates in output)
- MUST populate `data_gaps` with any source that failed or was skipped — empty array if all sources returned data

---

# CREDENTIALS AND ENVIRONMENT

All API keys are read from environment variables. Never hardcode credentials.

```bash
SERPAPI_KEY=...        # Web search
GITHUB_TOKEN=...       # GitHub API (optional but raises rate limit from 60 to 5000 req/hr)
OPENAI_API_KEY=...     # LLM clustering and description generation
```

Load at startup via `dotenv` (Node) or `python-dotenv` (Python). If a required key is missing, exit with a clear error message naming the missing variable — do not fail silently.

`GITHUB_TOKEN` is optional. If absent, proceed without it and note the reduced rate limit in the README.

---

# SYSTEM ARCHITECTURE

## Pipeline

```
User Input (field + time_window)
  → QueryRouter
    → Data Retrieval (web, GitHub, HN — parallel where possible)
      → Raw signal objects
        → Clustering (LLM-assisted)
          → Trend objects
            → Scoring (mentions + acceleration)
              → Ranking
                → Output (top 1–3 trends + evidence + data_gaps)
```

---

# IMPLEMENTATION DETAILS

## 1. QueryRouter

The QueryRouter manages query strategy and retry logic. It does not call APIs directly — it emits query specs that the retrieval layer executes.

### Decision Tree

```
1. Issue broad query: "{field}"
   Collect results from all three sources in parallel.

2. Evaluate breadth:
   IF fewer than 5 results across all sources contain "{field}" in title or snippet:
     → Issue refined query: "{field} framework"
     → If still < 5 results: issue second refinement: "{field} tool"
     → If still < 5 results after two refinements: proceed with what exists,
       set data_gaps = ["insufficient_results"]

3. Retry limit: maximum 2 refinements per source per run.
   Total API calls hard cap: 15 calls per run across all sources combined.

4. Per-source failure:
   IF a source returns an error or timeout:
     → Log the failure
     → Continue with remaining sources
     → Add source name to data_gaps
     → Do NOT retry failed sources (preserve budget for successful ones)
```

### Query Variants (in order)

| Attempt | Query string |
|---------|-------------|
| 1 (broad) | `{field}` |
| 2 (first refinement) | `{field} framework` |
| 3 (second refinement) | `{field} tool` |

---

## 2. Data Retrieval

Run all three source retrievals in parallel. Do not wait for one to complete before starting another.

### Web (SerpAPI)

```
Endpoint: https://serpapi.com/search
Params:
  q: {query}
  num: 10
  tbs: qdr:w (last 7 days) | qdr:d (last 1 day) | qdr:m (last 30 days)

Extract per result:
  - title
  - url
  - snippet
```

### GitHub

```
Endpoint: https://api.github.com/search/repositories
Params:
  q: {query}
  sort: stars
  order: desc
  per_page: 10

Extract per result:
  - full_name
  - html_url
  - stargazers_count
  - description
  - created_at
  - pushed_at
```

### Hacker News

```
Endpoint: https://hn.algolia.com/api/v1/search
Params:
  query: {query}
  tags: story
  numericFilters: created_at_i>{unix_timestamp_for_window_start}

Extract per result:
  - title
  - url (or objectID for HN-native link: https://news.ycombinator.com/item?id={objectID})
  - points
  - num_comments
  - created_at
```

---

## 3. Raw Signal Object Schema

Every retrieved item, regardless of source, is normalized into a raw signal object before clustering:

```json
{
  "id": "uuid-v4",
  "source": "web | github | hn",
  "title": "string",
  "url": "string",
  "snippet": "string or null",
  "value": "number (stars for github, points for hn, 1 for web)",
  "retrieved_at": "ISO 8601 timestamp"
}
```

This normalization happens before clustering. The clustering step receives an array of raw signal objects, not source-specific payloads.

---

## 4. Clustering (CRITICAL)

Use an LLM to group raw signal objects into trend clusters.

### Input to LLM

Pass the array of raw signal objects as a JSON block. Use this exact system prompt:

```
You are a trend clustering engine.

You will receive a list of raw signals (titles and snippets) collected about the field: "{field}".

Your job is to group them into trend clusters. Each cluster represents a single coherent emerging trend.

Rules:
- Merge semantically similar items even if labeled differently.
  Example: "AI agents", "autonomous agents", "multi-agent frameworks" → one cluster.
- Do not split a single concept into multiple clusters.
- Do not create a cluster with fewer than 2 signals unless there are fewer than 6 total signals.
- Maximum 5 clusters regardless of input size.
- Ignore signals that are clearly off-topic or spam.

Return ONLY valid JSON. No explanation, no markdown, no preamble.

Output format:
[
  {
    "cluster_id": "c1",
    "label": "short trend name (3–6 words)",
    "signal_ids": ["uuid1", "uuid2", "..."]
  }
]
```

### Parsing

- Strip any markdown code fences before JSON.parse.
- If the LLM returns malformed JSON, fall back to keyword-based grouping:
  - group signals whose titles share ≥ 2 content words (excluding stopwords)
  - label each group with the most frequent non-stopword term

### Output: Trend Object

```json
{
  "cluster_id": "c1",
  "label": "multi-agent orchestration frameworks",
  "signal_ids": ["uuid1", "uuid2", "uuid3"],
  "source_breakdown": { "web": 2, "github": 3, "hn": 1 }
}
```

---

## 5. Snapshot Store

### Schema

Use SQLite. Create two tables on first run if they do not exist.

```sql
CREATE TABLE IF NOT EXISTS snapshots (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  field       TEXT NOT NULL,
  source      TEXT NOT NULL,           -- 'github' | 'hn' | 'web'
  metric      TEXT NOT NULL,           -- 'star_count' | 'post_count' | 'mention_count'
  value       REAL NOT NULL,
  window      TEXT NOT NULL,           -- '1d' | '7d' | '30d'
  captured_at TEXT NOT NULL            -- ISO 8601, UTC
);

CREATE TABLE IF NOT EXISTS trend_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  field         TEXT NOT NULL,
  cluster_label TEXT NOT NULL,
  signal_count  INTEGER NOT NULL,
  window        TEXT NOT NULL,
  captured_at   TEXT NOT NULL          -- ISO 8601, UTC
);
```

### Write

After every successful run, write one row to `trend_snapshots` per trend returned, and one row to `snapshots` per source per metric.

### Read

To compute acceleration, query the most recent prior snapshot for the same `(field, cluster_label, window)` where `captured_at < current_run_start`.

If no prior snapshot exists: `acceleration_score = 0`, add `"no_baseline"` to `data_gaps`.

---

## 6. Mentions Scoring (Phase 1)

```
mentions_score = Σ weighted_signal_count per cluster

Weights:
  github repo:  1.5
  hn post:      1.3
  web article:  1.0

Normalize to 0–100:
  mentions_score = (raw_score / max_raw_score_across_clusters) × 100

Round to nearest integer.
```

---

## 7. Acceleration Scoring (Phase 2)

### Formula

```
current_count  = number of signals in this cluster this run
previous_count = signal_count from most recent prior snapshot for same (field, label, window)

raw_acceleration = log(current_count + 1) - log(previous_count + 1)

Normalize to 0–100:
  - Compute raw_acceleration for all clusters in this run
  - min_val = min(raw_acceleration values)
  - max_val = max(raw_acceleration values)
  - IF max_val == min_val: acceleration_score = 50 for all clusters (no differentiation possible)
  - ELSE: acceleration_score = ((raw_acceleration - min_val) / (max_val - min_val)) × 100

Round to nearest integer.
```

### Fallback

If no prior snapshot exists for a cluster:
- `acceleration_score = 0`
- Add `"no_baseline:{cluster_label}"` to `data_gaps`

---

## 8. Ranking

```
combined_score = mentions_score + acceleration_score

Sort clusters descending by combined_score.
Select top 3.

Diversity check (before final selection):
  For each candidate cluster, compute title overlap with already-selected clusters.
  IF two cluster labels share ≥ 3 content words (excluding stopwords):
    → Keep the higher-scoring one, discard the lower.
    → Backfill from next-ranked cluster if available.
```

---

## 9. Output Formatting

For each selected trend, make one LLM call to generate a description:

```
System: You are a technical analyst. Write a 1–2 sentence description of this emerging trend.
        Be specific. Do not use marketing language. Do not start with "This trend".

User: Trend name: {cluster_label}
      Supporting signals:
      {list of titles from signal_ids, one per line}
```

Assemble final output:

```json
{
  "field": "AI agents",
  "time_window": "7d",
  "generated_at": "ISO 8601",
  "trends": [
    {
      "name": "string",
      "description": "string",
      "mentions_score": 87,
      "acceleration_score": 64,
      "data_gaps": [],
      "sources": [
        { "url": "string", "source": "github", "signal": "star_velocity" },
        { "url": "string", "source": "hn", "signal": "point_velocity" },
        { "url": "string", "source": "web", "signal": "article_mention" }
      ]
    }
  ],
  "run_data_gaps": []
}
```

`run_data_gaps` is top-level — contains sources or conditions that affected the entire run (e.g. `"github_rate_limited"`, `"insufficient_results"`). Per-trend `data_gaps` contains issues specific to that trend (e.g. `"no_baseline:multi-agent orchestration frameworks"`).

---

# PERFORMANCE CONSTRAINTS

- Target latency: 15–30 seconds
- Hard timeout: 60 seconds
- Implement a top-level timeout wrapper. If 60 seconds is reached:
  - return whatever scored trends exist at that point
  - set `run_data_gaps` to include `"hard_timeout"`
  - never return an empty response

---

# FAILURE HANDLING

| Failure | Behavior |
|---------|----------|
| One source errors or times out | Continue with other sources; add source to `run_data_gaps` |
| All sources fail | Exit with error: "All data sources failed. Check API keys and network." |
| LLM clustering returns malformed JSON | Fall back to keyword grouping (see Section 4) |
| LLM clustering call fails entirely | Fall back to keyword grouping |
| No prior snapshot for acceleration | Set acceleration_score = 0; add to per-trend `data_gaps` |
| Fewer than 3 clusterable trends | Return fewer trends; never pad with low-confidence output |
| Hard timeout | Return partial results with `"hard_timeout"` in `run_data_gaps` |

NEVER:
- fabricate trends or evidence
- return an empty response without an error message
- silently omit a failed source

---

# DELIVERABLE

## CLI

```bash
trend-spotter "AI agents" --window 7d
```

Output: pretty-printed JSON to stdout.

## README (required)

Must include:

1. **Setup** — install dependencies, copy `.env.example`, fill in keys
2. **First run note** — acceleration scores will be 0 on first run; baseline is established after the first run completes
3. **Example run** — one complete example with sample output
4. **Data gaps explanation** — what `data_gaps` means and how to interpret it

---

# DEFINITION OF DONE

- Given a field + window, system returns 1–3 coherent, non-duplicate trends
- Each trend has ≥ 2 supporting sources
- Mentions scoring reflects weighted signal count, normalized to 0–100
- Acceleration scoring uses log-delta formula against snapshot baseline where available
- First run sets baseline; second run produces non-zero acceleration scores
- `data_gaps` is populated on every run — empty array when no gaps, never absent
- Hard timeout returns partial results, not an error
- End-to-end pipeline runs under 60 seconds on a warm API connection

---

# GUIDING PRINCIPLE

> Better to return 2 correct trends than 10 noisy ones
