"""Read-only, access-limited reservation inventory and catalogue exposure."""

FIRST_PAGE = "/providers/Microsoft.Capacity/reservations?api-version=2022-11-01&take=100"


def page_query():
    return r"""resourcecontainers
| take 1
| summarize Seed = count()
| project RawPage = dynamic({ReservationPageRecord:json})
| extend Page = iff(gettype(RawPage) == 'string', parse_json(tostring(RawPage)), RawPage)
| extend RecordsKnown = gettype(Page.value) == 'array', Records = Page.value,
         NextLink = tostring(Page.nextLink)
| extend NextPagePath = extract(
    @'(?i)^(?:https://management[.]azure[.]com)?(/providers/Microsoft[.]Capacity/reservations[?][^\s#]+)$',
    1, NextLink)
| extend NextPagePath = iff(RecordsKnown and isnotempty(NextLink), NextPagePath, '')"""


def coverage_query():
    return page_query() + r"""
| project Evidence = case(
    not(RecordsKnown), 'Unknown - no valid reservation response; check request errors and reservation permissions',
    array_length(Records) > 100, 'Partial - API page exceeds the 100-row display limit; reconcile in Reservations',
    isnotempty(NextLink) and isempty(NextPagePath), 'Partial - unsupported continuation; reconcile in Reservations',
    isnotempty(NextLink), 'More pages - use Next reservation page; this is not a complete inventory',
    array_length(Records) == 0, 'No visible reservations on this page - not proof the customer owns none',
    'Last API page - visibility is still limited to reservations you can read'),
  RowsInPage = array_length(Records),
  Scope = 'Current tenant, reservation access; independent of the VM subscription/lifecycle filters',
  NextPagePath"""


def inventory_query(regional_prices, excluded_series, excluded_pattern):
    import json
    prices = json.dumps(regional_prices, separators=(",", ":"))
    exclusions = json.dumps(sorted(s.lower() for s in excluded_series))
    return page_query() + r"""
| where RecordsKnown
| mv-expand Reservation = Records limit 2000
| extend ResourceType = tostring(Reservation.properties.reservedResourceType)
| where ResourceType =~ 'VirtualMachines' or isempty(ResourceType)
| extend ReservedSku = tostring(Reservation.sku.name),
         ReservationId = tostring(Reservation.id),
         ReservationName = tostring(Reservation.properties.displayName),
         Region = tostring(Reservation.location),
         Quantity = tolong(Reservation.properties.quantity),
         ExpiryOn = todatetime(coalesce(Reservation.properties.expiryDateTime, Reservation.properties.expiryDate)),
         State = tostring(coalesce(Reservation.properties.displayProvisioningState, Reservation.properties.provisioningState)),
         Term = tostring(Reservation.properties.term),
         AutoRenew = tostring(Reservation.properties.renew),
         AppliedScope = tostring(Reservation.properties.appliedScopeType),
         AppliedScopes = tostring(Reservation.properties.appliedScopes),
         AppliedScopeProperties = tostring(Reservation.properties.appliedScopeProperties),
         PurchaseDate = tostring(coalesce(Reservation.properties.purchaseDateTime, Reservation.properties.purchaseDate)),
         Archived = tostring(Reservation.properties.archived)
| extend CatalogueRecord = tostring(parse_json(tolower('{LifecycleSkuMap}'))[tolower(ReservedSku)])
| extend Fields = split(CatalogueRecord, '|')
| extend Lifecycle = toupper(tostring(Fields[0])), Series = tostring(Fields[12]),
         RetirementDate = tostring(Fields[2]), SeriesRiCutoff = tostring(Fields[11]),
         SeriesPriceSignal = tostring(Fields[13]) == '1'
""" + f"""| extend RegionalPriceSignal = isnotnull(parse_json('{prices}')[tolower(Region)])
    and isnotempty(CatalogueRecord) and Series !in (dynamic({exclusions}))
    and not(tolower(ReservedSku) matches regex @'{excluded_pattern}')
""" + r"""| extend Exposure = case(
    isempty(ResourceType) or isempty(ReservationId), 'Unknown reservation metadata',
    isempty(CatalogueRecord), 'Unassessed SKU - manual review',
    Lifecycle in ('RETIRE', 'EOL'), 'Retirement / EOL',
    isnotempty(SeriesRiCutoff), 'RI purchase/renewal cutoff',
    SeriesPriceSignal or RegionalPriceSignal, 'Pricing review - not an existing RI price change',
    'No captured impact')
| where '{ReservationView}' == 'all' or Exposure != 'No captured impact'
| extend ReservationName = coalesce(ReservationName, ReservationId, 'Unknown'),
         ReservedSku = coalesce(ReservedSku, 'Unknown'), Region = coalesce(Region, 'Unknown'),
         Quantity = iff(Quantity >= 0, Quantity, tolong('')),
         Expiry = iff(isnull(ExpiryOn), 'Unknown', format_datetime(ExpiryOn, 'yyyy-MM-dd')),
         State = coalesce(State, 'Unknown'),
         TermState = case(isnull(ExpiryOn), 'Unknown', ExpiryOn <= now(), 'Expired - historical record',
                          'Unexpired date - benefit/state not certified'),
         Details = 'Details'
| project ReservationName, ReservedSku, Region, Quantity, Expiry, State, Exposure, Details,
          Term, AutoRenew, TermState, AppliedScope, AppliedScopes, AppliedScopeProperties,
          PurchaseDate, Archived, Lifecycle, Series, RetirementDate, SeriesRiCutoff,
          SeriesPriceSignal, RegionalPriceSignal, ReservationId
| order by Exposure asc, Expiry asc, ReservationName asc"""
