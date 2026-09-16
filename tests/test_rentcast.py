import pytest

from tracker import rentcast


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


def test_single_page():
    session = FakeSession([FakeResponse(200, [{"id": "a"}, {"id": "b"}])])
    listings, calls = rentcast.fetch_sale_listings({"city": "Merced"}, api_key="k", session=session)
    assert [l["id"] for l in listings] == ["a", "b"]
    assert calls == 1
    url, params, headers = session.calls[0]
    assert url.endswith("/listings/sale")
    assert params == {"city": "Merced", "limit": 500, "offset": 0}
    assert headers["X-Api-Key"] == "k"


def test_pagination_follows_offset_and_respects_max_pages():
    full_page = [{"id": str(i)} for i in range(rentcast.PAGE_SIZE)]
    session = FakeSession([FakeResponse(200, full_page), FakeResponse(200, full_page), FakeResponse(200, [{"id": "x"}])])
    listings, calls = rentcast.fetch_sale_listings({}, api_key="k", session=session, max_pages=2)
    assert len(listings) == 2 * rentcast.PAGE_SIZE
    assert calls == 2
    assert session.calls[1][1]["offset"] == rentcast.PAGE_SIZE


def test_404_means_no_results():
    session = FakeSession([FakeResponse(404, {"error": "No results"})])
    listings, calls = rentcast.fetch_sale_listings({}, api_key="k", session=session)
    assert listings == []
    assert calls == 1


def test_error_status_raises():
    session = FakeSession([FakeResponse(401, {"error": "bad key"})])
    with pytest.raises(rentcast.RentCastError):
        rentcast.fetch_sale_listings({}, api_key="k", session=session)


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    with pytest.raises(rentcast.RentCastError):
        rentcast.fetch_sale_listings({}, session=FakeSession([]))
