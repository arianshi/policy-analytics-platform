"""Approximate list prices in USD per 1M tokens (input, output). Estimates for cost *tracking* only -
verify against each vendor's current price sheet before relying on them for billing."""

PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-pro": (1.25, 5.00),
    "anthropic.claude-3-5-haiku-20241022-v1:0": (0.80, 4.00),
    "offline": (0.0, 0.0),
}
DEFAULT_PRICE = (1.00, 3.00)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, DEFAULT_PRICE)
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 6)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
