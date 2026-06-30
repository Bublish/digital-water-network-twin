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
        db.insert_seed_node_results([{"run_id": ..., "sim_hour": 1.0, "node_id": "J1", ...}])
        db.insert_seed_link_results([{"run_id": ..., "sim_hour": 1.0, "link_id": "P1", ...}])
        db.insert_live_node_results([{"run_id": ..., "sim_hour": 1.0, "node_id": "J1", ...}])
        db.insert_live_link_results([{"run_id": ..., "sim_hour": 1.0, "link_id": "P1", ...}])
        db.insert_control_decision([{"sim_id": ..., "pump_id": "P1", "ml_commanded": "OPEN", ...}])
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
    
    def save_network(self, inp_bytes: bytes, filename: str = NETWORK_FILE) -> None:
        """Upload (or overwrite) an EPANET .inp file in Supabase storage."""
        self._client.storage.from_(NETWORK_BUCKET).upload(
            path=filename,
            file=inp_bytes,
            file_options={"upsert": "true"},
        )

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

    def insert_control_decision(self, rows: list[dict]) -> None:
        """Insert one row per pump per step into control_decisions (XAI audit table)."""
        self._client.table("control_decisions").insert(rows).execute()

    # ------------------------------------------------------------------
    # Result reads (prediction engine)
    # ------------------------------------------------------------------

    def seed_node_results_empty(self) -> bool:
        res = self._client.table("seed_node_results").select("node_id").limit(1).execute()
        return len(res.data) == 0

    def seed_node_results_step_hours(self) -> float | None:
        """Smallest positive gap between consecutive sim_hours for one node,
        used to verify the seed table is at 15-min (0.25 h) resolution."""
        head = self._client.table("seed_node_results").select("node_id").limit(1).execute()
        if not head.data:
            return None
        node_id = head.data[0]["node_id"]
        res = (self._client.table("seed_node_results")
               .select("sim_hour").eq("node_id", node_id)
               .order("sim_hour").limit(5).execute())
        hours = sorted({float(r["sim_hour"]) for r in res.data})
        gaps = [b - a for a, b in zip(hours, hours[1:]) if b - a > 0]
        return min(gaps) if gaps else None

    def fetch_seed_node_results(self, page_size: int = 1000) -> list[dict]:
        """All seed node rows, paginated (Supabase caps a single response)."""
        rows: list[dict] = []
        page = 0
        while True:
            res = (self._client.table("seed_node_results")
                   .select("node_id, sim_hour, pressure_psi, head_ft, demand_gpm")
                   .order("node_id").order("sim_hour")
                   .range(page * page_size, page * page_size + page_size - 1).execute())
            batch = res.data
            rows.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return rows

    def fetch_seed_node_series(self, node_id: str) -> list[dict]:
        res = (self._client.table("seed_node_results")
               .select("sim_hour, pressure_psi, head_ft, demand_gpm")
               .eq("node_id", node_id).order("sim_hour").execute())
        return res.data

    def fetch_live_node_series(self, node_id: str, run_id: str) -> list[dict]:
        res = (self._client.table("live_node_results")
               .select("sim_hour, pressure_psi, head_ft, demand_gpm")
               .eq("node_id", node_id).eq("run_id", run_id)
               .order("sim_hour").execute())
        return res.data

    # ------------------------------------------------------------------
    # DEBUG ONLY: live-table cleanup
    # ------------------------------------------------------------------

    def clear_live_tables(self) -> None:
        """
        DEBUG ONLY: delete all rows from the live_* and control_decisions tables.
        Call this on shutdown during dev so test runs don't accumulate.
        REMOVE THIS METHOD CALL FROM lifespan() BEFORE GOING TO PRODUCTION.
        """
        # Supabase requires a filter on delete; UUID '00000000-...' won't match real data,
        # and .neq('...') matches everything. We use a sim_hour filter for live_* tables
        # (sim_hour is non-negative for real rows) and run_id filter on others.
        self._client.table("live_node_results").delete().gte("sim_hour", -1).execute()
        self._client.table("live_link_results").delete().gte("sim_hour", -1).execute()
        self._client.table("control_decisions").delete().gte("sim_time_hr", -1).execute()
