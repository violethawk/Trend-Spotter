# Trend Spotter — Signal Extraction in the Real World

A system for identifying and evaluating emerging trends within a specific field across multiple time horizons.

Type in a domain. Select a time window.  
Get the **top 1–3 trends that actually matter**, with supporting evidence and a durability verdict.

---

## Why This Exists

The internet is saturated with trends.

Most tools answer:
> "What is popular right now?"

This system attempts to answer a harder question:

> **"What actually matters — and is likely to continue mattering?"**

There are no answer keys.  
No deterministic correctness.

Only:
> **judgment under uncertainty**

---

## What It Does

Given a field (e.g. `"AI agents"`, `"healthcare AI"`):

- Searches and ingests recent signals across a selected time window
- Clusters and deduplicates emerging themes
- Scores each theme across **mentions, acceleration, and durability**
- Classifies trends into a 2×2 decision surface
- Surfaces **supporting evidence** (articles, repos, discussions) for every claim
- Stores predictions so future windows can evaluate accuracy

---

## Quick Start

```bash
# Analyze a field over the last 7 days
trend-spotter "AI agents" --window 7d

# Shorter window for faster, noisier signals
trend-spotter "healthcare AI" --window 1d
```

**Example output:**

```
Field:  AI agents
Window: Last 7 days

Trend 1 — Multi-agent orchestration frameworks
  Durability:    82   Acceleration: 91
  Trajectory:    rising
  Classification: Compounding
  Evidence:
    - https://...  (GitHub: +4,200 stars in 7d)
    - https://...  (HN discussion, 340 points)
    - https://...  (3 new production case studies)

Trend 2 — Autonomous coding agents in production
  Durability:    74   Acceleration: 68
  Trajectory:    rising
  Classification: Durable / Slow
  Evidence:
    - https://...
    - https://...
```

---

## The Core Problem

The challenge is not detecting trends.

It is:

> **distinguishing signal from noise early enough to act**

This requires separating momentum from durability, identifying who is engaging (builders vs. spectators), and understanding *why* something is growing — not just *that* it is.

---

## Architecture

```
User (field + time window)
  → QueryRouter
      ├─ web_search   (articles, news, discussions)
      ├─ github_api   (repo creation, star velocity, fork rate)
      └─ hn_api       (submissions, point velocity, comment depth)
    → Semantic clustering (LLM-assisted entity resolution)
      → Scoring engine   (mentions · acceleration · durability)
        → 2×2 classifier
          → Output (top 1–3 trends + evidence + prediction store)
            → Snapshot store (daily baseline for acceleration calculation)
```

### The QueryRouter

The system's agentic core. Given a field and time window, the router decides:

- Which sources to query and in what order
- Whether initial results warrant deeper search (retry with refined queries)
- How to handle conflicting signals across sources
- When evidence is sufficient to score vs. when to gather more

The router is not a fixed pipeline. It selects tools dynamically based on what it finds, retrying with narrower queries when initial results are too broad and broadening when evidence is sparse.

**Rate limit handling:** The router tracks API budget per run and falls back gracefully — if GitHub is rate-limited mid-run, it completes scoring on available signals and flags the gap in the output rather than failing silently.

---

## The Phases

This system is built in stages, each introducing more real-world complexity.

### Phase 1 — Mentions

> "What's being talked about?"

- Count mentions across sources
- Rank by frequency and recency
- Deduplicate near-identical content via **LLM-assisted semantic clustering**

Raw mentions are not clustered by keyword match. "AI agents," "autonomous coding agents," and "multi-agent frameworks" are semantically related but lexically distinct — naive deduplication misses the overlap and inflates apparent trend count. A dedicated clustering pass groups mentions by inferred concept before scoring begins.

**Output:** Top 1–3 most discussed themes

**Limitation:** Surfaces hype as easily as substance. The clustering step adds latency and token cost — treat this as a real constraint, not a footnote.

---

### Phase 2 — Acceleration

> "What's growing fastest?"

- Measure rate of change in mentions over the window
- Identify spikes vs. steady growth
- Weight recent mentions more heavily than older ones
- Normalize signals across sources before combining

**Cross-source normalization:** GitHub stars and HN points exist on different scales with different decay rates. A viral HN post at 340 points and a repo gaining 4,200 stars in a week are not directly comparable without normalization. The scoring engine applies log scaling to compress outliers and z-score normalization within each source before aggregating — so no single source can dominate the acceleration score by virtue of scale alone.

**Snapshotting:** Acceleration requires historical data. The system runs a daily cron job that passively snapshots baseline metrics for tracked domains (star counts, submission rates, comment velocity) even when no trend query is active. Without this, acceleration can only be calculated after the system has been observing a domain for at least one full window.

**Output:** Trends ranked by momentum, not just volume

**Limitation:** Fast growth ≠ long-term importance.

---

### Phase 3 — Durability Heuristics

> "What has signs of lasting?"

Acceleration gets you early. Durability tells you whether to act.

Each signal below is a proxy for something real:

| Signal | Why it matters | How it's measured |
|--------|---------------|-------------------|
| Builder activity | Builders invest time, not attention | New repos + GitHub star velocity (7d) |
| Adoption quality | Who engages determines staying power | HN point velocity; ratio of technical to general commentary |
| Discourse depth | Volume of complaints ≠ signal; depth of analysis does | Average comment depth; presence of implementation-level discussion vs. hype |
| Cross-platform presence | Real trends appear independently across communities | Overlap between HN, Reddit, academic preprints, GitHub |
| Problem anchoring | Named use cases outlast demos | Presence of production case studies or customer references |
| Composability | Others building on it signals ecosystem formation | Dependent repos; downstream forks; API wrapper count |

**Sentiment is a filter, not a score.** A spike in mentions where the dominant sentiment is "this is broken" or "avoid this" is subtracted from the durability signal, not added to it. High volume of negative discourse gets penalized; high volume of analytical or implementation-focused discourse gets rewarded.

Each signal contributes to a `durability_score` (0–100). Weights are configurable and will be tuned by the feedback loop over time (see Phase 5).

---

### Phase 4 — Classification

> "What should I do about this?"

Each trend is placed on two axes:

- **Durability** — likelihood of persistence (0–100)
- **Acceleration** — current momentum (0–100)

Resulting in a 2×2 output:

|  | High Acceleration | Low Acceleration |
|--|---|---|
| **High Durability** | **Compounding** — act now | **Durable / Slow** — monitor |
| **Low Durability** | **Flash Trend** — watch, don't bet | **Ignore** |

The classification is the system's explicit bet. It is stored with a timestamp and revisited in Phase 5.

---

### Phase 5 — Feedback Loop

> "Was the system right?"

This is what transforms Trend Spotter from a reporting tool into a learning system.

**What gets stored** (per prediction):

```json
{
  "field": "AI agents",
  "window_end": "2025-03-01",
  "trend": "multi-agent orchestration frameworks",
  "scores": { "durability": 82, "acceleration": 91 },
  "classification": "Compounding",
  "evidence_snapshot": ["url1", "url2", "url3"]
}
```

**Evaluation** (run 30 and 90 days later):

- Did the trend's GitHub star velocity sustain or decay?
- Did cross-platform presence hold?
- Did production references accumulate or stall?

**What "correct" means:**

- Compounding → sustained growth across 2+ signals at 30d and 90d
- Flash Trend → spike followed by decay within 30d
- Durable / Slow → still present at 90d, acceleration ≤ original
- Ignore → absent from top signals at 30d

**How accuracy reshapes the system:**

Durability signal weights are updated based on which signals best predicted persistence in hindsight. A signal consistently correlated with 90-day survival gets upweighted; one that proved decorative gets downweighted.

**The cold start problem:** The feedback loop requires time to become useful. For the first 30–90 days, the system has no outcome data — initial weights are hand-tuned heuristics, and early outputs will be noisier than later ones. This is a known constraint, not a design flaw. The system improves monotonically as predictions mature.

This is the closest thing to ground truth available in a domain where the only answer key is time.

---

## Output Schema

```json
{
  "field": "AI agents",
  "window": "7d",
  "generated_at": "2025-03-08T14:22:00Z",
  "trends": [
    {
      "name": "multi-agent orchestration frameworks",
      "description": "Rapid ecosystem formation: new frameworks, upstream forks, and production case studies appearing faster than the hype cycle alone would predict.",
      "scores": {
        "durability": 82,
        "acceleration": 91
      },
      "classification": "Compounding",
      "trajectory": "rising",
      "durability_signals": {
        "builder_activity": 88,
        "adoption_quality": 79,
        "cross_platform_presence": 84,
        "problem_anchoring": 71,
        "composability": 90
      },
      "evidence": [
        { "url": "https://...", "source": "github", "signal": "star_velocity", "value": "+4200 in 7d" },
        { "url": "https://...", "source": "hackernews", "signal": "point_velocity", "value": "340 pts" },
        { "url": "https://...", "source": "web", "signal": "production_reference", "value": "3 case studies" }
      ],
      "prediction_id": "uuid-abc123"
    }
  ]
}
```

---

## Known Hard Problems

These are not edge cases. They are load-bearing execution risks that any serious implementation must address upfront.

**Entity resolution / semantic clustering** is the biggest Phase 1 bottleneck. Trend labels bleed into each other at the concept level — "AI agents," "autonomous agents," and "agentic coding" are the same trend seen from different angles. Keyword deduplication fails here. An LLM clustering pass is required before scoring, which adds latency and cost to every run. Budget for it.

**Cross-source normalization** is required before any signal aggregation. GitHub stars and HN points are not on the same scale and do not decay at the same rate. Without log scaling + z-score normalization within each source, a single viral post will dominate the acceleration score regardless of what the rest of the data says.

**The cold start problem** means Phase 5 is blind for the first 30–90 days. Initial durability weights are hand-tuned. Accept noisy early outputs and plan for a manual calibration period before automated weight tuning kicks in.

**API rate limits** are an operational constraint, not an edge case. Aggressively querying GitHub and HN across a 7-day window to calculate acceleration will hit limits. The QueryRouter must track API budget per run, implement exponential backoff, and degrade gracefully when a source is unavailable — flagging the gap rather than failing or silently omitting the signal.

---

## Design Principles

**Constrained output** — only 1–3 trends per field per window. Forced scarcity is a forcing function for judgment.

**Explainable scoring** — every score decomposes into named signals with individual values. No black-box rankings.

**Evidence-linked** — every trend is tied to real sources with the specific signal each source supports.

**Agentic, not scripted** — the QueryRouter decides tool order and retries dynamically. The pipeline adapts to what it finds, not a fixed execution sequence.

**Judgment over volume** — better to be directionally right than exhaustively complete.

**Predictions are first-class** — every output is a stored bet, not just a report. The system has no value without knowing whether it was right.

---

## What This Is Not

- Not a news aggregator
- Not a social media dashboard
- Not a real-time feed

This is:

> **a system for making small, explicit bets about what matters — and learning from whether those bets were right**

---

## Roadmap

| Phase | Status | Description |
|-------|--------|-------------|
| 1 — Mentions | Planned | Count and rank; LLM-assisted semantic clustering |
| 2 — Acceleration | Planned | Rate of change scoring; daily snapshot cron; cross-source normalization |
| 3 — Durability | Planned | Multi-signal quality scoring with sentiment filter |
| 4 — Classification | Planned | 2×2 output surface |
| 5 — Feedback loop | Planned | Prediction storage + accuracy evaluation at 30d/90d |
| 6 — Weight tuning | Future | Auto-adjust durability signal weights from outcomes |
| 7 — Cross-domain comparison | Future | Identify trends emerging in parallel across fields |
| 8 — API | Future | Downstream consumption by decision systems |

---

## License

MIT
