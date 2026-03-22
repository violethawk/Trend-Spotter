# PRD — Trend Spotter

## 1. Overview

Trend Spotter is a system that identifies and evaluates emerging trends within a specific field across defined time horizons.

Given a domain and time window, the system returns **1–3 high-signal trends**, each supported by evidence and scored for **durability (likelihood to persist)** and **acceleration (current momentum)**.

The goal is not to surface what is popular, but to:
> **identify what matters early enough to act**

---

## 2. Problem

The internet produces a continuous stream of “trends,” most of which are:
- short-lived
- redundant
- driven by attention rather than substance

Existing tools (news aggregators, social feeds, Google Trends) optimize for:
- volume
- recency
- engagement

They do not answer:
> **Which emerging signals are likely to persist?**

As a result, operators (builders, investors, analysts) face:
- high noise
- delayed insight
- poor early decision-making

---

## 3. Users

### Primary User

**Operator exploring a domain under uncertainty**

Examples:
- engineer evaluating what to build
- investor scanning emerging narratives
- strategist monitoring a sector

---

## 4. Core Use Cases

1. **Scan a domain quickly**
   - Input: `"AI agents"`, window = `7d`
   - Output: 1–3 trends with evidence

2. **Differentiate signal from hype**
   - Identify trends with structural backing vs. attention spikes

3. **Track emerging shifts over time**
   - Compare outputs across 1d / 7d / 30d windows

4. **Support early decisions**
   - build vs. ignore
   - invest vs. wait
   - explore vs. deprioritize

---

## 5. Product Requirements

### Input

- `field` (string)
- `time_window` (enum: 1d, 7d, 30d, 90d, 365d)

---

### Output

Return **1–3 trends**, each with:

- `name`
- `description` (1–2 sentences)
- `scores`
  - durability (0–100)
  - acceleration (0–100)
- `classification`
  - Compounding
  - Durable / Slow
  - Flash Trend
  - Ignore
- `trajectory`
  - rising / stable / declining
- `evidence` (2–5 links with labeled signals)

---

### Constraints

- **Hard cap of 3 trends per query**
- **All outputs must include supporting evidence**
- **Scores must be explainable via decomposed signals**
- **System must degrade gracefully under missing data (no silent failure)**

---

## 6. System Behavior

### Pipeline

1. QueryRouter selects sources (web, GitHub, HN)
2. Retrieve and aggregate signals
3. Perform semantic clustering (LLM-assisted)
4. Deduplicate themes into trend candidates
5. Score each candidate:
   - mentions
   - acceleration
   - durability
6. Rank and select top 1–3 trends
7. Return structured output with evidence
8. Store prediction for future evaluation

---

## 7. Scoring Model

### Acceleration

Measures current momentum:
- rate of change in mentions
- cross-source normalized growth
- recent weighting

---

### Durability

Measures likelihood of persistence via:

- builder activity
- adoption quality
- discourse depth
- cross-platform presence
- problem anchoring
- composability

Each signal contributes to a weighted score (0–100)

---

## 8. Classification Logic

|                | High Acceleration | Low Acceleration |
|----------------|------------------|------------------|
| High Durability | Compounding      | Durable / Slow   |
| Low Durability  | Flash Trend      | Ignore           |

Classification represents the system’s **explicit prediction**.

---

## 9. Success Metrics

### Short-term (system quality)

- % of outputs with valid supporting evidence
- clustering accuracy (manual evaluation)
- reduction in duplicate trends

---

### Medium-term (predictive quality)

- % of trends correctly classified at 30 days
- % of trends correctly classified at 90 days

---

### Long-term (user value)

- decision impact (qualitative)
- repeat usage across domains

---

## 10. Non-Goals

- Real-time news aggregation  
- Exhaustive trend coverage  
- Fully automated “perfect” predictions  
- High-frequency trading or latency-sensitive use cases  

---

## 11. Risks

- **Entity resolution failure** → fragmented trends  
- **Cross-source imbalance** → skewed scoring  
- **Cold start problem** → no feedback loop early  
- **API rate limits** → incomplete signal set  

Mitigation:
- LLM clustering
- normalization
- explicit degradation + flagging
- phased rollout

---

## 12. Roadmap

- Phase 1: Mentions-based ranking  
- Phase 2: Acceleration scoring  
- Phase 3: Durability heuristics  
- Phase 4: 2×2 classification  
- Phase 5: Feedback loop (prediction tracking)  
- Phase 6: Weight tuning from outcomes  

---

## 13. Guiding Principle

> **Better to surface 2 correct signals than 20 plausible ones**

Trend Spotter is designed for:
- **clarity over coverage**
- **judgment over aggregation**
- **learning over static output**
