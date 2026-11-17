"""Geocode addresses with caching, request limits, and offline defaults."""

import json
import re
from pathlib import Path
from time import sleep
from urllib.parse import urlparse

import pandas as pd
import requests

from .utils import INTERIM, write_json

COORD_COLUMNS = ["equipment_id", "address", "lat", "lon", "raw_addr",
                 "provider", "status", "precision"]


def _address_key(value):
    """Normalize address text for cache comparisons only.

    Args:
        value: Address text or a missing value.

    Returns:
        str: Case-folded text with normalized whitespace.
    """
    if pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def load_coordinate_cache(paths):
    """Load JSON or Parquet caches, giving the first valid source priority.

    Args:
        paths: Ordered paths. Missing files are ignored.

    Returns:
        pandas.DataFrame: Valid coordinate pairs, unique by equipment_id.
            Records without precision metadata use unknown.

    Raises:
        ValueError: An existing cache is missing required fields.
    """
    frames = []
    for item in paths:
        path = Path(item)
        if not path.exists():
            continue
        if path.suffix == ".parquet":
            frame = pd.read_parquet(path)
        else:
            frame = pd.DataFrame(json.loads(path.read_text(encoding="utf-8")))
        if frame.empty:
            continue
        required = {"equipment_id", "address", "lat", "lon"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Cache sem colunas obrigatórias: {path.name}")
        frame["equipment_id"] = frame["equipment_id"].astype("string")
        for column in ["lat", "lon"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        valid = frame["lat"].between(-90, 90) & frame["lon"].between(-180, 180)
        frame = frame.loc[valid].copy()
        if "provider" not in frame:
            frame["provider"] = "google" if "google" in path.name else "unspecified"
        if "precision" not in frame:
            frame["precision"] = "unknown"
        frame["status"] = "ok"
        frames.append(frame.reindex(columns=COORD_COLUMNS))
    if not frames:
        return pd.DataFrame(columns=COORD_COLUMNS)
    return pd.concat(frames, ignore_index=True).drop_duplicates("equipment_id", keep="first")


def geocode_addresses(addresses, cached=None, provider=None, api_key=None,
                      nominatim_url="http://localhost:8080", delay=1.1,
                      max_requests=5, cache_path=INTERIM / "geocoding_cache.json",
                      user_agent="CartaSocialPipeline/1.0",
                      nominatim_username=None, nominatim_password=None):
    """Reuse matching coordinates and optionally query a geocoding provider.

    Args:
        addresses: Output of prepare_addresses, with one row per equipment ID.
        cached: Output of load_coordinate_cache, or None.
        provider: None for offline use, or google or nominatim for new requests.
        api_key: Google API key. Never written to results.
        nominatim_url: Nominatim instance URL; defaults to a local service.
        delay: Pause before each request, in seconds.
        max_requests: Maximum new requests in this call.
        cache_path: JSON results file updated after each request.
        user_agent: HTTP application identifier, optionally supplied from .env.
        nominatim_username: Optional username for private-instance HTTP Basic auth.
        nominatim_password: Optional matching password, never persisted in results.

    Returns:
        pandas.DataFrame: One row per ID with status ok, pending, not_found,
            missing_address, or error. Coordinates remain estimates.

    Notes:
        Cache reuse requires matching IDs and addresses. Transient failures
        are retried on later runs. There is no automatic postal-centroid
        fallback or switch to a paid provider.
    """
    if provider not in (None, "google", "nominatim"):
        raise ValueError("provider must be None, 'google', or 'nominatim'.")
    if max_requests < 0 or delay < 0:
        raise ValueError("Request limit and delay must be non-negative.")
    if addresses["equipment_id"].astype(str).duplicated().any():
        raise ValueError("Exactly one address per equipment is required.")
    if provider == "google" and not api_key:
        raise ValueError("Set GOOGLE_MAPS_API_KEY to use Google.")
    if provider == "nominatim" and nominatim_password and not nominatim_username:
        raise ValueError("NOMINATIM_USERNAME is required when a password is supplied.")
    if urlparse(nominatim_url).hostname == "nominatim.openstreetmap.org" and delay < 1.1:
        raise ValueError("This project requires a delay of at least 1.1 seconds for public Nominatim.")
    cache_path = Path(cache_path)
    saved = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else []
    lookup = {}
    if cached is not None:
        for record in cached.to_dict("records"):
            lookup[(str(record["equipment_id"]), _address_key(record["address"]))] = record
    # New results override imported caches only for a matching address.
    for record in saved:
        reusable = record["status"] == "ok"
        same_provider = provider is None or record.get("provider") == provider
        if reusable or (record["status"] == "not_found" and same_provider):
            lookup[(str(record["equipment_id"]), _address_key(record["address"]))] = record
    google = None
    output = []
    requests_used = 0
    with requests.Session() as session:
        session.headers["User-Agent"] = user_agent
        if provider == "nominatim" and nominatim_username:
            session.auth = (nominatim_username, nominatim_password or "")
        for _, row in addresses.iterrows():
            identifier, address = str(row["equipment_id"]), row["address"]
            key = (identifier, _address_key(address))
            if key in lookup:
                output.append(lookup[key])
                continue
            record = {"equipment_id": identifier, "address": address, "lat": None,
                      "lon": None, "raw_addr": None, "provider": provider,
                      "status": "pending", "precision": "unknown"}
            if not address or not row["street"]:
                record["status"] = "missing_address"
            elif provider is not None and requests_used < max_requests:
                sleep(delay)
                requests_used += 1
                try:
                    if provider == "google":
                        if google is None:
                            from geopy.geocoders import GoogleV3
                            google = GoogleV3(api_key=api_key, timeout=15)
                        location = google.geocode(address, region="pt", language="pt")
                        if location:
                            record.update(lat=location.latitude, lon=location.longitude,
                                          raw_addr=location.address)
                            geometry = location.raw.get("geometry", {})
                            record["precision"] = geometry.get("location_type", "unknown")
                    else:
                        response = session.get(nominatim_url.rstrip("/") + "/search",
                                               params={"q": address, "format": "jsonv2",
                                                       "limit": 1, "countrycodes": "pt"},
                                               timeout=15)
                        if response.status_code == 404:
                            # PHP instances may expose only /search.php.
                            # The second request also counts towards the request limit.
                            if requests_used >= max_requests:
                                raise requests.RequestException("Request limit reached before trying the alternative endpoint.")
                            sleep(delay)
                            requests_used += 1
                            response = session.get(nominatim_url.rstrip("/") + "/search.php",
                                                   params={"q": address, "format": "jsonv2",
                                                           "limit": 1, "countrycodes": "pt"}, timeout=15)
                        response.raise_for_status()
                        hits = response.json()
                        if hits:
                            record.update(lat=float(hits[0]["lat"]), lon=float(hits[0]["lon"]),
                                          raw_addr=hits[0].get("display_name"))
                    record["status"] = "not_found" if record["lat"] is None else "ok"
                    if record["status"] == "ok":
                        if not (-90 <= record["lat"] <= 90 and -180 <= record["lon"] <= 180):
                            raise ValueError("Coordinates are outside global latitude and longitude bounds.")
                except ImportError:
                    raise
                except Exception:
                    # API exception messages may contain credentials; persist only the status.
                    record.update(status="error", lat=None, lon=None)
                saved = [old for old in saved if not (
                    str(old["equipment_id"]) == identifier and _address_key(old["address"]) == key[1])]
                saved.append(record)
                write_json(saved, cache_path)
            output.append(record)
    return pd.DataFrame(output, columns=COORD_COLUMNS)
