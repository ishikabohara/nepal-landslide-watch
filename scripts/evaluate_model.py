#!/usr/bin/env python3
"""Recompute saved-model holdout predictions and export scientific figures.

No fitting, downloading, or modification of active input/model files occurs.
The displayed application score is a calibration percentile, not a physical
landslide probability. Reliability below audits the internal calibrated
reported-cell-day estimate, not that displayed percentile.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import os
import sys
import tempfile
from datetime import date, timedelta, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "nepal-research-matplotlib"))

import joblib
import numpy as np
from sklearn.metrics import precision_recall_curve

from app.features import FeatureEngine, build_labels
# Import initializes the platform OpenMP runtime before unpickling XGBoost.
from app.modeling import calibrate_values, metrics, reliability


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No records for {path}")
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False))


def reconstruct(data_dir: Path):
    card = json.loads((data_dir / "model/model_card.json").read_text())
    bundle = joblib.load(data_dir / "model/model.joblib")
    if card["model_version"] != bundle["card"]["model_version"]:
        raise ValueError("Saved bundle and model card identify different fitted models")
    engine = FeatureEngine(data_dir)
    if list(bundle["features"]) != card["features"]:
        raise ValueError("Feature schema mismatch")
    labels, audit = build_labels(json.loads((data_dir / "processed/events.json").read_text()), engine)
    day = date.fromisoformat(card["evaluation_period"]["start"])
    end = date.fromisoformat(card["evaluation_period"]["end"])
    parts = {key: [] for key in ("dates", "cell_index", "reported", "raw_estimate", "calibrated_estimate")}
    while day <= end:
        batch = engine.for_day(day)
        valid = np.flatnonzero(batch.valid)
        y = np.zeros(len(engine.cells), dtype=np.uint8)
        y[list(labels.get(day.isoformat(), set()))] = 1
        raw = bundle["model"].predict_proba(batch.values[valid])[:, 1]
        calibrated = calibrate_values(bundle["calibrator"], raw)
        parts["dates"].append(np.full(len(valid), day.isoformat(), dtype="U10"))
        parts["cell_index"].append(valid.astype(np.int16))
        parts["reported"].append(y[valid])
        parts["raw_estimate"].append(raw)
        parts["calibrated_estimate"].append(calibrated)
        day += timedelta(days=1)
    arrays = {key: np.concatenate(value) for key, value in parts.items()}
    arrays["calibration_percentile"] = (
        np.searchsorted(bundle["reference"], arrays["calibrated_estimate"], side="right")
        / len(bundle["reference"])
    ).astype(np.float32)
    arrays["cell_ids"] = np.asarray(engine.cell_ids)
    measured = metrics(arrays["reported"], arrays["calibrated_estimate"], bundle["threshold"])
    comparisons = {}
    for key, expected in card["metrics"].items():
        actual = measured[key]
        if expected is None:
            matches = actual is None
        elif isinstance(expected, (int, float)):
            matches = bool(np.isclose(actual, expected, atol=1e-12, rtol=1e-8))
        else:
            matches = actual == expected
        comparisons[key] = {"saved": expected, "recomputed": actual, "matches": matches}
    failures = [key for key, result in comparisons.items() if not result["matches"]]
    if failures:
        raise ValueError(f"Recomputed evaluation differs from saved card: {failures}; investigate input/model changes")
    return card, bundle, arrays, measured, comparisons, audit


def figures(output: Path, card: dict, measured: dict, precision, recall, bins, tradeoffs):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11, "axes.titlesize": 17,
        "axes.labelsize": 11, "axes.spines.top": False, "axes.spines.right": False,
        "axes.edgecolor": "#b8c2cc", "axes.labelcolor": "#253445", "text.color": "#253445",
        "xtick.color": "#46576a", "ytick.color": "#46576a", "grid.color": "#e6ebef",
        "figure.facecolor": "white", "axes.facecolor": "white", "svg.fonttype": "none",
        "savefig.dpi": 220})
    primary, secondary = "#137b80", "#c36b35"
    period = f"{card['evaluation_period']['start']} to {card['evaluation_period']['end']}"

    def save(fig, name):
        fig.savefig(output / f"{name}.png", bbox_inches="tight", facecolor="white")
        fig.savefig(output / f"{name}.svg", bbox_inches="tight", facecolor="white")
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(9, 6))
    fig.subplots_adjust(bottom=.19, top=.83, left=.13, right=.97)
    # Plot only points where the recall changes plus endpoints; every report
    # transition is retained, while long runs of identical recall are removed.
    changes = np.flatnonzero(np.r_[True, np.diff(recall) != 0])
    selected = np.unique(np.r_[changes, changes[1:] - 1, len(recall) - 1])
    ax.step(recall[selected], precision[selected], where="post", color=primary, lw=1.8,
        label=f"Saved terrain + rainfall model · AP {measured['average_precision']:.5f}")
    ax.axhline(measured["prevalence"], color=secondary, ls="--", lw=1.4,
        label=f"Constant-score reference · prevalence {100 * measured['prevalence']:.3f}%")
    ax.scatter([measured["recall"]], [measured["precision"]], s=48, color="#253445", zorder=5,
        label="Frozen 2024 95th-percentile threshold")
    ax.set(xlim=(0, 1), ylim=(measured["prevalence"] * .65, 1.05), yscale="log",
        xlabel="Recall: fraction of reported cell-days detected", ylabel="Precision: fraction of alerts with a matched report")
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=2))
    ax.grid(axis="both", which="major", alpha=.8)
    ax.legend(loc="upper right", frameon=False, fontsize=9)
    fig.suptitle("Temporal holdout: precision–recall", x=.13, ha="left", y=.965, fontsize=17)
    fig.text(.13, .89, f"{period}  |  {measured['rows']:,} cell-days  |  {measured['positives']:,} reported positives", fontsize=10)
    fig.text(.13, .065, "BIPAD recorded-location target; no matched report does not establish physical absence.\nLogarithmic precision axis exposes the low absolute precision under severe class imbalance.", fontsize=9, color="#566777")
    save(fig, "precision_recall")

    fig, ax = plt.subplots(figsize=(8, 6))
    fig.subplots_adjust(bottom=.21, top=.83, left=.14, right=.97)
    predicted = np.array([row["predicted"] for row in bins])
    observed = np.array([row["observed"] for row in bins])
    limit = max(float(predicted.max()), float(observed.max())) * 1.13
    ax.plot([0, limit], [0, limit], color="#8996a4", ls="--", lw=1.2, label="Perfect agreement for the catalogue target")
    ax.plot(predicted, observed, "o-", color=primary, lw=1.7, ms=6, label="2025 equal-count bins (approximately 53,760 rows each)")
    ax.annotate(f"Highest-score decile:\n{100*predicted[-1]:.3f}% estimated, {100*observed[-1]:.3f}% observed",
        xy=(predicted[-1], observed[-1]), xytext=(limit*.23, limit*.83), fontsize=9,
        arrowprops={"arrowstyle": "-", "color": "#7b8996"})
    ax.set(xlim=(0, limit), ylim=(0, limit), xlabel="Mean internal calibrated recorded-event estimate",
        ylabel="Observed fraction of cell-days with a report")
    ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=1))
    ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=1))
    ax.grid(alpha=.8)
    ax.legend(loc="lower right", frameon=False, fontsize=8)
    fig.suptitle("Reliability of the internal recorded-event estimate", x=.14, ha="left", y=.965, fontsize=15)
    fig.text(.14, .89, "Calibration fitted on 2024; evaluated on 2025 rows excluded from fitting", fontsize=9)
    fig.text(.14, .06, "This is not a reliability plot of the displayed percentile score or a physical failure probability.\nBin uncertainty is not shown; correlated storm days invalidate independent-row error bars.", fontsize=9, color="#566777")
    save(fig, "reliability")

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.7))
    fig.subplots_adjust(bottom=.22, top=.79, left=.08, right=.97, wspace=.3)
    x = np.array([row["alert_fraction"] for row in tradeoffs])
    rec = np.array([row["recall"] for row in tradeoffs])
    prec = np.array([row["precision"] for row in tradeoffs])
    order = np.argsort(x)
    axes[0].plot(x[order], rec[order], "o-", color=primary, lw=1.8)
    axes[1].plot(x[order], prec[order], "o-", color=secondary, lw=1.8)
    for row in tradeoffs:
        label = f"p{int(round(row['calibration_percentile'] * 100))}"
        for ax, value in [(axes[0], row["recall"]), (axes[1], row["precision"])]:
            ax.annotate(label, (row["alert_fraction"], value), xytext=(5, 7), textcoords="offset points", fontsize=9)
    for ax in axes:
        ax.set(xlabel="Actual 2025 alert fraction", xlim=(0, max(x)*1.14))
        ax.xaxis.set_major_formatter(PercentFormatter(1, decimals=0))
        ax.yaxis.set_major_formatter(PercentFormatter(1, decimals=1))
        ax.grid(alpha=.8)
    axes[0].set(ylabel="Recall of reported cell-days", ylim=(0, 1))
    axes[1].set(ylabel="Precision against the catalogue", ylim=(0, max(prec)*1.28))
    fig.suptitle("The alert burden is large at every evaluated threshold", x=.08, ha="left", y=.965, fontsize=16)
    fig.text(.08, .87, "Thresholds fixed at 2024 calibration percentiles; each point is independently recomputed on 2025", fontsize=10)
    fig.text(.08, .065, f"At p95: {measured['matched_alerts']:,} matched alerts, {measured['alerts_without_matched_report']:,} alerts without a matched report, and {measured['missed_reported_cell_days']:,} missed reported cell-days.\nPercentile categories are research operating points, not public warning thresholds.", fontsize=9, color="#566777")
    save(fig, "alert_tradeoff")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/evaluation")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    card, bundle, arrays, measured, comparisons, audit = reconstruct(args.data_dir)
    y, p = arrays["reported"], arrays["calibrated_estimate"]
    np.savez_compressed(args.output_dir / "evaluation.npz", **arrays)
    precision, recall, thresholds = precision_recall_curve(y, p)
    write_csv(args.output_dir / "precision_recall.csv", [
        {"threshold": float(thresholds[i]) if i < len(thresholds) else "",
         "precision": float(pr), "recall": float(re), "constant_score_prevalence": measured["prevalence"]}
        for i, (pr, re) in enumerate(zip(precision, recall))])
    bins = reliability(y, p)
    write_csv(args.output_dir / "reliability.csv", bins)
    tradeoffs = []
    for percentile in (.8, .9, .95, .99):
        threshold = float(np.quantile(bundle["reference"], percentile))
        result = {"calibration_percentile": percentile, **metrics(y, p, threshold)}
        tradeoffs.append(result)
    write_csv(args.output_dir / "alert_tradeoff.csv", tradeoffs)
    baseline_rows = [{"name": "XGBoost: terrain + season + rainfall", "verification": "recomputed from saved fitted model", **measured}]
    baseline_rows.extend({"name": record["name"], "verification": "training-time model-card metrics; fitted baseline not retained", **record["metrics"]} for record in card["baselines"])
    write_csv(args.output_dir / "model_comparison.csv", baseline_rows)
    daily = []
    for day in np.unique(arrays["dates"]):
        mask = arrays["dates"] == day
        alerted = p[mask] >= bundle["threshold"]
        reported = y[mask] == 1
        daily.append({"date": day, "evaluated_cells": int(mask.sum()), "reported_cells": int(reported.sum()),
            "alerted_cells": int(alerted.sum()), "matched_alerts": int((alerted & reported).sum())})
    write_csv(args.output_dir / "daily_outcomes.csv", daily)
    source_hashes = {name: digest(args.data_dir / name) for name in ["model/model.joblib", "model/model_card.json",
        "processed/grid.geojson", "processed/events.json", "processed/rainfall_daily.npz"]}
    summary = {"evaluated_at": datetime.now(timezone.utc).isoformat(), "model_version": card["model_version"],
        "period": card["evaluation_period"], "metrics": measured, "metric_comparisons": comparisons,
        "all_saved_metrics_match": True, "label_audit": audit, "source_sha256": source_hashes,
        "read_only_evaluation": True, "retraining_performed": False,
        "package_versions": {name: importlib.metadata.version(name) for name in ["numpy", "scikit-learn", "xgboost", "matplotlib", "joblib"]},
        "baseline_curve_policy": "Only the saved main-model PR curve and analytical constant-score prevalence reference are plotted; fitted baseline models were not retained.",
        "interpretation": "Internal calibrated estimates target a BIPAD report at its recorded location; application scores are calibration percentiles, not physical probabilities."}
    write_json(args.output_dir / "evaluation_summary.json", summary)
    figures(args.output_dir, card, measured, precision, recall, bins, tradeoffs)
    print(json.dumps({"model_version": card["model_version"], "rows": measured["rows"],
        "positives": measured["positives"], "average_precision": measured["average_precision"],
        "all_saved_metrics_match": True, "output_dir": str(args.output_dir)}, indent=2))


if __name__ == "__main__":
    main()
