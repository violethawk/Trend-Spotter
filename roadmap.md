# Roadmap — Trend Spotter

This roadmap is structured as a sequence of capability layers.  
Each phase introduces a new dimension of realism, moving from simple detection → structured judgment → learning from outcomes.

Each phase is:
- independently useful
- intentionally incomplete
- a foundation for the next layer

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
  - group related mentions into unified “trend objects”
- Deduplication across sources
- Rank trends by frequency + recency

### Output
- Top 1–3 trends per field per time window
- Evidence links per trend

### Key Learnings
- How noisy raw signals are
- Failure modes in clustering (over/under-grouping)
- Cost + latency of LLM-based deduplication

### Definition of Done
- Given a field + window, system consistently returns 1–3 coherent trends
- No obvious duplicate trends in output
- Each trend has at least 2 supporting sources

---

## Phase 2 — Acceleration (Momentum)

### Objective
Move from “what is talked about” → “what is rising”

### Capabilities
- Time-series tracking via daily snapshot store
  - GitHub stars, HN points, post frequency
- Acceleration calculation:
  - rate of change over selected window
  - recency weighting
- Cross-source normalization:
  - log scaling to compress outliers
  - z-score normalization within each source

### Output
- Trends ranked by momentum, not just volume
- Acceleration score (0–100) per trend

### Key Learnings
- Which sources dominate without normalization
- Differences between spikes vs sustained growth
- Sensitivity of acceleration to window size

### Definition of Done
- Acceleration score reflects observable momentum differences
- No single source (e.g. HN) dominates scoring unfairly
- Historical snapshots enable calculation across all supported windows

---

## Phase 3 — Durability (Structural Signal)

### Objective
Introduce signals that approximate long-term persistence

### Capabilities
- Durability scoring using:
  - builder activity (repos, commits, forks)
  - adoption quality (who is engaging)
  - discourse depth (analysis vs hype)
  - cross-platform presence
  - problem anchoring (real use cases)
  - composability (ecosystem formation)
- Sentiment as a filter (not additive signal)

### Output
- Durability score (0–100)
- Decomposed signal contributions per trend

### Key Learnings
- Which signals correlate with “real” trends
- Which signals are noisy or decorative
- Tradeoffs between interpretability vs complexity

### Definition of Done
- Durability scores are explainable via component signals
- Clear separation between hype-driven and structurally-backed trends
- Signal breakdown included in output

---

## Phase 4 — Classification (Decision Layer)

### Objective
Convert scores into actionable judgment

### Capabilities
- 2×2 classification:
  - axes: durability vs acceleration
- Labels:
  - Compounding
  - Durable / Slow
  - Flash Trend
  - Ignore
- Trajectory detection:
  - rising / stable / declining

### Output
- 1–3 trends with:
  - classification
  - scores
  - trajectory
  - supporting evidence

### Key Learnings
- Whether classification aligns with human intuition
- Edge cases (e.g. high acceleration, ambiguous durability)
- Sensitivity of thresholds

### Definition of Done
- Every trend has a clear classification
- Classification is consistent across similar inputs
- Output supports immediate interpretation (no additional analysis required)

---

## Phase 5 — Feedback Loop (Learning System)

### Objective
Introduce time-delayed evaluation and model improvement

### Capabilities
- Store predictions:
  - trend, scores, classification, evidence snapshot
- Scheduled evaluation (30d, 90d):
  - re-measure signals
  - compare against original classification
- Define correctness criteria per class:
  - Compounding → sustained multi-signal growth
  - Flash → spike then decay
  - Durable / Slow → persistence without acceleration
  - Ignore → disappearance from signal set

### Output
- Accuracy metrics by classification
- Historical record of predictions and outcomes

### Key Learnings
- Which signals actually predict persistence
- System biases (false positives / false negatives)
- Lag between signal emergence and validation

### Definition of Done
- Predictions consistently evaluated at 30d and 90d
- Accuracy metrics available per class
- System can identify its own errors

---

## Phase 6 — Weight Tuning (Adaptive Model)

### Objective
Update durability scoring based on observed outcomes

### Capabilities
- Adjust signal weights based on predictive success
- Downweight signals that correlate poorly with persistence
- Upweight signals that consistently predict 90-day survival

### Output
- Updated durability scoring model
- Versioned weight configurations

### Key Learnings
- Which proxies are actually predictive
- Stability vs volatility of signal importance
- Risk of overfitting to recent outcomes

### Definition of Done
- Durability weights updated from real outcome data
- Improved classification accuracy vs baseline
- Weight changes are explainable and traceable

---

## Phase 7 — Cross-Domain Analysis

### Objective
Identify patterns that emerge across multiple domains

### Capabilities
- Compare trends across fields
- Detect parallel emergence (same signal appearing in different domains)
- Identify domain-specific vs universal patterns

### Output
- Cross-domain trend summaries
- Signals with multi-domain persistence

### Key Learnings
- Transferability of signals across domains
- Early indicators of broad structural shifts

### Definition of Done
- System can identify trends appearing across ≥2 domains
- Cross-domain signals are surfaced distinctly from domain-specific ones

---

## Phase 8 — API & Integration

### Objective
Expose Trend Spotter as a system others can build on

### Capabilities
- API endpoint:
  - input: field + window
  - output: structured trend objects
- Integration hooks:
  - dashboards
  - decision support tools
  - internal analytics systems

### Output
- Stable API with documented schema
- External consumption of trend data

### Key Learnings
- How downstream systems use outputs
- Latency and reliability requirements
- Integration constraints

### Definition of Done
- API returns consistent, structured results
- External systems can consume output without transformation
- System handles production-level usage constraints

---

## Guiding Principle

Each phase moves the system from:

> **describing the present → making and evaluating bets about the future**

Progress is measured not by feature count, but by:

> **how well the system learns from being wrong**
