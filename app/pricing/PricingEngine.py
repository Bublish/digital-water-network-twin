"""
PricingEngine — real-time EPEX spot price lookup for the simulation.

Fetches DE-LU day-ahead prices from the Energy-Charts API (CC BY 4.0) in
15-minute resolution and exposes a synchronous `get_current_price` lookup
by simulated datetime.

Clock model
-----------
The simulation has a `time_scale`: sim_time may advance much faster than
wall-clock time. Pricing is anchored to wall-clock UTC at /sim/start so
sim_dt maps onto real EPEX timestamps. At high time_scale, sim_dt will
eventually outrun published prices (EPEX publishes ~today + tomorrow);
when that happens, the engine falls back to FLAT_RATE_EUR_PER_KWH.

Config (env vars, read at construction)
---------------------------------------
- PRICING_ENABLED          : "true"/"false". Default "true". When false,
                             API is never called; get_current_price always
                             returns the flat rate.
- FLAT_RATE_EUR_PER_KWH    : float, default 0.08. Used when disabled, on
                             API failure with no prior data, or when sim_dt
                             is outside the cached/fetchable window.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.energy-charts.info/price"
BIDDING_ZONE = "DE-LU"
SLOT_SECONDS = 900  # 15 minutes
FETCH_WINDOW_HOURS = 48
REFRESH_THRESHOLD_HOURS = 4
MIN_FETCH_INTERVAL_SEC = 60.0          # rate-limit real-time API calls
FUTURE_BACKOFF_SEC = 300.0             # back off when API has nothing new
FETCH_TIMEOUT_SEC = 5.0

BERLIN_TZ = ZoneInfo("Europe/Berlin")


def _floor_to_slot(dt: datetime) -> int:
    """Floor a datetime to its 15-minute slot, return unix seconds (UTC)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ts = int(dt.timestamp())
    return ts - (ts % SLOT_SECONDS)


class PricingEngine:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

        enabled_env = os.environ.get("PRICING_ENABLED", "true").strip().lower()
        self._enabled: bool = enabled_env not in ("false", "0", "no")
        try:
            self._flat_rate: float = float(os.environ.get("FLAT_RATE_EUR_PER_KWH", "0.08"))
        except ValueError:
            logger.warning("FLAT_RATE_EUR_PER_KWH not a float; using 0.08")
            self._flat_rate = 0.08

        self._cache: dict[int, float] = {}      # unix_slot -> EUR/kWh
        self._license_info: str | None = None
        self._anchor_utc: datetime | None = None
        self._last_known_price: float | None = None
        self._last_fetch_monotonic: float = 0.0
        self._future_backoff_until: float = 0.0  # monotonic; suppress refetch
        self._unit: str | None = None

    # ---------------- public API ----------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def flat_rate(self) -> float:
        return self._flat_rate

    @property
    def license_info(self) -> str | None:
        return self._license_info

    def set_anchor(self, wall_anchor_utc: datetime) -> None:
        """Pin sim_time=0 to a wall-clock UTC datetime. Clears any cached prices."""
        if wall_anchor_utc.tzinfo is None:
            wall_anchor_utc = wall_anchor_utc.replace(tzinfo=UTC)
        self._anchor_utc = wall_anchor_utc.astimezone(UTC)
        self._cache.clear()
        self._future_backoff_until = 0.0
        logger.info(f"PricingEngine anchor set to {self._anchor_utc.isoformat()}")

    def get_current_price(self, sim_dt: datetime) -> float:
        """
        Look up the EUR/kWh price for the 15-min slot containing sim_dt.

        Falls back to FLAT_RATE_EUR_PER_KWH when:
          - pricing is disabled
          - the slot isn't in the cache and no prior price is known
        """
        if not self._enabled:
            return self._flat_rate

        slot = _floor_to_slot(sim_dt)
        price = self._cache.get(slot)
        if price is not None:
            self._last_known_price = price
            return price

        # Out-of-window or pre-cache: flat-rate fallback (per design decision).
        return self._flat_rate

    async def refresh_if_needed(self, sim_dt: datetime) -> None:
        """
        Refetch if fewer than REFRESH_THRESHOLD_HOURS of prices remain in
        the cache forward of sim_dt. Rate-limited and backs off when the
        API has nothing new (sim_dt past published prices).
        """
        if not self._enabled:
            return

        now_mono = time.monotonic()
        if now_mono < self._future_backoff_until:
            return
        if now_mono - self._last_fetch_monotonic < MIN_FETCH_INTERVAL_SEC and self._cache:
            return

        slot = _floor_to_slot(sim_dt)
        cache_end = max(self._cache) if self._cache else 0
        remaining_sec = cache_end - slot
        if self._cache and remaining_sec >= REFRESH_THRESHOLD_HOURS * 3600:
            return

        await self._fetch(sim_dt)

    def forecast(self) -> list[dict]:
        """Cached price schedule for the frontend: sorted list of slots."""
        out = []
        for unix_slot in sorted(self._cache):
            dt = datetime.fromtimestamp(unix_slot, tz=UTC)
            out.append({
                "time_utc": dt.isoformat(),
                "time_berlin": dt.astimezone(BERLIN_TZ).isoformat(),
                "unix_seconds": unix_slot,
                "price_eur_per_kwh": self._cache[unix_slot],
            })
        return out

    # ---------------- internal ----------------

    async def _fetch(self, sim_dt: datetime) -> None:
        """Fetch a FETCH_WINDOW_HOURS-wide window starting at sim_dt."""
        start = sim_dt.astimezone(UTC).replace(microsecond=0)
        end = start + timedelta(hours=FETCH_WINDOW_HOURS)
        params = {
            "bzn": BIDDING_ZONE,
            "start": start.strftime("%Y-%m-%dT%H:%MZ"),
            "end":   end.strftime("%Y-%m-%dT%H:%MZ"),
        }
        self._last_fetch_monotonic = time.monotonic()
        try:
            resp = await self._http.get(API_URL, params=params, timeout=FETCH_TIMEOUT_SEC)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:
            logger.warning(f"Energy-Charts fetch failed: {exc!r}; keeping existing cache")
            return

        timestamps = body.get("unix_seconds") or []
        prices_eur_per_mwh = body.get("price") or []
        if not timestamps or not prices_eur_per_mwh or len(timestamps) != len(prices_eur_per_mwh):
            # API returned nothing usable for this window — likely sim_dt is
            # past published prices. Back off so we don't hammer the endpoint.
            self._future_backoff_until = time.monotonic() + FUTURE_BACKOFF_SEC
            logger.info(
                f"Energy-Charts returned no prices for "
                f"{params['start']}..{params['end']}; backing off {FUTURE_BACKOFF_SEC}s"
            )
            return

        added = 0
        for ts, eur_mwh in zip(timestamps, prices_eur_per_mwh):
            if eur_mwh is None:
                continue
            slot = int(ts) - (int(ts) % SLOT_SECONDS)
            if slot in self._cache:
                continue
            self._cache[slot] = float(eur_mwh) / 1000.0
            added += 1

        self._license_info = body.get("license_info") or self._license_info
        self._unit = body.get("unit") or self._unit

        if added == 0:
            self._future_backoff_until = time.monotonic() + FUTURE_BACKOFF_SEC
            logger.info(f"Energy-Charts returned {len(timestamps)} slots, all duplicates; backing off")
        else:
            logger.info(f"PricingEngine cached {added} new slots (total {len(self._cache)})")
