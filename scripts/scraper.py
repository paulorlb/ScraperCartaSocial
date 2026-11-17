"""Extract Carta Social records at the equipment and social-service level.

HTML selectors target the portal detail layout and may need revision when
the portal changes. Importing this module does not make network requests.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from time import sleep

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .utils import INTERIM, RAW, write_json
from .source_urls import add_equipment_ids

BASE_URL = "https://www.cartasocial.pt/resultados-da-pesquisa"
SERVICE_COLUMNS = ["Resposta Social", "Capacidade", "Utentes", "Horário",
                   "Última Atualização", "Certificações"]


def build_session(total_retries=3, user_agent="CartaSocialPipeline/1.0"):
    """Create a session with bounded retries for GET requests.

    Args:
        total_retries: Maximum retries for each HTTP request.
        user_agent: HTTP application identifier, optionally supplied from .env.

    Returns:
        requests.Session: Session that must be closed by the caller.
    """
    session = requests.Session()
    session.headers["User-Agent"] = user_agent
    retry = Retry(total=total_retries, backoff_factor=1,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"], respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def equipment_url(equipment_id):
    """Build the portal detail URL for an equipment identifier.

    Args:
        equipment_id: Integer identifier used by the source portal.

    Returns:
        str: URL with the original portal query parameters.
    """
    prefix = "_SocialLetterPortlet_WAR_cartasocialportlet"
    params = {
        "p_p_id": "SocialLetterPortlet_WAR_cartasocialportlet",
        "p_p_lifecycle": "0", "p_p_state": "normal", "p_p_mode": "view",
        "p_p_col_id": "column-1", "p_p_col_count": "1",
        prefix + "__facesViewIdRender": "/views/socialLetter/list/view/equipment/equipment_detail.xhtml",
        prefix + "_idEquipment": int(equipment_id),
    }
    return requests.Request("GET", BASE_URL, params=params).prepare().url


def parse_equipment_html(html, equipment_id):
    """Extract contact, institutional, and social-service sections.

    Args:
        html: HTML content of an equipment detail page.
        equipment_id: Identifier of the requested equipment.

    Returns:
        dict or None: Structured source data, or None for explicit unavailability.

    Raises:
        ValueError: The page does not contain the expected detail structure.
    """
    soup = BeautifulSoup(html, "html.parser")
    message = soup.find("div", class_="results-info-message")
    if message and "não se encontra disponível" in message.get_text(strip=True).lower():
        return None
    sections = soup.find_all("div", class_="span5")
    if not sections:
        raise ValueError("Expected detail sections are missing; check the HTML or portal access.")
    details = []
    for section in sections[:2]:
        values = {}
        for label, value in zip(section.find_all("label"), section.find_all("p")):
            values[label.get_text(strip=True)] = value.get_text(strip=True)
        details.append(values)
    if not any(details):
        raise ValueError("No recognized equipment detail fields were found.")
    table_data = []
    table = soup.find("table")
    body = table.find("tbody") if table else None
    if body:
        for row in body.find_all("tr"):
            cells = []
            for index, cell in enumerate(row.find_all("td")):
                link = cell.find("a", href=True)
                value = link["href"] if index == 5 and link else cell.get_text(strip=True)
                cells.append(value)
            if cells:
                table_data.append(cells)
    return {"equipment_id": int(equipment_id), "contact_data": details[0],
            "institutional_data": details[1] if len(details) > 1 else {},
            "table_data": table_data}


def flatten_entry(entry):
    """Convert an equipment record into social-service rows.

    Args:
        entry: Dictionary returned by parse_equipment_html.

    Returns:
        list[dict]: One row per service, retaining original source field labels.
            Equipment without listed services produces an empty list.
    """
    records = []
    for service in entry["table_data"]:
        record = dict(entry.get("contact_data", {}))
        record.update(entry.get("institutional_data", {}))
        record["Equipment ID"] = entry["equipment_id"]
        for index, name in enumerate(SERVICE_COLUMNS):
            record[name] = service[index] if index < len(service) else ""
        records.append(record)
    return records


def export_records(raw_dir=RAW / "scraping"):
    """Rebuild service records and a separate ID/URL index from JSON snapshots.

    Args:
        raw_dir: Directory containing snapshots and both output CSV files.

    Returns:
        pandas.DataFrame: Service records with original source labels. Also writes
            equipment_urls.csv with equipment_id and url, including equipment
            without service rows. The service CSV does not contain URL columns.
    """
    raw_dir = Path(raw_dir)
    records = []
    url_records = []
    for path in sorted(raw_dir.glob("equipment_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        records.extend(flatten_entry(payload["data"]))
        url_records.append({"equipment_id": payload["data"]["equipment_id"], "url": payload["url"]})
    columns = ["Equipment ID"] + SERVICE_COLUMNS
    result = pd.DataFrame(records) if records else pd.DataFrame(columns=columns)
    url_index = add_equipment_ids(pd.DataFrame(url_records, columns=["equipment_id", "url"]))
    url_index = url_index.drop_duplicates()
    if url_index["equipment_id"].duplicated().any():
        raise ValueError("Snapshots contain conflicting URLs for one equipment ID.")
    raw_dir.mkdir(parents=True, exist_ok=True)
    temporary = raw_dir / "equipments.csv.tmp"
    result.to_csv(temporary, index=False, encoding="utf-8")
    temporary.replace(raw_dir / "equipments.csv")
    temporary_urls = raw_dir / "equipment_urls.csv.tmp"
    url_index.to_csv(temporary_urls, index=False, encoding="utf-8")
    temporary_urls.replace(raw_dir / "equipment_urls.csv")
    return result


def run_scraper(start_id, end_id, raw_dir=RAW / "scraping",
                state_dir=INTERIM / "scraper", sleep_interval=5, timeout=30,
                user_agent="CartaSocialPipeline/1.0"):
    """Collect an inclusive ID range, resuming records and retrying failures.

    Args:
        start_id: First positive equipment identifier to request.
        end_id: Last equipment identifier, included in the range.
        raw_dir: Directory for HTML pages and JSON snapshots.
        state_dir: Directory for per-ID states: ok, no_data, or error.
        sleep_interval: Pause after each requested equipment, in seconds.
        timeout: Maximum request wait, in seconds.
        user_agent: HTTP application identifier, optionally supplied from .env.

    Returns:
        pandas.DataFrame: All service records stored in raw_dir.

    Notes:
        Failures are recorded and retried on the next execution. Snapshot and state directories identify the collection being resumed.
    """
    if start_id < 1 or end_id < start_id or sleep_interval < 0 or timeout <= 0:
        raise ValueError("Invalid ID range, pause, or timeout.")
    raw_dir, state_dir = Path(raw_dir), Path(state_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    with build_session(user_agent=user_agent) as session:
        for equipment_id in range(start_id, end_id + 1):
            snapshot = raw_dir / f"equipment_{equipment_id}.json"
            state = state_dir / f"equipment_{equipment_id}.json"
            if snapshot.exists():
                continue
            if state.exists():
                previous = json.loads(state.read_text(encoding="utf-8"))
                if previous["status"] == "no_data":
                    continue
            url = equipment_url(equipment_id)
            status, error = "error", None
            try:
                response = session.get(url, timeout=timeout)
                response.raise_for_status()
                (raw_dir / f"equipment_{equipment_id}.html").write_text(response.text, encoding="utf-8")
                data = parse_equipment_html(response.text, equipment_id)
                status = "no_data" if data is None else "ok"
                if data is not None:
                    write_json({"url": url, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                "data": data}, snapshot)
            except (requests.RequestException, ValueError) as exc:
                error = str(exc)
            write_json({"equipment_id": equipment_id, "status": status, "error": error,
                        "timestamp": datetime.now(timezone.utc).isoformat()}, state)
            print(f"ID {equipment_id}: {status}")
            sleep(sleep_interval)
    return export_records(raw_dir)
