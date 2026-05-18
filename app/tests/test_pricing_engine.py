"""
PricingEngine unit tests with a mocked Energy-Charts response.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.pricing.PricingEngine import PricingEngine


ANCHOR = datetime(2026, 5, 18, 8, 0, tzinfo=UTC)


def _fake_response(slot_count: int = 192, start: datetime = ANCHOR, base_eur_mwh: float = 80.0):
    """Build a fake Energy-Charts /price response covering `slot_count` 15-min slots."""
    timestamps = [int((start + timedelta(minutes=15 * i)).timestamp()) for i in range(slot_count)]
    prices = [base_eur_mwh + i for i in range(slot_count)]  # EUR/MWh, ascending so we can verify lookup
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={
        "unix_seconds": timestamps,
        "price":        prices,
        "unit":         "EUR / MWh",
        "license_info": "CC BY 4.0 | Energy-Charts.info",
    })
    return mock_resp


@pytest.mark.asyncio
async def test_fetch_caches_prices_and_converts_to_eur_per_kwh(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "true")
    http = MagicMock()
    http.get = AsyncMock(return_value=_fake_response())
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)

    await engine.refresh_if_needed(ANCHOR)

    # 192 slots cached, all converted from EUR/MWh -> EUR/kWh (÷1000)
    assert len(engine._cache) == 192
    first_slot = int(ANCHOR.timestamp())
    assert engine._cache[first_slot] == pytest.approx(80.0 / 1000.0)
    assert engine.license_info == "CC BY 4.0 | Energy-Charts.info"


@pytest.mark.asyncio
async def test_get_current_price_rounds_down_to_slot(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "true")
    http = MagicMock()
    http.get = AsyncMock(return_value=_fake_response(slot_count=4))
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)
    await engine.refresh_if_needed(ANCHOR)

    # Lookup 7 minutes past the start of slot 2 (08:30 + 7min) -> should snap to 08:30 (slot index 2)
    dt = ANCHOR + timedelta(minutes=37)
    assert engine.get_current_price(dt) == pytest.approx((80.0 + 2) / 1000.0)

    # Exactly the slot boundary
    dt = ANCHOR + timedelta(minutes=45)
    assert engine.get_current_price(dt) == pytest.approx((80.0 + 3) / 1000.0)


@pytest.mark.asyncio
async def test_out_of_window_returns_flat_rate(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "true")
    monkeypatch.setenv("FLAT_RATE_EUR_PER_KWH", "0.12")
    http = MagicMock()
    http.get = AsyncMock(return_value=_fake_response(slot_count=4))  # only 1h of prices
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)
    await engine.refresh_if_needed(ANCHOR)

    # 10 hours past the cached window -> flat-rate fallback
    dt = ANCHOR + timedelta(hours=10)
    assert engine.get_current_price(dt) == pytest.approx(0.12)


@pytest.mark.asyncio
async def test_disabled_engine_never_calls_api_and_returns_flat_rate(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "false")
    monkeypatch.setenv("FLAT_RATE_EUR_PER_KWH", "0.09")
    http = MagicMock()
    http.get = AsyncMock(return_value=_fake_response())
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)

    await engine.refresh_if_needed(ANCHOR)
    assert engine.get_current_price(ANCHOR) == pytest.approx(0.09)
    http.get.assert_not_called()


@pytest.mark.asyncio
async def test_api_failure_keeps_existing_cache(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "true")
    http = MagicMock()
    http.get = AsyncMock(return_value=_fake_response(slot_count=20))
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)
    await engine.refresh_if_needed(ANCHOR)
    cached_before = dict(engine._cache)

    # Now force the next fetch to fail; existing cache must survive
    http.get.side_effect = RuntimeError("network down")
    # Bypass the rate-limit guard so a fetch is attempted
    engine._last_fetch_monotonic = 0.0
    await engine.refresh_if_needed(ANCHOR)
    assert engine._cache == cached_before
    # Cached price remains available despite the failed refresh
    assert engine.get_current_price(ANCHOR) == pytest.approx(cached_before[int(ANCHOR.timestamp())])
    assert len(cached_before) == 20


@pytest.mark.asyncio
async def test_api_failure_with_empty_cache_still_respects_min_fetch_interval(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "true")
    http = MagicMock()
    http.get = AsyncMock(side_effect=RuntimeError("network down"))
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)

    with patch("app.pricing.PricingEngine.time.monotonic", side_effect=[1000.0, 1000.0, 1001.0]):
        await engine.refresh_if_needed(ANCHOR)
        await engine.refresh_if_needed(ANCHOR)

    assert http.get.call_count == 1


@pytest.mark.asyncio
async def test_refresh_skipped_while_cache_has_enough_runway(monkeypatch):
    monkeypatch.setenv("PRICING_ENABLED", "true")
    http = MagicMock()
    # Return 48h worth of slots (192). Plenty of runway.
    http.get = AsyncMock(return_value=_fake_response(slot_count=192))
    engine = PricingEngine(http_client=http)
    engine.set_anchor(ANCHOR)
    await engine.refresh_if_needed(ANCHOR)
    assert http.get.call_count == 1

    # Bypass the rate-limit interval; cache still has >4h ahead -> no second fetch
    engine._last_fetch_monotonic = 0.0
    await engine.refresh_if_needed(ANCHOR + timedelta(hours=1))
    assert http.get.call_count == 1
