# PRD — Trend Spotter

## 1. Overview

Trend Spotter is a system that identifies and evaluates emerging trends within a specific field across defined time horizons.

Given a domain and time window, the system returns **1–3 high-signal trends**, each supported by evidence and scored for:

- **Durability** — likelihood the trend will persist
- **Acceleration** — current momentum

The system's goal is not to surface what is popular, but to:

> **identify what matters early enough to act**

---

## 2. Problem

The internet produces a continuous stream of "trends," most of which are:

- short-lived
- redundant
- driven by attention rather than substance

Existing tools (news aggregators, social feeds, search trends):

- optimize for **volume and recency**
- do not evaluate **structural strength or persistence**

As a result, operators:

- react late (after consensus forms)
- over-index on hype cycles
- struggle to distinguish signal from noise

---

## 3. Primary User

### Operator: Builder Evaluating What to Build

The primary user is a technical operator deciding where to invest time and effort.

Examples:
- engineer choosing what product or feature to build
- founder evaluating which emerging space to enter
- technical lead scanning for high-leverage opportunities

### Core Need

> Identify early signals that are structurally real, not just visible

### Decision Context

- build vs ignore
- explore vs deprioritize
- commit vs wait

### Product Implications

- outputs must be **constrained (1–3 trends)**
- evidence must be **concrete and actionable**
- scoring must be **transparent and explainable**

---

## 4. Core Use Cases

1. **Rapid domain scan** — Input: `"AI agents"`, window = `7d`. Output: 1–3 trends with evidence.
2. **Signal vs hype differentiation** — Identify structurally backed trends vs attention spikes.
3. **Time-horizon comparison** — Compare trends across 1d / 7d / 30d windows.
4. **Decision support** — Determine whether to build, explore, or ignore.

---

## 5. Product Requirements

### Input

| Field | Type | Notes |
|-------|------|-------|
| `field` | string | e.g. `"AI agents"`, `"healthcare AI"` |
| `time_window` | enum | `1d`, `7d`, `30d`, `90d`, `365d` |

---

### Output

Return **1–3 trends**, each with:

| Field | Type | Notes |
|-------|------|-------|
| `name` | string | Short label for the trend |
| `description` | string | 1–2 sentences |
| `scores.durability` | int 0–100 | Likelihood of persistence |
| `scores.acceleration` | int 0–100 | Current momentum |
| `durability_signals` | object | Named sub-scores (see Section 7) |
| `classification` | enum | Compounding / Durable Slow / Flash Trend / Ignore |
| `trajectory` | enum | rising / stable / declining |
| `evidence` | array | 2–5 items, each with `url`, `source`, `signal`, `value` |
| `data_gaps` | array | Sources that were unavailable or rate-limited |
| `prediction_id` | uuid | Links this output to the prediction store |

---

### Constraints

- Hard cap of **3 trends per query**
- All outputs must include **supporting evidence**
- Scores must be **decomposable into named signals**
- System must **degrade gracefully** under missing data
- System must **never silently omit signals** — flag gaps explicitly in `data_gaps`
- System must **never fabricate completeness** — fewer trends is correct behavior under sparse data

---

## 6. System Architecture

```
User (field + time window)
  → QueryRouter
      ├─ web_search   (articles, blogs, news)
      ├─ github_api   (repos, stars, forks)
      └─ hn_api       (posts, comments, engagement)
    → Raw signal objects
      → Semantic clustering (LLM-assisted entity resolution)
        → Trend objects (deduplicated, named)
          → Scoring engine (mentions · acceleration · durability)
            → Scored trend objects
              → 2×2 classifier
                → Classified trend objects
                  → Output (top 1–3 trends + evidence + data_gaps)
                    → Prediction store
Snapshot store (daily cron → baseline data for acceleration)
```

### Data Contracts Between Pipeline Stages

Each stage consumes and emits a typed object. These contracts define what engineers implement — nothing passes between stages as unstructured text.

**Raw signal object** (QueryRouter → Clustering)
```json
{
  "source": "github | hackernews | web",
  "url": "https://...",
  "title": "...",
  "text_snippet": "...",
  "signal_type": "repo_creation | star_velocity | submission | article",
  "value": 4200,
  "unit": "stars_7d",
  "retrieved_at": "2025-03-08T14:00:00Z"
}
```

**Trend object** (Clustering → Scoring)
```json
{
  "cluster_id": "uuid",
  "label": "multi-agent orchestration frameworks",
  "raw_signals": ["<signal_id>", "..."],
  "source_count": 3,
  "first_seen": "2025-03-01T00:00:00Z",
  "last_seen": "2025-03-08T14:00:00Z"
}
```

**Scored trend object** (Scoring → Classifier)
```json
{
  "cluster_id": "uuid",
  "label": "...",
  "scores": {
    "acceleration": 91,
    "durability": 82
  },
  "durability_signals": {
    "builder_activity": 88,
    "adoption_quality": 79,
    "discourse_depth": 74,
    "cross_platform_presence": 84,
    "problem_anchoring": 71,
    "composability": 90
  },
  "evidence": [
    { "url": "https://...", "source": "github", "signal": "star_velocity", "value": "+4200 in 7d" }
  ]
}
```

**Classified trend object** (Classifier → Output)

Extends scored trend object with:
```json
{
  "classification": "Compounding",
  "trajectory": "rising",
  "prediction_id": "uuid-abc123"
}
```

---

### QueryRouter

The QueryRouter dynamically determines:

- which sources to query
- query order and depth
- when to retry with refined queries
- when evidence is sufficient to proceed

#### Behavior

- prioritizes high-signal sources first
- retries with narrower queries if results are too broad
- broadens search if signals are sparse
- operates within a bounded execution budget (see Section 9)

#### Rate Limit Handling

- tracks API budget per run
- applies exponential backoff and per-source fallback
- completes scoring with partial data if necessary
- records unavailable sources in `data_gaps` — never silently omits them

---

### Snapshot Store (Baseline Data Layer)

Acceleration requires historical baselines. Without this layer, rate-of-change cannot be computed reliably.

A **daily cron job** snapshots per tracked domain:

| Source | Metrics stored |
|--------|---------------|
| GitHub | stars, forks, repo creation rate |
| Hacker News | submission frequency, point velocity, comment depth |
| Web | article count per domain per day |

#### Purpose

- enables acceleration calculation across all time windows
- prevents cold-start bias on first query for a new domain
- reduces repeated API calls during active queries

---

## 7. Scoring Model

### Acceleration

Measures current momentum relative to baseline:

- rate of change in mentions vs. snapshot baseline
- recency-weighted (recent signals weighted more heavily)
- cross-source normalized before aggregation

#### Normalization

Raw signals from different sources are not directly comparable. A GitHub repo gaining 4,200 stars and an HN post at 340 points exist on different scales with different decay rates.

Normalization approach:
1. **Log scaling** within each source to compress outliers
2. **Z-score normalization** per source relative to that source's historical distribution
3. **Weighted aggregation** across sources (weights tuned by feedback loop over time)

This prevents any single source from dominating the acceleration score by virtue of scale alone.

---

### Durability

Measures likelihood of persistence across six named signals:

| Signal | What it proxies | How it's measured |
|--------|----------------|-------------------|
| Builder activity | Time investment, not attention | New repos + GitHub star velocity (7d) |
| Adoption quality | Who is engaging | HN point velocity; ratio of technical to general commentary |
| Discourse depth | Substance vs hype | Average comment depth; presence of implementation-level discussion |
| Cross-platform presence | Independent emergence | Signal overlap: HN + Reddit + GitHub + preprints |
| Problem anchoring | Real use cases exist | Count of production case studies or named customer references |
| Composability | Ecosystem formation | Dependent repos; downstream forks; API wrapper count |

Each signal contributes a sub-score (0–100). The `durability_score` is a weighted average. Weights start as hand-tuned heuristics and are updated by the feedback loop (see Section 10).

#### Sentiment Filter

Sentiment is applied as a **multiplicative penalty**, not a positive signal:

- dominant negative sentiment (complaints, "avoid this", "broken") reduces durability score
- dominant analytical or implementation-focused sentiment leaves score unchanged
- high praise without implementation depth is treated neutrally — not rewarded

The filter prevents a trend from scoring high on durability simply because it is loudly discussed.

---

## 8. Classification

Each scored trend is placed on two axes and assigned a quadrant:

|  | High Acceleration | Low Acceleration |
|--|---|---|
| **High Durability** | **Compounding** — act now | **Durable / Slow** — monitor |
| **Low Durability** | **Flash Trend** — watch, don't bet | **Ignore** |

Thresholds (initial, tunable):
- High: score ≥ 65
- Low: score < 65

The classification is the system's **explicit bet**, stored in the prediction store with a timestamp for future evaluation.

---

## 9. Performance Requirements

### Latency Targets

| Percentile | Target |
|-----------|--------|
| P50 | 15–30 seconds |
| P95 | ≤ 60 seconds |
| Hard timeout | 90 seconds |

### Execution Model

- synchronous query with bounded execution window
- under time pressure, QueryRouter may limit source count or skip retries
- partial results are valid outputs — missing data is flagged, not hidden

### Degradation Tiers

| Condition | Behavior |
|-----------|----------|
| One source rate-limited | Score on remaining sources; flag in `data_gaps` |
| Snapshot baseline missing | Omit acceleration score; note in output |
| Fewer than 3 clusterable trends found | Return fewer trends; do not pad |
| Hard timeout hit | Return best available scored trends at cutoff |

---

## 10. Feedback Loop

This is what transforms Trend Spotter from a reporting tool into a learning system.

### Stored Prediction Schema

```json
{
  "prediction_id": "uuid-abc123",
  "field": "AI agents",
  "window": "7d",
  "window_end": "2025-03-08",
  "trend": "multi-agent orchestration frameworks",
  "scores": {
    "durability": 82,
    "acceleration": 91
  },
  "durability_signals": {
    "builder_activity": 88,
    "adoption_quality": 79,
    "discourse_depth": 74,
    "cross_platform_presence": 84,
    "problem_anchoring": 71,
    "composability": 90
  },
  "classification": "Compounding",
  "evidence_snapshot": [
    { "url": "https://...", "source": "github", "signal": "star_velocity", "value": "+4200 in 7d" }
  ],
  "created_at": "2025-03-08T14:22:00Z",
  "evaluated_at_30d": null,
  "evaluated_at_90d": null
}
```

### Evaluation Jobs

Run automatically at 30d and 90d after `window_end`. For each prediction, re-query the same signals and compare to snapshot baseline.

### Correctness Criteria (per classification)

| Classification | Correct at 30d | Correct at 90d |
|---------------|----------------|----------------|
| Compounding | Growth sustained across ≥ 2 signals | Growth sustained or accelerated |
| Durable / Slow | Still present; acceleration ≤ original | Still present at 90d |
| Flash Trend | Spike has decayed | Largely absent from top signals |
| Ignore | Absent from top signals | Absent from top signals |

A prediction is marked **correct**, **incorrect**, or **ambiguous** (signal present but below threshold).

### Weight Update Mechanism

After each evaluation cycle:

1. For each durability signal, compute its correlation with correct 90d predictions over the trailing N predictions (initially N=50, grows over time)
2. Signals with consistently high predictive correlation get upweighted
3. Signals consistently uncorrelated with 90d outcomes get downweighted
4. Weights are bounded (floor: 0.5×, ceiling: 2×) to prevent any single signal from dominating

### Cold Start Constraint

For the first 30–90 days, no evaluation data exists. Initial weights are hand-tuned heuristics. Early outputs will be noisier than post-calibration outputs. This is expected behavior — document it in the UI, not a bug to suppress.

---

## 11. Success Metrics

### Short-term — System Quality (measurable at launch)

| Metric | Target | Method |
|--------|--------|--------|
| Evidence coverage | ≥ 90% of outputs have ≥ 2 valid evidence links | Automated output validation |
| Clustering accuracy | ≥ 85% agreement on a held-out eval set of 50 manually labeled cluster groups | Human eval, run once per major clustering model change |
| Duplicate reduction | ≤ 10% of returned trends are semantically redundant with another returned trend | Human spot-check on 20 queries per week |
| Graceful degradation | 100% of runs with missing sources produce explicit `data_gaps` output | Automated contract test |

### Medium-term — Predictive Quality (measurable at 30d and 90d)

| Metric | Target | Method |
|--------|--------|--------|
| Classification accuracy at 30d | ≥ 60% correct on Compounding + Flash Trend predictions | Automated evaluation job |
| Classification accuracy at 90d | ≥ 65% correct on Compounding + Flash Trend predictions | Automated evaluation job |
| Ambiguous rate | ≤ 20% of evaluations marked ambiguous | Tracked in prediction store |

*Note: 60–65% is the initial bar, not the ceiling. Weight tuning should push this higher over time.*

### Long-term — User Value (measurable at 90d+)

| Metric | Target | Method |
|--------|--------|--------|
| Repeat usage | ≥ 40% of users run ≥ 3 queries within 30 days | Usage logs |
| Decision attribution | At least 1 confirmed build/explore decision per 10 queries (qualitative) | User interviews, quarterly |

---

## 12. Failure Handling

| Failure Mode | Behavior |
|-------------|----------|
| Source unavailable | Complete run on remaining sources; add source to `data_gaps` |
| Rate limit hit mid-run | Apply backoff; continue with partial data; flag in output |
| Clustering ambiguity | Merge conservatively — one broader trend over two uncertain ones |
| Insufficient data | Return fewer than 3 trends; never pad with low-confidence output |
| Hard timeout | Return best available scored trends at cutoff; mark output as `partial: true` |
| Snapshot baseline missing | Omit acceleration score for affected trend; note explicitly |

The system must never:
- fabricate completeness
- silently drop signals
- return a padded third trend when only two meet quality threshold

---

## 13. Non-Goals

These are explicit scope boundaries — not future work, but things this system will not do even if asked.

- **Real-time streaming** — the system runs on-demand queries, not continuous monitoring
- **Exhaustive trend coverage** — forced scarcity (1–3 trends) is a feature, not a limitation
- **Perfect prediction accuracy** — the system is calibrated for directional correctness, not precision
- **Multi-user accounts or saved preferences** — single operator, stateless sessions in v1
- **Scheduled / recurring queries** — no cron-triggered user queries; the snapshot store cron is infrastructure, not a user feature
- **Cross-domain comparison UI** — comparing "AI agents" vs "crypto" trends is future work (Phase 7)
- **Latency-sensitive or real-time use cases** — P50 of 15–30s is by design; this is not a dashboard widget

---

## 14. Known Risks

| Risk | Mitigation |
|------|-----------|
| Entity resolution errors | LLM-assisted semantic clustering; conservative merge on ambiguity |
| Cross-source score imbalance | Log scaling + z-score normalization per source |
| Cold start (no feedback data) | Hand-tuned initial weights; documented expected noise in early outputs |
| API rate limits | QueryRouter budget tracking; exponential backoff; graceful degradation |
| Clustering model drift | Periodic human eval against held-out set; re-calibrate if accuracy drops below threshold |

---

## 15. Roadmap

| Phase | Description | Exit Criteria |
|-------|-------------|---------------|
| 1 — Mentions | Count and rank; LLM-assisted semantic clustering | Clustering accuracy ≥ 85% on eval set |
| 2 — Acceleration | Rate of change scoring; daily snapshot cron; cross-source normalization | Acceleration scores present on ≥ 90% of queries with baseline data |
| 3 — Durability | Multi-signal quality scoring with sentiment filter | All 6 durability signals instrumented and decomposed in output |
| 4 — Classification | 2×2 output surface | Classified output on 100% of scored trends |
| 5 — Feedback loop | Prediction storage + 30d/90d evaluation jobs | First evaluation cycle completes; weight update runs once |
| 6 — Weight tuning | Auto-adjust durability signal weights from outcomes | Classification accuracy at 90d improves ≥ 5 points vs hand-tuned baseline |
| 7 — Cross-domain | Trends emerging in parallel across fields | TBD |
| 8 — API | Downstream consumption by decision systems | TBD |

---

## 16. Guiding Principle

> Better to surface 2 correct signals than 20 plausible ones
