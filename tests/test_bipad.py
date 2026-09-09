"""Tests for BIPAD Portal ingestion.

Shared code, not a per-outlet file: app/ingestion/bipad.py is used by the
`bipad` CLI command and by anything that later joins incidents to articles.

Everything runs off saved API responses; no network.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from app.ingestion.bipad import (
    API,
    BOGUS_COUNT,
    BipadError,
    Geography,
    _paginate,
    fetch_incidents,
    incident_url,
    load_geography,
    load_hazards,
    to_incident,
)
from app.ingestion.fetcher import FetchResult
from app.settings import NPT


class StubFetcher:
    """Serves saved JSON by URL prefix, and records what was requested."""

    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    def get(self, url, *, etag=None, last_modified=None, rate_limit=None):
        self.requested.append(url)
        for fragment, payload in self.routes.items():
            if fragment in url:
                if callable(payload):
                    payload = payload(url)
                text = payload if isinstance(payload, str) else json.dumps(payload)
                return FetchResult(url=url, status=200, text=text, content=b"")
        raise AssertionError(f"unstubbed URL: {url}")


@pytest.fixture
def api(fixture_text):
    def load(name):
        return json.loads(fixture_text(name))

    return {
        "/incident/": load("bipad_incident_page.json"),
        "/hazard/": load("bipad_hazard.json"),
        "/province/": load("bipad_province.json"),
        "/district/": load("bipad_district.json"),
        "/municipality/": load("bipad_municipality.json"),
        "/ward/": load("bipad_ward.json"),
    }


@pytest.fixture
def fetcher(api):
    """Mimics the live API, including its refusal to end a listing.

    The saved incident page carries a real `next` link, exactly as the portal
    sends it. Serving that page on every request is what the portal actually
    does until you run off the end, at which point it returns an empty
    `results` with `next` STILL set -- so the stub does the same.
    """
    routes = dict(api)
    page = routes["/incident/"]
    served = {"n": 0}

    def incidents(url):
        served["n"] += 1
        if served["n"] == 1:
            return page
        return {"count": BOGUS_COUNT, "next": f"{API}/incident/?offset=1000", "results": []}

    routes["/incident/"] = incidents
    return StubFetcher(routes)


# -- pagination ---------------------------------------------------------------


class TestPagination:
    """The portal's `next` link is unusable, and this is the whole reason
    _paginate looks the way it does."""

    def test_count_is_the_int64_sentinel_not_a_real_count(self, api):
        assert api["/incident/"]["count"] == BOGUS_COUNT

    def test_stops_on_an_empty_page_even_though_next_is_set(self):
        """The live API returns {"results": [], "next": "...offset=5500"} once
        you run off the end. Following `next` until it goes null never
        terminates -- it ran for five minutes and returned nothing."""
        pages = [
            {"results": [{"id": 1}], "next": f"{API}/incident/?offset=1"},
            {"results": [], "next": f"{API}/incident/?offset=2"},
        ]
        served = []

        def serve(url):
            served.append(url)
            return pages[min(len(served) - 1, len(pages) - 1)]

        fetcher = StubFetcher({"/incident/": serve})

        rows = list(_paginate(fetcher, f"{API}/incident/"))

        assert rows == [{"id": 1}]
        assert len(fetcher.requested) == 2, "kept paging past the empty page"

    def test_follows_next_while_pages_have_content(self):
        pages = [
            {"results": [{"id": 1}], "next": f"{API}/incident/?offset=1"},
            {"results": [{"id": 2}], "next": f"{API}/incident/?offset=2"},
            {"results": [], "next": f"{API}/incident/?offset=3"},
        ]
        calls = []

        def serve(url):
            calls.append(url)
            return pages[min(len(calls) - 1, len(pages) - 1)]

        rows = list(_paginate(StubFetcher({"/incident/": serve}), f"{API}/incident/"))

        assert [r["id"] for r in rows] == [1, 2]

    def test_missing_results_key_is_an_error_not_a_silent_empty(self):
        fetcher = StubFetcher({"/incident/": {"detail": "Not found."}})

        with pytest.raises(BipadError, match="results"):
            list(_paginate(fetcher, f"{API}/incident/"))

    def test_non_json_response_is_an_error(self):
        fetcher = StubFetcher({"/incident/": "<!doctype html><html>"})

        with pytest.raises(BipadError, match="non-JSON"):
            list(_paginate(fetcher, f"{API}/incident/"))


# -- query building -----------------------------------------------------------


class TestQuery:
    def test_expand_loss_is_requested(self):
        """Without expand=loss the API returns `loss` as a bare integer FK and
        every infrastructure column silently lands at zero."""
        assert "expand=loss" in incident_url(date(2026, 8, 1))

    def test_date_bounds_are_npt(self):
        url = incident_url(date(2026, 8, 1), date(2026, 9, 1))

        assert "05%3A45" in url, "dates must be sent as Nepal time, not UTC"
        assert "incident_on__gt" in url and "incident_on__lt" in url

    def test_province_filter_is_optional(self):
        assert "province" not in incident_url(date(2026, 8, 1))
        assert "province=1" in incident_url(date(2026, 8, 1), province=1)


# -- geography ----------------------------------------------------------------


class TestGeography:
    def test_ward_resolves_all_the_way_up_to_province(self, fetcher):
        geo = load_geography(fetcher)

        placed = geo.resolve([1173])

        assert placed["province"]
        assert placed["district"]
        assert placed["municipality"]
        assert placed["ward"]

    def test_unknown_ward_yields_empty_not_a_crash(self, fetcher):
        geo = load_geography(fetcher)

        assert geo.resolve([999999]) == {}
        assert geo.resolve([]) == {}
        assert geo.resolve(None) == {}


# -- record flattening --------------------------------------------------------


class TestToIncident:
    def test_titles_and_region_are_populated(self, fetcher, api):
        geo, hazards = load_geography(fetcher), load_hazards(fetcher)
        raw = api["/incident/"]["results"][0]

        incident = to_incident(raw, geo, hazards)

        assert incident.title
        assert incident.title_ne, "the Nepali title is the one worth analysing"
        assert incident.hazard and incident.hazard_ne
        assert incident.province and incident.district and incident.municipality

    def test_infrastructure_counts_survive(self, fetcher, api):
        """The reason this dataset is worth pulling at all."""
        geo, hazards = load_geography(fetcher), load_hazards(fetcher)
        rows = [to_incident(r, geo, hazards) for r in api["/incident/"]["results"]]

        assert sum(r.houses_destroyed for r in rows) > 0
        assert sum(r.estimated_loss for r in rows) > 0

    def test_null_measurements_become_zero(self):
        """BIPAD writes an absent measurement as null, so a bare `or 0` is not
        optional -- summing None raises."""
        geo, hazards = Geography(), {}
        raw = {"id": 1, "title": "x", "loss": {"estimatedLoss": None, "peopleDeathCount": None}}

        incident = to_incident(raw, geo, hazards)

        assert incident.estimated_loss == 0
        assert incident.people_death == 0

    def test_unexpanded_loss_does_not_crash(self):
        """If someone drops expand=loss, `loss` arrives as an int. That should
        cost the damage columns, not the whole run."""
        raw = {"id": 1, "title": "x", "loss": 476885}

        incident = to_incident(raw, Geography(), {})

        assert incident.houses_destroyed == 0

    def test_timestamps_are_nepal_time(self, fetcher, api):
        geo, hazards = load_geography(fetcher), load_hazards(fetcher)
        incident = to_incident(api["/incident/"]["results"][0], geo, hazards)

        assert incident.incident_on.utcoffset().total_seconds() == 5 * 3600 + 45 * 60
        # incidentOn is date-only; reportedOn is where the clock lives.
        assert (incident.incident_on.hour, incident.incident_on.minute) == (0, 0)

    def test_naive_timestamps_are_read_as_npt(self):
        raw = {"id": 1, "title": "x", "incidentOn": "2026-08-30T00:00:00"}

        incident = to_incident(raw, Geography(), {})

        assert incident.incident_on == datetime(2026, 8, 30, tzinfo=NPT)

    def test_point_is_lon_lat_in_geojson_order(self, fetcher, api):
        geo, hazards = load_geography(fetcher), load_hazards(fetcher)
        incident = to_incident(api["/incident/"]["results"][0], geo, hazards)

        # GeoJSON is [lon, lat]; swapping them puts every Nepali incident in
        # China, since Nepal spans lat 26-30 and lon 80-88.
        assert 26 <= incident.lat <= 31
        assert 80 <= incident.lon <= 89

    def test_as_dict_serialises_datetimes_for_csv_and_jsonl(self, fetcher, api):
        geo, hazards = load_geography(fetcher), load_hazards(fetcher)
        row = to_incident(api["/incident/"]["results"][0], geo, hazards).as_dict()

        assert isinstance(row["incident_on"], str)
        json.dumps(row, ensure_ascii=False)      # must not raise


# -- filtering ----------------------------------------------------------------


class TestFetchIncidents:
    def test_yields_every_verified_record(self, fetcher, api):
        geo, hazards = load_geography(fetcher), load_hazards(fetcher)

        rows = list(fetch_incidents(fetcher, geo, hazards, since=date(2026, 8, 1)))

        assert len(rows) == len(api["/incident/"]["results"])
        assert all(r.verified and r.approved for r in rows)

    def test_unverified_records_are_dropped_by_default(self, api):
        """The portal also carries unverified citizen reports. They must not
        reach a published number unless asked for."""
        page = {"results": [{"id": 1, "title": "unchecked", "verified": False, "approved": False}],
                "next": None}
        fetcher = StubFetcher({"/incident/": page})

        kept = list(fetch_incidents(fetcher, Geography(), {}, since=date(2026, 8, 1)))
        forced = list(
            fetch_incidents(
                fetcher, Geography(), {}, since=date(2026, 8, 1), verified_only=False
            )
        )

        assert kept == []
        assert len(forced) == 1
