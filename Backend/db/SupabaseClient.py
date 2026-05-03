import os
from datetime import UTC, datetime

from dotenv import find_dotenv, load_dotenv
from supabase import Client, create_client

NETWORK_BUCKET = "network"
NETWORK_FILE = "WSN1.inp"


class SupabaseDB:
    """
    Singleton Supabase client. All storage and DB I/O for the project goes through here.

    Usage:
        db = SupabaseDB()
        inp_bytes = db.download_network()
        db.save_tank_state({"T1": 12.5, "T2": 8.3})
        state = db.load_tank_state()
        db.insert_seed_node_results([{"run_id": ..., "sim_hour": 1.0, "node_id": "J1", ...}])
        db.insert_seed_link_results([{"run_id": ..., "sim_hour": 1.0, "link_id": "P1", ...}])
        db.insert_live_node_results([{"run_id": ..., "sim_hour": 1.0, "node_id": "J1", ...}])
        db.insert_live_link_results([{"run_id": ..., "sim_hour": 1.0, "link_id": "P1", ...}])
    """

    _instance: "SupabaseDB | None" = None
    _client: Client | None = None

    def __new__(cls) -> "SupabaseDB":
        if cls._instance is None:
            obj = super().__new__(cls)
            obj._connect()
            cls._instance = obj
        return cls._instance

    def _connect(self) -> None:
        load_dotenv(find_dotenv())
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if not url:
            raise EnvironmentError("SUPABASE_URL not set in environment or .env file")
        if not key:
            raise EnvironmentError("SUPABASE_KEY not set in environment or .env file")
        self._client = create_client(url, key)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def download_network(self) -> bytes:
        """Download the EPANET network .inp file from Supabase storage."""
        data = self._client.storage.from_(NETWORK_BUCKET).download(NETWORK_FILE)
        if not data:
            raise RuntimeError(
                f"'{NETWORK_FILE}' not found in Supabase '{NETWORK_BUCKET}' bucket"
            )
        return data

    # ------------------------------------------------------------------
    # Network state (tank levels)
    # ------------------------------------------------------------------

    def save_tank_state(self, state: dict[str, float]) -> None:
        """Upsert tank water levels (ft above tank floor) to the network_state table."""
        rows = [
            {"tank_id": tid, "level_ft": lvl, "updated_at": datetime.now(UTC).isoformat()}
            for tid, lvl in state.items()
        ]
        self._client.table("network_state").upsert(rows).execute()

    def load_tank_state(self) -> dict[str, float] | None:
        """Return the latest saved tank levels, or None if no state has been saved yet."""
        response = self._client.table("network_state").select("tank_id, level_ft").execute()
        if not response.data:
            return None
        return {row["tank_id"]: row["level_ft"] for row in response.data}

    # ------------------------------------------------------------------
    # Simulation results
    # ------------------------------------------------------------------

    def insert_seed_node_results(self, rows: list[dict]) -> None:
        """Bulk-insert node rows from a seed run into seed_node_results."""
        self._client.table("seed_node_results").insert(rows).execute()

    def insert_seed_link_results(self, rows: list[dict]) -> None:
        """Bulk-insert link rows from a seed run into seed_link_results."""
        self._client.table("seed_link_results").insert(rows).execute()

    def insert_live_node_results(self, rows: list[dict]) -> None:
        """Insert real-time node snapshot rows into live_node_results."""
        self._client.table("live_node_results").insert(rows).execute()

    def insert_live_link_results(self, rows: list[dict]) -> None:
        """Insert real-time link snapshot rows into live_link_results."""
        self._client.table("live_link_results").insert(rows).execute()
