# Processing methodology

## Scope and observation unit

Each service record describes a social service offered by an equipment
(facility). Equipment identifiers can repeat across different service types.
The workflow accepts all available categories or any exact-label subset
configured in notebook 02. It does not assume a predefined category list.

An equipment is not necessarily an institution. Record counts, distinct
equipment counts, and owning-institution counts answer different questions.
An input collection does not establish complete national coverage or a
representative sample.

## Reproducibility

Machine-specific data paths come from root config.toml. All input and output
data remain under the configured external root. Root .env supplies credentials
to notebooks when needed, without embedding them in code or datasets.

The selected service table is saved once and used for both geocoding and
export. Category counts and spatial coverage in the metadata describe the
actual configured selection.

## Cleaning

Source labels are standardized to English. Surrounding whitespace is removed,
and empty strings become missing values. Capacity and user counts must be
non-negative integers. Invalid non-missing values receive quality flags.
Missing counts are not imputed as zero.

Dates use YYYY-MM-DD. Invalid non-missing dates receive a flag. Cleaning
preserves rows, including exact duplicates. Duplicate removal requires a
documented decision about the observation unit.

## Equipment addresses

Address preparation requires one consistent address per equipment identifier.
Conflicts trigger an explicit error rather than an arbitrary selection.

When optional workbook mode is used, its configured address worksheet is the
canonical address source. It is filtered to selected equipment, and coverage
is checked before geocoding. Otherwise addresses come from the service table.

## Source URLs

Equipment IDs are extracted from the source URL query parameter. A valid
mapping requires an absolute Carta Social URL with exactly one positive
equipment ID. Existing IDs are validated against the embedded parameter.
Malformed, ambiguous, or conflicting mappings raise errors.

The structured URL index remains separate from service records. New collection
snapshots preserve the actual request URL, including equipment without listed
services. Both CSV exports can be rebuilt from those snapshots.

## Geocoding

Cache reuse requires both equipment ID and normalized address to match.
Normalization changes only case and whitespace for comparison. A different
address is not assigned a cached coordinate silently.

| Status | Interpretation |
| --- | --- |
| ok | Coordinates are available; accuracy is not certified |
| pending | No matching result and no new result available within the chosen request budget |
| missing_address | No usable street address |
| not_found | The selected provider returned no match |
| error | The query or response handling failed |

Coordinates without precision metadata are marked unknown. New Google
results retain location_type when available. There is no automatic postal
centroid substitution or paid-provider fallback.

Latitude and longitude are checked against global bounds. This is a numeric
check, not proof of correct facility location. Spatial validation against
administrative boundaries and inspection of a sample remain necessary.

## Geographic export

The join from service records to coordinates is many-to-one. Duplicate
coordinate IDs are rejected to prevent multiplication of records. A left join
retains every selected service in the complete table.

Only valid coordinate pairs generate point geometries. EPSG:4326 expresses
longitude and latitude in degrees, with longitude as x and latitude as y.
Services at the same equipment overlap on the map.

Unresolved records can be spatially uneven. Assess their distribution and
location quality before drawing territorial conclusions.

## Request management

Collection uses bounded retries, a configurable request delay, and per-ID
snapshots. Existing results are reused, while failed requests can be retried.
Explicit source unavailability is distinguished from unexpected pages.

Geocoding has a request budget and incremental caching. Credentials are
read from .env when needed and are not persisted in results. HTTP errors
from geocoding are stored as statuses rather than messages that could expose
credentials.

Provider terms, rate limits, and attribution requirements apply. Accessibility
matrices are outside this workflow's scope.
