"""Plan-to-product mapping for Klai subscription tiers."""

PLAN_PRODUCTS: dict[str, list[str]] = {
    "free": [],
    "core": ["chat"],
    "professional": ["chat", "scribe"],
    "complete": ["chat", "scribe", "knowledge"],
}


def get_plan_products(plan: str) -> list[str]:
    """Return products for a plan. Returns [] for unknown plans (safe default)."""
    return PLAN_PRODUCTS.get(plan, [])
