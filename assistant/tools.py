import json
from django.db.models import Q
from core.models import Product
from orders.services import create_draft_order_service
from core.exceptions import APIError

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "search_parts",
            "description": "Search the catalogue for motorcycle or car parts by query, vehicle model, or category. Retrieves live matching products with price and stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords describing the part (e.g. 'brake pads', 'chain sprocket', 'oil filter')."
                    },
                    "vehicle": {
                        "type": "string",
                        "description": "Vehicle make and model (e.g. 'Bajaj Pulsar 150', 'KTM Duke 390', 'Royal Enfield Classic 350')."
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category filter (e.g. 'Brakes', 'Chains', 'Engine', 'Body')."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Check the exact live stock and current price for a specific product SKU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sku": {
                        "type": "string",
                        "description": "The exact product SKU, e.g. 'BRK-1044'."
                    }
                },
                "required": ["sku"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_draft_order",
            "description": "Create a DRAFT order for the dealer with one or more product items. Note: This creates a DRAFT only and does not confirm or deduct stock.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dealer_id": {
                        "type": "integer",
                        "description": "The ID of the dealer placing the draft order."
                    },
                    "items": {
                        "type": "array",
                        "description": "List of order items with sku and integer quantity >= 1.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "integer", "minimum": 1}
                            },
                            "required": ["sku", "quantity"]
                        }
                    }
                },
                "required": ["dealer_id", "items"]
            }
        }
    }
]


def execute_search_parts(query: str, vehicle: str = None, category: str = None) -> dict:
    qs = Product.objects.all()

    if vehicle:
        v = vehicle.strip()
        qs = qs.filter(Q(vehicle_fitment__icontains=v) | Q(vehicle_fitment__icontains="Universal"))

    if category:
        qs = qs.filter(category__icontains=category.strip())

    if query:
        q_clean = query.strip()
        # Look for multi-token match or general contains
        tokens = [t for t in q_clean.split() if len(t) > 2]
        query_filter = Q(name__icontains=q_clean) | Q(sku__icontains=q_clean) | Q(brand__icontains=q_clean) | Q(description__icontains=q_clean)
        for token in tokens:
            query_filter |= Q(name__icontains=token)
        qs = qs.filter(query_filter)

    results = []
    for p in qs[:8]:
        results.append({
            "sku": p.sku,
            "name": p.name,
            "category": p.category,
            "brand": p.brand,
            "vehicle_fitment": p.vehicle_fitment,
            "price": f"{p.price:.2f}",
            "stock": p.stock,
            # Untrusted description trimmed to summary without potential injection payloads
            "description": p.description[:120] if p.description else "",
        })

    return {"count": len(results), "products": results}


def execute_check_stock(sku: str) -> dict:
    try:
        p = Product.objects.get(sku=sku.strip())
        return {
            "sku": p.sku,
            "name": p.name,
            "stock": p.stock,
            "price": f"{p.price:.2f}",
            "vehicle_fitment": p.vehicle_fitment,
        }
    except Product.DoesNotExist:
        return {"error": f"Product with SKU '{sku}' not found in database."}


def execute_create_draft_order(dealer_id: int, items: list, actions_list: list) -> dict:
    try:
        order = create_draft_order_service(dealer_id=dealer_id, items_data=items)
        action_entry = {
            "type": "draft_order_created",
            "order_number": order.order_number,
        }
        actions_list.append(action_entry)
        return {
            "status": "success",
            "order_number": order.order_number,
            "dealer_id": order.dealer_id,
            "status_text": order.status,
            "grand_total": f"{order.grand_total:.2f}",
            "items_count": order.items.count(),
        }
    except APIError as e:
        return {"error": e.message, "code": e.code}
    except Exception as e:
        return {"error": str(e)}
