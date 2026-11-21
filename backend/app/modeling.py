"""CPU training, calibration, blocked evaluation and auditable inference.

The model estimates a BIPAD reported-location target. Unknown catalogue coverage
precludes interpreting its outputs as physical slope-failure probabilities.
"""
from __future__ import annotations

import ctypes
import fcntl
import hashlib
import importlib.util
import json
import platform
import shutil
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix,
    f1_score, precision_score, recall_score, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

# The scikit-learn macOS wheel includes the same OpenMP runtime XGBoost needs.
# Load it explicitly when Homebrew libomp is not installed; Linux uses libgomp.
if platform.system() == "Darwin":
    lib = Path(importlib.util.find_spec("sklearn").origin).parent / ".dylibs/libomp.dylib"
    if lib.exists():
        ctypes.CDLL(str(lib))
from xgboost import DMatrix, XGBClassifier

from .features import FEATURES, LABELS, STATIC_FEATURES, FeatureEngine, build_labels, issue_time, rainfall_cutoff
from .terrain import write_json


def metrics(y, p, threshold: float) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    pred = p >= threshold
    tn, fp, fn, tp = confusion_matrix(y,pred,labels=[0,1]).ravel()
    return {"rows":int(len(y)),"positives":int(y.sum()),"prevalence":float(y.mean()),
        "average_precision":float(average_precision_score(y,p)) if y.sum() else None,
        "roc_auc":float(roc_auc_score(y,p)) if 0<y.sum()<len(y) else None,
        "brier_score":float(brier_score_loss(y,p)),
        "precision":float(precision_score(y,pred,zero_division=0)),
        "recall":float(recall_score(y,pred,zero_division=0)),
        "f1":float(f1_score(y,pred,zero_division=0)),"threshold":float(threshold),
        "alert_fraction":float(pred.mean()),"alerts_without_matched_report":int(fp),
        "matched_alerts":int(tp),"missed_reported_cell_days":int(fn),
        "reported_negative_cell_days":int(tn),
        "missed_event_rate":float(fn/(tp+fn)) if tp+fn else None,
        "false_positive_rate_against_catalogue":float(fp/(fp+tn)) if fp+tn else None,
        "unmatched_alert_fraction":float(fp/(tp+fp)) if tp+fp else None}


def reliability(y, p) -> list[dict]:
    edges=np.unique(np.quantile(p,np.linspace(0,1,11)))
    result=[]
    for i,(low,high) in enumerate(zip(edges[:-1],edges[1:])):
        selected=(p>=low)&((p<=high) if i==len(edges)-2 else (p<high))
        if selected.any():
            result.append({"count":int(selected.sum()),"predicted":float(np.mean(p[selected])),
                "observed":float(np.mean(y[selected])),"low":float(low),"high":float(high)})
    return result


def calibrate_values(calibrator, p):
    logits=np.log(np.clip(p,1e-7,1-1e-7)/(1-np.clip(p,1e-7,1-1e-7)))
    return calibrator.predict_proba(logits.reshape(-1,1))[:,1]


def dataset(engine: FeatureEngine, end_year: int):
    labels,audit=build_labels(json.loads((engine.data_dir/"processed/events.json").read_text()),engine)
    xs,ys,dates,cells=[],[],[],[]
    for day in engine.available_days():
        if day.year>end_year:
            continue
        batch=engine.for_day(day)
        ids=np.flatnonzero(batch.valid)
        y=np.zeros(len(engine.cells),dtype=np.uint8)
        positive=list(labels.get(day.isoformat(),set()))
        y[positive]=1
        xs.append(batch.values[ids]); ys.append(y[ids]); cells.append(ids.astype(np.int16))
        dates.append(np.full(len(ids),day.isoformat(),dtype="U10"))
    if not xs:
        raise ValueError("No complete real environmental feature windows are available")
    return np.concatenate(xs),np.concatenate(ys),np.concatenate(dates),np.concatenate(cells),audit


def train(data_dir: Path) -> dict:
    engine=FeatureEngine(data_dir)
    complete_year=min(date.today().year-1,max(date.fromisoformat(d).year for d in engine.days))
    X,y,dates,cell_indices,audit=dataset(engine,complete_year)
    years=np.array([int(s[:4]) for s in dates],dtype=np.int16)
    # Full seasons/years remain intact. The first 15 days of heldout years are
    # purged so antecedent windows do not overlap the previous partition.
    day_after_purge=np.array([s[5:] >= "01-16" for s in dates])
    unique=sorted(np.unique(years))
    if len(unique)<4:
        raise ValueError("At least four distinct calendar years are required for train/tune/calibration/test")
    test_year,cal_year,tune_year=unique[-1],unique[-2],unique[-3]
    fit=(years<tune_year)
    tune=(years==tune_year)&day_after_purge
    train_rows=years<=tune_year
    cal=(years==cal_year)&day_after_purge
    test=(years==test_year)&day_after_purge
    for name,mask in [("fit",fit),("tune",tune),("calibration",cal),("test",test)]:
        if y[mask].sum()<20:
            raise ValueError(f"Insufficient positive recorded cell-days for {name}: {int(y[mask].sum())}")
    print(f"Dataset {len(y):,} real cell-days; train {int(train_rows.sum()):,}; test {int(test.sum()):,}",flush=True)
    candidates=[]
    for depth, positive_weight in [(d,w) for d in (2,3,4) for w in (1,20)]:
        model=XGBClassifier(n_estimators=180,max_depth=depth,learning_rate=.06,
            min_child_weight=12,reg_lambda=10,subsample=1,colsample_bytree=1,
            objective="binary:logistic",eval_metric="logloss",tree_method="hist",n_jobs=4,random_state=42,
            scale_pos_weight=positive_weight)
        model.fit(X[fit],y[fit])
        score=average_precision_score(y[tune],model.predict_proba(X[tune])[:,1])
        candidates.append({"max_depth":depth,"scale_pos_weight":positive_weight,"tuning_average_precision":float(score)})
        print(f"Tuning depth={depth}, positive weight={positive_weight}: average precision {score:.5f}",flush=True)
    best=max(candidates,key=lambda item:item["tuning_average_precision"])
    params=dict(n_estimators=180,max_depth=best["max_depth"],learning_rate=.06,min_child_weight=12,
        reg_lambda=10,objective="binary:logistic",eval_metric="logloss",tree_method="hist",n_jobs=4,random_state=42,
        scale_pos_weight=best["scale_pos_weight"])
    model=XGBClassifier(**params).fit(X[train_rows],y[train_rows])
    raw_cal=model.predict_proba(X[cal])[:,1]
    cal_logits=np.log(np.clip(raw_cal,1e-7,1-1e-7)/(1-np.clip(raw_cal,1e-7,1-1e-7)))
    calibrator=LogisticRegression(C=100,max_iter=500).fit(cal_logits.reshape(-1,1),y[cal])
    p_cal=calibrate_values(calibrator,raw_cal)
    p_test=calibrate_values(calibrator,model.predict_proba(X[test])[:,1])
    # Display bands are calibration-population percentiles, not probability bins
    # or civil-protection warning thresholds. The evaluation threshold is also frozen here.
    quantiles=np.quantile(p_cal,[.80,.95,.99])
    threshold=float(np.quantile(p_cal,.95))
    test_metrics=metrics(y[test],p_test,threshold)
    baseline_results=[]
    prevalence=float(y[train_rows].mean())
    baseline_results.append({"name":"Training prevalence","metrics":metrics(y[test],np.full(test.sum(),prevalence),threshold)})
    for name,columns in [("Terrain + season",list(range(len(STATIC_FEATURES)+2))),
                         ("Terrain + season + rainfall",list(range(len(FEATURES))))]:
        baseline=make_pipeline(StandardScaler(),LogisticRegression(max_iter=400,C=.3))
        baseline.fit(X[train_rows][:,columns],y[train_rows])
        bcal=baseline.predict_proba(X[cal][:,columns])[:,1]
        bthreshold=float(np.quantile(bcal,.95))
        bp=baseline.predict_proba(X[test][:,columns])[:,1]
        baseline_results.append({"name":f"Logistic: {name}","metrics":metrics(y[test],bp,bthreshold)})
    static=XGBClassifier(**params).fit(X[train_rows,:len(STATIC_FEATURES)+2],y[train_rows])
    static_cal=static.predict_proba(X[cal,:len(STATIC_FEATURES)+2])[:,1]
    static_p=static.predict_proba(X[test,:len(STATIC_FEATURES)+2])[:,1]
    baseline_results.append({"name":"XGBoost: terrain + season","metrics":metrics(y[test],static_p,float(np.quantile(static_cal,.95)))})
    print("Temporal evaluation",json.dumps(test_metrics),flush=True)
    # Independent geographic generalization: no eastern cells in training or calibration.
    lons=np.array([f["properties"]["lon"] for f in engine.cells])[cell_indices]
    west_train=train_rows&(lons<85.0)
    west_cal=cal&(lons<85.0)
    east_test=test&(lons>=85.5)
    spatial=None
    if y[west_train].sum()>=20 and y[east_test].sum()>=20:
        spatial_params={**params,"max_depth":3,"scale_pos_weight":1}
        spatial_model=XGBClassifier(**spatial_params).fit(X[west_train],y[west_train])
        wp=spatial_model.predict_proba(X[west_cal])[:,1]
        ep=spatial_model.predict_proba(X[east_test])[:,1]
        spatial=metrics(y[east_test],ep,float(np.quantile(wp,.95)))
        spatial["design"]="Predeclared depth 3 and positive weight 1; train west of 85E; evaluate future-year east of 85.5E, with a 0.5-degree excluded buffer. Spatial metrics use uncalibrated classifier output."
    # Date-block bootstrap, preserving all correlated cells from each sampled day.
    rng=np.random.default_rng(42)
    test_dates=dates[test]
    unique_dates=np.unique(test_dates)
    group_indices=[np.flatnonzero(test_dates==d) for d in unique_dates]
    boot=[]
    for _ in range(80):
        ids=np.concatenate([group_indices[i] for i in rng.integers(0,len(group_indices),len(group_indices))])
        if y[test][ids].sum():
            boot.append(average_precision_score(y[test][ids],p_test[ids]))
    confidence=[float(v) for v in np.quantile(boot,[.025,.975])] if boot else None
    fingerprint=hashlib.sha256()
    fingerprint.update(bytes(model.get_booster().save_raw()))
    fingerprint.update(json.dumps(FEATURES).encode())
    fingerprint.update(X[train_rows].tobytes())
    fingerprint.update(y[train_rows].tobytes())
    fingerprint.update(np.asarray(calibrator.coef_).tobytes())
    fingerprint.update(np.asarray(calibrator.intercept_).tobytes())
    version=f"bipad-imerg-xgb-{test_year}-{fingerprint.hexdigest()[:12]}"
    now=datetime.now(timezone.utc).isoformat()
    limitations=[
        "Experimental reported-location model, not a validated public early-warning service.",
        "BIPAD locations have unreported positional accuracy and frequently repeat; they may represent administrative centres, not failure sites.",
        "Most BIPAD triggers are unspecified. This is not a verified rainfall-triggered inventory; known earthquake records are excluded.",
        "No matched report is unlabeled background, not verified physical absence; reporting varies over space and time.",
        "Displayed hazard percentiles rank model output against calibration cell-days; they are not landslide probabilities.",
        "Daily date labels support fixed Nepal calendar-day predictions, not validated rolling hourly forecasts.",
        "Rainfall estimates have 0.1 degree spacing and operational latency; terrain does not provide slope-level forecast precision.",
        "The historical IMERG mirror is reprocessed; a conservative 6 h 15 min lag simulates availability but does not reproduce original as-issued products.",
        "The 2026 near-real-time IMERG input revision differs from historical V07B; current predictions require prospective validation.",
        "GFS weather context is separate and is not an input to this trained model. SMAP is not included without authenticated quality-controlled data.",
        "Bootstrap uses day blocks and may still understate dependence across multi-day storms.",
    ]
    card={"model_name":"XGBoost · terrain and antecedent rainfall","model_version":version,
        "status":"experimental","target":"At least one BIPAD landslide report assigned to its recorded 0.1 degree cell during the next Nepal calendar day.",
        "score_type":"relative_hazard_percentile","trained_at":now,
        "training_period":{"start":str(min(dates[train_rows])),"end":str(max(dates[train_rows]))},
        "evaluation_period":{"start":str(min(dates[test])),"end":str(max(dates[test]))},
        "calibration_period":{"start":str(min(dates[cal])),"end":str(max(dates[cal]))},
        "training_rows":int(train_rows.sum()),"positive_events":int(y[train_rows].sum()),
        "features":FEATURES,"metrics":test_metrics,"baselines":baseline_results,
        "spatial_evaluation":spatial,"average_precision_95pct_day_bootstrap":confidence,
        "reliability":reliability(y[test],p_test),"calibration_metrics":metrics(y[cal],p_cal,threshold),
        "hyperparameter_search":candidates,"selected_parameters":params,"label_audit":audit,
        "risk_bands":{"method":f"Percentiles of {cal_year} calibration-population model estimates",
            "percentile_edges":[.80,.95,.99],"model_probability_edges":[float(v) for v in quantiles],
            "meaning":"Relative categories only; not official alert thresholds"},
        "limitations":limitations,
        "methodology":"Daily midnight Asia/Kathmandu target; all rainfall features end by the previous day at 12:00 UTC. Complete real cell-days, with unreported cell-days treated as unlabeled background. Chronological tuning, separate sigmoid calibration and a 2025 test from January 16 onward excluded from fitting; an independent future-year geographic holdout is also evaluated. Development included inspecting preliminary 2025 results, so unseen prospective validation remains necessary. Native XGBoost TreeSHAP contributions explain model log-odds, not causality.",
        "reproduction":"python scripts/train_model.py",
        "forecast_rainfall_used":False,"soil_moisture_used":False,"random_seed":42}
    card["input_provenance"]={name:hashlib.sha256((data_dir/name).read_bytes()).hexdigest()
        for name in ["processed/grid.geojson","processed/events.json","processed/rainfall_daily.npz"]}
    card["alert_tradeoffs"]=[{"calibration_percentile":q,"metrics":metrics(y[test],p_test,float(np.quantile(p_cal,q)))} for q in [.8,.9,.95,.99]]
    card["threshold_policy"]="Main model and fitted baselines use their own calibration-population 95th percentile, giving the same nominal 5% calibration alert budget. Actual test alert fractions may differ. The constant-prevalence reference is a probability baseline only."
    path=data_dir/"model"
    path.mkdir(parents=True,exist_ok=True)
    joblib.dump({"model":model,"calibrator":calibrator,"reference":np.sort(p_cal),
        "features":FEATURES,"card":card,"threshold":float(threshold)},path/"model.joblib")
    model.save_model(path/"xgboost.ubj")
    write_json(path/"model_card.json",card)
    write_json(data_dir/"processed/label_audit.json",audit)
    write_json(path/"evaluation_predictions.json",{
        "note":"Daily aggregated test outcomes; counts are recorded grid-cell days.",
        "days":[{"date":d,"reported_cells":int(y[test][test_dates==d].sum()),
            "alerted_cells":int((p_test[test_dates==d]>=threshold).sum()),
            "matched_alerts":int(((p_test[test_dates==d]>=threshold)&(y[test][test_dates==d]==1)).sum())}
            for d in unique_dates]})
    return card


def predict(data_dir: Path, day: date, *, operational: bool=False) -> dict:
    """Serialize publication across schedulers, CLI invocations and exports."""
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "publish.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _predict_locked(data_dir, day, operational=operational)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _predict_locked(data_dir: Path, day: date, *, operational: bool=False) -> dict:
    import copy
    import math
    bundle=joblib.load(data_dir/"model/model.joblib")
    model,calibrator=bundle["model"],bundle["calibrator"]
    run_id=f"{day.isoformat()}-{'forecast' if operational else 'replay'}-{bundle['card']['model_version']}"
    directory=data_dir/"published/runs"/run_id
    # Check under the publication lock, before reading inputs or doing inference.
    # Reusing an existing operational run never changes its original issue time.
    if (directory/"manifest.json").exists() and (directory/"forecast.geojson").exists():
        return json.loads((directory/"manifest.json").read_text())
    now=datetime.now(timezone.utc)
    valid_from=issue_time(day)
    if operational:
        if not valid_from-timedelta(minutes=20)<=now<valid_from:
            raise ValueError("Operational forecasts must be generated before the target day, within the20-minute issuance window. Use historical replay otherwise.")
        # Current acquisition must be complete before publication; precise original
        # source delivery logs are retained by the source adapters, not fabricated.
        if rainfall_cutoff(day)>now-timedelta(hours=4):
            raise ValueError("Input cutoff is too recent for declared rainfall latency")
    engine=FeatureEngine(data_dir)
    batch=engine.for_day(day)
    valid=np.flatnonzero(batch.valid)
    p=np.full(len(engine.cells),np.nan)
    contribution=np.full((len(engine.cells),len(FEATURES)+1),np.nan)
    if len(valid):
        p[valid]=calibrate_values(calibrator,model.predict_proba(batch.values[valid])[:,1])
        contribution[valid]=model.get_booster().predict(DMatrix(batch.values[valid]),pred_contribs=True)
    percentiles=np.searchsorted(bundle["reference"],p,side="right")/len(bundle["reference"])
    gfs={}
    context=data_dir/"processed/gfs_forecast.json"
    if context.exists():
        candidate=json.loads(context.read_text())
        try:
            start=datetime.fromisoformat(candidate.get("valid_from","").replace("Z","+00:00"))
            end=datetime.fromisoformat(candidate.get("valid_to","").replace("Z","+00:00"))
            if start==valid_from and end==valid_from+timedelta(days=1):
                gfs=candidate
        except ValueError:
            pass
    result=copy.deepcopy(engine.grid)
    summary={k:0 for k in ["low","moderate","high","very_high","unavailable"]}
    places_path=data_dir/"processed/places.json"
    places=json.loads(places_path.read_text()) if places_path.exists() else []
    if isinstance(places,dict): places=places.get("places",places.get("results",[]))
    nearest_places = None
    if places:
        from scipy.spatial import cKDTree
        tree = cKDTree([[float(q["lat"]),float(q["lon"])*.88] for q in places])
        _, nearest_places = tree.query([[f["properties"]["lat"],f["properties"]["lon"]*.88] for f in engine.cells])
    for i,f in enumerate(result["features"]):
        props=f["properties"]
        if nearest_places is not None:
            place=places[int(nearest_places[i])]
            props["place_name"]=f"Near {place['name']}"
        else:
            props["place_name"]=f"{props['lat']:.2f}°N, {props['lon']:.2f}°E"
        available=bool(batch.valid[i])
        rank=float(percentiles[i]) if available else None
        level=("very_high" if rank>=.99 else "high" if rank>=.95 else "moderate" if rank>=.80 else "low") if available else "unavailable"
        summary[level]+=1
        props.update(score=rank,probability=None,raw_model_probability=float(p[i]) if available else None,
            risk_level=level,score_type="relative_hazard_percentile",forecast_rain_24h_mm=None,
            soil_moisture=None,input_age_hours=round(((now if operational else valid_from)-rainfall_cutoff(day)).total_seconds()/3600,2),
            observation_age_now_hours=round((now-rainfall_cutoff(day)).total_seconds()/3600,2),
            quality_flags=["experimental","report_location_uncertain","trigger_unconfirmed", "not_physical_probability"],
            drivers=[],rainfall_series=[],input_sources={"rainfall":"NASA IMERG Early via dynamical.org",
                "terrain":"Copernicus DEM GLO-90","rainfall_observed_through":rainfall_cutoff(day).isoformat(),
                "forecast_rainfall_role":"context_only_not_used_by_model"})
        if not available:
            props["quality_flags"].append("incomplete_rainfall_or_terrain")
        if day.year>=2026:
            props["quality_flags"].append("imerg_2026_version_transition")
        for name in FEATURES:
            value=float(batch.values[i,FEATURES.index(name)])
            props[name]=round(value,3) if math.isfinite(value) else None
        for k,d in enumerate(batch.rain_days):
            value=float(batch.rainfall[k,i])
            props["rainfall_series"].append({"time":f"{d}T12:00:00Z","rain_mm":round(value,3) if math.isfinite(value) else None})
        if available:
            indices=np.argsort(np.abs(contribution[i,:-1]))[-4:][::-1]
            props["drivers"]=[{"feature":FEATURES[j],"label":LABELS[FEATURES[j]],
                "value":float(batch.values[i,j]),"contribution":round(float(contribution[i,j]),5),
                "unit":"model_log_odds"} for j in indices]
        weather=gfs.get("cells",{}).get(props["cell_id"],{})
        if weather:
            props["forecast_rain_24h_mm"]=weather.get("forecast_rain_24h_mm")
            props["input_sources"]["forecast_cycle"]=gfs.get("cycle")
    generated_at=datetime.now(timezone.utc)
    if operational and generated_at>=valid_from:
        raise ValueError("Operational inference finished after the issuance deadline; no forecast was published or backdated.")
    # Freshness refers to completed inference, not the beginning of a slow run.
    # Issuance is stamped separately once the large forecast file is staged.
    age_reference=generated_at if operational else valid_from
    for feature in result["features"]:
        feature["properties"].update(
            input_age_hours=round((age_reference-rainfall_cutoff(day)).total_seconds()/3600,2),
            input_age_reference_at=age_reference.isoformat(),
            observation_age_now_hours=round((generated_at-rainfall_cutoff(day)).total_seconds()/3600,2))
    manifest={"run_id":run_id,"issued_at":generated_at.isoformat() if operational else valid_from.isoformat(),
        "generated_at":generated_at.isoformat(),"valid_from":valid_from.isoformat(),
        "valid_to":(valid_from+timedelta(days=1)).isoformat(),"mode":"operational" if operational else "historical",
        "issue_time_is_simulated":not operational,"model_version":bundle["card"]["model_version"],
        "target":bundle["card"]["target"],"grid_resolution_degrees":.1,
        "score_type":"relative_hazard_percentile","cell_count":len(result["features"]),
        "data_cutoff":rainfall_cutoff(day).isoformat(),"source_times":{"rainfall":rainfall_cutoff(day).isoformat()},
        "summary":summary,"limitations":bundle["card"]["limitations"],
        "explanation":"Reconstructed historical daily estimate. It was not actually issued at the simulated issue time." if not operational else "Experimental daily forecast issued before the target period; model predicts recorded administrative locations."}
    manifest["input_provenance"]={"model_fingerprint":bundle["card"]["model_version"],
        "features_sha256":hashlib.sha256(batch.values.tobytes()).hexdigest(),
        "rainfall_window_sha256":hashlib.sha256(batch.rainfall.tobytes()).hexdigest(),
        "grid_sha256":hashlib.sha256((data_dir/"processed/grid.geojson").read_bytes()).hexdigest()}
    manifest["evaluation_partition"] = "excluded_or_outside_study"
    for period, label in [("training_period", "training"), ("calibration_period", "calibration"), ("evaluation_period", "held_out_test")]:
        bounds = bundle["card"][period]
        if bounds["start"] <= day.isoformat() <= bounds["end"]:
            manifest["evaluation_partition"] = label
            break
    if day.isoformat() > bundle["card"]["evaluation_period"]["end"]:
        manifest["evaluation_partition"] = "post_test_experimental"
    # Publish immutable run files before changing the current pointer.
    directory.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix=".staging-",dir=directory.parent))
    pending_current=None
    try:
        write_json(stage/"forecast.geojson",result)
        issued_at=datetime.now(timezone.utc)
        if operational and issued_at>=valid_from:
            raise ValueError("Operational staging finished after the issuance deadline; no forecast was published or backdated.")
        if operational:
            manifest["issued_at"]=issued_at.isoformat()
        write_json(stage/"manifest.json",manifest)
        current=data_dir/"published/current.json"
        old=json.loads(current.read_text()) if current.exists() else None
        if old is None or old["valid_from"]<=manifest["valid_from"]:
            pending_current=current.with_name(f".current-{uuid.uuid4().hex}.json")
            write_json(pending_current,manifest)
        # The deadline is checked after all JSON serialization, immediately
        # before the short atomic renames that make the complete run visible.
        if operational and datetime.now(timezone.utc)>=valid_from:
            raise ValueError("Operational publication missed the issuance deadline; no forecast was published or backdated.")
        if directory.exists():
            directory.rename(directory.with_name(f".incomplete-{run_id}-{uuid.uuid4().hex[:8]}"))
        stage.rename(directory)
        if pending_current is not None:
            pending_current.replace(current)
    finally:
        shutil.rmtree(stage,ignore_errors=True)
        if pending_current is not None:
            pending_current.unlink(missing_ok=True)
    return manifest
