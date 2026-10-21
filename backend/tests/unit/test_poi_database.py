"""Unit tests for the POI database (Phase 24, Worker W4).

Contract under test (W2 + W3 code against the same signatures):

* ``POIDatabase`` + singleton ``get_poi_database()``
* ``nearest(map_id, x, y, max_radius=0.05) -> dict | None``
* ``by_name(map_id, name) -> dict | None``
* ``all_pois(map_id) -> list[dict]``
* ``maps() -> list[str]``
* POI dict: ``{"name", "map", "x", "y", "type", "tier"}`` with
  ``x``/``y`` normalised ``0..1``, origin top-left.
* Unknown map → empty / ``None``, never an exception.

Coordinates in the data files are approximate naming references (see
each file's ``coords_note``) — these tests therefore assert *naming*
behaviour at/near a POI's own stored coordinates, never survey-grade
accuracy.
"""

import json
import math
from pathlib import Path

import pytest

from app.services.poi import POIDatabase, get_poi_database
from app.services.poi.poi_database import DATA_DIR, POI_TYPES, VALID_TIERS

EXPECTED_MAPS = ["erangel", "livik", "miramar", "rondo", "sanhok"]

# Contract size bands from the Phase 24 brief.
SIZE_BANDS = {
    "erangel": (45, 60),
    "miramar": (20, 30),
    "sanhok": (20, 30),
    "rondo": (10, 15),
    "livik": (10, 15),
}

POI_KEYS = {"name", "map", "x", "y", "type", "tier"}


@pytest.fixture(scope="module")
def db():
    return POIDatabase()


# ── loading ─────────────────────────────────────────────────────


def test_maps_lists_every_data_file(db):
    assert db.maps() == EXPECTED_MAPS


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_map_sizes_match_the_brief(db, map_id):
    lo, hi = SIZE_BANDS[map_id]
    assert lo <= len(db.all_pois(map_id)) <= hi


def test_data_files_carry_the_honesty_note():
    for map_id in EXPECTED_MAPS:
        payload = json.loads((DATA_DIR / f"{map_id}.json").read_text())
        assert payload["map"] == map_id
        assert payload["version"] == 1
        assert "approximate" in payload["coords_note"]
        assert "not survey-grade" in payload["coords_note"]


def test_singleton_is_shared():
    assert get_poi_database() is get_poi_database()
    assert isinstance(get_poi_database(), POIDatabase)
    assert get_poi_database().maps() == EXPECTED_MAPS


# ── POI dict shape / data hygiene ───────────────────────────────


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_every_poi_has_the_contract_shape(db, map_id):
    for poi in db.all_pois(map_id):
        assert set(poi) == POI_KEYS
        assert isinstance(poi["name"], str) and poi["name"]
        assert poi["map"] == map_id
        assert isinstance(poi["x"], float) and isinstance(poi["y"], float)
        assert poi["type"] in POI_TYPES
        assert poi["tier"] in VALID_TIERS


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_all_coords_within_unit_square(db, map_id):
    for poi in db.all_pois(map_id):
        assert 0.0 <= poi["x"] <= 1.0
        assert 0.0 <= poi["y"] <= 1.0


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_no_duplicate_names_per_map(db, map_id):
    names = [p["name"].casefold() for p in db.all_pois(map_id)]
    assert len(names) == len(set(names))


def test_returned_dicts_are_copies(db):
    poi = db.by_name("erangel", "Pochinki")
    poi["name"] = "MUTATED"
    assert db.by_name("erangel", "Pochinki")["name"] == "Pochinki"
    listing = db.all_pois("erangel")
    listing[0]["tier"] = 99
    assert db.all_pois("erangel")[0]["tier"] in VALID_TIERS


# ── nearest() ───────────────────────────────────────────────────


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_nearest_at_own_coordinates_finds_itself(db, map_id):
    for poi in db.all_pois(map_id):
        hit = db.nearest(map_id, poi["x"], poi["y"])
        assert hit is not None
        assert hit["name"] == poi["name"]


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_nearest_near_coordinates_finds_the_poi(db, map_id):
    for poi in db.all_pois(map_id):
        # A small nudge (well inside the default radius, and smaller
        # than half the gap to any neighbour in these datasets for
        # the sampled POIs) must still name the same place.
        hit = db.nearest(map_id, poi["x"] + 0.004, poi["y"] - 0.004)
        assert hit is not None
        assert hit["name"] == poi["name"]


def test_nearest_known_hot_drops(db):
    assert db.nearest("erangel", 0.44, 0.53)["name"] == "Pochinki"
    assert db.nearest("erangel", 0.48, 0.89)["name"] == "Sosnovka Military Base"
    assert db.nearest("sanhok", 0.50, 0.48)["name"] == "Bootcamp"
    assert db.nearest("miramar", 0.50, 0.50)["name"] == "Pecado"


def test_nearest_returns_none_beyond_radius(db):
    # Open water south-west of Erangel: no POI within the default radius.
    assert db.nearest("erangel", 0.02, 0.98) is None
    # Open sea north of Sanhok.
    assert db.nearest("sanhok", 0.50, 0.01) is None
    # A real POI queried with a radius too small to reach it.
    pochinki = db.by_name("erangel", "Pochinki")
    assert db.nearest("erangel", pochinki["x"] + 0.03, pochinki["y"],
                      max_radius=0.01) is None


def test_nearest_respects_custom_radius(db):
    pochinki = db.by_name("erangel", "Pochinki")
    x, y = pochinki["x"] + 0.08, pochinki["y"]
    assert db.nearest("erangel", x, y) is None  # default 0.05 too small
    hit = db.nearest("erangel", x, y, max_radius=0.10)
    assert hit is not None and hit["name"] == "Pochinki"


def test_nearest_picks_the_closest_poi(db):
    # Between Pochinki and its church (0.04 apart): a point 0.01 from
    # Pochinki must name Pochinki, not the church.
    pochinki = db.by_name("erangel", "Pochinki")
    hit = db.nearest("erangel", pochinki["x"], pochinki["y"] - 0.01)
    assert hit["name"] == "Pochinki"


def test_nearest_rejects_non_finite_inputs(db):
    assert db.nearest("erangel", math.nan, 0.5) is None
    assert db.nearest("erangel", 0.5, math.inf) is None
    assert db.nearest("erangel", 0.5, 0.5, max_radius=-1.0) is None


# ── by_name() ───────────────────────────────────────────────────


@pytest.mark.parametrize("map_id", EXPECTED_MAPS)
def test_by_name_round_trip(db, map_id):
    for poi in db.all_pois(map_id):
        found = db.by_name(map_id, poi["name"])
        assert found == poi


def test_by_name_is_case_insensitive_and_trims(db):
    assert db.by_name("erangel", "  pochinki ")["name"] == "Pochinki"
    assert db.by_name("ERANGEL", "POCHINKI")["name"] == "Pochinki"
    assert db.by_name("sanhok", "paradise resort")["name"] == "Paradise Resort"


def test_by_name_unknown_name_returns_none(db):
    assert db.by_name("erangel", "Not A Real Place") is None
    # A real name on the wrong map is still unknown there.
    assert db.by_name("erangel", "Pecado") is None
    assert db.by_name("miramar", "Pochinki") is None


# ── unknown-map safety ──────────────────────────────────────────


def test_unknown_map_never_raises(db):
    assert db.all_pois("taego") == []
    assert db.all_pois("") == []
    assert db.by_name("taego", "Pochinki") is None
    assert db.nearest("taego", 0.5, 0.5) is None
    assert db.nearest("taego", 0.5, 0.5, max_radius=1.0) is None


def test_map_id_normalisation(db):
    assert db.all_pois(" Erangel ") == db.all_pois("erangel")
    assert db.by_name("SANHOK", "Bootcamp") is not None


# ── content spot-checks (names casters actually use) ────────────


def test_erangel_signature_pois_present(db):
    for name in (
        "Georgopol", "Georgopol Containers", "Hospital", "Pochinki",
        "School", "Sosnovka Military Base", "Sosnovka Island",
        "Novorepnoye", "Mylta", "Mylta Power", "Yasnaya Polyana",
        "Rozhok", "Severny", "Stalber", "Lipovka", "Primorsk",
        "Gatka", "Quarry", "Prison", "Mansion", "Farm", "Shelter",
        "Zharki", "Kameshki", "Shooting Range", "Ferry Pier",
        "Ruins", "Water Town",
        "Military Base Bridge West", "Military Base Bridge East",
    ):
        assert db.by_name("erangel", name) is not None, name


def test_miramar_signature_pois_present(db):
    for name in (
        "Pecado", "Hacienda del Patrón", "Los Leones", "El Pozo",
        "San Martín", "Monte Nuevo", "Chumacera", "La Cobrería",
        "Valle del Mar", "Puerto Paraíso", "Minas Generales",
        "Graveyard", "Ladrillera", "El Azahar", "Torre Ahumada",
        "Campo Militar", "Oasis", "Impala", "Los Higos",
        "Minas del Sur", "Prison",
    ):
        assert db.by_name("miramar", name) is not None, name


def test_sanhok_signature_pois_present(db):
    for name in (
        "Bootcamp", "Paradise Resort", "Ruins", "Pai Nan", "Khao",
        "Mongnai", "Camp Alpha", "Camp Bravo", "Camp Charlie",
        "Getaway", "Lakawi", "Na Kham", "Sahmee", "Ban Tai",
        "Docks", "Ha Tinh", "Tat Mok", "Bhan", "Tambang",
    ):
        assert db.by_name("sanhok", name) is not None, name


def test_hot_drops_are_tier_1(db):
    assert db.by_name("erangel", "Pochinki")["tier"] == 1
    assert db.by_name("erangel", "Sosnovka Military Base")["tier"] == 1
    assert db.by_name("sanhok", "Bootcamp")["tier"] == 1
    assert db.by_name("miramar", "Pecado")["tier"] == 1
    # Every map has at least one hot drop and one quiet POI.
    for map_id in EXPECTED_MAPS:
        tiers = {p["tier"] for p in db.all_pois(map_id)}
        assert 1 in tiers and 3 in tiers


def test_bridges_are_typed_as_bridges(db):
    for name in ("Military Base Bridge West", "Military Base Bridge East"):
        assert db.by_name("erangel", name)["type"] == "bridge"


# ── loading robustness ──────────────────────────────────────────


def test_missing_data_dir_is_empty_not_an_error(tmp_path: Path):
    empty = POIDatabase(data_dir=tmp_path / "does-not-exist")
    assert empty.maps() == []
    assert empty.all_pois("erangel") == []
    assert empty.nearest("erangel", 0.5, 0.5) is None


def test_malformed_data_file_is_skipped(tmp_path: Path):
    (tmp_path / "broken.json").write_text("{not json")
    (tmp_path / "ok.json").write_text(json.dumps({
        "map": "ok", "version": 1,
        "coords_note": "approximate naming references; not survey-grade",
        "pois": [
            {"name": "Good", "map": "ok", "x": 0.5, "y": 0.5,
             "type": "city", "tier": 1},
            {"name": "Bad Coords", "map": "ok", "x": 2.0, "y": 0.5,
             "type": "city", "tier": 1},
            {"name": "Bad Tier", "map": "ok", "x": 0.5, "y": 0.5,
             "type": "city", "tier": 9},
        ],
    }))
    db2 = POIDatabase(data_dir=tmp_path)
    assert db2.maps() == ["ok"]
    assert [p["name"] for p in db2.all_pois("ok")] == ["Good"]
