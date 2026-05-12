from unittest.mock import MagicMock, patch


def test_insert_control_decision_calls_correct_table():
    from db.SupabaseClient import SupabaseDB

    # Force a fresh instance, mock the client
    SupabaseDB._instance = None
    with patch("db.SupabaseClient.create_client") as mock_create, \
         patch("db.SupabaseClient.load_dotenv"), \
         patch.dict("os.environ", {"SUPABASE_URL": "x", "SUPABASE_KEY": "y"}):
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        db = SupabaseDB()
        rows = [{"sim_id": "abc", "pump_id": "P1", "ml_commanded": "OPEN",
                 "applied_status": "OPEN", "mode": "AUTO", "model_id": "stub-v0",
                 "sim_time_hr": 1.5}]
        db.insert_control_decision(rows)

        mock_client.table.assert_called_with("control_decisions")
        mock_client.table.return_value.insert.assert_called_with(rows)
        mock_client.table.return_value.insert.return_value.execute.assert_called_once()

    SupabaseDB._instance = None  # reset for other tests
