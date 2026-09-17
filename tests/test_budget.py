import json
from pathlib import Path

import pytest

from tracker import collect, rentcast


def make_config(tmp_path, budget=45, queries=1, max_pages=2):
    return {
        "queries": [{"city": "Merced"}] * queries,
        "max_pages_per_query": max_pages,
        "monthly_call_budget": budget,
        "usage_file": str(tmp_path / "usage.json"),
        "cities": ["Merced"],
        "data_file": str(tmp_path / "listings.csv"),
    }


def fake_fetch(pages_returned):
    def _fetch(params, max_pages=2, on_request=None, **kw):
        for _ in range(pages_returned):
            on_request()
        return [{"id": "a", "formattedAddress": "1 A St, Merced, CA", "city": "Merced"}], pages_returned
    return _fetch


def test_calls_are_recorded_per_month(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setattr(rentcast, "fetch_sale_listings", fake_fetch(1))
    collect.run(config, "2026-09-16")
    collect.run(config, "2026-09-17")
    assert json.loads(Path(config["usage_file"]).read_text()) == {"2026-09": 2}


def test_refuses_when_worst_case_would_exceed_budget(tmp_path, monkeypatch):
    config = make_config(tmp_path, budget=10)
    Path(config["usage_file"]).write_text(json.dumps({"2026-09": 9}))
    called = []
    monkeypatch.setattr(rentcast, "fetch_sale_listings", lambda *a, **k: called.append(1))
    with pytest.raises(collect.BudgetExceeded):
        collect.run(config, "2026-09-16")
    assert called == []
    assert not Path(config["data_file"]).exists()


def test_new_month_resets_count(tmp_path, monkeypatch):
    config = make_config(tmp_path, budget=10)
    Path(config["usage_file"]).write_text(json.dumps({"2026-09": 10}))
    monkeypatch.setattr(rentcast, "fetch_sale_listings", fake_fetch(1))
    collect.run(config, "2026-10-01")
    assert json.loads(Path(config["usage_file"]).read_text()) == {"2026-09": 10, "2026-10": 1}


def test_failed_request_is_still_charged(tmp_path, monkeypatch):
    config = make_config(tmp_path)

    def failing(params, max_pages=2, on_request=None, **kw):
        on_request()
        raise rentcast.RentCastError("401")

    monkeypatch.setattr(rentcast, "fetch_sale_listings", failing)
    with pytest.raises(rentcast.RentCastError):
        collect.run(config, "2026-09-16")
    assert json.loads(Path(config["usage_file"]).read_text()) == {"2026-09": 1}


def test_replay_from_file_costs_nothing(tmp_path):
    config = make_config(tmp_path)
    fixture = Path(__file__).parent / "fixtures" / "rentcast_sale_listings.json"
    collect.run(config, "2026-09-16", from_file=str(fixture))
    assert not Path(config["usage_file"]).exists()


def test_main_exit_code_on_budget(tmp_path, monkeypatch):
    config = make_config(tmp_path, budget=0)
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(config))
    assert collect.main(["--config", str(cfg_path), "--date", "2026-09-16"]) == 2
