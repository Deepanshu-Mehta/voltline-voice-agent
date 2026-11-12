import pytest

from app.tools import match_product, run_tool


@pytest.mark.parametrize("query, sku", [
    ("Pulse Buds", "VL-A100"),
    ("pulse buds pro", "VL-A300"),
    ("VL-A300", "VL-A300"),
    ("a300", "VL-A300"),
    ("the horizon headphones", "VL-H500"),
    ("keyboard", "VL-K800"),
    ("dock", "VL-D900"),
    ("outdoor camera", "VL-C450"),
    ("65 watt charger", "VL-G650"),
])
def test_match_product(products, query, sku):
    match, _ = match_product(query, products)
    assert match is not None and match.sku == sku


def test_ambiguous_product_returns_candidates(products):
    match, candidates = match_product("safeview camera", products)
    assert match is None
    assert {c.sku for c in candidates} >= {"VL-C420", "VL-C450"}


def test_unknown_product(tool_ctx):
    result = run_tool(tool_ctx, "get_price", {"product": "PlayStation 5"})
    assert result["found"] is False


def test_order_status_accepts_spoken_digits(tool_ctx):
    result = run_tool(tool_ctx, "get_order_status", {"order_id": "4 5 1 2"})
    assert result["found"] and result["status"] == "shipped"
    assert result["order_id_spoken"] == "4 5 1 2"
    assert result["items"] == ["1 x Pulse Buds Pro"]
    assert "estimated_delivery" in result


def test_unknown_order(tool_ctx):
    assert run_tool(tool_ctx, "get_order_status", {"order_id": "9999"})["found"] is False


def test_stock_out_of_stock_has_restock_date(tool_ctx):
    result = run_tool(tool_ctx, "check_stock", {"product": "Horizon Headphones"})
    assert result["in_stock"] is False
    assert "December 4" in result["restock_expected"]


def test_stock_low(tool_ctx):
    result = run_tool(tool_ctx, "check_stock", {"product": "VL-C450"})
    assert result["in_stock"] and result["availability"].startswith("low stock")


def test_price(tool_ctx):
    result = run_tool(tool_ctx, "get_price", {"product": "Pulse Buds Pro"})
    assert result["price_text"] == "$149.99" and result["care_plus_price_usd"] == 29


def test_callback_validates_phone(tool_ctx):
    assert run_tool(tool_ctx, "book_callback", {"name": "Sam", "phone": "555", "preferred_time": "noon"})["booked"] is False
    ok = run_tool(tool_ctx, "book_callback", {"name": "Sam", "phone": "+1 (512) 555-0147", "preferred_time": "noon"})
    assert ok["booked"] and ok["callback_id"] == 1001
    assert ok["phone_spoken"] == "5 1 2, 5 5 5, 0 1 4 7"


def test_ticket_numbers_do_not_look_like_orders(tool_ctx):
    result = run_tool(tool_ctx, "create_support_ticket", {"issue": "Left earbud is silent", "order_id": "4512"})
    assert result["created"] and result["ticket_number"] == 20001


def test_tool_errors_are_returned_not_raised(tool_ctx):
    assert "error" in run_tool(tool_ctx, "get_price", {})
    assert "error" in run_tool(tool_ctx, "nope", {})
