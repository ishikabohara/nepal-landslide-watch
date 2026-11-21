# Forecasting reported landslide cell-days in Nepal with antecedent satellite rainfall

**Research status:** retrospective experimental prototype, evaluated September 8, 2026.
**Fitted model:** `bipad-imerg-xgb-2025-3d1b520e6abe`.
**Evaluation target:** at least one BIPAD landslide report assigned to its recorded 0.1° cell during the next Nepal calendar day.

## Finding

The saved terrain, season, and antecedent-rainfall model contains useful ranking information for future reported cell-days, but its absolute alert precision is low. On January 16–December 31, 2025, average precision is **0.007166** against a reported-positive prevalence of **0.001012**. The relevant strong ablation is an XGBoost model with terrain and season: its average precision is **0.006792**. Adding rainfall increases average precision by **0.000373**, or approximately **5.50% relative**, in this one held-out year. That modest difference has not been established as statistically reliable.

At the threshold fixed from the 95th percentile of the 2024 calibration population, the main model detects **205 of 544** reported positive cell-days and generates **27,172 alerts without a matched report**. It misses **339** reported positive cell-days. These results justify further research and prospective monitoring, not public warning deployment or a claim of accurate physical landslide probabilities.

The evaluation script reconstructs 537,600 test predictions from actual feature arrays and the saved fitted model, then verifies that every reported scalar metric agrees with the model card. No model is retrained to generate the figures in this report. The agreement check and input hashes are saved in [evaluation_summary.json](../artifacts/evaluation/evaluation_summary.json).

## Question and unit of prediction

The research question is whether antecedent rainfall adds predictive information beyond static terrain and season when forecasting the next day's **recorded landslide locations**. A day is defined in `Asia/Kathmandu`. A prediction issued at 00:00 Nepal time on date D targets the entire following local calendar day D.

The spatial unit is a 0.1° cell, approximately ten kilometres across, intersected with the Nepal boundary. There are **1,536 modeled cells**. A cell-day is positive if one or more eligible BIPAD records have that date and recorded location in the cell; multiple records at the same cell and date collapse to one positive. Other evaluated cell-days are unlabeled background. They are not field-verified examples of physical stability.

This distinction changes the scientific interpretation. The model may learn both environmental susceptibility and where landslides are reported or assigned administrative coordinates. A high score does not locate an unstable slope within the cell. A low score does not establish safety.

## Data and label audit

| Input | Acquired data and role | Material limitation |
| --- | --- | --- |
| BIPAD hazard 17 | 6,142 public landslide incident records; primary label source | All 6,142 retained records have unconfirmed rainfall triggers and unreported positional accuracy |
| NASA COOLR Nepal | 587 reports retained for audit and map context; excluded from the primary training target | Different reporting process and varying location uncertainty; not silently pooled with BIPAD |
| NASA IMERG Early via dynamical.org | Actual 30-minute, 0.1° rainfall rates; complete 2018–2025 daily aggregates for research | Reprocessed satellite estimates, coarse spatial support, delivery lag, and a 2026 revision transition |
| Copernicus DEM GLO-90 | Public 2021 release, 90 m digital surface model aggregated within cells | Surface elevations include vegetation and structures; fine terrain resolution does not imply fine forecast resolution |
| geoBoundaries Nepal ADM0 | Public simplified national boundary | Boundary delineation and simplification affect edge cells |

The full label audit records **5,170 unique positive cell-days** and **972 duplicate records at an existing cell-day** across the complete BIPAD inventory. These are inventory-wide counts, not the number of training positives. The training partition contains **2,198** positive cell-days. No known earthquake-triggered records were excluded by the available trigger field; the absence of an explicit earthquake label does not demonstrate that earthquake-induced failures are absent.

BIPAD timestamps are treated conservatively as day-level labels. Approval or verification in a portal is not equivalent to accurate failure-site coordinates or verified trigger attribution. Event fields needed for analysis are retained; personal descriptions and contact details are not used.

Data sources: [BIPAD public incident API](https://bipadportal.gov.np/api/v1/incident/?hazard=17&limit=10000&ordering=id), [NASA COOLR service](https://gis.earthdata.nasa.gov/portal/rest/services/Landslides/COOLR_Reports_Points/FeatureServer/0), [IMERG replica documentation](https://dynamical.org/catalog/nasa-imerg-analysis-early/), [Copernicus DEM public archive](https://registry.opendata.aws/copernicus-dem/), and [Nepal boundary metadata](https://www.geoboundaries.org/api/current/gbOpen/NPL/ADM0/).

## Features and availability boundary

Every rainfall feature ends by **12:00 UTC on D−1**. Nepal midnight on D is **18:15 UTC on D−1**, so the feature cutoff precedes issue time by **6 hours 15 minutes**. For example, the September 8 local-day prediction uses rainfall no later than September 7 at 12:00 UTC. Its 14-day window comprises the daily intervals labeled August 25 through September 7.

Daily rainfall rows are labeled by the UTC date of their **ending** noon. Each interval is `[previous noon, named-date noon)`. The replica supplies a half-hour mean rate in `kg m-2 s-1`, equivalent to mm/s. Multiplying each rate by **1,800 seconds** gives half-hour millimetres. Exactly **48 finite half-hours** are required for each daily cell value; a missing observation produces NaN, not zero. This conversion was checked directly against cached raw rates. The eight-year training archive contains no missing daily values on the acquired Nepal grid.

The thirteen model features are:

- Terrain: elevation, median slope, 90th-percentile slope, relief, and steep-terrain fraction.
- Season: sine and cosine of annual phase.
- Rainfall: preceding 24-hour, 72-hour, 7-day, and 14-day totals; an exponentially weighted antecedent wetting index; and change from the preceding daily total.

The wetting index sums fourteen daily totals with weights `exp(-age_in_days / 4)`. This is a rainfall summary, not measured soil moisture. Neither SMAP nor forecast rainfall enters the fitted model. Any displayed GFS weather forecast is separate context and does not change this model's features.

NASA describes IMERG Early's nominal latency as about four hours. The conservative cutoff is an availability simulation, not a reconstruction of the files that were actually delivered at historical issue times. Retrospective processing can use inputs unavailable during initial processing. The public replica's measured delivery age must also be checked separately. [NASA IMERG documentation](https://gpm.nasa.gov/data/imerg).

The historical rainfall uses V07B; the current Collection 07 feed transitions to V07C on March 4, 2026. Exact archive snapshots and hashes are recorded in [rainfall_provenance.json](../data/processed/rainfall_provenance.json). The 2026 deployment distribution therefore needs its own prospective audit. Static terrain is also used retrospectively; this is not a reconstruction of a system actually deployed in 2018.

## Experimental design

| Stage | Dates | Purpose |
| --- | --- | --- |
| Initial fitting for parameter selection | Available 2018–2022 rows | Fit each candidate model |
| Chronological tuning | January 16–December 31, 2023 | Select depth and positive-class weight by average precision |
| Final model fitting | January 15, 2018–December 31, 2023 | Refit the selected configuration on 3,343,872 real cell-days |
| Sigmoid calibration and threshold selection | January 16–December 31, 2024 | Fit the reported-event calibration and freeze alert percentiles |
| Temporal test | January 16–December 31, 2025 | Evaluate 537,600 cell-days, including 544 reported positives |

The first fifteen days of tuning, calibration, and test years are purged to prevent antecedent windows from overlapping the preceding partition. This is a chronological split, not a random split of neighboring cell-days. The 2025 test outcomes are not used to fit the model, calibrator, or operating thresholds in the implemented procedure. However, preliminary 2025 results were inspected during development, so this is not a pristine blind test of the entire research process. These are retrospective validation results. Subsequent design changes should be evaluated on a new holdout or a prospective period rather than repeatedly optimized against 2025.

The selected XGBoost configuration has 180 trees, maximum depth 4, learning rate 0.06, minimum child weight 12, L2 regularization 10, and positive-class weight 1. Six candidate depth/weight combinations were considered. The seed is 42. A sigmoid calibration model is fitted to the main classifier's logit scores on the 2024 calibration set.

The application displays the percentile of the calibrated estimate relative to the 2024 reference population. **A displayed percentile of 0.95 is not a 95% probability of a landslide.** The internal calibrated number is only an estimate of the catalogue-defined report target, and its calibration is itself imperfect. Risk-band boundaries are research categories, not thresholds issued by Nepal's authorities.

## Temporal holdout results

| Model | Average precision | ROC AUC | Brier score | Recall at its frozen threshold | Test alert fraction |
| --- | ---: | ---: | ---: | ---: | ---: |
| Constant training-prevalence reference | 0.001012 | 0.5000 | 0.00101101 | — | — |
| Logistic: terrain + season | 0.003630 | 0.8206 | 0.00100935 | 21.69% | 5.01% |
| Logistic: terrain + season + rainfall | 0.004433 | 0.8349 | 0.00101229 | 25.74% | 4.38% |
| XGBoost: terrain + season | 0.006792 | 0.8570 | 0.00100799 | 30.51% | 5.02% |
| **XGBoost: terrain + season + rainfall** | **0.007166** | **0.8720** | **0.00100918** | **37.68%** | **5.09%** |

Each fitted model uses its own 2024 95th-percentile threshold, giving the same nominal 5% calibration alert budget. Test alert fractions differ because the distributions change. The constant score is a probability reference, not an alert-budget comparator. Its average precision equals test prevalence regardless of the chosen positive constant.

The main-model values are independently recomputed by the evaluation script. Fitted baseline models were not retained in the saved bundle; their table values are training-time measurements from the model card. They are explicitly marked as such in [model_comparison.csv](../artifacts/evaluation/model_comparison.csv). No unverified baseline PR curves are drawn.

The main model's AP is approximately 7.08 times prevalence, but that comparison is much easier than beating terrain and season. The more informative XGBoost ablation improves by only 5.50% in relative AP. Moreover, the terrain-only XGBoost has a slightly better Brier score. These results do not support a broad claim that every aspect of prediction improves when rainfall is added.

![Precision–recall curve](../artifacts/evaluation/precision_recall.png)

*Figure 1. Recomputed temporal-test precision–recall curve and the constant-score prevalence reference. The precision axis is logarithmic because reported positives are rare. The marked threshold was fixed on the calibration year. AP is the non-interpolated average-precision metric, not trapezoidal area under a smoothed curve.*

The saved model card reports an 80-replicate day-block bootstrap 95% interval for main-model AP of **0.005362–0.010142**. Resampling whole days preserves within-day spatial dependence, but multi-day storms remain dependent across blocks. This interval concerns the main model's AP; it is **not** a paired confidence interval for its improvement over the terrain baseline. A conclusion that rainfall's incremental gain is statistically established would be unjustified.

## Calibration and alert burden

![Reliability diagram](../artifacts/evaluation/reliability.png)

*Figure 2. Reliability of the internal calibrated report estimate in ten approximately equal-count test bins. This does not evaluate the displayed percentile score as a probability. Bins contain approximately 53,760 rows each; independent-row error bars are omitted because neighboring cells and storm days are correlated.*

The highest-score decile has an average internal estimate of **0.900%**, but an observed report fraction of **0.603%**. Calibration fitted in 2024 overestimates the report rate in this high-score part of the 2025 distribution. Both the reliability plot and the low absolute event prevalence argue against displaying a physical “landslide probability.”

| 2024 threshold percentile | 2025 cell-days alerted | Precision | Recall | Alerts without a matched report | Missed positive cell-days |
| --- | ---: | ---: | ---: | ---: | ---: |
| 80th | 20.75% | 0.402% | 82.54% | 111,127 | 95 |
| 90th | 10.21% | 0.598% | 60.29% | 54,538 | 216 |
| 95th | 5.09% | 0.749% | 37.68% | 27,172 | 339 |
| 99th | 0.93% | 1.201% | 11.03% | 4,936 | 484 |

![Alert tradeoff](../artifacts/evaluation/alert_tradeoff.png)

*Figure 3. Thresholds are selected from calibration scores and held fixed during evaluation. The points show the precision–recall cost of increasing the number of alerted cell-days, rather than an optimized public warning policy.*

At the 95th percentile, **99.25% of alerted cell-days have no matched BIPAD report**. Calling all these physical false alarms would go beyond the inventory's evidence: some failures may be unreported or assigned inaccurate coordinates. Conversely, catalogue incompleteness cannot be used to assume that these alerts were correct. The measured operational burden remains large—approximately **78 alerted cells per evaluated day** on average—with many recorded positives missed.

## Geographic generalization

A separate future-year geographic experiment trains west of **85.0° E**, chooses its threshold from western calibration rows, and tests east of **85.5° E**, leaving a 0.5° excluded longitude buffer. Its tree depth and positive weight are predeclared rather than selected from the eastern test. The result in the saved model card is:

- 153,650 test cell-days and 187 reported positives;
- average precision **0.006778** and ROC AUC **0.8515**;
- precision **0.733%**, recall **27.27%**, and alert fraction **4.53%**.

This is a more demanding generalization check than a random spatial split, but it is only one west-to-east partition. Reporting practices, terrain, and storm regimes differ geographically. The spatial model uses uncalibrated classifier output, and its fitted artifact was not retained. These values are therefore reported from the saved training-time evaluation; the read-only evaluator does not regenerate that separate model or its curves.

## What this study does and does not establish

The reproducible contribution is a real-data acquisition pipeline, a precisely timed forecasting target, a coarse spatial model, stronger baselines than a constant score, chronological and geographic tests, and explicit accounting of label and deployment limitations. There is measurable ranking signal for the catalogue target. Rainfall's incremental contribution over terrain and season is modest in the retained experiment.

This study does not establish physical slope-failure probabilities, verified rainfall-trigger causation, warning effectiveness, or reliability at individual homes and roads. TreeSHAP contributions describe the fitted model's log-odds decomposition; they do not establish causal drivers. BIPAD's repeated or administrative locations and uneven coverage can produce apparent spatial predictability. Current V07C rainfall, future reporting changes, and sensor or source outages create additional deployment differences.

A defensible next experiment would preserve every incoming forecast and source-arrival timestamp prospectively, then evaluate later reports without retrospective substitution. A separate rainfall-extension study should use archived forecasts with genuine initialization times and a matched training/serving product; future observed rainfall or reanalysis must not be passed off as a forecast. A soil-moisture ablation should use authenticated, quality-controlled SMAP with realistic availability lags. A paired storm-block comparison, multiple geographic folds, and an independently located event inventory would better test the small rainfall gain.

## Reproduction and artifacts

To reproduce the **read-only evaluation of the existing fitted model**, from the repository root:

```sh
.venv/bin/python scripts/evaluate_model.py
```

The command reconstructs features and labels for the model card's test period, loads the saved model and calibrator, recomputes predictions, verifies every saved scalar metric, and writes:

| Artifact | Contents |
| --- | --- |
| `artifacts/evaluation/evaluation.npz` | Per-row dates, cell indices, binary report labels, raw estimates, calibrated estimates, displayed reference percentiles, and cell-ID lookup |
| `evaluation_summary.json` | Model version, input hashes, metric-by-metric agreement and execution time |
| `precision_recall.csv` | Full threshold-level PR coordinates and the analytical prevalence reference |
| `reliability.csv` | Quantile-bin boundaries, sample counts, mean estimates, and observed report rates |
| `alert_tradeoff.csv` | Independently recomputed operating-point metrics |
| `model_comparison.csv` | Main-model verification and explicitly identified training-time baseline measurements |
| `daily_outcomes.csv` | Report, alert, and matched-alert counts by test date |
| `precision_recall`, `reliability`, `alert_tradeoff` as PNG and SVG | Scientific figures; SVG retains editable text |

To acquire inputs or retrain, use the separately documented acquisition and training commands; those are intentionally not invoked by the evaluator. Keep the fitted model, model card, original historical rainfall artifacts, source snapshots, dependency lock, and evaluation outputs together. New model decisions made after inspecting this report require a fresh evaluation claim. The present artifacts document the measured result, including its disappointing alert precision, rather than a desired success story.
