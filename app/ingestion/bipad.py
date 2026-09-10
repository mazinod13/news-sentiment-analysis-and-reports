"""BIPAD Portal incident ingestion (https://bipadportal.gov.np).

Nepal's government disaster registry. NOT a news outlet -- there is no prose
here and no selector pack, so it deliberately sits outside the Source/Scraper
machinery. The portal is a Vite SPA whose HTML is a 12 KB empty shell; all the
data comes from an open, unauthenticated JSON API discovered in its bundle.

What it is good for: regional and infrastructure analysis. Every incident is
geocoded to a ward and carries a `loss` object with house / road / bridge /
electricity damage counts.

What it is NOT good for: sentiment. Titles are template-generated
("Fire at Dharche Rural Municipality-5") and carry no opinion. Measured against
the news scrapers: BIPAD holds 644 landslides and 536 snake bites since July
that got no coverage at all, while the Bhotekoshi flood that dominated three
province front pages has no BIPAD record. Treat the two datasets as
complementary, never as the same kind of thing.

    from app.ingestion.bipad import load_geography, fetch_incidents
    geo = load_geography(fetcher)
    for incident in fetch_incidents(fetcher, geo, since=date(2026, 8, 1)):
        ...
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from urllib.parse import urlencode

from app.ingestion.fetcher import FetchError
from app.settings import NPT

log = logging.getLogger(__name__)

API = "https://bipadportal.gov.np/api/v1"

# The API reports count as 9223372036854775807 (int64 max) rather than running
# a real COUNT. Never size a loop off it -- follow `next` until it is null.
BOGUS_COUNT = 9223372036854775807

PAGE_SIZE = 500

# Guard against an unbounded crawl if `next` ever stops terminating.
MAX_PAGES = 200


class BipadError(Exception):
    """The portal returned something we cannot use."""


@dataclass
class Geography:
    """ward id -> the administrative names above it.

    Incidents carry ward ids only, so this lookup is what turns a record into
    something you can group by province or district. The four reference tables
    are small (7 / 77 / 774 / 6803 rows) and change almost never.
    """

    wards: dict[int, dict] = field(default_factory=dict)

    def resolve(self, ward_ids: list[int] | None) -> dict:
        for ward_id in ward_ids or []:
            if ward_id in self.wards:
                return self.wards[ward_id]
        return {}


@dataclass
class Incident:
    """One BIPAD incident, flattened for analysis."""

    id: int
    title: str
    title_ne: str | None
    hazard: str | None
    hazard_ne: str | None
    incident_on: datetime | None
    reported_on: datetime | None

    province: str | None = None
    district: str | None = None
    municipality: str | None = None
    municipality_ne: str | None = None
    ward: str | None = None
    lat: float | None = None
    lon: float | None = None

    # people
    people_death: int = 0
    people_missing: int = 0
    people_injured: int = 0
    people_affected: int = 0
    families_affected: int = 0

    # infrastructure -- the reason this dataset is worth having
    infra_destroyed: int = 0
    houses_destroyed: int = 0
    houses_affected: int = 0
    roads_destroyed: int = 0
    roads_affected: int = 0
    bridges_destroyed: int = 0
    bridges_affected: int = 0
    electricity_destroyed: int = 0
    electricity_affected: int = 0

    estimated_loss: float = 0.0
    infrastructure_economic_loss: float = 0.0
    agriculture_economic_loss: float = 0.0

    verified: bool = False
    approved: bool = False
    data_source: str | None = None

    def as_dict(self) -> dict:
        row = asdict(self)
        for key in ("incident_on", "reported_on"):
            value = row[key]
            row[key] = value.isoformat() if value else None
        return row


def _get_json(fetcher, url: str) -> dict:
    try:
        response = fetcher.get(url)
    except FetchError as exc:
        raise BipadError(str(exc)) from exc
    try:
        return json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise BipadError(f"non-JSON response from {url}: {exc}") from exc


def _paginate(fetcher, url: str):
    """Yield every result across pages.

    An empty page is the ONLY reliable terminator. `next` is computed from
    `count`, and `count` is BOGUS_COUNT, so the API happily hands you a next
    link forever: offset=5000 on a 126-row query still returns
    `{"results": [], "next": "...offset=5500"}`. Following `next` until it goes
    null means MAX_PAGES requests every run, which is how this first ran for
    five minutes and returned nothing.
    """
    seen_pages = 0
    while url:
        payload = _get_json(fetcher, url)
        results = payload.get("results")
        if results is None:
            raise BipadError(f"no `results` key in response from {url}")
        if not results:
            return
        yield from results
        url = payload.get("next")
        seen_pages += 1
        if seen_pages >= MAX_PAGES:
            log.warning("stopped after %d pages -- pagination did not terminate", MAX_PAGES)
            return


def load_geography(fetcher) -> Geography:
    """Fetch the four reference tables and flatten them into a ward lookup.

    Four requests, and the result is worth caching for the process lifetime:
    the tables change when Nepal redraws local boundaries, i.e. almost never.
    """
    provinces = {p["id"]: p for p in _paginate(fetcher, f"{API}/province/?limit=100")}
    districts = {d["id"]: d for d in _paginate(fetcher, f"{API}/district/?limit=200")}
    municipalities = {m["id"]: m for m in _paginate(fetcher, f"{API}/municipality/?limit=1000")}

    wards: dict[int, dict] = {}
    for ward in _paginate(fetcher, f"{API}/ward/?limit=10000"):
        municipality = municipalities.get(ward.get("municipality"), {})
        district = districts.get(municipality.get("district"), {})
        province = provinces.get(district.get("province"), {})
        wards[ward["id"]] = {
            "ward": ward.get("title"),
            "municipality": municipality.get("title_en") or municipality.get("title"),
            "municipality_ne": municipality.get("title_ne"),
            "district": district.get("title_en") or district.get("title"),
            "province": province.get("title_en") or province.get("title"),
        }

    log.info("geography loaded: %d provinces, %d districts, %d municipalities, %d wards",
             len(provinces), len(districts), len(municipalities), len(wards))
    return Geography(wards=wards)


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(NPT) if parsed.tzinfo else parsed.replace(tzinfo=NPT)


def _num(loss: dict, key: str) -> int | float:
    """BIPAD writes an absent measurement as null, not 0."""
    return loss.get(key) or 0


def to_incident(raw: dict, geo: Geography, hazards: dict[int, dict]) -> Incident:
    loss = raw.get("loss") or {}
    if not isinstance(loss, dict):        # unexpanded: the API returned a bare FK
        loss = {}
    hazard = hazards.get(raw.get("hazard"), {})
    where = geo.resolve(raw.get("wards"))

    point = raw.get("point") or {}
    coords = point.get("coordinates") or []
    lon, lat = (coords + [None, None])[:2]

    return Incident(
        id=raw["id"],
        title=raw.get("title") or "",
        title_ne=raw.get("titleNe"),
        hazard=hazard.get("titleEn") or hazard.get("title"),
        hazard_ne=hazard.get("titleNe"),
        # incidentOn is date-only (always midnight); reportedOn carries the
        # real clock time. Keep both -- which one you want depends on whether
        # you are asking when it happened or when anyone found out.
        incident_on=_dt(raw.get("incidentOn")),
        reported_on=_dt(raw.get("reportedOn")),
        province=where.get("province"),
        district=where.get("district"),
        municipality=where.get("municipality"),
        municipality_ne=where.get("municipality_ne"),
        ward=where.get("ward"),
        lat=lat,
        lon=lon,
        people_death=_num(loss, "peopleDeathCount"),
        people_missing=_num(loss, "peopleMissingCount"),
        people_injured=_num(loss, "peopleInjuredCount"),
        people_affected=_num(loss, "peopleAffectedCount"),
        families_affected=_num(loss, "familyAffectedCount"),
        infra_destroyed=_num(loss, "infrastructureDestroyedCount"),
        houses_destroyed=_num(loss, "infrastructureDestroyedHouseCount"),
        houses_affected=_num(loss, "infrastructureAffectedHouseCount"),
        roads_destroyed=_num(loss, "infrastructureDestroyedRoadCount"),
        roads_affected=_num(loss, "infrastructureAffectedRoadCount"),
        bridges_destroyed=_num(loss, "infrastructureDestroyedBridgeCount"),
        bridges_affected=_num(loss, "infrastructureAffectedBridgeCount"),
        electricity_destroyed=_num(loss, "infrastructureDestroyedElectricityCount"),
        electricity_affected=_num(loss, "infrastructureAffectedElectricityCount"),
        estimated_loss=_num(loss, "estimatedLoss"),
        infrastructure_economic_loss=_num(loss, "infrastructureEconomicLoss"),
        agriculture_economic_loss=_num(loss, "agricultureEconomicLoss"),
        verified=bool(raw.get("verified")),
        approved=bool(raw.get("approved")),
        data_source=raw.get("dataSource"),
    )


def load_hazards(fetcher) -> dict[int, dict]:
    return {h["id"]: h for h in _paginate(fetcher, f"{API}/hazard/?limit=200")}


def incident_url(since: date, until: date | None = None, *, province: int | None = None) -> str:
    """Build the incident query.

    `expand=loss` is what inlines the damage counts; without it `loss` comes
    back as a bare integer FK and every infrastructure column lands at zero.
    """
    params = {
        "incident_on__gt": datetime(since.year, since.month, since.day, tzinfo=NPT).isoformat(),
        "expand": "loss",
        "ordering": "-incident_on",
        "limit": PAGE_SIZE,
    }
    if until:
        params["incident_on__lt"] = datetime(
            until.year, until.month, until.day, tzinfo=NPT
        ).isoformat()
    if province:
        params["province"] = province
    return f"{API}/incident/?{urlencode(params)}"


def fetch_incidents(
    fetcher,
    geo: Geography,
    hazards: dict[int, dict],
    *,
    since: date,
    until: date | None = None,
    province: int | None = None,
    verified_only: bool = True,
):
    """Yield Incident records for the window.

    verified_only filters to verified AND approved records. The portal also
    carries unverified citizen reports; keep them out of any published number.
    """
    for raw in _paginate(fetcher, incident_url(since, until, province=province)):
        if verified_only and not (raw.get("verified") and raw.get("approved")):
            continue
        yield to_incident(raw, geo, hazards)
