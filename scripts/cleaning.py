"""Standardize source fields and prepare records without modifying raw data."""

import re

import pandas as pd

SOURCE_COLUMNS = {
    "Equipment ID": "equipment_id",
    "Resposta Social": "service_type",
    "Capacidade": "capacity",
    "Utentes": "users",
    "Horário": "opening_hours",
    "Última Atualização": "last_updated",
    "Certificações": "certifications",
    "Morada": "street",
    "Código Postal": "postal_address",
    "Telefone / Fax": "telephone_fax",
    "Email": "email",
    "Entidade proprietária": "owner",
    "Natureza Jurídica": "legal_status",
    "LocationCoordinates": "source_coordinates",
}


def standardize_columns(data):
    """Map original source labels to the project's English field names.

    Args:
        data: Source or already standardized DataFrame.

    Returns:
        pandas.DataFrame: Copy with English field names; values are unchanged.

    Raises:
        ValueError: Source and standardized labels would create duplicate fields.
    """
    result = data.rename(columns=SOURCE_COLUMNS).copy()
    if result.columns.duplicated().any():
        raise ValueError("Source and standardized columns produce duplicate field names.")
    return result


def clean_services(data):
    """Standardize fields, trim text, and convert counts and dates without dropping rows.

    Args:
        data: DataFrame with source labels or standardized English field names.

    Returns:
        pandas.DataFrame: Cleaned copy with English fields and invalid-value flags.
    """
    result = standardize_columns(data)
    for column in result.select_dtypes(include=["object", "string"]).columns:
        result[column] = result[column].astype("string").str.strip()
        result[column] = result[column].replace("", pd.NA)
    for column in ["capacity", "users"]:
        if column in result:
            original = result[column]
            numeric = pd.to_numeric(original, errors="coerce")
            invalid = numeric.isna() | (numeric < 0) | (numeric % 1 != 0)
            result[column + "_invalid"] = original.notna() & invalid
            result[column] = numeric.mask(invalid).astype("Int64")
    column = "last_updated"
    if column in result:
        original = result[column]
        result[column] = pd.to_datetime(original, format="%Y-%m-%d", errors="coerce")
        result[column + "_invalid"] = original.notna() & result[column].isna()
    return result


def select_services(data, service_types=None):
    """Select any source service categories, or retain all categories by default.

    Args:
        data: Source or standardized service table.
        service_types: Sequence of exact source category labels, or None for all.

    Returns:
        pandas.DataFrame: Copy with English field names and the selected rows.

    Raises:
        ValueError: The category field is missing, the selection is empty,
            a single string was passed, or a requested label is unavailable.
    """
    result = standardize_columns(data)
    if "service_type" not in result:
        raise ValueError("Missing required column: service_type.")
    if service_types is None:
        return result
    if isinstance(service_types, str):
        raise ValueError("Pass a list of service labels, or None for all categories.")
    labels = list(service_types)
    if not labels:
        raise ValueError("Select at least one category, or use None for all categories.")
    unavailable = set(labels) - set(result["service_type"].dropna())
    if unavailable:
        raise ValueError(f"Service categories not present in the input: {sorted(unavailable)}")
    return result.loc[result["service_type"].isin(labels)].copy()


def prepare_addresses(data):
    """Prepare one address per equipment and flag postal-code format issues.

    Args:
        data: Source or standardized table with equipment ID, street, and postal address.

    Returns:
        pandas.DataFrame: equipment_id, address, postal_code, locality,
        postal_code_valid, and street. Identifiers are strings.

    Raises:
        ValueError: Identifiers are missing or an ID has conflicting addresses.
    """
    data = standardize_columns(data)
    required = ["equipment_id", "street", "postal_address"]
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    rows, seen = [], {}
    for _, row in data.iterrows():
        if pd.isna(row["equipment_id"]):
            raise ValueError("An equipment record has no identifier.")
        equipment_id = str(row["equipment_id"]).strip()
        if not equipment_id:
            raise ValueError("An equipment record has no identifier.")
        street = "" if pd.isna(row["street"]) else str(row["street"]).strip()
        postal = "" if pd.isna(row["postal_address"]) else str(row["postal_address"]).strip()
        signature = (street, postal)
        if equipment_id in seen:
            if seen[equipment_id] != signature:
                raise ValueError(f"Conflicting addresses for equipment {equipment_id}.")
            continue
        seen[equipment_id] = signature
        match = re.match(r"^(\d{4}-\d{3})(?:\s+(.*))?$", postal)
        parts = [part for part in [street, postal] if part]
        address = ", ".join(parts + ["Portugal"]) if parts else ""
        rows.append({"equipment_id": equipment_id, "street": street,
                     "postal_code": match.group(1) if match else "",
                     "locality": (match.group(2) or "") if match else postal,
                     "postal_code_valid": bool(match), "address": address})
    return pd.DataFrame(rows, columns=["equipment_id", "street", "postal_code",
                                      "locality", "postal_code_valid", "address"])
