"""
Predictive Layer Block
- Linear / polynomial / exponential trend extrapolation (SymPy + numpy)
- Cost escalation projection (CAGR model)
- Schedule EAC/ETC via earned value
- Monte Carlo risk-adjusted estimates
- Confidence intervals
"""

import math
import time
from typing import Any, Dict, List, Optional, Tuple
from app.core.universal_base import UniversalBlock


class PredictiveEngineBlock(UniversalBlock):
    name = "predictive_engine"
    version = "1.0.0"
    description = "Predictive layer: cost forecasting, trend projection, EVM extrapolation, Monte Carlo simulation"
    layer = 3
    tags = ["reasoning", "prediction", "forecasting", "ml", "construction", "evm"]
    requires = []

    default_config = {
        "monte_carlo_iterations": 10000,
        "confidence_levels": [0.50, 0.80, 0.90],
        "default_escalation_rate": 0.04,   # 4% p.a. default cost escalation
    }

    ui_schema = {
        "input": {
            "type": "json",
            "placeholder": '{"operation": "cost_forecast", "base_cost": 1000000, "years": 3, "escalation_rate": 0.04}',
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "prediction",     "type": "number", "label": "Predicted Value"},
                {"name": "confidence_intervals", "type": "json", "label": "Confidence Intervals"},
                {"name": "model_used",     "type": "text",   "label": "Model"},
                {"name": "formula",        "type": "text",   "label": "Formula"},
            ],
        },
        "quick_actions": [
            {"icon": "💰", "label": "Cost Forecast",    "prompt": "Forecast cost with escalation over 3 years"},
            {"icon": "📅", "label": "EVM Forecast",     "prompt": "Project completion cost from EVM metrics"},
            {"icon": "📈", "label": "Trend Fit",        "prompt": "Fit trend to historical data points"},
            {"icon": "🎲", "label": "Monte Carlo",      "prompt": "Run Monte Carlo simulation for risk estimate"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        data = input_data if isinstance(input_data, dict) else {}

        operation = data.get("operation") or params.get("operation", "cost_forecast")

        ops = {
            "cost_forecast":    self._cost_forecast,
            "trend_fit":        self._trend_fit,
            "evm_forecast":     self._evm_forecast,
            "monte_carlo":      self._monte_carlo,
            "schedule_predict": self._schedule_predict,
            "regression":       self._regression,
        }

        handler = ops.get(operation)
        if not handler:
            return {"status": "error", "error": f"Unknown operation '{operation}'. Use: {list(ops.keys())}"}
        return await handler(data, params)

    # ── Cost Escalation Forecast ───────────────────────────────────────────────

    async def _cost_forecast(self, data: Dict, params: Dict) -> Dict:
        base_cost      = float(data.get("base_cost", 0))
        years          = float(data.get("years", 1))
        rate           = float(data.get("escalation_rate", self.config.get("default_escalation_rate", 0.04)))
        volatility     = float(data.get("volatility", rate * 0.5))   # σ of rate
        model          = data.get("model", "cagr")

        if base_cost <= 0:
            return {"status": "error", "error": "base_cost must be > 0"}

        try:
            import sympy as sp
            C0, r, t = sp.symbols("C0 r t", positive=True)

            if model == "cagr":
                expr = C0 * (1 + r) ** t
                formula = f"C0 × (1 + r)^t = {base_cost:,.2f} × (1 + {rate:.4f})^{years:.1f}"
            elif model == "continuous":
                expr = C0 * sp.exp(r * t)
                formula = f"C0 × e^(r×t) = {base_cost:,.2f} × e^({rate:.4f}×{years:.1f})"
            else:
                expr = C0 * (1 + r * t)
                formula = f"C0 × (1 + r×t) = {base_cost:,.2f} × (1 + {rate:.4f}×{years:.1f})"

            prediction = float(expr.subs({C0: base_cost, r: rate, t: years}))
            cost_increase = prediction - base_cost

        except ImportError:
            # Fallback without sympy
            prediction = base_cost * ((1 + rate) ** years)
            formula = f"{base_cost:,.2f} × (1 + {rate:.4f})^{years:.1f}"
            cost_increase = prediction - base_cost
            model = "cagr_fallback"

        # Confidence intervals via normal approximation of rate uncertainty
        ci = self._cost_ci(base_cost, rate, volatility, years)

        # Year-by-year projection
        yearly = [
            {"year": y, "cost": round(base_cost * ((1 + rate) ** y), 2)}
            for y in range(int(years) + 1)
        ]

        return {
            "status": "success",
            "prediction": round(prediction, 2),
            "base_cost": base_cost,
            "cost_increase": round(cost_increase, 2),
            "escalation_rate": rate,
            "years": years,
            "model_used": model,
            "formula": formula,
            "confidence_intervals": ci,
            "yearly_projection": yearly,
        }

    # ── Trend Fitting ──────────────────────────────────────────────────────────

    async def _trend_fit(self, data: Dict, params: Dict) -> Dict:
        x_vals = data.get("x", [])
        y_vals = data.get("y", [])
        degree = int(data.get("degree", 1))
        forecast_x = data.get("forecast_x", [])

        if len(x_vals) < 2 or len(y_vals) < 2:
            return {"status": "error", "error": "Need at least 2 (x, y) data points"}

        try:
            import numpy as np
        except ImportError:
            return {"status": "error", "error": "numpy not installed"}

        x = np.array(x_vals, dtype=float)
        y = np.array(y_vals, dtype=float)

        # Fit polynomial
        coeffs = np.polyfit(x, y, degree)
        poly = np.poly1d(coeffs)

        # R²
        y_pred = poly(x)
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        # Formula string
        terms = []
        for i, c in enumerate(coeffs):
            power = degree - i
            if power == 0:
                terms.append(f"{c:.4f}")
            elif power == 1:
                terms.append(f"{c:.4f}x")
            else:
                terms.append(f"{c:.4f}x^{power}")
        formula = " + ".join(terms)

        # Forecasts
        forecasts = []
        if forecast_x:
            for fx in forecast_x:
                forecasts.append({"x": fx, "predicted_y": round(float(poly(fx)), 4)})

        return {
            "status": "success",
            "model_used": f"polynomial_degree_{degree}",
            "formula": formula,
            "coefficients": coeffs.tolist(),
            "r_squared": round(r2, 4),
            "prediction": float(poly(x[-1])),
            "forecasts": forecasts,
            "confidence_intervals": {},
            "fitted_values": y_pred.tolist(),
        }

    # ── Earned Value Forecast ──────────────────────────────────────────────────

    async def _evm_forecast(self, data: Dict, params: Dict) -> Dict:
        bac  = float(data.get("bac",  0))    # Budget at Completion
        ev   = float(data.get("ev",   0))    # Earned Value
        ac   = float(data.get("ac",   0))    # Actual Cost
        pv   = float(data.get("pv",   0))    # Planned Value
        method = data.get("method", "cpi")   # cpi, spi, hybrid

        if bac <= 0:
            return {"status": "error", "error": "bac (Budget at Completion) must be > 0"}

        cpi  = ev / ac  if ac  > 0 else 1.0
        spi  = ev / pv  if pv  > 0 else 1.0
        sv   = ev - pv
        cv   = ev - ac

        if method == "cpi":
            eac = bac / cpi if cpi > 0 else bac
            formula = "EAC = BAC / CPI"
        elif method == "spi":
            eac = ac + (bac - ev) / spi if spi > 0 else bac
            formula = "EAC = AC + (BAC - EV) / SPI"
        elif method == "hybrid":
            w_cpi, w_spi = 0.8, 0.2
            eac = ac + (bac - ev) / (w_cpi * cpi + w_spi * spi)
            formula = "EAC = AC + (BAC - EV) / (0.8×CPI + 0.2×SPI)"
        else:
            eac = ac + (bac - ev)
            formula = "EAC = AC + (BAC - EV)"

        etc  = eac - ac
        vac  = bac - eac
        tcpi = (bac - ev) / (bac - ac) if (bac - ac) > 0 else 0

        # P50/P80 range via ±10% EAC uncertainty
        p50 = eac
        p80 = eac * 1.10
        p20 = eac * 0.92

        return {
            "status": "success",
            "prediction": round(eac, 2),
            "model_used": f"evm_{method}",
            "formula": formula,
            "eac": round(eac, 2),
            "etc": round(etc, 2),
            "vac": round(vac, 2),
            "cpi": round(cpi, 4),
            "spi": round(spi, 4),
            "sv":  round(sv, 2),
            "cv":  round(cv, 2),
            "tcpi": round(tcpi, 4),
            "confidence_intervals": {"P20": round(p20, 2), "P50": round(p50, 2), "P80": round(p80, 2)},
            "status_narrative": self._evm_narrative(cpi, spi),
        }

    # ── Monte Carlo Simulation ─────────────────────────────────────────────────

    async def _monte_carlo(self, data: Dict, params: Dict) -> Dict:
        items      = data.get("items", [])   # [{min, likely, max, name}]
        iterations = int(data.get("iterations", self.config.get("monte_carlo_iterations", 10000)))
        ci_levels  = data.get("confidence_levels", self.config.get("confidence_levels", [0.50, 0.80, 0.90]))

        if not items:
            # Fallback: simple 3-point estimate
            lo    = float(data.get("min",    data.get("optimistic",  0)))
            mode  = float(data.get("likely", data.get("most_likely", 0)))
            hi    = float(data.get("max",    data.get("pessimistic", 0)))
            if hi <= lo:
                return {"status": "error", "error": "max must be > min"}
            items = [{"name": "total", "min": lo, "likely": mode, "max": hi}]

        try:
            import numpy as np
        except ImportError:
            return {"status": "error", "error": "numpy not installed"}

        rng = np.random.default_rng(42)
        total_samples = np.zeros(iterations)

        item_stats = []
        for item in items:
            lo    = float(item.get("min",    item.get("optimistic",  0)))
            mode  = float(item.get("likely", item.get("most_likely", 0)))
            hi    = float(item.get("max",    item.get("pessimistic", 0)))
            if hi <= lo:
                hi = lo * 1.5

            # PERT distribution approximation via beta
            mean_pert = (lo + 4 * mode + hi) / 6
            std_pert  = (hi - lo) / 6
            samples   = rng.normal(mean_pert, std_pert, iterations)
            samples   = np.clip(samples, lo, hi)
            total_samples += samples

            item_stats.append({
                "name": item.get("name", "item"),
                "mean": round(float(np.mean(samples)), 2),
                "std":  round(float(np.std(samples)),  2),
                "p10":  round(float(np.percentile(samples, 10)), 2),
                "p50":  round(float(np.percentile(samples, 50)), 2),
                "p90":  round(float(np.percentile(samples, 90)), 2),
            })

        ci_results = {
            f"P{int(lvl*100)}": round(float(np.percentile(total_samples, lvl * 100)), 2)
            for lvl in ci_levels
        }

        return {
            "status": "success",
            "model_used": "monte_carlo_pert",
            "iterations": iterations,
            "prediction": round(float(np.mean(total_samples)), 2),
            "mean": round(float(np.mean(total_samples)), 2),
            "std":  round(float(np.std(total_samples)),  2),
            "min":  round(float(np.min(total_samples)),  2),
            "max":  round(float(np.max(total_samples)),  2),
            "confidence_intervals": ci_results,
            "item_statistics": item_stats,
            "formula": "PERT: mean=(min+4×likely+max)/6, σ=(max−min)/6",
        }

    # ── Schedule Prediction ────────────────────────────────────────────────────

    async def _schedule_predict(self, data: Dict, params: Dict) -> Dict:
        planned_duration = float(data.get("planned_duration_days", 0))
        actual_progress  = float(data.get("actual_progress_pct",   0))
        elapsed_days     = float(data.get("elapsed_days",          0))
        spi              = float(data.get("spi", 1.0))

        if planned_duration <= 0:
            return {"status": "error", "error": "planned_duration_days required"}

        remaining_work_pct = max(0, 100 - actual_progress)
        if spi > 0:
            predicted_remaining_days = (remaining_work_pct / 100 * planned_duration) / spi
        else:
            predicted_remaining_days = remaining_work_pct / 100 * planned_duration

        predicted_total = elapsed_days + predicted_remaining_days
        delay_days      = predicted_total - planned_duration

        return {
            "status": "success",
            "model_used": "spi_extrapolation",
            "prediction": round(predicted_total, 1),
            "predicted_total_days": round(predicted_total, 1),
            "predicted_remaining_days": round(predicted_remaining_days, 1),
            "delay_days": round(delay_days, 1),
            "on_schedule": delay_days <= 0,
            "spi": spi,
            "formula": "Predicted = elapsed + (remaining_work / SPI × planned_duration)",
            "confidence_intervals": {
                "P50": round(predicted_total, 1),
                "P80": round(predicted_total * 1.10, 1),
            },
        }

    # ── Symbolic Regression ────────────────────────────────────────────────────

    async def _regression(self, data: Dict, params: Dict) -> Dict:
        x_vals  = data.get("x", [])
        y_vals  = data.get("y", [])
        model   = data.get("model", "linear")   # linear, polynomial, exponential, power

        if len(x_vals) < 2:
            return {"status": "error", "error": "Need at least 2 data points"}

        try:
            import numpy as np
        except ImportError:
            return {"status": "error", "error": "numpy not installed"}

        x = np.array(x_vals, dtype=float)
        y = np.array(y_vals, dtype=float)

        if model == "exponential":
            log_y = np.log(np.clip(y, 1e-9, None))
            coeffs = np.polyfit(x, log_y, 1)
            a = math.exp(coeffs[1])
            b = coeffs[0]
            y_pred = a * np.exp(b * x)
            formula = f"y = {a:.4f} × e^({b:.4f}×x)"
        elif model == "power":
            log_x = np.log(np.clip(x, 1e-9, None))
            log_y = np.log(np.clip(y, 1e-9, None))
            coeffs = np.polyfit(log_x, log_y, 1)
            a = math.exp(coeffs[1])
            b = coeffs[0]
            y_pred = a * (x ** b)
            formula = f"y = {a:.4f} × x^{b:.4f}"
        else:
            degree = int(data.get("degree", 1))
            coeffs = np.polyfit(x, y, degree)
            poly   = np.poly1d(coeffs)
            y_pred = poly(x)
            formula = str(poly)
            coeffs  = coeffs.tolist()

        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum((y - np.mean(y)) ** 2))
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        forecast_x = float(data.get("forecast_x", x[-1]))
        if model == "exponential":
            forecast_y = a * math.exp(b * forecast_x)
        elif model == "power":
            forecast_y = a * (forecast_x ** b)
        else:
            forecast_y = float(np.poly1d(coeffs)(forecast_x))

        return {
            "status": "success",
            "model_used": model,
            "formula": formula,
            "r_squared": round(r2, 4),
            "prediction": round(forecast_y, 4),
            "forecast_x": forecast_x,
            "confidence_intervals": {},
            "fitted_values": y_pred.tolist(),
        }

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _cost_ci(self, base: float, rate: float, vol: float, years: float) -> Dict:
        try:
            import numpy as np
            rng = np.random.default_rng(42)
            rates = rng.normal(rate, vol, 20000)
            costs = base * ((1 + rates) ** years)
            return {
                f"P{int(lvl*100)}": round(float(np.percentile(costs, lvl * 100)), 2)
                for lvl in self.config.get("confidence_levels", [0.50, 0.80, 0.90])
            }
        except Exception:
            return {"P50": round(base * (1 + rate) ** years, 2)}

    def _evm_narrative(self, cpi: float, spi: float) -> str:
        cost_status = "over budget" if cpi < 1 else "under budget" if cpi > 1 else "on budget"
        sched_status = "behind schedule" if spi < 1 else "ahead of schedule" if spi > 1 else "on schedule"
        return f"Project is {cost_status} (CPI={cpi:.3f}) and {sched_status} (SPI={spi:.3f})"
