"""Read-only, explicitly scoped quota-group inspection for native Workbooks."""

import json
import uuid

API_VERSION = "2025-09-01"
ENDPOINTS = {
    "group-quota-membership": (
        "GET", "{QuotaGroupId}/subscriptions/{QuotaGroupSubscription}"
        f"?api-version={API_VERSION}"),
    "group-quota-pool": (
        "GET", "{QuotaGroupId}/resourceProviders/Microsoft.Compute/groupQuotaLimits/"
        "{QuotaGroupRegion}" f"?api-version={API_VERSION}"),
}


def arm_request(name, columns, table_path="$"):
    method, path = ENDPOINTS[name]
    return json.dumps({
        "version": "ARMEndpoint/1.0", "method": method, "path": path,
        "headers": [], "data": None, "urlParams": [], "batchDisabled": False,
        "transformers": [{"type": "jsonpath", "settings": {
            "tablePath": table_path,
            "columns": [{"columnid": column, "path": path} for column, path in columns]}}],
    }, separators=(",", ":"))


def merge_parameter(name, table, column):
    merge_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}:{table}:{column}"))
    return {
        "version": "KqlParameterItem/1.0", "name": name, "type": 1,
        "isHiddenWhenLocked": True, "value": "", "queryType": 7,
        "query": json.dumps({
            "version": "Merge/1.0",
            "merges": [{"id": merge_id, "mergeType": "table", "leftTable": table}],
            "projectRename": [{"originalName": f"[{table}].{column}",
                               "mergedName": column, "fromId": merge_id}],
        }, separators=(",", ":")),
    }


def evidence_query():
    return """resourcecontainers
| take 1
| summarize Seed=count()
| project Member=parse_json("{QuotaGroupMemberRecord:escapejson}"), Pool=parse_json("{QuotaGroupPoolRecord:escapejson}"),
    SelectedGroup="{QuotaGroupId:escapejson}",
    SelectedSubscription=tolower("{QuotaGroupSubscription:escapejson}"),
    SelectedRegion=tolower("{QuotaGroupRegion:escapejson}"),
    SelectedMetric=tolower("{QuotaGroupMetric:escapejson}")
| extend Member=iff(gettype(Member) == 'string', parse_json(tostring(Member)), Member),
         Pool=iff(gettype(Pool) == 'string', parse_json(tostring(Pool)), Pool)
| extend ExpectedMember=tolower(strcat(SelectedGroup, '/subscriptions/', SelectedSubscription)),
    ExpectedPool=tolower(strcat(SelectedGroup,
        '/resourceProviders/Microsoft.Compute/groupQuotaLimits/', SelectedRegion)),
    MemberState=tostring(Member.properties.provisioningState),
    PoolState=tostring(Pool.properties.provisioningState),
    HasMorePages=isnotempty(tostring(Pool.properties.nextLink)) or isnotempty(tostring(Pool.nextLink)),
    Values=Pool.properties.value
| extend MemberVerified=isnotempty(SelectedGroup) and isnotempty(SelectedSubscription)
        and tolower(tostring(Member.id)) == ExpectedMember
        and tolower(tostring(Member.properties.subscriptionId)) == SelectedSubscription
        and MemberState =~ 'Succeeded',
    PoolScopeVerified=isnotempty(SelectedGroup) and isnotempty(SelectedRegion)
        and tolower(tostring(Pool.id)) == ExpectedPool and PoolState =~ 'Succeeded',
    ValuesTruncated=array_length(Values) > 2000
| extend Values=iff(coalesce(array_length(Values), 0) > 0, Values, dynamic([{}]))
| mv-expand Value=Values limit 2000
| extend Family=tolower(coalesce(tostring(Value.properties.name.value),
                                  tostring(Value.properties.resourceName)))
| extend Matches=isnotempty(SelectedMetric) and Family == SelectedMetric,
    InvalidValue=isnull(tolong(Value.properties['limit']))
        or isnull(tolong(Value.properties.availableLimit))
        or tolong(Value.properties['limit']) < 0 or tolong(Value.properties.availableLimit) < 0
        or tolong(Value.properties.availableLimit) > tolong(Value.properties['limit'])
| summarize MetricRecords=countif(Matches), InvalidValues=countif(Matches and InvalidValue),
    MatchingValues=make_list_if(Value, Matches, 2)
    by SelectedGroup, SelectedSubscription, SelectedRegion, SelectedMetric, MemberVerified,
       MemberState, PoolScopeVerified, PoolState, HasMorePages, ValuesTruncated
| extend Value=MatchingValues[0]
| extend GroupLimit=tolong(Value.properties['limit']),
    Unallocated=tolong(Value.properties.availableLimit),
    AllocationPage=Value.properties.allocatedToSubscriptions
| extend Allocations=AllocationPage.value
| extend AllocationsTruncated=array_length(Allocations) > 2000,
    AllocationHasMore=isnotempty(tostring(AllocationPage.nextLink)),
    AllocationsShape=gettype(Allocations)
| extend Allocations=iff(coalesce(array_length(Allocations), 0) > 0,
                         Allocations, dynamic([{}]))
| mv-expand Allocation=Allocations limit 2000
| extend SelectedAllocation=tolower(tostring(Allocation.subscriptionId)) == SelectedSubscription
    and isnotempty(SelectedSubscription)
| summarize AllocationRecords=countif(SelectedAllocation),
    Allocated=maxif(tolong(Allocation.quotaAllocated), SelectedAllocation),
    AllocationIncomplete=countif(AllocationsTruncated or AllocationHasMore
        or AllocationsShape != 'array'),
    InvalidAllocations=countif(SelectedAllocation and
        (isnull(tolong(Allocation.quotaAllocated)) or tolong(Allocation.quotaAllocated) < 0))
    by SelectedGroup, SelectedSubscription, SelectedRegion, SelectedMetric, MemberVerified,
       MemberState, PoolScopeVerified, PoolState, HasMorePages, ValuesTruncated,
       MetricRecords, InvalidValues, GroupLimit, Unallocated
| extend PoolVerified=PoolScopeVerified and not(HasMorePages) and not(ValuesTruncated)
    and MetricRecords == 1 and InvalidValues == 0,
    AllocationVerified=AllocationRecords <= 1 and AllocationIncomplete == 0
        and InvalidAllocations == 0
| extend Evidence=case(not(MemberVerified), 'Unknown - membership not verified',
    not(PoolVerified), 'Unknown - pool missing, partial or invalid',
    not(AllocationVerified), 'Unknown - allocation incomplete or invalid',
    'Verified - selected scope only')
| project ManagementGroupAndQuotaGroup=SelectedGroup, Subscription=SelectedSubscription,
    Region=SelectedRegion, Metric=SelectedMetric,
    Membership=iff(MemberVerified, 'Verified member', 'Unknown - verify membership/access'),
    ['Group quota evidence']=Evidence,
    ['Group limit']=iff(PoolVerified, GroupLimit, tolong('')),
    ['Unallocated group cores']=iff(PoolVerified, Unallocated, tolong('')),
    ['Already allocated to subscription']=iff(PoolVerified and MemberVerified and AllocationVerified,
        coalesce(Allocated, tolong(0)), tolong('')),
    ['Next action']=case(Evidence startswith 'Unknown',
        'Resolve API errors, membership and evidence coverage in Azure Quotas',
        Unallocated > 0, 'Allocation required; then refresh subscription quota checks',
        'No unallocated pool quota; assess an increase or verified eligible transfer'),
    CheckedAtUtc=now()"""
