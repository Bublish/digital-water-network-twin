from unittest.mock import MagicMock, patch


def _db_with_chain():
    """Fresh SupabaseDB whose query builder returns a self-chaining mock."""
    from app.db.SupabaseClient import SupabaseDB
    SupabaseDB._instance = None
    with patch("app.db.SupabaseClient.create_client") as mock_create, \
         patch("app.db.SupabaseClient.load_dotenv"), \
         patch.dict("os.environ", {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}):
        client = MagicMock()
        mock_create.return_value = client
        db = SupabaseDB()
    chain = MagicMock()
    for m in ("select", "eq", "order", "range", "limit"):
        getattr(chain, m).return_value = chain
    client.table.return_value = chain
    return db, chain


def test_seed_node_results_empty_true_when_no_rows():
    db, chain = _db_with_chain()
    chain.execute.return_value = MagicMock(data=[])
    assert db.seed_node_results_empty() is True


def test_seed_node_results_empty_false_when_rows():
    db, chain = _db_with_chain()
    chain.execute.return_value = MagicMock(data=[{"node_id": "J1"}])
    assert db.seed_node_results_empty() is False


def test_fetch_seed_node_results_paginates():
    db, chain = _db_with_chain()
    page1 = [{"node_id": "J1", "sim_hour": i, "pressure_psi": 1.0,
              "head_ft": 1.0, "demand_gpm": 1.0} for i in range(2)]
    page2 = [{"node_id": "J1", "sim_hour": 2, "pressure_psi": 1.0,
              "head_ft": 1.0, "demand_gpm": 1.0}]
    chain.execute.side_effect = [MagicMock(data=page1), MagicMock(data=page2)]
    rows = db.fetch_seed_node_results(page_size=2)
    assert len(rows) == 3


def test_fetch_live_node_series_filters_node_and_run():
    db, chain = _db_with_chain()
    chain.execute.return_value = MagicMock(data=[{"sim_hour": 0.0, "pressure_psi": 60.0,
                                                  "head_ft": 200.0, "demand_gpm": 10.0}])
    rows = db.fetch_live_node_series("J1", "run-1")
    assert rows and rows[0]["pressure_psi"] == 60.0
    # node + run were used as equality filters
    eq_args = [c.args for c in chain.eq.call_args_list]
    assert ("node_id", "J1") in eq_args
    assert ("run_id", "run-1") in eq_args
