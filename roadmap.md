# Roadmap — Trend Spotter

This roadmap is structured as a sequence of capability layers, moving from simple detection → structured judgment → learning from outcomes.

Each phase is:
- independently useful
- intentionally incomplete
- a foundation for the next layer

---

## Dependency Chain

The phase order is not arbitrary. Each phase has a hard dependency on the one before it:

| Phase | Requires | What breaks without it |
|-------|----------|----------------------|
| 2 — Acceleration | Phase 1 snapshot store + trend objects | No baseline to measure rate of change against; acceleration scores are meaningless without historical reference |
| 3 — Durability | Phase 1 clustering + Phase 2 normalization | Durability scores without acceleration context can't distinguish rising structural trends from stable but stagnant ones |
| 4 — Classification | Phase 2 + Phase 3 scores | The 2×2 requires both axes; a single score produces a rank, not a decision |
| 5 — Feedback Loop | Phase 4 classifications + 30–90 days of predictions maturing | No predictions to evaluate; weight update has no outcome signal to learn from |
| 6 — Weight Tuning | Phase 5 outcome data (minimum ~50 evaluated predictions) | Insufficient sample for correlation; tuning against sparse data risks overfitting to noise |
| 7 — Cross-Domain | Phase 3–4 running reliably across multiple domains | Cross-domain comparison requires consistent per-domain trend objects with comparable scoring |
| 8 — API | Phases 1–4 stable and tested | Exposing an unstable pipeline externally creates integration debt that blocks downstream consumers |

**Critical path note:** Phase 5 has a structural lag that is not implementation time — it is clock time. Predictions must be stored in Phase 4 and then allowed to mature for 30–90 days before evaluation is meaningful. Phase 5 can be *built* in parallel with Phase 4, but it cannot produce useful output until ~90 days after Phase 4 ships. Plan for this gap explicitly.

---

## Effort Signals

Rough estimates. These reflect implementation complexity, not calendar time, and assume a single focused builder.

| Phase | Effort | Notes |
|-------|--------|-------|
| 1 — Mentions | 2–3 weeks | LLM clustering is the primary unknown; budget time for prompt iteration |
| 2 — Acceleration | 1–2 weeks | Snapshot store is infrastructure-heavy upfront; scoring math is straightforward |
| 3 — Durability | 2–3 weeks | Six signals, each requiring its own extraction logic; sentiment filter adds complexity |
| 4 — Classification | 3–5 days | Mostly wiring Phase 2 + 3 outputs; threshold tuning takes iteration |
| 5 — Feedback Loop | 1–2 weeks | Build is fast; useful output requires 30–90 days of clock time after Phase 4 ships |
| 6 — Weight Tuning | 1–2 weeks | Requires ~50 evaluated predictions before running; timing depends on Phase 5 data volume |
| 7 — Cross-Domain | 2–3 weeks | Requires schema normalization across domains; definition of done needs further scoping |
| 8 — API | 1–2 weeks | Straightforward if pipeline is stable; contract design is the main design decision |

**Phases 1–4** can be sequenced tightly — the system becomes usable at end of Phase 4.  
**Phase 5** should be built during Phase 4 so predictions start accumulating immediately at launch.  
**Phases 6–8** are post-validation work; do not start until Phase 5 has produced at least one evaluation cycle.

---

## Phase 1 — Mentions (Detection)

### Objective
Establish a working pipeline that extracts candidate trends from raw data.

### Capabilities
- QueryRouter retrieves signals from:
  - web (articles, blogs, news)
  - GitHub (repos, stars)
  - Hacker News (posts, comments)
- LLM-assisted semantic clustering
  - group related mentions into unified trend objects
- Deduplication across sources
- Rank trends by frequency + recency

### Output
- Top 1–3 trends per field per time window
- Evidence links per trend

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| How noisy raw signals are | If >50% of clustering outputs require manual correction, redesign the clustering prompt before proceeding to Phase 2 |
| Failure modes in clustering (over/under-grouping) | Track merge errors and split errors separately; if either exceeds 15% on a 50-query eval set, treat as a Phase 1 defect |
| Cost + latency of LLM-based deduplication | If P95 latency exceeds 90s in Phase 1 alone, re-architect before adding Phase 2–3 overhead |

### Definition of Done
- Given a field + window, system consistently returns 1–3 coherent trends
- No obvious duplicate trends in output
- Each trend has at least 2 supporting sources
- Clustering accuracy ≥ 85% on held-out eval set of 50 manually labeled cluster groups

---

## Phase 2 — Acceleration (Momentum)

### Objective
Move from "what is talked about" → "what is rising."

**Why Phase 2 before Phase 3:** Durability scoring without acceleration context produces a static picture. You can identify structurally strong trends but cannot distinguish one that is rising from one that peaked 6 months ago. Acceleration is the temporal layer that makes durability actionable.

### Infrastructure: Snapshot Store

Phase 2 requires a daily cron job that runs regardless of user queries. This is not optional — without baseline snapshots, rate-of-change cannot be computed.

Snapshots stored per tracked domain:

| Source | Metrics |
|--------|---------|
| GitHub | stars, forks, repo creation rate |
| Hacker News | submission frequency, point velocity, comment depth |
| Web | article count per domain per day |

The snapshot store should be built and running **before** Phase 2 scoring is implemented — it needs at least 7 days of data before a 7d acceleration window is computable.

### Capabilities
- Acceleration calculation:
  - rate of change over selected window vs snapshot baseline
  - recency weighting (recent signals weighted more heavily)
- Cross-source normalization:
  - log scaling to compress outliers
  - z-score normalization within each source
  - prevents any single source from dominating by scale

### Output
- Trends ranked by momentum, not just volume
- Acceleration score (0–100) per trend

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| Which sources dominate without normalization | Validate that no single source accounts for >60% of acceleration score variance after normalization is applied |
| Differences between spikes vs sustained growth | If the system cannot distinguish a 1-day spike from a 7-day trend at the same total volume, the recency weighting function needs adjustment |
| Sensitivity of acceleration to window size | Document the observed sensitivity; surface it in output so users understand that 1d and 7d windows will often produce different rankings |

### Definition of Done
- Acceleration score reflects observable momentum differences between trends
- No single source dominates scoring unfairly (validated by signal attribution breakdown)
- Historical snapshots enable calculation across all supported windows
- Snapshot store has been running for ≥ 7 days before acceleration scoring is enabled

---

## Phase 3 — Durability (Structural Signal)

### Objective
Introduce signals that approximate long-term persistence.

**Why Phase 3 before Phase 4:** The 2×2 classifier requires both axes. Acceleration alone produces a momentum rank. Adding durability is what creates the decision surface — the distinction between a Flash Trend (high acceleration, low durability) and a Compounding signal (high on both) cannot be made without it.

### Capabilities
- Durability scoring across six named signals:

| Signal | What it proxies | How it's measured |
|--------|----------------|-------------------|
| Builder activity | Time investment, not attention | New repos + GitHub star velocity (7d) |
| Adoption quality | Practitioner vs spectator engagement | HN point velocity; ratio of technical to general commentary |
| Discourse depth | Analytical vs performative discussion | Average comment depth; presence of implementation-level content |
| Cross-platform presence | Independent emergence across communities | Signal overlap: HN + Reddit + GitHub + preprints |
| Problem anchoring | Real use cases, not demos | Count of production case studies or named customer references |
| Composability | Ecosystem formation | Dependent repos; downstream forks; API wrapper count |

- Sentiment as a multiplicative penalty filter (not an additive signal):
  - dominant negative sentiment reduces durability score
  - dominant analytical sentiment leaves score unchanged
  - volume of praise without implementation depth is treated neutrally

### Output
- Durability score (0–100)
- Decomposed signal contributions per trend
- Sentiment flag where applicable

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| Which signals correlate with "real" trends | Note per-signal variance at Phase 3 exit; this is the pre-tuning baseline that Phase 6 will improve against |
| Which signals are noisy or decorative | Any signal with near-zero variance across outputs should be flagged for review before Phase 6 weight tuning |
| Tradeoffs between interpretability vs complexity | If users cannot explain why a trend scored high on durability from the decomposed output, the signal breakdown is not granular enough |

### Definition of Done
- Durability scores are explainable via component signals
- Clear separation in scores between hype-driven and structurally-backed trends on a test set
- Signal breakdown included in every output
- All 6 signals instrumented and producing non-trivial variance

---

## Phase 4 — Classification (Decision Layer)

### Objective
Convert scores into actionable judgment.

**Why Phase 4 before Phase 5:** Classifications are the unit of prediction. The feedback loop evaluates whether the classification was correct — not whether the raw scores were accurate. Phase 4 must be shipping classifications before Phase 5 can begin accumulating predictions to evaluate.

**Build Phase 5 storage now:** The prediction store should be built and active at Phase 4 launch — not after. Every classification output from day one should be written to the prediction store. Delaying this delays the Phase 5 evaluation cycle by exactly as long as you wait.

### Capabilities
- 2×2 classification:
  - axes: durability (0–100) vs acceleration (0–100)
  - initial thresholds: High ≥ 65, Low < 65 (tunable)
- Labels:
  - **Compounding** — act now
  - **Durable / Slow** — monitor
  - **Flash Trend** — watch without committing
  - **Ignore** — deprioritize
- Trajectory detection: rising / stable / declining

### Output
- 1–3 trends with classification, scores, trajectory, evidence, and `prediction_id`

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| Whether classification aligns with human intuition | Run 10 classifications past a domain expert before launch; if >3 feel wrong, revisit thresholds |
| Edge cases (high acceleration, ambiguous durability) | Document observed edge cases; build explicit handling for ambiguous quadrant proximity before Phase 5 |
| Sensitivity of thresholds | Test ±10 point threshold shifts; if classification changes on >30% of outputs, thresholds are too sensitive and scoring variance needs reduction |

### Definition of Done
- Every trend has a clear classification
- Classification is consistent across similar inputs (tested with near-duplicate queries)
- Prediction store is active and writing from day one of Phase 4 launch
- Output supports immediate interpretation without additional analysis

---

## Phase 5 — Feedback Loop (Learning System)

### Objective
Introduce time-delayed evaluation and establish the system's ability to measure its own accuracy.

**The clock time constraint:** Phase 5 can be built during Phase 4 — the prediction store, evaluation job logic, and correctness criteria can all be implemented before any predictions have matured. But the system cannot produce meaningful accuracy metrics until 30 days after Phase 4 ships (for 30d evaluations) and 90 days after (for 90d evaluations). This is not a build constraint — it is a reality constraint. Plan milestones accordingly.

### Capabilities
- Stored prediction schema (written at Phase 4 output time):

```json
{
  "prediction_id": "uuid",
  "field": "AI agents",
  "window": "7d",
  "window_end": "2025-03-08",
  "trend": "multi-agent orchestration frameworks",
  "scores": { "durability": 82, "acceleration": 91 },
  "durability_signals": { "builder_activity": 88, "adoption_quality": 79, "..." },
  "classification": "Compounding",
  "evidence_snapshot": [{ "url": "...", "source": "github", "signal": "star_velocity" }],
  "created_at": "2025-03-08T14:22:00Z",
  "evaluated_at_30d": null,
  "evaluated_at_90d": null
}
```

- Scheduled evaluation jobs at 30d and 90d post `window_end`
- Re-queries same signals and compares against snapshot baseline

### Correctness Criteria

| Classification | Correct at 30d | Correct at 90d |
|---------------|----------------|----------------|
| Compounding | Growth sustained across ≥ 2 signals | Growth sustained or accelerated |
| Durable / Slow | Still present; acceleration ≤ original | Still present |
| Flash Trend | Spike has decayed | Largely absent |
| Ignore | Absent from top signals | Absent from top signals |

Outcomes are marked: **correct**, **incorrect**, or **ambiguous** (signal present but below threshold).

### Output
- Accuracy metrics by classification at 30d and 90d
- Historical record of predictions and outcomes
- Per-signal predictive correlation (input to Phase 6)

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| Which signals actually predict persistence | Record per-signal correlation with correct outcomes; hand off directly to Phase 6 |
| System biases (false positives / false negatives) | If Flash Trend false positive rate > 40%, the acceleration threshold is too low; adjust before Phase 6 |
| Lag between signal emergence and validation | If 90d outcomes are systematically ambiguous (>25% ambiguous rate), revisit correctness criteria definitions |

### Definition of Done
- Predictions consistently evaluated at 30d and 90d
- Accuracy metrics available per classification
- System can identify and record its own errors
- At least one full evaluation cycle complete before Phase 6 begins

---

## Phase 6 — Weight Tuning (Adaptive Model)

### Objective
Update durability scoring based on observed outcomes. This phase should not begin until Phase 5 has produced at least ~50 evaluated predictions — below that threshold, correlation estimates are unreliable and tuning risks overfitting to noise.

### Capabilities
- Per-signal predictive correlation computed over trailing N predictions (initial N = 50)
- Weight update algorithm:
  - signals with high 90d-survival correlation get upweighted
  - signals consistently uncorrelated with persistence get downweighted
  - bounds: floor 0.5×, ceiling 2× current weight (prevents runaway dominance)
- Versioned weight configurations (every update stored with timestamp and sample size)

### Output
- Updated durability scoring model
- Versioned weight configurations with change log
- Before/after accuracy comparison per update

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| Which proxies are actually predictive | Any signal falling below 0.5× weight after two consecutive update cycles should be considered for removal |
| Stability vs volatility of signal importance | If weights oscillate significantly between update cycles, increase N before next update |
| Risk of overfitting to recent outcomes | Use a rolling window, not cumulative — recent outcomes should be weighted more than outcomes from 18 months ago |

### Definition of Done
- Durability weights updated from real outcome data (minimum 50 evaluated predictions)
- Classification accuracy at 90d improves ≥ 5 percentage points vs hand-tuned baseline
- Weight changes are explainable and traceable via version log
- No single signal has been tuned to weight > 2× its original value without explicit review

---

## Phase 7 — Cross-Domain Analysis

### Objective
Identify trends emerging in parallel across multiple domains — a signal that a structural shift is broader than any single field.

**Status:** Design requires further scoping. The capabilities below are directional; exit criteria and effort estimates should be revisited once Phases 1–4 are stable across ≥ 3 distinct domains.

### Capabilities
- Cross-domain query: run scoring across multiple fields simultaneously
- Parallel emergence detection: flag trends appearing independently in ≥ 2 domains within the same time window
- Domain-specific vs universal signal classification

### Output
- Cross-domain trend summaries
- Trends flagged as multi-domain with per-domain evidence

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| Transferability of signals across domains | If durability signals weight very differently by domain (e.g. GitHub star velocity is irrelevant for healthcare), domain-specific weight profiles may be needed |
| Early indicators of broad structural shifts | Define a threshold for "universal" emergence (≥ 3 domains, independent signals) before building the detection logic |

### Definition of Done
- System can identify trends appearing across ≥ 2 domains with independent evidence per domain
- Cross-domain signals are surfaced distinctly from domain-specific ones
- Schema is consistent enough that per-domain trend objects are directly comparable

---

## Phase 8 — API & Integration

### Objective
Expose Trend Spotter as a stable, consumable interface for downstream systems.

**Status:** Should not begin until Phases 1–4 are in production and stable. API design is a commitment — an unstable pipeline creates integration debt that blocks downstream consumers and is expensive to version away.

### Capabilities
- REST API endpoint:
  - `POST /trends` — input: `{ field, time_window }`, output: structured trend objects per schema
  - `GET /predictions/{id}` — retrieve stored prediction and evaluation status
- Documented response schema with versioning
- Rate limiting and graceful error responses

### Output
- Stable API with documented schema
- External consumption of trend data without transformation

### Key Learnings — and What to Do with Them

| Learning | Decision gate |
|----------|--------------|
| How downstream systems use outputs | Conduct at least 2 consumer interviews before finalizing the API schema — assumptions about downstream use are frequently wrong |
| Latency and reliability requirements | If consumers require < 5s response time, the synchronous query model will not work; evaluate async + webhook pattern before committing to a contract |
| Integration constraints | Identify whether consumers need raw signal data or only classified output; this determines how much of the internal schema to expose |

### Definition of Done
- API returns consistent, structured results matching the documented schema
- External systems can consume output without transformation
- Error responses are explicit and actionable (not generic 500s)
- Latency SLA is defined and met under representative load
- At least one external consumer integrated before marking complete

---

## Guiding Principle

Each phase moves the system from:

> **describing the present → making and evaluating bets about the future**

Progress is measured not by feature count, but by:

> **how well the system learns from being wrong**
