"""Join and export geographic records for equipment and social services."""

from pathlib import Path
from uuid import uuid4

import geopandas as gpd
import pandas as pd

from .cleaning import standardize_columns


def join_coordinates(services, coordinates):
    """Join coordinates without multiplying or dropping service records.

    Args:
        services: Source or standardized service table with an equipment ID.
        coordinates: Table with equipment_id, lat, and lon; one row per ID.

    Returns:
        pandas.DataFrame: English field names, including services without coordinates.

    Raises:
        pandas.errors.MergeError: Multiple coordinate records share an equipment ID.
    """
    left = standardize_columns(services)
    left["equipment_id"] = left["equipment_id"].astype("string")
    right = coordinates.copy()
    right["equipment_id"] = right["equipment_id"].astype("string")
    return left.merge(right, on="equipment_id", how="left", validate="many_to_one")


def make_points(data):
    """Create WGS84 points from valid latitude and longitude pairs.

    Args:
        data: DataFrame with lat, lon, and optional metadata columns.

    Returns:
        geopandas.GeoDataFrame: EPSG:4326 points, retaining lat and lon.
    """
    result = data.copy()
    result["lat"] = pd.to_numeric(result["lat"], errors="coerce")
    result["lon"] = pd.to_numeric(result["lon"], errors="coerce")
    valid = result["lat"].between(-90, 90) & result["lon"].between(-180, 180)
    result = result.loc[valid].copy()
    return gpd.GeoDataFrame(result, geometry=gpd.points_from_xy(result["lon"], result["lat"]),
                            crs="EPSG:4326")


def save_geopackage(points, path, layer, metadata=None):
    """Replace a single-dataset GeoPackage through a temporary output file.

    Args:
        points: GeoDataFrame containing the points to export.
        path: Destination GeoPackage path; any existing file is replaced in full.
        layer: Layer name inside the GeoPackage.
        metadata: Optional string-valued dataset metadata describing the export.

    Returns:
        Path: Written output path.

    Raises:
        ValueError: No valid points are available for export.
    """
    if points.empty:
        raise ValueError("No valid coordinates are available for export.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.{uuid4().hex}.gpkg")
    try:
        points.to_file(temporary, layer=layer, driver="GPKG", index=False, metadata=metadata)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
