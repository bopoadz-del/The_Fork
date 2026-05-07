"""ML Engine Block - Unified machine learning: train, predict, evaluate, explain, persist"""

import os
import json
import time
import pickle
import hashlib
from typing import Any, Dict, List, Optional
from app.core.universal_base import UniversalBlock

_MODEL_STORE = os.environ.get("ML_MODEL_STORE", "/tmp/cerebrum_ml_models")


class MLEngineBlock(UniversalBlock):
    name = "ml_engine"
    version = "1.0.0"
    description = "Unified ML: train/predict/evaluate/explain regression, classification, clustering, anomaly detection"
    layer = 3
    tags = ["ml", "ai", "training", "prediction", "sklearn", "domain"]
    requires = []

    default_config = {
        "model_store": _MODEL_STORE,
        "default_algorithm": "random_forest",
        "test_size": 0.2,
        "random_state": 42,
        "auto_scale": True,
    }

    # Algorithm registry
    _ALGORITHMS = {
        # Regression
        "linear_regression":      ("sklearn.linear_model",   "LinearRegression",      "regression"),
        "ridge":                  ("sklearn.linear_model",   "Ridge",                 "regression"),
        "lasso":                  ("sklearn.linear_model",   "Lasso",                 "regression"),
        "random_forest_regressor":("sklearn.ensemble",       "RandomForestRegressor", "regression"),
        "gradient_boosting_reg":  ("sklearn.ensemble",       "GradientBoostingRegressor", "regression"),
        "svr":                    ("sklearn.svm",             "SVR",                   "regression"),
        "xgboost_reg":            ("xgboost",                "XGBRegressor",          "regression"),
        # Classification
        "logistic_regression":    ("sklearn.linear_model",   "LogisticRegression",    "classification"),
        "random_forest":          ("sklearn.ensemble",       "RandomForestClassifier","classification"),
        "gradient_boosting":      ("sklearn.ensemble",       "GradientBoostingClassifier", "classification"),
        "svc":                    ("sklearn.svm",             "SVC",                   "classification"),
        "xgboost":                ("xgboost",                "XGBClassifier",         "classification"),
        # Clustering
        "kmeans":                 ("sklearn.cluster",        "KMeans",                "clustering"),
        "dbscan":                 ("sklearn.cluster",        "DBSCAN",                "clustering"),
        "agglomerative":          ("sklearn.cluster",        "AgglomerativeClustering","clustering"),
        # Anomaly Detection
        "isolation_forest":       ("sklearn.ensemble",       "IsolationForest",       "anomaly"),
        "lof":                    ("sklearn.neighbors",      "LocalOutlierFactor",    "anomaly"),
        "one_class_svm":          ("sklearn.svm",            "OneClassSVM",           "anomaly"),
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"operation": "train", "algorithm": "random_forest", "X": [[1,2],[3,4]], "y": [0,1], "model_id": "my_model"}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "model_id", "type": "text", "label": "Model ID"},
                {"name": "metrics", "type": "json", "label": "Metrics"},
                {"name": "predictions", "type": "list", "label": "Predictions"},
                {"name": "feature_importance", "type": "json", "label": "Feature Importance"},
            ],
        },
        "quick_actions": [
            {"icon": "🤖", "label": "Train Model", "prompt": "Train a random forest on this dataset"},
            {"icon": "🔮", "label": "Predict", "prompt": "Run predictions with trained model"},
            {"icon": "📊", "label": "Evaluate", "prompt": "Show model performance metrics"},
            {"icon": "🔍", "label": "Explain", "prompt": "Explain feature importance"},
        ],
    }

    def __init__(self, hal_block=None, config: Dict = None):
        super().__init__(hal_block, config)
        os.makedirs(self.config.get("model_store", _MODEL_STORE), exist_ok=True)

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        operation = data.get("operation") or params.get("operation", "train")

        ops = {
            "train":           self._train,
            "predict":         self._predict,
            "evaluate":        self._evaluate,
            "explain":         self._explain,
            "list_models":     self._list_models,
            "delete_model":    self._delete_model,
            "list_algorithms": self._list_algorithms,
            "cross_validate":  self._cross_validate,
            "hyperparameter_tune": self._hyperparameter_tune,
            "feature_select":  self._feature_select,
            "drift_detect":    self._drift_detect,
        }

        handler = ops.get(operation)
        if not handler:
            return {"status": "error", "error": f"Unknown operation: {operation}. Available: {list(ops.keys())}"}

        return await handler(data, params)

    # ── Train ──────────────────────────────────────────────────────────────────

    async def _train(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
            from sklearn.model_selection import train_test_split
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        X = np.array(data.get("X") or params.get("X", []))
        y_raw = data.get("y") or params.get("y")
        y = np.array(y_raw) if y_raw is not None else None

        if X.size == 0:
            return {"status": "error", "error": "X (features) required for training"}

        algorithm = data.get("algorithm") or params.get("algorithm", self.config.get("default_algorithm", "random_forest"))
        model_id = data.get("model_id") or params.get("model_id") or _auto_id(algorithm)
        algo_params = data.get("algo_params", {})
        test_size = float(data.get("test_size", self.config.get("test_size", 0.2)))
        auto_scale = data.get("auto_scale", self.config.get("auto_scale", True))

        algo_info = self._ALGORITHMS.get(algorithm)
        if not algo_info:
            return {"status": "error", "error": f"Unknown algorithm: {algorithm}. Use list_algorithms."}

        try:
            model_instance = self._instantiate(algo_info[0], algo_info[1], algo_params)
        except Exception as e:
            return {"status": "error", "error": f"Algorithm instantiation error: {e}"}

        task_type = algo_info[2]
        metrics: Dict = {}
        start = time.time()

        try:
            if task_type == "clustering":
                if auto_scale:
                    scaler = StandardScaler()
                    X_scaled = scaler.fit_transform(X)
                    model_instance.fit(X_scaled)
                    labels = model_instance.labels_ if hasattr(model_instance, "labels_") else model_instance.fit_predict(X_scaled)
                else:
                    model_instance.fit(X)
                    labels = model_instance.labels_ if hasattr(model_instance, "labels_") else model_instance.fit_predict(X)
                metrics = {"n_clusters": int(len(set(labels)) - (1 if -1 in labels else 0)), "noise_points": int((labels == -1).sum())}
                pipeline = Pipeline([("scaler", StandardScaler()), ("model", model_instance)]) if auto_scale else model_instance

            elif task_type == "anomaly":
                if auto_scale:
                    scaler = StandardScaler()
                    X_scaled = scaler.fit_transform(X)
                    model_instance.fit(X_scaled)
                else:
                    model_instance.fit(X)
                pipeline = Pipeline([("scaler", StandardScaler()), ("model", model_instance)]) if auto_scale else model_instance
                metrics = {"fitted": True}

            else:
                if y is None:
                    return {"status": "error", "error": "y (labels/targets) required for supervised learning"}

                X_train, X_test, y_train, y_test = train_test_split(
                    X, y, test_size=test_size, random_state=self.config.get("random_state", 42)
                )

                if auto_scale:
                    pipeline = Pipeline([("scaler", StandardScaler()), ("model", model_instance)])
                else:
                    pipeline = model_instance

                pipeline.fit(X_train, y_train)
                y_pred = pipeline.predict(X_test)
                metrics = self._compute_metrics(y_test, y_pred, task_type)

        except Exception as e:
            return {"status": "error", "error": f"Training error: {e}"}

        train_time = round(time.time() - start, 3)

        # Feature importance
        feat_importance = self._get_feature_importance(
            model_instance,
            data.get("feature_names", [f"f{i}" for i in range(X.shape[1])])
        )

        # Persist model
        self._save_model(model_id, pipeline, {
            "algorithm": algorithm,
            "task_type": task_type,
            "feature_names": data.get("feature_names", []),
            "metrics": metrics,
            "train_samples": X.shape[0],
            "n_features": X.shape[1],
            "trained_at": time.time(),
        })

        return {
            "status": "success",
            "model_id": model_id,
            "algorithm": algorithm,
            "task_type": task_type,
            "metrics": metrics,
            "train_time_s": train_time,
            "train_samples": X.shape[0],
            "n_features": X.shape[1],
            "feature_importance": feat_importance,
        }

    # ── Predict ────────────────────────────────────────────────────────────────

    async def _predict(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        model_id = data.get("model_id") or params.get("model_id")
        X = np.array(data.get("X") or params.get("X", []))

        if not model_id:
            return {"status": "error", "error": "model_id required"}
        if X.size == 0:
            return {"status": "error", "error": "X (features) required"}

        pipeline, meta = self._load_model(model_id)
        if pipeline is None:
            return {"status": "error", "error": f"Model '{model_id}' not found"}

        try:
            predictions = pipeline.predict(X).tolist()
            probabilities = None
            if hasattr(pipeline, "predict_proba"):
                try:
                    probabilities = pipeline.predict_proba(X).tolist()
                except Exception:
                    pass
        except Exception as e:
            return {"status": "error", "error": f"Prediction error: {e}"}

        return {
            "status": "success",
            "model_id": model_id,
            "predictions": predictions,
            "probabilities": probabilities,
            "n_samples": len(predictions),
            "algorithm": meta.get("algorithm", ""),
            "task_type": meta.get("task_type", ""),
        }

    # ── Evaluate ───────────────────────────────────────────────────────────────

    async def _evaluate(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        model_id = data.get("model_id") or params.get("model_id")
        X = np.array(data.get("X") or params.get("X", []))
        y = np.array(data.get("y") or params.get("y", []))

        if not model_id:
            return {"status": "error", "error": "model_id required"}

        pipeline, meta = self._load_model(model_id)
        if pipeline is None:
            return {"status": "error", "error": f"Model '{model_id}' not found"}

        try:
            y_pred = pipeline.predict(X)
            metrics = self._compute_metrics(y, y_pred, meta.get("task_type", "regression"))
        except Exception as e:
            return {"status": "error", "error": f"Evaluation error: {e}"}

        return {
            "status": "success",
            "model_id": model_id,
            "metrics": metrics,
            "n_samples": len(y),
            "algorithm": meta.get("algorithm", ""),
        }

    # ── Explain ────────────────────────────────────────────────────────────────

    async def _explain(self, data: Dict, params: Dict) -> Dict:
        model_id = data.get("model_id") or params.get("model_id")
        if not model_id:
            return {"status": "error", "error": "model_id required"}

        pipeline, meta = self._load_model(model_id)
        if pipeline is None:
            return {"status": "error", "error": f"Model '{model_id}' not found"}

        model = pipeline.named_steps.get("model", pipeline) if hasattr(pipeline, "named_steps") else pipeline
        feature_names = data.get("feature_names") or meta.get("feature_names") or []

        importance = self._get_feature_importance(model, feature_names)

        # SHAP if available and X provided
        shap_values = None
        X_raw = data.get("X")
        if X_raw:
            try:
                import shap
                import numpy as np
                X = np.array(X_raw)
                explainer = shap.TreeExplainer(model)
                shap_values = explainer.shap_values(X).tolist()
            except Exception:
                pass

        return {
            "status": "success",
            "model_id": model_id,
            "feature_importance": importance,
            "shap_values": shap_values,
            "algorithm": meta.get("algorithm", ""),
            "n_features": meta.get("n_features", 0),
            "feature_names": feature_names,
        }

    # ── Cross-validate ─────────────────────────────────────────────────────────

    async def _cross_validate(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
            from sklearn.model_selection import cross_val_score
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        X = np.array(data.get("X") or params.get("X", []))
        y = np.array(data.get("y") or params.get("y", []))
        algorithm = data.get("algorithm") or params.get("algorithm", "random_forest")
        cv = int(data.get("cv", 5))
        scoring = data.get("scoring") or ("r2" if algorithm.endswith("_reg") or algorithm in ("linear_regression", "ridge", "lasso", "svr") else "accuracy")

        algo_info = self._ALGORITHMS.get(algorithm)
        if not algo_info:
            return {"status": "error", "error": f"Unknown algorithm: {algorithm}"}

        model_instance = self._instantiate(algo_info[0], algo_info[1], data.get("algo_params", {}))
        pipeline = Pipeline([("scaler", StandardScaler()), ("model", model_instance)])

        try:
            scores = cross_val_score(pipeline, X, y, cv=cv, scoring=scoring)
        except Exception as e:
            return {"status": "error", "error": f"Cross-validation error: {e}"}

        return {
            "status": "success",
            "algorithm": algorithm,
            "cv_folds": cv,
            "scoring": scoring,
            "scores": scores.tolist(),
            "mean": round(float(scores.mean()), 4),
            "std": round(float(scores.std()), 4),
            "min": round(float(scores.min()), 4),
            "max": round(float(scores.max()), 4),
        }

    # ── Hyperparameter tuning ──────────────────────────────────────────────────

    async def _hyperparameter_tune(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
            from sklearn.model_selection import GridSearchCV, RandomizedSearchCV
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        X = np.array(data.get("X") or params.get("X", []))
        y = np.array(data.get("y") or params.get("y", []))
        algorithm = data.get("algorithm") or params.get("algorithm", "random_forest")
        param_grid = data.get("param_grid", {})
        method = data.get("method", "random")   # "grid" or "random"
        n_iter = int(data.get("n_iter", 10))
        cv = int(data.get("cv", 3))

        algo_info = self._ALGORITHMS.get(algorithm)
        if not algo_info:
            return {"status": "error", "error": f"Unknown algorithm: {algorithm}"}

        model_instance = self._instantiate(algo_info[0], algo_info[1], {})
        pipeline = Pipeline([("scaler", StandardScaler()), ("model", model_instance)])

        # Prefix param_grid keys with "model__"
        prefixed = {f"model__{k}": v for k, v in param_grid.items()}

        try:
            if method == "grid":
                search = GridSearchCV(pipeline, prefixed, cv=cv, n_jobs=-1)
            else:
                search = RandomizedSearchCV(pipeline, prefixed, n_iter=n_iter, cv=cv, n_jobs=-1, random_state=42)
            search.fit(X, y)
        except Exception as e:
            return {"status": "error", "error": f"Tuning error: {e}"}

        best_params = {k.replace("model__", ""): v for k, v in search.best_params_.items()}

        return {
            "status": "success",
            "algorithm": algorithm,
            "best_params": best_params,
            "best_score": round(float(search.best_score_), 4),
            "method": method,
            "n_iter": n_iter,
        }

    # ── Feature selection ──────────────────────────────────────────────────────

    async def _feature_select(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
            from sklearn.feature_selection import SelectKBest, f_classif, f_regression, mutual_info_classif
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        X = np.array(data.get("X") or params.get("X", []))
        y = np.array(data.get("y") or params.get("y", []))
        k = int(data.get("k", min(10, X.shape[1] if X.ndim > 1 else 1)))
        task = data.get("task_type", "classification")
        feature_names = data.get("feature_names", [f"f{i}" for i in range(X.shape[1] if X.ndim > 1 else 0)])

        score_fn = f_regression if task == "regression" else f_classif

        try:
            selector = SelectKBest(score_fn, k=k)
            selector.fit(X, y)
            mask = selector.get_support()
            scores = selector.scores_

            selected = [
                {"feature": feature_names[i] if i < len(feature_names) else f"f{i}",
                 "score": round(float(scores[i]), 4),
                 "selected": bool(mask[i])}
                for i in range(len(scores))
            ]
            selected.sort(key=lambda x: x["score"], reverse=True)
        except Exception as e:
            return {"status": "error", "error": f"Feature selection error: {e}"}

        return {
            "status": "success",
            "selected_features": [s["feature"] for s in selected if s["selected"]],
            "all_scores": selected,
            "k": k,
            "task_type": task,
        }

    # ── Drift detection ────────────────────────────────────────────────────────

    async def _drift_detect(self, data: Dict, params: Dict) -> Dict:
        try:
            import numpy as np
            from scipy.stats import ks_2samp, chi2_contingency
        except ImportError as e:
            return {"status": "error", "error": f"Missing dep: {e}"}

        X_ref = np.array(data.get("X_reference") or params.get("X_reference", []))
        X_new = np.array(data.get("X_new") or params.get("X_new", []))
        threshold = float(data.get("p_threshold", 0.05))
        feature_names = data.get("feature_names", [f"f{i}" for i in range(X_ref.shape[1] if X_ref.ndim > 1 else 0)])

        if X_ref.size == 0 or X_new.size == 0:
            return {"status": "error", "error": "X_reference and X_new required"}

        results = []
        n_features = X_ref.shape[1] if X_ref.ndim > 1 else 1

        for i in range(n_features):
            col_ref = X_ref[:, i] if X_ref.ndim > 1 else X_ref
            col_new = X_new[:, i] if X_new.ndim > 1 else X_new
            stat, p_val = ks_2samp(col_ref, col_new)
            fname = feature_names[i] if i < len(feature_names) else f"f{i}"
            results.append({
                "feature": fname,
                "ks_statistic": round(float(stat), 4),
                "p_value": round(float(p_val), 6),
                "drift_detected": bool(p_val < threshold),
            })

        drifted = [r["feature"] for r in results if r["drift_detected"]]

        return {
            "status": "success",
            "drift_detected": len(drifted) > 0,
            "drifted_features": drifted,
            "n_drifted": len(drifted),
            "n_features": n_features,
            "p_threshold": threshold,
            "feature_results": results,
        }

    # ── Model management ───────────────────────────────────────────────────────

    async def _list_models(self, data: Dict, params: Dict) -> Dict:
        store = self.config.get("model_store", _MODEL_STORE)
        models = []
        if os.path.exists(store):
            for fname in os.listdir(store):
                if fname.endswith(".meta.json"):
                    model_id = fname[:-len(".meta.json")]
                    meta_path = os.path.join(store, fname)
                    try:
                        with open(meta_path) as f:
                            meta = json.load(f)
                        models.append({"model_id": model_id, **meta})
                    except Exception:
                        models.append({"model_id": model_id})
        return {"status": "success", "models": models, "count": len(models)}

    async def _delete_model(self, data: Dict, params: Dict) -> Dict:
        model_id = data.get("model_id") or params.get("model_id")
        if not model_id:
            return {"status": "error", "error": "model_id required"}
        store = self.config.get("model_store", _MODEL_STORE)
        deleted = []
        for suffix in (".pkl", ".meta.json"):
            path = os.path.join(store, model_id + suffix)
            if os.path.exists(path):
                os.remove(path)
                deleted.append(path)
        return {"status": "success", "model_id": model_id, "deleted_files": deleted}

    async def _list_algorithms(self, data: Dict, params: Dict) -> Dict:
        by_type: Dict[str, List[str]] = {}
        for name, info in self._ALGORITHMS.items():
            t = info[2]
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(name)
        return {"status": "success", "algorithms": by_type, "total": len(self._ALGORITHMS)}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _instantiate(self, module: str, cls_name: str, params: Dict):
        import importlib
        mod = importlib.import_module(module)
        cls = getattr(mod, cls_name)
        return cls(**params)

    def _compute_metrics(self, y_true, y_pred, task_type: str) -> Dict:
        try:
            import numpy as np
            if task_type == "regression":
                from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
                mae = float(mean_absolute_error(y_true, y_pred))
                rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
                r2 = float(r2_score(y_true, y_pred))
                mean_y = float(np.mean(np.abs(y_true)))
                mape = mae / mean_y if mean_y > 0 else 0.0
                return {"mae": round(mae, 4), "rmse": round(rmse, 4), "r2": round(r2, 4), "mape": round(mape, 4)}
            else:
                from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
                acc = float(accuracy_score(y_true, y_pred))
                f1 = float(f1_score(y_true, y_pred, average="weighted", zero_division=0))
                prec = float(precision_score(y_true, y_pred, average="weighted", zero_division=0))
                rec = float(recall_score(y_true, y_pred, average="weighted", zero_division=0))
                return {"accuracy": round(acc, 4), "f1": round(f1, 4), "precision": round(prec, 4), "recall": round(rec, 4)}
        except Exception as e:
            return {"error": str(e)}

    def _get_feature_importance(self, model, feature_names: List[str]) -> Dict:
        importance = {}
        try:
            if hasattr(model, "feature_importances_"):
                imp = model.feature_importances_
                names = feature_names if len(feature_names) == len(imp) else [f"f{i}" for i in range(len(imp))]
                pairs = sorted(zip(names, imp.tolist()), key=lambda x: x[1], reverse=True)
                importance = {name: round(val, 5) for name, val in pairs}
            elif hasattr(model, "coef_"):
                import numpy as np
                coef = model.coef_.flatten() if model.coef_.ndim > 1 else model.coef_
                names = feature_names if len(feature_names) == len(coef) else [f"f{i}" for i in range(len(coef))]
                pairs = sorted(zip(names, [abs(c) for c in coef.tolist()]), key=lambda x: x[1], reverse=True)
                importance = {name: round(val, 5) for name, val in pairs}
        except Exception:
            pass
        return importance

    def _save_model(self, model_id: str, pipeline, meta: Dict):
        store = self.config.get("model_store", _MODEL_STORE)
        os.makedirs(store, exist_ok=True)
        with open(os.path.join(store, model_id + ".pkl"), "wb") as f:
            pickle.dump(pipeline, f)
        with open(os.path.join(store, model_id + ".meta.json"), "w") as f:
            json.dump(meta, f, indent=2)

    def _load_model(self, model_id: str):
        store = self.config.get("model_store", _MODEL_STORE)
        pkl_path = os.path.join(store, model_id + ".pkl")
        meta_path = os.path.join(store, model_id + ".meta.json")
        if not os.path.exists(pkl_path):
            return None, {}
        try:
            with open(pkl_path, "rb") as f:
                pipeline = pickle.load(f)
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    meta = json.load(f)
            return pipeline, meta
        except Exception:
            return None, {}


def _auto_id(algorithm: str) -> str:
    ts = str(int(time.time()))[-6:]
    return f"{algorithm}_{ts}"
