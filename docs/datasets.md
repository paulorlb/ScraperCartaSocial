# Dataset guide

All paths below are relative to the external data root in config.toml.
No dataset is read from or written into the repository by the active workflow.

## Inputs

Configure source locations in the inputs section of config.toml.

| Input | Required fields |
| --- | --- |
| Service CSV | Equipment ID, Morada, Código Postal, and service attributes described below |
| Equipment URL CSV | url; equipment_id is optional and is added or validated from the URL |
| Optional URL text log | One source URL per non-empty line |
| Optional coordinate cache | equipment_id, address, lat, lon |
| Optional selection workbook | Configured service and address worksheets using the corresponding source fields |

The service CSV uses source labels, or standardized English fields where the
processing function accepts them. Equipment identifiers are read as strings.
Source category labels, addresses, and institution names retain their values.

The URL index maps equipment_id to url. It does not add URLs to the service
table. Several service rows can refer to one equipment URL. URL IDs without
matching service records are retained rather than assumed invalid. Parsing
an identifier does not verify that the live page is currently available.

## Standardized service fields

| Source field | Working field |
| --- | --- |
| Equipment ID | equipment_id |
| Resposta Social | service_type |
| Capacidade | capacity |
| Utentes | users |
| Horário | opening_hours |
| Última Atualização | last_updated |
| Certificações | certifications |
| Morada | street |
| Código Postal | postal_address |
| Telefone / Fax | telephone_fax |
| Email | email |
| Entidade proprietária | owner |
| Natureza Jurídica | legal_status |
| LocationCoordinates, if present | source_coordinates |

Cleaning adds capacity_invalid, users_invalid, and last_updated_invalid.
Address preparation derives address, postal_code, locality, and postal_code_valid.
Geocoding adds lat, lon, raw_addr, provider, status, and precision.

## Generated files

| Relative path | Contents |
| --- | --- |
| raw/scraping/equipment_ID.html | Collected page snapshot |
| raw/scraping/equipment_ID.json | Parsed equipment data, source URL, and UTC retrieval timestamp |
| raw/scraping/equipments.csv | Service records rebuilt from snapshots |
| raw/scraping/equipment_urls.csv | Equipment ID/URL mappings rebuilt from snapshots |
| interim/scraper/ | Per-equipment request state |
| interim/services_clean.csv | Full cleaned input table |
| interim/services_selected.csv | Configured service subset used by subsequent steps |
| interim/equipment_coordinates.csv | One coordinate result per selected equipment, including unresolved cases |
| interim/geocoding_cache.json | Incremental results from new geocoding requests |
| processed/carta_social_services.csv | All selected service records, including those without coordinates |
| processed/carta_social_service_locations.gpkg | Valid point features in service_locations, EPSG:4326 |
| processed/carta_social_dataset_metadata.json | Actual service categories, record counts, facility counts, and spatial coverage |

Interim contains only outputs of the active workflow. A service record with
no usable coordinates remains in the complete CSV and is excluded from the
point layer. Several records may share one facility location.

Re-execution refreshes current outputs. A single-dataset GeoPackage is replaced
as a whole so obsolete layers do not persist. If there are no valid points,
the current point export is removed and metadata reports its absence.
