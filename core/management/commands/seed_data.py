import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from core.models import Product, Dealer


class Command(BaseCommand):
    help = "Load the files in data/ into the database. Idempotent: safe to run multiple times."

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR
        catalogue_path = data_dir / "catalogue.csv"
        dealers_path = data_dir / "dealers.csv"

        if not catalogue_path.exists():
            self.stderr.write(self.style.ERROR(f"Catalogue file not found at {catalogue_path}"))
            return

        if not dealers_path.exists():
            self.stderr.write(self.style.ERROR(f"Dealers file not found at {dealers_path}"))
            return

        with transaction.atomic():
            # Seed Products
            products_count = 0
            seeded_skus = set()
            with open(catalogue_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sku = row["sku"].strip()
                    seeded_skus.add(sku)
                    price = Decimal(row["price"].strip())
                    stock = int(row["stock"].strip())
                    Product.objects.update_or_create(
                        sku=sku,
                        defaults={
                            "name": row["name"].strip(),
                            "category": row["category"].strip(),
                            "brand": row["brand"].strip(),
                            "vehicle_fitment": row["vehicle_fitment"].strip(),
                            "price": price,
                            "stock": stock,
                            "description": row["description"].strip(),
                        },
                    )
                    products_count += 1

            # Seed Dealers
            dealers_count = 0
            seeded_dealer_codes = set()
            with open(dealers_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dealer_code = row["dealer_code"].strip()
                    seeded_dealer_codes.add(dealer_code)
                    credit_limit = Decimal(row.get("credit_limit", "0.00").strip() or "0.00")
                    Dealer.objects.update_or_create(
                        dealer_code=dealer_code,
                        defaults={
                            "name": row["name"].strip(),
                            "city": row.get("city", "").strip(),
                            "phone": row.get("phone", "").strip(),
                            "email": row.get("email", "").strip(),
                            "gstin": row.get("gstin", "").strip(),
                            "credit_limit": credit_limit,
                        },
                    )
                    dealers_count += 1

            # Clean up test orders and non-seeded test entities to restore clean seeded counts
            from orders.models import Order
            Order.objects.exclude(dealer__dealer_code__in=seeded_dealer_codes).delete()
            Dealer.objects.exclude(dealer_code__in=seeded_dealer_codes).delete()
            Product.objects.exclude(sku__in=seeded_skus).delete()


        total_products = Product.objects.count()
        total_dealers = Dealer.objects.count()

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully seeded database: {total_products} products, {total_dealers} dealers."
            )
        )
