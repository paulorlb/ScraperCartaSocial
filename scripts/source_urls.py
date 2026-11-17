"""Extract and validate equipment identifiers in source URL indexes."""

from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import pandas as pd

EQUIPMENT_ID_PARAMETER = "_SocialLetterPortlet_WAR_cartasocialportlet_idEquipment"


def equipment_id_from_url(url):
    """Extract the equipment identifier embedded in a Carta Social URL.

    Args:
        url: Recorded absolute HTTP or HTTPS Carta Social detail URL.

    Returns:
        str: Positive equipment identifier in canonical decimal form.

    Raises:
        ValueError: The URL is missing, has an unexpected host, or does not
            contain exactly one positive equipment identifier.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("A non-empty source URL is required.")
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or parts.hostname not in (
        "www.cartasocial.pt", "cartasocial.pt"
    ):
        raise ValueError("Expected an absolute Carta Social source URL.")
    values = parse_qs(parts.query, keep_blank_values=True).get(EQUIPMENT_ID_PARAMETER, [])
    if len(values) != 1 or not re.fullmatch(r"[0-9]+", values[0]) or int(values[0]) < 1:
        raise ValueError("Source URL must contain exactly one positive equipment ID.")
    return str(int(values[0]))


def add_equipment_ids(url_records):
    """Add or validate an equipment_id column while retaining all URL rows.

    Args:
        url_records: DataFrame containing url and optionally equipment_id.

    Returns:
        pandas.DataFrame: Copy with equipment_id as the first column.
            URLs, row order, duplicate rows, and unrelated columns are preserved.

    Raises:
        ValueError: A URL is invalid, the url column is missing, or an existing
            identifier disagrees with the ID embedded in its URL.
    """
    if "url" not in url_records:
        raise ValueError("The source URL table must contain a url column.")
    result = url_records.copy()
    identifiers = []
    for url in result["url"]:
        identifiers.append(equipment_id_from_url(url))
    parsed = pd.Series(identifiers, index=result.index, dtype="string")
    if "equipment_id" in result:
        existing = result["equipment_id"].astype("string").str.strip()
        if not existing.eq(parsed).fillna(False).all():
            raise ValueError("An equipment_id disagrees with the ID embedded in its URL.")
        result = result.drop(columns="equipment_id")
    result.insert(0, "equipment_id", parsed)
    return result


def load_equipment_urls(csv_path=None, txt_path=None):
    """Read a URL log and verify equivalent URL contents when both formats exist.

    Args:
        csv_path: Optional CSV path containing url and optionally equipment_id.
        txt_path: Optional UTF-8 text path containing one URL per non-empty line.

    Returns:
        pandas.DataFrame: equipment_id and url columns. Identical duplicate rows
            are collapsed, with one consistent URL per equipment identifier.

    Raises:
        ValueError: Inputs are absent, URL contents disagree, or a mapping is invalid
            or ambiguous. Explicitly supplied missing files raise FileNotFoundError.
    """
    if csv_path is None and txt_path is None:
        raise ValueError("Provide a CSV URL log, a text URL log, or both.")
    csv_table = txt_table = None
    if csv_path is not None:
        csv_table = add_equipment_ids(pd.read_csv(csv_path, dtype="string", encoding="utf-8-sig"))
    if txt_path is not None:
        lines = Path(txt_path).read_text(encoding="utf-8-sig").splitlines()
        urls = [line.strip() for line in lines if line.strip()]
        txt_table = add_equipment_ids(pd.DataFrame({"url": urls}))
    if csv_table is not None and txt_table is not None:
        if set(csv_table["url"]) != set(txt_table["url"]):
            raise ValueError("The CSV and text URL logs contain different URLs.")
    table = csv_table if csv_table is not None else txt_table
    table = table[["equipment_id", "url"]].drop_duplicates()
    if table["equipment_id"].duplicated().any():
        raise ValueError("An equipment identifier has multiple different source URLs.")
    return table
