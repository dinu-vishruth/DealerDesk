import csv
import math
import statistics
from pathlib import Path
from django.conf import settings
from core.models import Product

BASE_DIR = settings.BASE_DIR
DATA_DIR = settings.DATA_DIR
ML_DIR = BASE_DIR / "ml"


class ForecastingService:
    _instance = None

    def __init__(self):
        self.forecast_cache = {}
        self.lead_times = {}
        self.sigmas = {}
        self._load_data()

    def _load_data(self):
        # 1. Load lead times
        lead_path = DATA_DIR / "lead_times.csv"
        if lead_path.exists():
            with open(lead_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    self.lead_times[row["sku"]] = int(row["lead_time_weeks"])

        # 2. Load forecast.csv
        forecast_path = ML_DIR / "forecast.csv"
        if forecast_path.exists():
            with open(forecast_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sku = row["sku"]
                    if sku not in self.forecast_cache:
                        self.forecast_cache[sku] = []
                    self.forecast_cache[sku].append({
                        "week_start": row["week_start"],
                        "forecast_units": float(row["forecast_units"]),
                    })

        # 3. Compute sample std dev (sigma) from sales_history.csv
        sales_path = DATA_DIR / "sales_history.csv"
        if sales_path.exists():
            sales_by_sku = {}
            with open(sales_path, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sku = row["sku"]
                    if sku not in sales_by_sku:
                        sales_by_sku[sku] = []
                    sales_by_sku[sku].append((row["week_start"], float(row["units_sold"])))

            for sku, history in sales_by_sku.items():
                history.sort(key=lambda x: x[0])
                recent_12 = [x[1] for x in history[-12:]]
                if len(recent_12) > 1:
                    # Sample standard deviation (n - 1)
                    self.sigmas[sku] = statistics.stdev(recent_12)
                else:
                    self.sigmas[sku] = 0.0

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get_forecast_for_sku(self, sku: str):
        # Always verify if forecast exists for sku
        return self.forecast_cache.get(sku)

    def get_reorder_suggestions(self):
        """
        Computes reorder suggestions for all 40 forecast SKUs.
        Reads live current_stock from Product table at request time.
        """
        # Batch fetch live stock for all target SKUs
        target_skus = sorted(self.lead_times.keys())
        live_stocks = dict(
            Product.objects.filter(sku__in=target_skus).values_list("sku", "stock")
        )

        results = []
        for sku in target_skus:
            L = self.lead_times[sku]
            fc_list = self.forecast_cache.get(sku, [])
            # First L weeks of forecast
            lead_time_demand = sum(x["forecast_units"] for x in fc_list[:L])
            sigma = self.sigmas.get(sku, 0.0)

            # Contract: safety_stock = ceil(1.65 * sigma * sqrt(L))
            safety_stock = math.ceil(1.65 * sigma * math.sqrt(L))

            # reorder_point = lead_time_demand + safety_stock
            reorder_point = lead_time_demand + safety_stock

            current_stock = live_stocks.get(sku, 0)

            # reorder_qty = max(0, ceil(reorder_point - current_stock))
            reorder_qty = max(0, math.ceil(reorder_point - current_stock))

            results.append({
                "sku": sku,
                "current_stock": current_stock,
                "lead_time_weeks": L,
                "lead_time_demand": round(lead_time_demand, 1),
                "safety_stock": safety_stock,
                "reorder_point": round(reorder_point, 1),
                "reorder_qty": reorder_qty,
            })

        return results
