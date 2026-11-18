"""Behavioral checks using synthetic HTML and tables without network access."""

from datetime import datetime, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from scripts.cleaning import clean_services, prepare_addresses, select_services, standardize_columns
from scripts.geocoder import geocode_addresses, load_coordinate_cache
from scripts.geography import join_coordinates, make_points, save_geopackage
from scripts.scraper import equipment_url, export_records, flatten_entry, parse_equipment_html, run_scraper
from scripts.snapshots import create_snapshot, export_dir_for, list_snapshots, read_manifest, resolve_snapshot
from scripts.source_urls import add_equipment_ids, equipment_id_from_url, load_equipment_urls

HTML = '''<div class="span5"><label>Morada</label><p>Rua A</p></div>
<div class="span5"><label>Entidade proprietária</label><p>Associação</p></div>
<table><tbody><tr><td>Creche</td><td>30</td><td>25</td><td>9-18</td>
<td>2025-01-01</td><td><a href="/certificado">Certificado</a></td></tr>
<tr><td>Centro de Dia</td><td>20</td></tr></tbody></table>'''


def test_parse_preserves_service_unit():
    entry = parse_equipment_html(HTML, 1)
    rows = flatten_entry(entry)
    assert len(rows) == 2
    assert rows[0]["Certificações"] == "/certificado"
    assert rows[1]["Utentes"] == ""
    assert rows[0]["Equipment ID"] == rows[1]["Equipment ID"] == 1
    assert parse_equipment_html('<div class="results-info-message">Não se encontra disponível</div>', 2) is None
    with pytest.raises(ValueError):
        parse_equipment_html("<html>Login</html>", 1)


UNAVAILABLE = '<div class="results-info-message">Não se encontra disponível</div>'


def fake_session(pages):
    """Mock HTTP session returning pages[equipment_id] for each detail URL."""
    session = MagicMock()
    session.__enter__.return_value = session

    def get(url, timeout):
        response = MagicMock()
        response.text = pages.get(int(equipment_id_from_url(url)), UNAVAILABLE)
        return response

    session.get.side_effect = get
    return session


def test_scraper_resume_retries_errors_without_duplicate_records(tmp_path):
    snapshot = create_snapshot(root=tmp_path / "snapshots")
    exports = tmp_path / "exports"
    pages = {1: HTML, 2: "<html>Temporary error</html>"}
    session = fake_session(pages)
    with patch("scripts.scraper.build_session", return_value=session):
        first = run_scraper(1, 2, snapshot, sleep_interval=0, output_dir=exports)
        assert len(first) == 2
        assert json.loads((snapshot / "state/equipment_2.json").read_text())["status"] == "error"
        manifest = read_manifest(snapshot)
        assert manifest["status"] == "partial"
        assert manifest["counts"] == {"ok": 1, "no_data": 0, "error": 1}
        with pytest.raises(ValueError, match="same range"):
            run_scraper(1, 3, snapshot, sleep_interval=0, output_dir=exports)
        pages[2] = HTML
        result = run_scraper(1, 2, snapshot, sleep_interval=0, output_dir=exports)
        assert len(result) == 4
        assert session.get.call_count == 3
        assert read_manifest(snapshot)["status"] == "complete"
        assert "url" not in result and "source_url" not in result
        url_index = pd.read_csv(exports / "equipment_urls.csv", dtype="string")
        assert url_index["equipment_id"].tolist() == ["1", "2"]
        assert url_index["url"].tolist() == [equipment_url(1), equipment_url(2)]
        with pytest.raises(ValueError, match="complete"):
            run_scraper(1, 2, snapshot, sleep_interval=0, output_dir=exports)
    assert session.get.call_count == 3


def test_new_snapshots_never_overwrite_earlier_collections(tmp_path):
    root = tmp_path / "snapshots"
    first = create_snapshot(root=root, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    second = create_snapshot(label="pilot", root=root, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert (first.name, second.name) == ("20260101T000000Z", "20260201T000000Z_pilot")
    with pytest.raises(FileExistsError):
        create_snapshot(root=root, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="labels"):
        create_snapshot(label="bad label", root=root)
    with patch("scripts.scraper.build_session", return_value=fake_session({1: HTML})):
        run_scraper(1, 1, first, sleep_interval=0, output_dir=tmp_path / "a")
    before = {path: path.read_bytes() for path in first.rglob("*") if path.is_file()}
    changed = HTML.replace("Rua A", "Rua B")
    with patch("scripts.scraper.build_session", return_value=fake_session({1: changed})):
        run_scraper(1, 1, second, sleep_interval=0, output_dir=tmp_path / "b")
    assert {path: path.read_bytes() for path in first.rglob("*") if path.is_file()} == before
    assert "Rua A" in (first / "html/equipment_1.html").read_text(encoding="utf-8")
    assert "Rua B" in (second / "html/equipment_1.html").read_text(encoding="utf-8")


def test_snapshot_listing_and_latest_resolution(tmp_path):
    root, exports = tmp_path / "snapshots", tmp_path / "exports"
    assert list_snapshots(root, exports).empty
    with pytest.raises(FileNotFoundError, match="No collection snapshots"):
        resolve_snapshot("latest", root)
    older = create_snapshot(root=root, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    newer = create_snapshot(root=root, now=datetime(2026, 3, 1, tzinfo=timezone.utc))
    (root / "not-a-snapshot").mkdir()
    with patch("scripts.scraper.build_session", return_value=fake_session({1: HTML})):
        run_scraper(1, 1, older, sleep_interval=0, output_dir=export_dir_for(older, exports))
    listing = list_snapshots(root, exports)
    assert listing["snapshot_id"].tolist() == [older.name, newer.name]
    assert listing["status"].tolist() == ["complete", "in_progress"]
    assert listing["csv_built"].tolist() == [True, False]
    assert resolve_snapshot(older.name, root) == older
    with pytest.warns(UserWarning, match="in_progress"):
        assert resolve_snapshot("latest", root) == newer
    with pytest.raises(FileNotFoundError):
        resolve_snapshot("20990101T000000Z", root)


def test_rebuild_from_html_matches_json_and_leaves_snapshot_unchanged(tmp_path):
    snapshot = create_snapshot(root=tmp_path / "snapshots")
    pages = {2: HTML, 3: "<html>Login</html>", 10: HTML.replace("Rua A", "Rua C")}
    with patch("scripts.scraper.build_session", return_value=fake_session(pages)):
        from_collection = run_scraper(1, 10, snapshot, sleep_interval=0, output_dir=tmp_path / "c")
    before = {path: path.read_bytes() for path in snapshot.rglob("*") if path.is_file()}
    from_json = export_records(snapshot, tmp_path / "json", source="json")
    from_html = export_records(snapshot, tmp_path / "html", source="html")
    assert {path: path.read_bytes() for path in snapshot.rglob("*") if path.is_file()} == before
    pd.testing.assert_frame_equal(from_json, from_collection)
    pd.testing.assert_frame_equal(from_json, from_html)
    assert from_json["Equipment ID"].tolist() == [2, 2, 10, 10]
    for folder in ("json", "html"):
        urls = pd.read_csv(tmp_path / folder / "equipment_urls.csv", dtype="string")
        assert urls["equipment_id"].tolist() == ["2", "10"]
    build = json.loads((tmp_path / "html/build.json").read_text(encoding="utf-8"))
    assert build["snapshot_id"] == snapshot.name and build["source"] == "html"
    assert build["parse_error_ids"] == [3]
    assert build["no_data_ids"] == [1, 4, 5, 6, 7, 8, 9]
    with pytest.raises(ValueError, match="source"):
        export_records(snapshot, tmp_path / "x", source="csv")


def test_cleaning_keeps_rows_and_flags_invalid_values():
    source = pd.DataFrame({"Capacidade": ["10", "invalid", "2.5", "-1"],
                           "Utentes": ["0", None, "3", "4"],
                           "Última Atualização": ["2025-01-01", "2025-02-30", None, None]})
    cleaned = clean_services(source)
    assert len(cleaned) == len(source)
    assert cleaned["capacity_invalid"].sum() == 3
    assert cleaned.loc[0, "users"] == 0
    assert pd.isna(cleaned.loc[1, "users"])
    assert cleaned["last_updated_invalid"].sum() == 1
    assert source.loc[1, "Capacidade"] == "invalid"


def test_addresses_detect_conflicts_but_allow_multiple_services():
    source = pd.DataFrame({"Equipment ID": ["1", "1"], "Morada": ["Rua A", "Rua A"],
                           "Código Postal": ["1000-001 Lisboa", "1000-001 Lisboa"]})
    assert len(prepare_addresses(source)) == 1
    source.loc[1, "Morada"] = "Rua B"
    with pytest.raises(ValueError, match="Conflicting"):
        prepare_addresses(source)


def test_english_schema_preserves_source_values_and_supports_address_preparation():
    source = pd.DataFrame({"Equipment ID": ["1"], "Morada": ["Rua A"],
                           "Código Postal": ["1000-001 Lisboa"],
                           "Resposta Social": ["Creche"]})
    cleaned = clean_services(source)
    assert cleaned["service_type"].tolist() == ["Creche"]
    assert "Equipment ID" in source and "equipment_id" in cleaned
    pd.testing.assert_frame_equal(prepare_addresses(source), prepare_addresses(cleaned))
    pd.testing.assert_frame_equal(cleaned, clean_services(cleaned))
    ambiguous = source.assign(equipment_id="2")
    with pytest.raises(ValueError, match="duplicate field names"):
        standardize_columns(ambiguous)


def test_cache_requires_same_address_and_does_not_use_network(tmp_path):
    addresses = pd.DataFrame({"equipment_id": ["1", "2"], "street": ["Rua A", "Rua B"],
                               "address": ["Rua A, Portugal", "Rua B, Portugal"]})
    cache = pd.DataFrame({"equipment_id": ["1", "2"],
                          "address": ["Rua A, Portugal", "Previous address"],
                          "lat": [38.7, 39.0], "lon": [-9.1, -8.0], "status": ["ok", "ok"]})
    with patch("requests.Session.get", side_effect=AssertionError("Network access is disabled")):
        result = geocode_addresses(addresses, cached=cache, cache_path=tmp_path / "cache.json")
    assert list(result["status"]) == ["ok", "pending"]
    assert not (tmp_path / "cache.json").exists()


def test_cache_filters_invalid_coordinates(tmp_path):
    path = tmp_path / "google.json"
    path.write_text(json.dumps([
        {"equipment_id": "1", "address": "A", "lat": 38, "lon": -9},
        {"equipment_id": "2", "address": "B", "lat": 38, "lon": None},
        {"equipment_id": "3", "address": "C", "lat": 900, "lon": -9},
    ]), encoding="utf-8")
    result = load_coordinate_cache([path])
    assert list(result["equipment_id"]) == ["1"]
    assert result.iloc[0]["precision"] == "unknown"


def test_join_keeps_missing_and_rejects_coordinate_duplication():
    services = pd.DataFrame({"Equipment ID": ["1", "1", "2"],
                             "Resposta Social": ["Creche", "Centro de Dia", "Creche"]})
    coords = pd.DataFrame({"equipment_id": ["1"], "lat": [38.7], "lon": [-9.1]})
    joined = join_coordinates(services, coords)
    assert len(joined) == 3
    points = make_points(joined)
    assert len(points) == 2
    assert points.crs.to_epsg() == 4326
    assert points.iloc[0].geometry.x == -9.1
    with pytest.raises(pd.errors.MergeError):
        join_coordinates(services, pd.concat([coords, coords]))


def test_geocoder_limits_requests_and_resumes_from_checkpoint(tmp_path):
    addresses = pd.DataFrame({"equipment_id": ["1", "2"], "street": ["Rua A", "Rua B"],
                               "address": ["Rua A, Portugal", "Rua B, Portugal"]})
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = [{"lat": "38.7", "lon": "-9.1", "display_name": "Rua"}]
    cache_path = tmp_path / "cache.json"
    with patch("requests.Session.get", return_value=response) as get:
        first = geocode_addresses(addresses, provider="nominatim", delay=0,
                                  max_requests=1, cache_path=cache_path)
        assert first["status"].tolist() == ["ok", "pending"]
        assert get.call_count == 1
        second = geocode_addresses(addresses, provider="nominatim", delay=0,
                                   max_requests=1, cache_path=cache_path)
        assert second["status"].tolist() == ["ok", "ok"]
        assert get.call_count == 2
    assert len(json.loads(cache_path.read_text(encoding="utf-8"))) == 2


def test_not_found_from_another_provider_does_not_block_explicit_query(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps([{
        "equipment_id": "1", "address": "Rua A, Portugal", "provider": "google",
        "status": "not_found", "lat": None, "lon": None,
    }]), encoding="utf-8")
    addresses = pd.DataFrame({"equipment_id": ["1"], "street": ["Rua A"],
                               "address": ["Rua A, Portugal"]})
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = []
    with patch("requests.Session.get", return_value=response) as get:
        result = geocode_addresses(addresses, provider="nominatim", delay=0,
                                   max_requests=1, cache_path=cache_path)
    assert get.call_count == 1
    assert result.iloc[0]["provider"] == "nominatim"
    assert result.iloc[0]["status"] == "not_found"


def test_service_selection_supports_all_categories_and_arbitrary_subsets():
    source = pd.DataFrame({"equipment_id": ["1", "2", "3", "4"],
                           "service_type": ["Childcare", "Home support", "Shelter", "Home support"]})
    assert len(select_services(source)) == 4
    selected = select_services(source, ["Home support", "Shelter"])
    assert selected["equipment_id"].tolist() == ["2", "3", "4"]
    assert len(source) == 4
    with pytest.raises(ValueError, match="not present"):
        select_services(source, ["Unavailable category"])
    with pytest.raises(ValueError, match="list"):
        select_services(source, "Shelter")
    with pytest.raises(ValueError, match="at least one"):
        select_services(source, [])


def test_geopackage_replacement_removes_obsolete_layers_and_keeps_metadata(tmp_path):
    points = make_points(pd.DataFrame({"equipment_id": ["1"], "lat": [38.7], "lon": [-9.1]}))
    destination = tmp_path / "locations.gpkg"
    save_geopackage(points, destination, "obsolete_layer")
    save_geopackage(points, destination, "service_locations",
                    metadata={"title": "Carta Social service locations"})
    with sqlite3.connect(destination) as connection:
        layers = connection.execute("SELECT table_name FROM gpkg_contents WHERE data_type='features'").fetchall()
        assert layers == [("service_locations",)]
        metadata = connection.execute("SELECT metadata FROM gpkg_metadata").fetchall()
        assert any("Carta Social service locations" in row[0] for row in metadata)


def test_url_id_extraction_rejects_invalid_or_ambiguous_identifiers():
    assert equipment_id_from_url(equipment_url(37)) == "37"
    invalid_urls = [
        "https://example.com/?_SocialLetterPortlet_WAR_cartasocialportlet_idEquipment=37",
        "https://www.cartasocial.pt/", "", None,
        equipment_url(0), equipment_url(-1),
        equipment_url(37) + "&_SocialLetterPortlet_WAR_cartasocialportlet_idEquipment=38",
    ]
    for url in invalid_urls:
        with pytest.raises(ValueError):
            equipment_id_from_url(url)


def test_url_id_column_preserves_urls_row_order_and_duplicate_rows():
    source = pd.DataFrame({"url": [equipment_url(5), equipment_url(1), equipment_url(5)]},
                          index=[10, 20, 30])
    result = add_equipment_ids(source)
    assert result["equipment_id"].tolist() == ["5", "1", "5"]
    assert result["url"].equals(source["url"])
    assert "equipment_id" not in source
    pd.testing.assert_frame_equal(add_equipment_ids(result), result)
    with pytest.raises(ValueError, match="disagrees"):
        add_equipment_ids(pd.DataFrame({"equipment_id": ["2"], "url": [equipment_url(1)]}))


def test_url_log_formats_must_agree_when_both_are_supplied(tmp_path):
    csv_path, txt_path = tmp_path / "urls.csv", tmp_path / "urls.txt"
    pd.DataFrame({"url": [equipment_url(1), equipment_url(2)]}).to_csv(csv_path, index=False)
    txt_path.write_text(equipment_url(2) + "\n" + equipment_url(1) + "\n", encoding="utf-8")
    assert load_equipment_urls(csv_path, txt_path)["equipment_id"].tolist() == ["1", "2"]
    assert len(load_equipment_urls(txt_path=txt_path)) == 2
    txt_path.write_text(equipment_url(3) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different URLs"):
        load_equipment_urls(csv_path, txt_path)


def test_scraper_exports_source_url_even_when_equipment_has_no_service_rows(tmp_path):
    payload = {"url": equipment_url(7), "data": {"equipment_id": 7, "table_data": []}}
    (tmp_path / "json").mkdir()
    (tmp_path / "json/equipment_7.json").write_text(json.dumps(payload), encoding="utf-8")
    services = export_records(tmp_path, tmp_path / "out")
    assert services.empty
    urls = pd.read_csv(tmp_path / "out/equipment_urls.csv", dtype="string")
    assert urls.to_dict("records") == [{"equipment_id": "7", "url": equipment_url(7)}]
    assert "url" not in pd.read_csv(tmp_path / "out/equipments.csv").columns


def test_private_nominatim_credentials_are_used_but_not_persisted(tmp_path):
    addresses = pd.DataFrame({"equipment_id": ["1"], "street": ["Street A"],
                               "address": ["Street A, Portugal"]})
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = []
    session = MagicMock()
    session.__enter__.return_value = session
    session.headers = {}
    session.get.return_value = response
    cache = tmp_path / "cache.json"
    with patch("requests.Session", return_value=session):
        geocode_addresses(addresses, provider="nominatim", delay=0,
                           nominatim_username="test-account", nominatim_password="test-secret",
                           user_agent="TestPipeline/1.0", cache_path=cache)
    assert session.auth == ("test-account", "test-secret")
    assert session.headers["User-Agent"] == "TestPipeline/1.0"
    stored = cache.read_text(encoding="utf-8")
    assert "test-secret" not in stored and "test-account" not in stored
