#!/usr/bin/env python3
"""
Build a self-service Azure Monitor Workbook for Azure VM lifecycle,
retirements and modernization readiness.

Source of truth:
  ../compute-fleet-modernization/references/sku-migration-map.json
  ../compute-fleet-modernization/references/readiness-checklist.json

Output:
  out/azure-vm-retirement-workbook.json   (paste into Workbooks Advanced Editor)
  out/deploy-workbook.json                (ARM template, one-click deploy)

SCOPE: public information only. Regional percentages are reported exposure signals,
not contractual rates or a bill forecast.
"""

import json
import os
import uuid

from migration_guidance import (CLOSEOUT, COMMON_GATES, ROUTES, WORKLOADS,
                                advisor_query, alternatives_query, target_fit_query)
from workload_evidence import (compute_evidence_query, configured_pools_query,
                               os_eligibility_query, service_inventory_query,
                               sql_evidence_query)
from reservation_evidence import (FIRST_PAGE, coverage_query as reservation_coverage_query,
                                  inventory_query as reservation_inventory_query)
from quota_group_evidence import (arm_request as group_arm_request,
                                  evidence_query as group_evidence_query, merge_parameter)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Reference data lives in the repo so a fresh clone can build. Fall back to the
# sibling compute-fleet-modernization checkout when working from the authoring
# tree, where that folder is the upstream source of truth.
_LOCAL_REFS = os.path.join(ROOT, "references")
_SIBLING_REFS = os.path.join(os.path.dirname(ROOT), "compute-fleet-modernization", "references")
REFS = _LOCAL_REFS if os.path.exists(
    os.path.join(_LOCAL_REFS, "sku-migration-map.json")) else _SIBLING_REFS

OUT = os.path.join(ROOT, "out")
WORKBOOK_DIR = os.path.join(ROOT, "workbook")
WORKBOOK_SIZE_LIMIT = 1_000_000
WORKBOOK_TITLE = "Azure VM Retirement & Upgrade Planner"
BASE_LOOKUP_PARAMETERS = ("LifecycleSkuMap", "TargetRegionMap", "RegionIdMap")
PATH_LOOKUP_PARAMETERS = {
    generation: (f"V{generation}PathSkuMap", f"V{generation}PathRegionMap",
                 f"V{generation}PathRegionIdMap")
    for generation in (5, 6, 7)
}
LOOKUP_PARAMETERS = BASE_LOOKUP_PARAMETERS + tuple(
    name for names in PATH_LOOKUP_PARAMETERS.values() for name in names) + (
        "QuotaFamilyMap", "QuotaCoreMap", "CompatibleTargetMap", "TargetSpecMap")

# Preview mode: restrict the embedded lookup to a named set of sizes so the
# generated workbook is small enough to paste into the portal for visual review.
# Layout, formatters and scoring are identical to the full build.
ONLY_SIZES = None

# Announced 28 Sep 2026: Dv3/Dsv3/Ev3/Esv3 enter End of Life, retire 2029-11-15.
V3_EOL_GROUPS = {"Dv3_Dsv3", "Ev3_Esv3", "Isolated_E64i_v3"}
V3_EOL_DATE = "2029-11-15"

# Azure VM price update effective 2027-02-01, per the Azure Service Health
# notification of 28 Sep 2026. The increase is scoped by SIZE SERIES, not by region: it applies
# across Azure public cloud and Azure operated by 21Vianet, and exempts only
# Azure Government and Azure Sovereign Cloud.
#
# Series names below are the catalogue's, mapped from the names used in the
# notification (for example "Bv1" covers Bsv1/Bmsv1/Blsv1, and "D"/"Ds" are
# Dv1/Dsv1 here). The workbook reports only that an update applies - the
# percentage is communicated to each tenant through its own Azure Service
# Health notification, and is deliberately not restated here.
PRICE_SERIES = {
    # notification "v1": Bv1, D, Ds, F, Fs, G, Gs, Ls, NP, HC
    "Bsv1", "Bmsv1", "Blsv1", "Dv1", "Dsv1", "Fv1", "Fsv1",
    "Gv1", "Gsv1", "Lsv1", "NPsv1", "HCv1", "HC",
    # notification "v2": Av2, Amv2, Dv2, Dsv2, Fsv2, Lsv2
    "Av2", "Amv2", "Dv2", "Dsv2", "Fsv2", "Lsv2",
}
PRICE_EFFECTIVE = "2027-02-01"

REGIONAL_PRICE_INCREASES = {
    "northeurope": 17, "francecentral": 11, "francesouth": 11,
    "westeurope": 9, "norwayeast": 9, "norwaywest": 9, "southeastasia": 8,
}
REGIONAL_PRICE_EXCLUDED_SERIES = {
    "Bsv1", "Bmsv1", "Blsv1", "Dv1", "Dsv1", "Fv1", "Fsv1",
    "Gv1", "Gsv1", "Lsv1", "NPsv1", "HCS", "Av2", "Amv2", "Dv2",
    "Dsv2", "Fsv2", "Lsv2", "Dsv7", "Ddsv7", "Dlsv7", "Dldsv7",
    "Esv7", "Edsv7",
}
REGIONAL_PRICE_SOURCE = (
    "https://learn.microsoft.com/en-us/answers/questions/6025662/"
    "is-there-any-official-announcement-for-azure-vm-in"
)
REGIONAL_PRICE_V7_EXCLUSION_PATTERN = (
    r"^standard_(d[0-9]+(-[0-9]+)?(s|ds|ls|lds)|e[0-9]+(-[0-9]+)?(s|ds))_v7$"
)

# Tag keys that commonly carry the environment, in the casings seen in the
# wild. Checked in order; the first non-empty value wins.
_ENV_TAG_KEYS = [
    "Environment", "environment", "ENVIRONMENT",
    "Env", "env", "ENV",
    "Tier", "tier", "Stage", "stage", "Usage", "usage",
]
ENV_TAGS = ("tolower(tostring(coalesce("
            + ", ".join("{col}['" + k + "']" for k in _ENV_TAG_KEYS)
            + ")))")

BLOCKING = {"gen1_not_supported", "temp_disk_removed", "memory_downgrade",
            "cpu_architecture_change"}

# Warning codes are embedded in every query, so they are stored as single
# characters and expanded back to full codes only where they are displayed.
WARN_SHORT = {
    "gen1_not_supported": "g",
    "nvme_required": "n",
    "no_exact_vcpu_match": "v",
    "memory_downgrade": "m",
    "sovereign_cloud_restriction": "s",
    "narrow_regional_availability": "r",
    "temp_disk_removed": "t",
    "no_three_az_regions": "z",
    "cpu_architecture_change": "a",
    "v5_target_review": "u",
    "mana_validation": "h",
    "path_target_unavailable": "k",
    "target_local_disk_capacity_unverified": "l",
}
SHORT_WARN = {v: k for k, v in WARN_SHORT.items()}

# Per-warning readiness penalty. gen1 is handled specially (only penalised when
# the VM's *actual* OS disk is Generation 1).
PENALTY = {
    "gen1_not_supported": 45,
    "cpu_architecture_change": 50,
    "memory_downgrade": 25,
    "temp_disk_removed": 20,
    "nvme_required": 15,
    "narrow_regional_availability": 10,
    "no_three_az_regions": 10,
    "no_exact_vcpu_match": 5,
    "sovereign_cloud_restriction": 0,
}

# Prerequisite copy, keyed by warning code. Derived from readiness-checklist.json
# and kept to public information.
PREREQ = {
    "gen1_not_supported": {
        "title": "Generation 1 to Generation 2 (BIOS to UEFI) conversion",
        "sev": "Blocking",
        "what": "Convert the OS disk from Generation 1 to Generation 2, or rebuild the VM from a Gen2 image. Confirm the OS version is supported for Gen2 and that any custom or Marketplace image has a Gen2 variant.",
        "why": "The selected target supports Generation 2 only. A Gen1 VM cannot be resized into it without conversion or rebuilding. A compatible v5 target can avoid this requirement.",
        "how": "Windows: MBR2GPT. Linux: distro-specific. Or redeploy from a Gen2 image.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/generation-2",
    },
    "nvme_required": {
        "title": "NVMe disk controller support",
        "sev": "Validate",
        "what": "Confirm the OS image supports the selected target's NVMe boot controller. Test boot on the target size before bulk migration. MANA networking is evaluated separately.",
        "why": "A target requiring NVMe can fail to boot an incompatible image. Driver presence alone is not boot proof; custom and older images need a target-specific rehearsal.",
        "how": "Check the supported OS list, then validate one VM per image lineage.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/nvme-overview",
    },
    "mana_validation": {
        "title": "MANA and accelerated networking validation",
        "sev": "Validate",
        "what": "Check the actual target hardware, guest driver and accelerated networking traffic independently from the storage controller. This can apply to v5 SCSI targets too.",
        "why": "MANA exposure depends on host hardware. Missing hardware is unknown driver readiness, not proof of failure; virtual-switch fallback may preserve connectivity with different performance.",
        "how": "Validate hardware, adapter, driver and traffic for each image lineage on the selected target.",
        "doc": "https://learn.microsoft.com/azure/virtual-network/accelerated-networking-mana-overview",
    },
    "v5_target_review": {
        "title": "No suitable v5 target in the captured catalogue",
        "sev": "Plan",
        "what": "Review the newer-generation recommendation manually. No catalogued v5 candidate preserves the required architecture, CPU, memory, local-disk and premium-storage capabilities without requiring NVMe.",
        "why": "V5-first is a preference, not permission to invent a SKU or downgrade workload requirements.",
        "how": "Refresh the target catalogue and confirm a supported migration path before choosing a newer target.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/overview",
    },
    "temp_disk_removed": {
        "title": "Local temp disk dependency removal",
        "sev": "Blocking",
        "what": "Find every dependency on the local temp disk (D:\\ on Windows, /mnt on Linux) - pagefile/swap, SQL Server tempdb, application scratch and cache directories, log staging. Relocate them, or choose a 'd' variant of the target series to keep local storage.",
        "why": "Non-'d' target series have no local disk. Anything pinned to that path breaks silently on first boot, and SQL tempdb failures take the instance down.",
        "how": "Move the page file and tempdb to a managed data disk, or select the 'd' variant target.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/managed-disks-overview",
    },
    "memory_downgrade": {
        "title": "Memory parity check",
        "sev": "Blocking",
        "what": "The same-vCPU target carries less memory than the current size. Confirm real memory utilisation before accepting it, or move to the memory-preserving alternative shown in the Target mapping tab.",
        "why": "Common on G-series and the large memory-optimised sizes. Silently under-provisioning RAM on a database or in-memory workload causes outages.",
        "how": "Baseline 30-90 days of memory metrics, then choose between the default and the memory-preserving target.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/overview",
    },
    "cpu_architecture_change": {
        "title": "CPU architecture port (x64 to Arm64)",
        "sev": "Blocking",
        "what": "The target uses a different CPU architecture. Every binary, agent, driver and third-party package must be rebuilt or re-sourced for the target architecture.",
        "why": "x64 and Arm64 binaries are not interchangeable. This is a porting project, not a resize, and needs its own timeline.",
        "how": "Inventory all installed software and confirm Arm64 availability before committing.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/overview",
    },
    "no_exact_vcpu_match": {
        "title": "vCPU count change",
        "sev": "Plan",
        "what": "No target exists at the current vCPU count, so the nearest size is larger. Right-size against actual utilisation rather than the current nameplate, and re-check per-core software licensing.",
        "why": "A vCPU increase can trigger a licensing true-up (SQL Server, Oracle, SAP, third-party agents) that outweighs the compute saving.",
        "how": "Baseline CPU utilisation for 30-90 days, then select the size that fits real demand.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/resize-vm",
    },
    "narrow_regional_availability": {
        "title": "Limited target region availability",
        "sev": "Plan",
        "what": "The recommended target is offered in a small number of regions. Confirm availability in the region this workload runs in before planning the move.",
        "why": "Recommending a size that cannot be deployed in the workload's region wastes a migration window.",
        "how": "az vm list-skus --location <region> --resource-type virtualMachines --size <target> --all",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/resize-vm",
    },
    "no_three_az_regions": {
        "title": "Availability zone parity",
        "sev": "Plan",
        "what": "The target is not offered across three availability zones in the regions where it is available. If this workload is zone-redundant, validate the zone footprint before moving.",
        "why": "Losing a zone silently reduces the availability SLA of the design.",
        "how": "Check zone availability for the target size in the workload's region.",
        "doc": "https://learn.microsoft.com/azure/reliability/availability-zones-overview",
    },
    "target_not_in_region": {
        "title": "Target size not available in this region",
        "sev": "Blocking",
        "what": "The recommended target size is not currently offered in this VM's region. Select an alternate target from the Target mapping tab, or plan a regional move.",
        "why": "The default recommendation cannot be deployed here. Proceeding would fail at resize time.",
        "how": "Review the alternate targets listed for this size, then confirm with az vm list-skus.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/resize-vm",
    },
    "quota_headroom": {
        "title": "Target family quota headroom",
        "sev": "Plan",
        "what": "Verify per-family vCPU quota for the target family in the target region and raise a quota request early if short.",
        "why": "Target families have separate quota buckets from the source. A fleet-wide resize can fail midway on quota, and approval is not instant.",
        "how": "az vm list-usage --location <region> -o table",
        "doc": "https://learn.microsoft.com/azure/quotas/view-quotas",
    },
    "availability_set": {
        "title": "Availability set resize constraint",
        "sev": "Plan",
        "what": "This VM is in an availability set. The target size must be available on the same cluster, or every VM in the set must be stopped and resized together.",
        "why": "Availability set members are pinned to a cluster that may not offer the target size.",
        "how": "Stop all VMs in the availability set, resize, then restart.",
        "doc": "https://learn.microsoft.com/azure/virtual-machines/sizes/resize-vm",
    },
    "scale_set": {
        "title": "Scale set SKU change",
        "sev": "Plan",
        "what": "This is a virtual machine scale set. Change the SKU on the scale set model and roll instances through the upgrade policy rather than resizing instances individually.",
        "why": "Scale set instances are governed by the model; editing instances directly drifts the set.",
        "how": "Update the scale set SKU, then apply the rolling upgrade policy.",
        "doc": "https://learn.microsoft.com/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-upgrade-scale-set",
    },
}


def load():
    with open(os.path.join(REFS, "sku-migration-map.json"), encoding="utf-8") as f:
        return json.load(f)


def esc(s):
    """Escape a python string for embedding in a single-quoted KQL literal."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("'", "\\'")


def num(v):
    """Trim trailing .0 so '8.0' serialises as '8'."""
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else str(f)
    except (TypeError, ValueError):
        return str(v)


def compatible_targets(record, generation, allow_unknown_temp_capacity=False):
    current = record.get("current") or {}
    return [
        t for t in record.get("targets") or []
        if t.get("sku", "").endswith(f"_v{generation}")
        and t.get("nvme_required") in (True, False)
        and (generation != 5 or t.get("nvme_required") is False)
        and t.get("cpu_arch") == current.get("cpu_arch")
        and current.get("cpu_arch") is not None
        and t.get("hyperv_generations")
        and t.get("vcpu") is not None and current.get("vcpu") is not None
        and t["vcpu"] >= current["vcpu"]
        and t.get("memory_gb") is not None and current.get("memory_gb") is not None
        and t["memory_gb"] >= current["memory_gb"]
        and (current.get("has_temp_disk") is False or t.get("has_temp_disk") is True)
        and (current.get("temp_disk_gb") is None
             or (allow_unknown_temp_capacity and t.get("temp_disk_gb") is None)
             or (t.get("temp_disk_gb") is not None
                 and t["temp_disk_gb"] >= current["temp_disk_gb"]))
        and (current.get("premium_io") is False or t.get("premium_io") is True)
    ]


def target_warning_codes(record, target):
    current = record.get("current") or {}
    codes = {w["code"] for w in record.get("warnings") or []} - {
        "gen1_not_supported", "nvme_required", "cpu_architecture_change",
        "memory_downgrade", "temp_disk_removed", "no_exact_vcpu_match",
        "narrow_regional_availability", "no_three_az_regions", "v5_target_review",
    }
    if "V1" not in target["hyperv_generations"].split(","):
        codes.add("gen1_not_supported")
    if target["nvme_required"]:
        codes.add("nvme_required")
    if target["vcpu"] != current["vcpu"]:
        codes.add("no_exact_vcpu_match")
    if target.get("deployable_region_count", 0) < 10:
        codes.add("narrow_regional_availability")
    if target.get("az3_region_count", 0) == 0:
        codes.add("no_three_az_regions")
    if current.get("has_temp_disk") and target.get("temp_disk_gb") is None:
        codes.add("target_local_disk_capacity_unverified")
    if target.get("accelerated_networking") is True:
        codes.add("mana_validation")
    return codes


def select_path_target(record, generation):
    candidates = compatible_targets(record, generation, allow_unknown_temp_capacity=(generation >= 6))
    if not candidates:
        return {}, {}, {"path_target_unavailable"}
    current = record["current"]
    target = min(candidates, key=lambda t: (
        t["vcpu"] - current["vcpu"], t["memory_gb"] - current["memory_gb"]))
    return target, target, target_warning_codes(record, target)


def select_targets(record, impacted):
    """Prefer a catalogued non-NVMe v5 target without weakening source capacity."""
    targets = record.get("targets") or []
    preferred = next((t for t in targets
                      if t.get("sku") == record.get("recommended_target")), None)
    preferred = preferred or (targets[0] if targets else {})
    memory_target = record.get("memory_preserving_target") or {}
    codes = {w["code"] for w in record.get("warnings") or []}
    current = record.get("current") or {}

    if impacted and (preferred.get("nvme_required") or
                     preferred.get("sku", "").endswith(("_v6", "_v7"))):
        candidates = compatible_targets(record, 5)
        if candidates:
            preferred = min(candidates, key=lambda t: (
                t["vcpu"] - current["vcpu"], t["memory_gb"] - current["memory_gb"]))
            memory_target = preferred
            codes = target_warning_codes(record, preferred)
        else:
            codes.add("v5_target_review")

    if (preferred.get("sku", "").endswith(("_v5", "_v6", "_v7"))
            and preferred.get("accelerated_networking") is True):
        codes.add("mana_validation")
    return preferred, memory_target, codes


def build_lookups(data, path_generation=None):
    skus = data["skus"]
    sku_map = {}
    spec_map = {}
    url_map = {}
    region_ids = {}
    tgt_regions = {}

    def rid(region):
        if region not in region_ids:
            region_ids[region] = format(len(region_ids), "x")
        return region_ids[region]

    for r in skus:
        if ONLY_SIZES is not None and r["sku"] not in ONLY_SIZES:
            continue
        group = r.get("group") or ""
        prio = r.get("priority") or ""
        retire = r.get("retirement_date") or ""
        gen = (r.get("generation") or "").lower()

        if group in V3_EOL_GROUPS and not retire:
            retire = V3_EOL_DATE
            tier = "EOL"
        elif prio == "P1_must_move":
            tier = "RETIRE"
        elif retire:
            tier = "RETIRE"
        else:
            tier = "OPTIONAL"

        # Pricing update flag: scoped to the series named in the Service Health
        # notification, not to every v1/v2 generation size. The generation
        # field alone over-matches (it pulls in M-series isolated and HBrs).
        price = "1" if (r.get("series") or "") in PRICE_SERIES else "0"

        cur = r.get("current") or {}
        targets = r.get("targets") or []
        impacted = tier in ("RETIRE", "EOL")
        if path_generation is not None and not impacted:
            continue
        preferred, mp, warning_codes = (
            select_path_target(r, path_generation) if path_generation is not None
            else select_targets(r, impacted))
        codes = sorted({WARN_SHORT.get(code, "") for code in warning_codes} - {""})
        alts = list(dict.fromkeys(t["sku"] for t in targets
                                 if t.get("sku") != preferred.get("sku")))[:3]

        # OPTIONAL sizes carry a slim record: they have no retirement date, so the
        # extra fields would only inflate every embedded query.
        fields = [
            tier,                                             # 0
            group,                                            # 1
            retire,                                           # 2
            preferred.get("sku") or "",                       # 3
            mp.get("sku") or "" if impacted else "",          # 4
            num(preferred.get("vcpu")),                       # 5
            num(preferred.get("memory_gb")),                  # 6
            num(cur.get("vcpu")),                             # 7
            num(cur.get("memory_gb")),                        # 8
            ",".join(codes),                                  # 9
            ",".join(alts) if impacted else "",               # 10
            r.get("ri_1yr_expiration") or "" if impacted else "",   # 11
            r.get("series") or "",                            # 12
            price,                                            # 13
            num(mp.get("vcpu")) if impacted else "",          # 14
            num(mp.get("memory_gb")) if impacted else "",     # 15
        ]
        sku_map[r["sku"]] = "|".join(fields).rstrip("|")

        series = r.get("series") or ""
        if series and r.get("spec_deltas") and series not in spec_map:
            spec_map[series] = r["spec_deltas"]
        if group and r.get("announcement_url") and group not in url_map:
            url_map[group] = r["announcement_url"]

        # Region availability, for impacted sizes only, encoded as region indices
        # to keep the embedded literal small.
        if impacted:
            for t in [preferred, mp]:
                sku = t.get("sku")
                if sku and t.get("deployable_regions") and sku not in tgt_regions:
                    tgt_regions[sku] = "|" + "|".join(
                        rid(x) for x in t["deployable_regions"]) + "|"

    return sku_map, spec_map, url_map, tgt_regions, region_ids


def kql_bag(d):
    """Compact single-line JSON suitable for a KQL parse_json('...') literal."""
    return esc(json.dumps(d, separators=(",", ":"), sort_keys=True))


def lookup_values(workbook):
    return {
        p["name"]: p["value"]
        for item in workbook["items"] if item.get("type") == 9
        for p in item["content"]["parameters"]
        if p["name"] in LOOKUP_PARAMETERS
    }


def expand_lookups(query, workbook):
    for name, value in lookup_values(workbook).items():
        query = query.replace("{" + name + "}", value)
    return query


def inventory_query(include_flexible_members=False, include_placement_zones=False):
    member_filter = "" if include_flexible_members else (
        "\n| where IsVmss or isempty(tostring(properties.virtualMachineScaleSet.id))")
    return f"""resources
| where type =~ 'microsoft.compute/virtualmachines' or type =~ 'microsoft.compute/virtualmachinescalesets'
| extend IsVmss = (type =~ 'microsoft.compute/virtualmachinescalesets')
// A Flexible-orchestration scale set surfaces BOTH the scale set (carrying
// sku.capacity) and every member VM as separate Resource Graph rows. Counting
// both double-reports the fleet, so member VMs are dropped here and the scale
// set alone represents them. Uniform scale sets do not expose member VMs at
// all, so they are unaffected. Flexible is the current Azure default, so this
// matters for most modern fleets.
{member_filter}
| extend VmSize = iff(IsVmss, tostring(sku.name), tostring(properties.hardwareProfile.vmSize))
| extend Instances = iff(IsVmss, toint(sku.capacity), 1)
| extend OsType = iff(IsVmss, tostring(properties.virtualMachineProfile.storageProfile.osDisk.osType), tostring(properties.storageProfile.osDisk.osType))
| extend OsDiskId = tolower(tostring(properties.storageProfile.osDisk.managedDisk.id))
| extend AvSetId = tostring(properties.availabilitySet.id)
| extend PowerState = tostring(split(tostring(properties.extended.instanceView.powerState.code), '/')[1])
| extend ComputePriority = iff(IsVmss, tostring(properties.virtualMachineProfile.priority), tostring(properties.priority))
| extend Zone = tostring(zones[0])
| extend ImageSku = iff(IsVmss, tostring(properties.virtualMachineProfile.storageProfile.imageReference.sku), tostring(properties.storageProfile.imageReference.sku))
| extend ImageId = iff(IsVmss, tostring(coalesce(properties.virtualMachineProfile.storageProfile.imageReference.id, properties.virtualMachineProfile.storageProfile.imageReference.sharedGalleryImageId, properties.virtualMachineProfile.storageProfile.imageReference.communityGalleryImageId)), tostring(coalesce(properties.storageProfile.imageReference.id, properties.storageProfile.imageReference.sharedGalleryImageId, properties.storageProfile.imageReference.communityGalleryImageId)))
| extend ImagePublisher = iff(IsVmss, tostring(properties.virtualMachineProfile.storageProfile.imageReference.publisher), tostring(properties.storageProfile.imageReference.publisher))
| extend ImageOffer = iff(IsVmss, tostring(properties.virtualMachineProfile.storageProfile.imageReference.offer), tostring(properties.storageProfile.imageReference.offer))
| extend GuestOsName = tostring(properties.extended.instanceView.osName)
| project ResourceId = id, Name = name, ResourceGroup = resourceGroup, SubscriptionId = subscriptionId,
          Region = location, VmSize, Instances, OsType, OsDiskId, AvSetId, PowerState, ComputePriority, Zone, ImageSku, ImageId, ImagePublisher, ImageOffer, GuestOsName, IsVmss, Tags = tags{', PlacementZones = zones' if include_placement_zones else ''}"""


def base_query(sku_map, tgt_regions, region_ids, include_optional=True,
               use_lookup_parameters=False, lookup_parameters=BASE_LOOKUP_PARAMETERS,
               include_flexible_members=False, include_placement_zones=False):
    """Fleet inventory joined to lifecycle metadata and target-specific scoring."""
    if use_lookup_parameters:
        m, reg, rids = ("{" + name + "}" for name in lookup_parameters)
    else:
        m, reg, rids = (kql_bag(value) for value in (sku_map, tgt_regions, region_ids))
    opt_filter = "" if include_optional else "\n| where Lifecycle != 'OPTIONAL'"
    return inventory_query(include_flexible_members, include_placement_zones) + f"""
| join kind=leftouter (
    resources
    | where type =~ 'microsoft.compute/disks'
    | project OsDiskId = tolower(id), DiskGen = tostring(properties.hyperVGeneration),
              DiskAdeConfigured = tobool(properties.encryptionSettingsCollection.enabled),
              DiskControllers = tostring(properties.supportedCapabilities.diskControllerTypes)
  ) on OsDiskId
| extend VmGeneration = case(
        isnotempty(DiskGen), DiskGen,
        ImageSku has 'gen2' or ImageSku has '-g2', 'V2',
        'Unknown')
| extend Rec = tostring(parse_json('{m}')[VmSize])
| where isnotempty(Rec)
| extend P = split(Rec, '|')
| extend Lifecycle = tostring(P[0]),
         SeriesGroup = tostring(P[1]),
         RetirementDate = tostring(P[2]),
         RecommendedTarget = tostring(P[3]),
         MemoryPreservingTarget = tostring(P[4]),
         TargetVcpu = tostring(P[5]),
         TargetMemoryGB = tostring(P[6]),
         CurrentVcpu = toint(P[7]),
         CurrentMemoryGB = toreal(P[8]),
         WarnCodes = tostring(P[9]),
         AlternateTargets = tostring(P[10]),
         RiEnded = tostring(P[11]),
         Series = tostring(P[12]),
         PriceUpdate = tostring(P[13]),
         MpVcpu = tostring(P[14]),
         MpMemoryGB = tostring(P[15]){opt_filter}
| extend RetiresOn = iff(isempty(RetirementDate), datetime(null), todatetime(RetirementDate))
| extend DaysToRetirement = iff(isempty(RetirementDate), toint(-1), datetime_diff('day', todatetime(RetirementDate), now()))
| extend RegionId = tostring(parse_json('{rids}')[tolower(Region)])
| extend TargetRegions = tostring(parse_json('{reg}')[RecommendedTarget])
| extend TargetInRegion = case(
        isempty(RecommendedTarget) or isempty(TargetRegions), 'Unverified',
        isempty(RegionId), 'Unverified',
        TargetRegions has strcat('|', RegionId, '|'), 'Yes',
        'No')
| extend PriceUpdate2027 = iff(PriceUpdate == '1', 'Yes', 'No')
// Reserved Instance purchases and renewals for these series ended 2026-07-01.
// This series-wide cutoff is not a customer's reservation expiry or billing coverage.
| extend RiEndsOn = iff(isempty(RiEnded), datetime(null), todatetime(RiEnded))
| extend RiStatus = case(
        isempty(RiEnded), 'No cutoff in captured catalogue; coverage not assessed',
        todatetime(RiEnded) < now(), 'Series purchase/renewal cutoff passed; coverage not assessed',
        'Series purchase/renewal cutoff upcoming; coverage not assessed')
// ---- readiness penalties, evaluated against the REAL fleet attributes ----
| extend NeedsGen2 = iff(WarnCodes has 'g' and VmGeneration == 'V1', 1, 0)
| extend GenUnknown = iff(WarnCodes has 'g' and VmGeneration == 'Unknown', 1, 0)
| extend PenGen = iff(NeedsGen2 == 1, {PENALTY['gen1_not_supported']}, iff(GenUnknown == 1, 20, 0))
| extend PenArch = iff(WarnCodes has 'a', {PENALTY['cpu_architecture_change']}, 0)
| extend PenMem = iff(WarnCodes has 'm', {PENALTY['memory_downgrade']}, 0)
| extend PenTemp = iff(WarnCodes has 't', {PENALTY['temp_disk_removed']}, 0)
| extend PenNvme = iff(WarnCodes has 'n', {PENALTY['nvme_required']}, 0)
| extend PenNarrow = iff(WarnCodes has 'r', {PENALTY['narrow_regional_availability']}, 0)
| extend PenAz = iff(WarnCodes has 'z' and isnotempty(Zone), {PENALTY['no_three_az_regions']}, 0)
| extend PenVcpu = iff(WarnCodes has 'v', {PENALTY['no_exact_vcpu_match']}, 0)
| extend PenRegion = iff(TargetInRegion == 'No', 30, 0)
| extend PenAvSet = iff(isnotempty(AvSetId), 5, 0)
| extend PenVmss = iff(IsVmss, 5, 0)
| extend ReadinessScore = toint(iff(100 - PenGen - PenArch - PenMem - PenTemp - PenNvme - PenNarrow - PenAz - PenVcpu - PenRegion - PenAvSet - PenVmss < 0, 0, 100 - PenGen - PenArch - PenMem - PenTemp - PenNvme - PenNarrow - PenAz - PenVcpu - PenRegion - PenAvSet - PenVmss))
| extend Complexity = case(
        ReadinessScore >= 85, 'Few scored platform changes - validation required',
        ReadinessScore >= 60, 'Moderate scored platform changes',
        ReadinessScore >= 35, 'Substantial scored platform changes',
        'Major scored platform changes')
| extend MigrationApproval = 'Not assessed - guest, application, HA, backup and rollback evidence required'
| extend Urgency = case(
        DaysToRetirement < 0, 'No retirement date',
        DaysToRetirement <= 180, 'Critical - under 6 months',
        DaysToRetirement <= 365, 'High - under 12 months',
        DaysToRetirement <= 730, 'Medium - under 24 months',
        'Planned - over 24 months')
| extend ActionCategory = case(
        Lifecycle == 'RETIRE', 'Retirement announced - action required',
        Lifecycle == 'EOL', 'End of life - plan migration',
        'Optional modernization')
| extend OfficialLifecycleStage = case(
        Lifecycle == 'EOL', 'End of life - captured announcement',
        'Unknown - reconcile official Current/Extended/End-of-life/Retired stage; announcement alone is not stage proof')
| extend Blockers = trim(', ', strcat(
        iff(NeedsGen2 == 1, 'Gen1 to Gen2 conversion, ', ''),
        iff(GenUnknown == 1, 'Confirm VM generation, ', ''),
        iff(PenArch > 0, 'Arm64 port, ', ''),
        iff(PenMem > 0, 'Memory downgrade, ', ''),
        iff(PenTemp > 0, 'Temp disk dependency, ', ''),
        iff(PenNvme > 0, 'NVMe boot validation, ', ''),
        iff(WarnCodes has 'h', 'MANA/accelerated networking validation, ', ''),
        iff(WarnCodes has 'u', 'No suitable v5 target - manual review, ', ''),
        iff(WarnCodes has 'k', 'No suitable target in this path - manual selection, ', ''),
        iff(WarnCodes has 'l', 'Target local-disk capacity unverified, ', ''),
        iff(PenRegion > 0, 'Target not in region, ', ''),
        iff(PenVcpu > 0, 'vCPU count change, ', '')))
| extend Blockers = iff(isempty(Blockers), 'None identified', Blockers)
// Size-level considerations, independent of any individual VM's configuration.
// Used by the size-by-size mapping so rows do not split on per-VM attributes.
| extend SizeConsiderations = trim(', ', strcat(
        iff(WarnCodes has 'g', 'Target is Gen2-only, ', ''),
        iff(WarnCodes has 'a', 'Arm64 architecture change, ', ''),
        iff(WarnCodes has 'm', 'Memory reduction at same vCPU, ', ''),
        iff(WarnCodes has 't', 'Local temp disk removed, ', ''),
        iff(WarnCodes has 'n', 'Target requires NVMe boot validation, ', ''),
        iff(WarnCodes has 'h', 'Hardware-dependent MANA validation, ', ''),
        iff(WarnCodes has 'u', 'No suitable v5 target - manual review, ', ''),
        iff(WarnCodes has 'k', 'No suitable target in this path - manual selection, ', ''),
        iff(WarnCodes has 'l', 'Target local-disk capacity unverified, ', ''),
        iff(WarnCodes has 'r', 'Narrow target region footprint, ', ''),
        iff(WarnCodes has 'z', 'Limited zone coverage, ', ''),
        iff(WarnCodes has 'v', 'No target at current vCPU count, ', '')))
| extend SizeConsiderations = iff(isempty(SizeConsiderations), 'Straight resize', SizeConsiderations)"""


def regional_price_query(data):
    series_by_sku = {}
    for record in data["skus"]:
        for item in [record] + (record.get("targets") or []):
            if item.get("sku") and item.get("series"):
                key = item["sku"].lower()
                if key in series_by_sku and series_by_sku[key] != item["series"]:
                    raise ValueError(f"Conflicting regional-price series metadata: {key}")
                series_by_sku[key] = item["series"]
    regions = kql_bag(REGIONAL_PRICE_INCREASES)
    series = kql_bag(series_by_sku)
    exclusions = kql_bag({s.lower(): True for s in REGIONAL_PRICE_EXCLUDED_SERIES})
    return inventory_query() + f"""
| extend AnnouncedRegionalPercent = toint(parse_json('{regions}')[tolower(Region)])
| where isnotnull(AnnouncedRegionalPercent)
| extend Series = tostring(parse_json('{series}')[tolower(VmSize)])
| extend ReportedExcluded = tobool(parse_json('{exclusions}')[tolower(Series)]) == true
        or tolower(VmSize) matches regex '{REGIONAL_PRICE_V7_EXCLUSION_PATTERN}'
| extend Exposure = case(
        ReportedExcluded == true, 'Reported regional exclusion - not a universal exemption',
        isempty(Series), 'Potential exposure - series/scope unknown',
        'Potential exposure - verify advisory and agreement')
| extend EffectiveDate = datetime({PRICE_EFFECTIVE}),
         Evidence = 'Public Q&A reproduction of customer notification; not a formal public announcement',
         Scope = 'VM compute only; not a bill estimate. Confirm meters, contract, reservations and regional scope in Service Health. Separate legacy-series changes may apply.',
         Source = '{REGIONAL_PRICE_SOURCE}'
| project Name, ResourceGroup, Region, VmSize, Instances, Series, Exposure,
          AnnouncedRegionalPercent, EffectiveDate, Evidence, Scope, Source, ResourceId
| order by AnnouncedRegionalPercent desc, Region asc, Name asc"""


def sql_modernization_query(sku_map, regions, region_ids):
    return base_query(
        sku_map, regions, region_ids, include_optional=False, use_lookup_parameters=True,
        include_flexible_members=True) + f"""
| where not(IsVmss)
| extend VmId = tolower(ResourceId)
| join kind=inner (
{sql_evidence_query()}
  ) on VmId
| extend DiscoverySignals = case(SqlExtensionCount > 0 and SqlRegistrationCount > 0, 'SQL IaaS extension and SQL VM registration',
             SqlExtensionCount > 0, 'SQL IaaS extension only', 'SQL VM registration only'),
         Opportunity = 'Retiring VM with SQL evidence - assess modernization',
         SqlRuntime = 'Unverified - extension or registration does not prove SQL is running',
         HaEvidence = iff(isempty(SqlGroupIds), 'HA topology not established', 'SQL VM group registered - active HA membership/health unverified'),
         Assessment = 'Validate SQL instances/version/edition, database compatibility, dependencies, HA/DR, performance, licensing and downtime; assess Azure SQL Managed Instance, Azure SQL Database or supported SQL on modern VMs.',
         Guidance = 'https://learn.microsoft.com/azure/azure-sql/migration-guides/'
| project Name, ResourceGroup, Region, VmSize, Lifecycle, RetiresOn, PowerState,
          DiscoverySignals, SqlExtensions, ExtensionStates, SqlExtensionCount,
          SqlRegistrationCount, SqlRegistrationIds, SqlRegistrationStates, SqlEditions,
          SqlLicenseTypes, SqlManagementModes, SqlGroupIds, HaEvidence, Opportunity,
          SqlRuntime, Assessment, RecommendedTarget, Guidance, ResourceId
| order by RetiresOn asc, Name asc"""


def quota_inventory_query(base):
    return base + """
| where Lifecycle != 'OPTIONAL'
| extend QuotaPath = case('{QuotaGeneration}' == '5', parse_json('{V5PathSkuMap}')[VmSize],
         '{QuotaGeneration}' == '6', parse_json('{V6PathSkuMap}')[VmSize],
         '{QuotaGeneration}' == '7', parse_json('{V7PathSkuMap}')[VmSize], '')
| extend RecommendedTarget = iff('{QuotaGeneration}' == 'default', RecommendedTarget,
         tostring(split(tostring(QuotaPath), '|')[3]))
""" + quota_scope_filter("SubscriptionId") + """
| extend SourceQuotaFamily = tostring(parse_json('{QuotaFamilyMap}')[tolower(VmSize)]),
         TargetQuotaFamily = tostring(parse_json('{QuotaFamilyMap}')[tolower(RecommendedTarget)]),
         SourceCores = tolong(parse_json('{QuotaCoreMap}')[tolower(VmSize)]) * tolong(Instances),
         TargetCores = tolong(parse_json('{QuotaCoreMap}')[tolower(RecommendedTarget)]) * tolong(Instances)
| extend SourceCredit = PowerState in~ ('running', 'stopped')
        and isnotempty(SourceQuotaFamily) and isnotnull(SourceCores) and SourceCores >= 0,
         Unresolved = isempty(TargetQuotaFamily) or isnull(TargetCores) or Instances < 0
        or ComputePriority =~ 'Spot' or TargetCores < 0
| extend RegionalIncrement = case(not(SourceCredit), TargetCores,
        TargetCores > SourceCores, TargetCores - SourceCores, tolong(0))
| extend FamilyIncrement = iff(SourceQuotaFamily == TargetQuotaFamily,
        RegionalIncrement, TargetCores)"""


def quota_scope_filter(column):
    return f"""| where '{{QuotaScope}}' == 'tenant' or {column} in~ ({{Subscriptions}})
    or strcat('/subscriptions/', {column}) in~ ({{Subscriptions}})
    or 'value::all' in~ ({{Subscriptions}})"""


def quota_demand_query(base):
    return quota_inventory_query(base) + """
| summarize Resources = count(), Instances = sum(Instances),
        CurrentCores = sum(SourceCores), SideBySideAdditionalCores = sum(TargetCores),
        CurrentUnknown = countif(isnull(SourceCores) or SourceCores < 0),
        TargetUnknown = countif(isnull(TargetCores) or TargetCores < 0),
        InPlaceAdditionalFamilyCores = sum(FamilyIncrement),
        InPlaceAdditionalRegionalCores = sum(RegionalIncrement),
        UnresolvedResources = countif(Unresolved),
        InPlaceUnverifiedResources = countif(not(SourceCredit)),
        TargetSizes = strcat_array(make_set(RecommendedTarget, 100), ', ')
    by SubscriptionId, Region, TargetQuotaFamily
| extend TargetQuotaFamily = iff(isempty(TargetQuotaFamily), 'Unknown', TargetQuotaFamily),
         TargetCores = iff(TargetUnknown > 0, tolong(''), SideBySideAdditionalCores),
         CurrentCores = iff(CurrentUnknown > 0, tolong(''), CurrentCores),
         EstimateStatus = iff(UnresolvedResources > 0, 'Unknown - incomplete metadata', 'Estimated - validate live quota')
| extend Delta = TargetCores - CurrentCores
| order by SubscriptionId asc, Region asc, TargetQuotaFamily asc"""


def quota_rows_query():
    return """QuotaResources
| where type =~ 'microsoft.compute/locations/usages'
| mv-expand Quota = properties.value limit 2000
| project SubscriptionId = tolower(subscriptionId), Region = tolower(location),
          Metric = tolower(tostring(Quota.name.value)),
          QuotaLimit = tolong(Quota['limit']), QuotaUsage = tolong(Quota.currentValue)
| summarize MetricRecords = count(), Limit = max(QuotaLimit), Usage = max(QuotaUsage)
    by SubscriptionId, Region, Metric"""


def quota_check_query(base):
    return quota_inventory_query(base) + """
| extend Metric = pack_array('cores', iff(isempty(TargetQuotaFamily), 'unknown', TargetQuotaFamily))
| mv-expand Metric
| extend Metric = tolower(tostring(Metric)),
         InPlaceAdditional = iff(Metric == 'cores', RegionalIncrement, FamilyIncrement)
| summarize CurrentCores = sum(SourceCores), TargetCores = sum(TargetCores),
        CurrentUnknown = countif(isnull(SourceCores) or SourceCores < 0),
        TargetUnknown = countif(isnull(TargetCores) or TargetCores < 0),
        SideBySideAdditionalCores = sum(TargetCores),
        InPlaceAdditionalCores = sum(InPlaceAdditional),
        InPlaceUnverifiedResources = countif(not(SourceCredit)),
        UnresolvedResources = countif(Unresolved) by SubscriptionId, Region, Metric
| join kind=leftouter (
""" + quota_rows_query() + """
) on SubscriptionId, Region, Metric
| extend Available = Limit - Usage,
         QuotaUnknown = isnull(MetricRecords) or MetricRecords != 1
             or isnull(Limit) or isnull(Usage) or Limit < 0 or Usage < 0 or UnresolvedResources > 0
| extend SideBySideShortfall = iff(QuotaUnknown, tolong(''),
             iff(SideBySideAdditionalCores > Limit - Usage, SideBySideAdditionalCores - (Limit - Usage), tolong(0))),
         InPlaceShortfall = iff(QuotaUnknown, tolong(''),
             iff(InPlaceAdditionalCores > Limit - Usage, InPlaceAdditionalCores - (Limit - Usage), tolong(0)))
| extend SideBySideStatus = case(QuotaUnknown,
            'Unknown - quota/metadata missing', SideBySideShortfall > 0, 'Not met - quota shortfall', 'Met - quota only'),
         InPlaceStatus = case(QuotaUnknown,
            'Unknown - quota/metadata missing',
            InPlaceShortfall > 0, 'Not met - quota shortfall', 'Met - quota only')
| extend AdditionalCores = iff('{QuotaMigrationMode}' == 'inplace', InPlaceAdditionalCores, SideBySideAdditionalCores),
         Shortfall = iff('{QuotaMigrationMode}' == 'inplace', InPlaceShortfall, SideBySideShortfall),
         SelectedStatus = iff('{QuotaMigrationMode}' == 'inplace', InPlaceStatus, SideBySideStatus)
| extend Conservative = '{QuotaMigrationMode}' == 'inplace' and InPlaceUnverifiedResources > 0
| extend QuotaStatus = case(SelectedStatus startswith 'Unknown', SelectedStatus,
              Shortfall > 0 and Conservative, 'Insufficient quota (conservative)',
              Shortfall > 0, 'Insufficient quota',
              Conservative, 'Sufficient - conservative quota check', 'Sufficient - quota only'),
         Delta = iff(CurrentUnknown > 0 or TargetUnknown > 0, tolong(''), TargetCores - CurrentCores),
         CurrentCores = iff(CurrentUnknown > 0, tolong(''), CurrentCores),
         TargetCores = iff(TargetUnknown > 0, tolong(''), TargetCores),
         QuotaCheck = iff(Metric == 'cores', 'Regional total', Metric),
         DemandEvaluatedAt = now()
| project SubscriptionId, Region, Metric, Limit, Usage, Available,
          CurrentCores, TargetCores, Delta, CurrentUnknown, TargetUnknown,
          AdditionalCores, Shortfall, QuotaStatus, QuotaCheck,
          SideBySideAdditionalCores, SideBySideShortfall, SideBySideStatus,
          InPlaceAdditionalCores, InPlaceShortfall, InPlaceStatus, InPlaceUnverifiedResources, UnresolvedResources, DemandEvaluatedAt
| order by SubscriptionId asc, Region asc, Metric asc"""


def quota_summary_query(base):
    return quota_check_query(base) + """
| extend SummaryScopes = pack_array(SubscriptionId, '__total__')
| mv-expand SummaryScopes
| summarize CurrentCores = sumif(CurrentCores, Metric == 'cores'),
        TargetCores = sumif(TargetCores, Metric == 'cores'),
        CurrentUnknown = sumif(CurrentUnknown, Metric == 'cores'),
        TargetUnknown = sumif(TargetUnknown, Metric == 'cores'),
        FailedChecks = countif(QuotaStatus startswith 'Insufficient'),
        UnknownChecks = countif(QuotaStatus startswith 'Unknown'),
        ConservativeChecks = countif(QuotaStatus contains 'conservative')
    by SubscriptionId = tostring(SummaryScopes)
| join kind=leftouter (
    ResourceContainers
    | where type =~ 'microsoft.resources/subscriptions'
    | project SubscriptionId = tolower(subscriptionId), SubscriptionName = name
) on SubscriptionId
| extend CurrentCores = iff(CurrentUnknown > 0, tolong(''), CurrentCores),
         TargetCores = iff(TargetUnknown > 0, tolong(''), TargetCores),
         TotalOrder = iff(SubscriptionId == '__total__', 0, 1),
         Scope = iff(SubscriptionId == '__total__',
             iff('{QuotaScope}' == 'tenant', 'TENANT TOTAL (readable subscriptions)', 'SELECTED SUBSCRIPTIONS TOTAL'),
             coalesce(SubscriptionName, SubscriptionId)),
         QuotaStatus = case(FailedChecks > 0 and UnknownChecks > 0, 'Insufficient quota / incomplete checks',
             FailedChecks > 0, 'Insufficient quota', UnknownChecks > 0, 'Unknown - incomplete checks',
             ConservativeChecks > 0, 'Sufficient - conservative quota check', 'Sufficient - quota only')
| order by TotalOrder asc, Scope asc
| project Scope, ['Current cores'] = CurrentCores, ['Target cores'] = TargetCores,
          ['Delta (+/-)'] = TargetCores - CurrentCores, ['Quota status'] = QuotaStatus,
          SubscriptionId, FailedChecks, UnknownChecks"""


def quota_headroom_query(base):
    return quota_inventory_query(base) + """
| where isnotempty(TargetQuotaFamily)
| summarize by Region, TargetQuotaFamily
| join kind=inner (
""" + quota_rows_query() + """
| summarize Limits = make_bag(pack(Metric, Limit)), Usages = make_bag(pack(Metric, Usage)),
        Counts = make_bag(pack(Metric, MetricRecords)) by SubscriptionId, Region
) on Region
| extend Family = tolower(TargetQuotaFamily)
| extend FamilyAvailable = tolong(Limits[Family]) - tolong(Usages[Family]),
         RegionalAvailable = tolong(Limits['cores']) - tolong(Usages['cores']),
         QuotaUnknown = isnull(Counts[Family]) or isnull(Counts['cores'])
             or tolong(Counts[Family]) != 1 or tolong(Counts['cores']) != 1
             or isnull(tolong(Limits[Family])) or isnull(tolong(Usages[Family]))
             or isnull(tolong(Limits['cores'])) or isnull(tolong(Usages['cores']))
             or tolong(Limits[Family]) < 0 or tolong(Usages[Family]) < 0
             or tolong(Limits['cores']) < 0 or tolong(Usages['cores']) < 0
| extend AvailableCores = iff(QuotaUnknown, tolong(''),
             iff(FamilyAvailable < RegionalAvailable, FamilyAvailable, RegionalAvailable))
| extend HeadroomStatus = case(QuotaUnknown, 'Unknown - quota missing',
             AvailableCores <= 0, 'No unused quota', 'Potential headroom - assess quota group eligibility')
| project SubscriptionId, Region, TargetQuotaFamily, AvailableCores, HeadroomStatus
| order by Region asc, TargetQuotaFamily asc, AvailableCores desc"""


def quota_status_formatter(column):
    return fmt(column, 18, formatOptions={"thresholdsOptions": "colors", "thresholdsGrid": [
        {"operator": "contains", "thresholdValue": "Insufficient", "representation": "red", "text": "{0}{1}"},
        {"operator": "contains", "thresholdValue": "Unknown", "representation": "orange", "text": "{0}{1}"},
        {"operator": "Default", "representation": "green", "text": "{0}{1}"},
    ]})


def quota_planning_items(base, regions, families):
    demand = arg_query(quota_demand_query(base), "quota-demand", export=True, size=1,
                       title="Migration breakdown: subscription, region and target family",
                       grid=grid_settings(
                           {"SubscriptionId": "30ch", "Region": "15ch", "TargetQuotaFamily": "28ch",
                            "CurrentCores": "12ch", "TargetCores": "12ch", "Delta": "10ch"},
                           [fmt(c, 5) for c in ("Resources", "Instances", "SideBySideAdditionalCores",
                            "InPlaceAdditionalFamilyCores", "InPlaceAdditionalRegionalCores",
                            "UnresolvedResources", "InPlaceUnverifiedResources", "TargetSizes", "EstimateStatus",
                            "CurrentUnknown", "TargetUnknown")]))
    summary = arg_query(quota_summary_query(base), "quota-summary", export=True, size=1,
                        title="Core migration plan: total and per subscription",
                        grid=grid_settings(
                            {"Scope": "3fr", "Current cores": "2fr", "Target cores": "2fr",
                             "Delta (+/-)": "2fr", "Quota status": "3fr"},
                            [quota_status_formatter("Quota status")]
                            + [fmt(c, 5) for c in ("SubscriptionId", "FailedChecks", "UnknownChecks")]))
    checks = arg_query(quota_check_query(base), "quota-check", export=True, size=1,
                       title="Automatic quota checks: every affected subscription / region / family",
                       grid=grid_settings(
                           {"SubscriptionId": "30ch", "Region": "15ch", "QuotaCheck": "28ch",
                            "AdditionalCores": "15ch", "Available": "15ch", "Shortfall": "12ch", "QuotaStatus": "32ch"},
                           [quota_status_formatter("QuotaStatus"), fmt("Shortfall", 18, formatOptions={
                               "thresholdsOptions": "colors", "thresholdsGrid": [
                                   {"operator": ">", "thresholdValue": "0", "representation": "red", "text": "{0}{1}"},
                                   {"operator": "Default", "representation": "gray", "text": "{0}{1}"}]})]
                           + [fmt(c, 5) for c in ("Metric", "Limit", "Usage", "CurrentCores", "TargetCores",
                            "Delta", "CurrentUnknown", "TargetUnknown", "SideBySideAdditionalCores",
                            "SideBySideShortfall", "SideBySideStatus", "InPlaceAdditionalCores",
                            "InPlaceShortfall", "InPlaceStatus", "InPlaceUnverifiedResources",
                            "UnresolvedResources", "DemandEvaluatedAt")]))
    headroom = arg_query(quota_headroom_query(base), "quota-headroom", export=True, size=1,
                         title="Quota discussion leads: unused quota across readable tenant subscriptions",
                         grid=grid_settings({"SubscriptionId": "30ch", "Region": "15ch",
                             "TargetQuotaFamily": "28ch", "AvailableCores": "15ch", "HeadroomStatus": "48ch"}))
    for item, detail in ((demand, "migration"), (checks, "checks"), (headroom, "headroom")):
        item["conditionalVisibility"] = {"parameterName": "QuotaDetails", "comparison": "isEqualTo", "value": detail}
    for item in (demand, summary, checks, headroom):
        item["content"]["crossComponentResources"] = ["{QuotaAllSubscriptions}"]
    parameters = [
        {"name": name, "type": 2, "isRequired": True, "label": label,
         "jsonData": json.dumps([{"value": value, "label": text} for value, text in choices]),
         "value": default}
        for name, label, default, choices in (
            ("QuotaScope", "Planning scope", "selected",
             [("selected", "Selected subscriptions"), ("tenant", "Tenant - all readable subscriptions")]),
            ("QuotaMigrationMode", "Migration approach", "inplace",
             [("sidebyside", "Side-by-side (keep sources)"), ("inplace", "In-place (conservative if needed)")]),
            ("QuotaDetails", "Show detail", "none",
             [("none", "Summary only"), ("migration", "Migration breakdown"),
              ("checks", "Quota checks and shortfalls"), ("headroom", "Tenant headroom / quota group discussion"),
              ("groups", "Live quota-group inspector"),
              ("key", "Column key & assumptions")]),
            ("QuotaGeneration", "Target scenario", "default",
             [("default", "Default V5-first"), ("5", "V5 only"), ("6", "V6 only"), ("7", "V7 only")]),
        )
    ]
    parameters.append({
        "name": "QuotaAllSubscriptions", "type": 6, "isRequired": True, "multiSelect": True,
        "isHiddenWhenLocked": True, "quote": "'", "delimiter": ",",
        "typeSettings": {"additionalResourceOptions": ["value::all"], "includeAll": True, "showDefault": False},
        "value": ["value::all"],
    })
    return [
        text_item(
            "## Capacity & quota\n\n"
            "Retirement/EOL migration quota estimates.\n\n"
            "**Current cores** = source quota-vCPUs, including deallocated inventory; "
            "**Target cores** = target quota-vCPUs; **Delta** = target minus current, not the quota request.\n\n"
            "**In-place (default):** resize existing VMs. Regional quota models positive core growth; "
            "a different target family still needs full target-family cores. Unverified sources get no core credit; "
            "this model does not confirm that resizing is supported.\n\n"
            "**Side-by-side:** deploy replacements while keeping sources allocated; quota must cover both fleets.\n\n"
            "**Conservative:** full target cores for deallocated/unknown sources. **Red:** insufficient quota. **Amber:** missing quota/target data. "
            "**Green:** quota only, not physical capacity or migration approval. Missing targets stay Unknown; "
            "spare quota elsewhere cannot cancel a shortfall.\n\n"
            "Independent of Live target availability. **Show detail** opens breakdowns, shortfalls, groups or the key.",
            "quota-intro"),
        {"type": 9, "name": "quota-parameters", "content": {
            "version": "KqlParameterItem/1.0", "style": "pills",
            "parameters": [dict(p, version="KqlParameterItem/1.0") for p in parameters]}},
        summary, demand, checks, headroom,
        {"type": 12, "name": "quota-group-inspector",
         "conditionalVisibility": {"parameterName": "QuotaDetails", "comparison": "isEqualTo", "value": "groups"},
         "content": {"version": "NotebookGroup/1.0", "groupType": "editable",
                     "items": quota_group_items(regions, families)}},
        conditional_text("quota-key", "QuotaDetails", "key",
            "### Column key & assumptions\n\n"
            "Totals plus subscription rows. **Planning scope:** selected or all readable tenant "
            "subscriptions; other tabs keep their scope.\n\n"
            "| Column | Key |\n| --- | --- |\n"
            "| Current cores | Retirement/EOL configured quota-vCPUs including deallocated inventory; VMSS counted once. Not subscription usage. |\n"
            "| Target cores | Scenario quota-vCPUs in current regions. Independent V5/V6/V7 paths; no fallback. Missing targets stay Unknown. |\n"
            "| Delta (+/-) | Target minus current, not the quota request; independent of approach. |\n"
            "| Quota status | Red: regional/family shortfall. Orange: missing quota/target data or Spot. Green: quota only. Conservative: no unverified source credit. |\n\n"
            "**Every affected subscription/region/family is checked automatically** against indexed Compute "
            "`QuotaResources`. Both constraints must pass; spare quota elsewhere cannot cancel shortfalls. "
            "Indexing lags: recheck Azure Quotas/CLI before migration. Missing records "
            "are Unknown, not unlimited.\n\n"
            "**In-place** grants source credit only for running/stopped allocations with known source metadata. "
            "Otherwise full target cores are conservative; downsizing gives no negative credits. "
            "A conservative shortfall does not prove every optimized resize fails.\n\n"
            "**Quota checks:** AdditionalCores is required headroom; Available is limit minus current usage; "
            "Shortfall is max(additional minus available, zero); blank means Unknown. Regional demand "
            "includes all families. **Tenant headroom** is unused family quota capped by regional headroom, "
            "not post-migration surplus or pooled quota. **Live quota-group inspector** checks one selected "
            "management group/group/subscription/region/metric, not tenant-wide membership or transfer eligibility. "
            "No quota is moved; verify membership, eligibility, limits and approvals separately.\n\n"
            "**Quota is not physical capacity or resize approval.** Validate SKU/zone access, policy, boot/drivers "
            "and allocation. Targets use `vCPUs`, not constrained `vCPUsAvailable`. Lifecycle filters "
            "do not remove demand; scenarios do not change Inventory recommendations. "
            "Plan service quotas, autoscaler maxima, surge and Batch separately. See [VM vCPU quotas]"
            "(https://learn.microsoft.com/azure/quotas/per-vm-quota-requests)."),
    ]


def quota_group_items(regions, families):
    parameters = [{
        "version": "KqlParameterItem/1.0", "name": "QuotaGroupDiscovery",
        "label": "Paste discovery JSON", "type": 1,
        "value": '{"status":"Not scanned","checkedAtUtc":"","groups":[]}',
    }, {
        "version": "KqlParameterItem/1.0", "name": "QuotaGroupId", "label": "Discovered quota group",
        "type": 2, "isRequired": True, "jsonData": "{QuotaGroupDiscovery:$.groups}",
        "typeSettings": {"showDefault": False},
    }]
    parameters.append({
        "version": "KqlParameterItem/1.0", "name": "QuotaGroupSubscription", "type": 6,
        "label": "Subscription to inspect", "isRequired": True,
        "typeSettings": {"additionalResourceOptions": ["value::1"], "showDefault": False},
        "value": "value::1",
    })
    for name, label, values in (
            ("QuotaGroupRegion", "Region to inspect", sorted(regions)),
            ("QuotaGroupMetric", "Quota metric to inspect", ["cores"] + sorted(set(families.values())))):
        parameters.append({
            "version": "KqlParameterItem/1.0", "name": name, "label": label, "type": 2,
            "isRequired": True, "jsonData": json.dumps(
                [{"value": v, "label": v} for v in values], separators=(",", ":")),
            "value": values[0],
        })
    parameters.append({
        "version": "KqlParameterItem/1.0", "name": "QuotaGroupEvidence", "label": "Evidence detail",
        "type": 2, "isRequired": True, "value": "summary",
        "jsonData": json.dumps([{"value": "summary", "label": "Summary"},
                                {"value": "raw", "label": "Summary + raw API evidence"}]),
    })
    items = [
        text_item(
            "### Live quota-group inspector\n\n"
            "Run the optional read-only [discovery script]"
            "(https://github.com/cjasset/AzureStuff/blob/main/workbooks/azure-vm-retirement-upgrade-planner/scripts/Find-QuotaGroups.ps1), "
            "paste its JSON below, then select a discovered group, subscription, region and metric. "
            "Discovery is a timestamped snapshot, not live; rerun to refresh. "
            "Incomplete scans list failed scopes in the JSON; empty lists do not prove no group exists.\n\n"
            "**Live evidence: one selected scope, not a tenant-wide scan**; independent of fleet filters/scenarios. "
            "API membership is **not inferred from management-group ancestry**. "
            "Allocate pool quota first, then refresh family and regional subscription checks. "
            "Already allocated quota is not added again. "
            "Pool/allocation continuation pages stay Unknown; review Azure Quotas for full coverage. "
            "Donor shareable quota, eligibility and capacity are not assessed. "
            "No group, membership or quota is changed. "
            "[Quota-group guidance](https://learn.microsoft.com/azure/quotas/quota-groups).",
            "group-quota-intro"),
        {"type": 9, "name": "group-quota-controls", "content": {
            "version": "KqlParameterItem/1.0", "style": "pills", "parameters": parameters}},
        text_item("Discovery status: **{QuotaGroupDiscovery:$.status}**; scanned UTC: "
                  "**{QuotaGroupDiscovery:$.checkedAtUtc}**. Snapshot only; membership/pool evidence below is live.",
                  "group-quota-discovery-status"),
    ]
    for name, parameter in (("group-quota-membership", "QuotaGroupMemberRecord"),
                            ("group-quota-pool", "QuotaGroupPoolRecord")):
        items.append({
            "type": 3, "name": name, "content": {
                "version": "KqlItem/1.0", "queryType": 12,
                "query": group_arm_request(name, [("Record", "$")]),
                "title": "Live membership evidence" if name.endswith("membership") else "Live regional group-pool evidence",
                "size": 3, "visualization": "table",
                "gridSettings": grid_settings({"Record": "100%"}),
                "noDataMessage": "Unknown: no API evidence. Check permissions, selected scope and API errors.",
            },
            "conditionalVisibility": {"parameterName": "QuotaGroupEvidence", "comparison": "isEqualTo", "value": "raw"},
        })
        items.append({"type": 9, "name": name + "-record", "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": [merge_parameter(parameter, name, "Record")]}})
    items.append(arg_query(
        group_evidence_query(), "group-quota-evidence", size=1, export=True,
        title="Quota-group evidence - separate from subscription quota checks",
        grid=grid_settings(
            {"Subscription": "30ch", "Region": "15ch", "Metric": "25ch", "Membership": "24ch",
             "Group quota evidence": "34ch", "Group limit": "12ch", "Unallocated group cores": "16ch",
             "Already allocated to subscription": "20ch", "Next action": "45ch"},
            [fmt("Group quota evidence", 18, formatOptions={
                "thresholdsOptions": "colors", "thresholdsGrid": [
                    {"operator": "contains", "thresholdValue": "Unknown", "representation": "orange", "text": "{0}{1}"},
                    {"operator": "Default", "representation": "blue", "text": "{0}{1}"}]})]
            + [fmt(c, 5) for c in ("ManagementGroupAndQuotaGroup", "CheckedAtUtc")])))
    return items


# --------------------------------------------------------------------------
# Workbook item helpers
# --------------------------------------------------------------------------

def text_item(md, name, width=None):
    item = {"type": 1, "content": {"json": md}, "name": name,
            "styleSettings": {"margin": "0", "padding": "0"}}
    if width:
        item["customWidth"] = width
    return item


def arg_query(query, name, visualization="table", size=0, title=None,
              grid=None, chart=None, tile=None, extra=None, width=None,
              export=False):
    content = {
        "version": "KqlItem/1.0",
        "query": query,
        "size": size,
        "queryType": 1,
        "resourceType": "microsoft.resourcegraph/resources",
        "crossComponentResources": ["{Subscriptions}"],
        "visualization": visualization,
        "noDataMessage": (
            "No matching SQL registration/extension evidence for retiring VMs in this scope. "
            "This does not prove SQL is absent. Check subscription access and workload inventory."
            if name == "sql-modernization-grid" else
            "No matching rows in the selected scope and filters. Check subscription access, "
            "filters and catalogue coverage; an empty result is not migration approval."
        ),
    }
    if title:
        content["title"] = title
    if grid:
        content["gridSettings"] = grid
    if chart:
        content["chartSettings"] = chart
    if tile:
        content["tileSettings"] = tile
    # Grids can offer a "Download to Excel" toolbar button. Enabled on the
    # detailed tables so the inventory can be taken into your own
    # planning spreadsheet; "all" exports every column, not just visible ones,
    # which matters because several prose columns are truncated on screen.
    if export:
        content["showExportToExcel"] = True
        content["exportToExcelOptions"] = "all"
    if extra:
        content.update(extra)
    item = {"type": 3, "content": content, "name": name}
    if width:
        item["customWidth"] = width
    return item


def fmt(col, formatter=None, width=None, **kw):
    d = {"columnMatch": col}
    if formatter is not None:
        d["formatter"] = formatter
    d.update(kw)
    if width:
        d.setdefault("formatOptions", {})["customColumnWidthSetting"] = width
    return d


def widths(spec):
    """Turn {column: width} into plain width-only formatter rules."""
    return [fmt(col, 0, width=w) for col, w in spec.items()]


# Date columns render as "4/30/2028, 7:00:00 AM" unless told otherwise.
DATE_FMT = {"showUtcTime": True, "formatName": "shortDatePattern"}


def date_col(col):
    return fmt(col, 6, dateFormat=DATE_FMT)


def grid_settings(width_spec, formatters=(), rows=100, **kw):
    """Column widths first, then the typed formatters that override them.

    Workbooks applies the last matching rule per property, so a width-only
    rule followed by a typed rule keeps both the width and the formatting
    only when the typed rule carries the width itself. Any column present in
    both is therefore emitted once, with the width folded into the typed rule.

    Fractional widths share available space instead of fixing character widths.
    `rows` caps grid result rows independently of the height set by query size.
    """
    typed = {f["columnMatch"]: f for f in formatters}
    rules = []
    for col, w in width_spec.items():
        if w.endswith("ch"):
            w = w[:-2] + "fr"
        elif w.endswith("%"):
            w = w[:-1] + "fr"
        if col in typed:
            f = dict(typed.pop(col))
            f.setdefault("formatOptions", {})
            f["formatOptions"] = dict(f["formatOptions"])
            f["formatOptions"]["customColumnWidthSetting"] = w
            rules.append(f)
        else:
            rules.append(fmt(col, 0, width=w))
    rules.extend(typed.values())
    out = {"formatters": rules, "rowLimit": rows}
    out.update(kw)
    return out


def group_item(name, tab_value, items):
    return {
        "type": 12,
        "content": {"version": "NotebookGroup/1.0", "groupType": "editable",
                    "items": items},
        "conditionalVisibility": {"parameterName": "WorkflowStage", "comparison": "isEqualTo",
                                  "value": tab_value},
        "name": name,
    }

WORKFLOW_STAGES = [
    ("Start here", "start"), ("1. Inventory", "inventory"), ("2. Assess", "assess"),
    ("3. Plan", "planning"), ("4. Migrate & validate", "migrate"),
    ("Methodology & references", "about"),
]
WORKFLOW_REPORTS = {
    "inventory": ("InventoryView", [
        ("Fleet overview", "overview"), ("Retirement & pricing", "impacted"),
        ("Reservation exposure", "reservations"),
        ("Workloads & owners", "discovery"), ("Catalogue coverage", "coverage")]),
    "assess": ("AssessView", [
        ("V5 / V6 / V7 paths", "paths"), ("Routes & gates", "routes"), ("Required actions", "prereq"),
        ("SQL opportunities", "sql")]),
    "planning": ("PlanView", [
        ("Live target availability", "availability"), ("Capacity & quota", "quota"),
        ("Target specifications", "mapping"),
        ("Compatible alternatives", "alternatives"), ("Suggested waves", "plan")]),
}
WORKFLOW_GUIDANCE = {
    "start": (
        "Start here",
        "Turn fleet evidence into a customer-owned migration plan.",
        "Select the subscriptions above. Start with retirement/end-of-life scope; add optional "
        "modernization only when you want to explore it. Open each numbered stage in order, or "
        "return directly to a stage when you already know your next task.",
        "Confirm Reader access to every intended subscription, an accountable workload owner, "
        "and the reason for acting: retirement, pricing exposure or optional modernization.",
        "A scoped inventory and a shared understanding of what the workbook can and cannot prove.",
    ),
    "inventory": (
        "1. Inventory - what do I have, and why should I act?",
        "Identify affected resources, workload owners and gaps in coverage.",
        "Review the fleet overview, separate retirement from regional pricing exposure and purchased "
        "Reservation exposure, identify "
        "service ownership, and inspect Catalogue coverage for sizes not assessed by the captured map.",
        "Export an affected-workload list, confirm scope and deadlines, and assign owners to "
        "unmapped SKUs and resources with missing or conflicting ownership signals.",
        "An owned workload list. Zero retirement rows does not rule out pricing exposure or unassessed SKUs.",
    ),
    "assess": (
        "2. Assess - what could prevent a successful migration?",
        "Choose a candidate path and identify prerequisite work and missing evidence.",
        "Review V5 first and compare V6/V7 where justified. Review Routes & gates, Required actions "
        "and SQL opportunities with the workload owner. Check live target availability and quota in Plan. "
        "Export the detailed grids only when you need the full evidence columns.",
        "Record a preferred target and migration method. Resolve Not met checks and assign every "
        "Unknown check an owner, evidence requirement and decision date.",
        "A candidate path and assessment backlog, not migration approval. Met is evidence for one "
        "check; Unknown is not a pass; Not applicable does not imply overall readiness.",
    ),
    "planning": (
        "3. Plan - what resources and sequencing will I need?",
        "Create a feasible, owner-approved plan for target availability, quota, remediation, pilots and waves.",
        "Use Live target availability for the selected target's subscription/region/zone access. "
        "Use Capacity & quota for fleet demand and regional/family limits. Review target specifications "
        "and adapt suggested waves to application dependencies.",
        "Confirm target eligibility and actual capacity separately, request quota where needed, "
        "schedule prerequisite work, and agree pilot, downtime, backup and rollback plans.",
        "An execution plan. Quota headroom is not physical capacity or transferable quota; the "
        "quota model defaults to V5-first; select the matching V5/V6/V7 scenario explicitly. "
        "Managed-pool peak/surge demand needs separate planning.",
    ),
    "migrate": (
        "4. Migrate & validate - how do I execute safely?",
        "Use an approved service-specific runbook, then validate the workload and close out the source.",
        "Work through the checklist below with the application and platform owners. Execute changes "
        "outside this read-only workbook using your approved deployment and change-management tools.",
        "Obtain explicit owner acceptance of application, performance, resilience and rollback "
        "evidence before expanding a wave or deleting old resources.",
        "A customer-maintained execution and acceptance record. This workbook does not execute "
        "migrations, track completion, or certify application readiness.",
    ),
    "about": (
        "Methodology & references",
        "Understand data sources, scoring, coverage and limitations.",
        "Review the scoring model and references before interpreting a flag. Return to Inventory > "
        "Catalogue coverage to see mapped versus unassessed sizes in your scope.",
        "Confirm that assumptions, cloud applicability and evidence quality match your environment.",
        "Transparent interpretation, not a supportability or migration certification.",
    ),
}


def navigation_item(name, parameter, options, style="tabs"):
    return {
        "type": 11, "name": name,
        "content": {
            "version": "LinkItem/1.0", "style": style,
            "links": [
                {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{name}:{key}")),
                 "cellValue": parameter, "linkTarget": "parameter", "linkLabel": label,
                 "subTarget": key, "preText": "",
                 "style": "primary" if style == "paragraph" and label.startswith(("Next:", "Recheck:")) else "link"}
                for label, key in options
            ],
        },
    }


def workflow_layout(items):
    reports = {item["conditionalVisibility"]["value"]: item
               for item in items if item.get("type") == 12}
    header = next(item for item in items if item["name"] == "header")
    reports["about"]["content"]["items"].insert(0, text_item(
        "### Scope and recommendation policy\n\n" + header["content"]["json"].split("\n\n", 1)[1],
        "workflow-policy"))
    header["content"]["json"] = (
        f"# {WORKBOOK_TITLE}\n\n"
        "Follow **Inventory > Assess > Plan > Migrate & validate** to turn fleet evidence "
        "into an owner-approved plan. Start below, or select a stage to continue.\n\n"
        "**Read-only:** this workbook makes no resource changes. "
        "**A platform score is not migration approval.**")
    coverage = [item for item in reports["about"]["content"]["items"] if item.get("type") == 3]
    reports["about"]["content"]["items"] = [
        item for item in reports["about"]["content"]["items"] if item.get("type") != 3]
    reports["coverage"] = group_item("grp-coverage", "coverage", [
        text_item("### Check what was not assessed\n\n"
                  "Sizes absent from the captured catalogue are **unassessed**, not approved or "
                  "necessarily retiring. Assign an owner to research their lifecycle and target path. "
                  "The all-sizes grid includes optional sizes independently of the lifecycle filter.",
                  "coverage-intro"),
        *coverage,
    ])
    result = [item for item in items if item.get("type") != 12]
    for index, (label, key) in enumerate(WORKFLOW_STAGES):
        title, goal, actions, checkpoint, outcome = WORKFLOW_GUIDANCE[key]
        stage_items = [text_item(
            f"## {title}\n\n**Goal:** {goal}\n\n**What to do:** {actions}\n\n"
            f"**Before moving on:** {checkpoint}\n\n**Outcome / interpretation:** {outcome}",
            f"workflow-guide-{key}")]
        if key in ("inventory", "assess", "planning"):
            stage_items.append(text_item(
                "**Evidence key:** catalogue/reference dates are shown in Start here. "
                "Inventory, ownership, Advisor and quota are **indexed ARG** data at refresh, "
                "not real-time allocation or guest telemetry. The selected SKU record is "
                "**live ARM** at inspection. Guest/application/recovery evidence is "
                "**customer-confirmed** and stays Unknown here until independently verified.",
                f"workflow-evidence-{key}"))
        if key == "start":
            stage_items.append(text_item(
                "| Stage | Question | Take away |\n| --- | --- | --- |\n"
                "| 1. Inventory | What do I have, and why act? | A scoped workload list with owners |\n"
                "| 2. Assess | What could block the path? | Candidate targets and an evidence backlog |\n"
                "| 3. Plan | What resources and sequencing are needed? | Target access, quota actions, pilots and waves |\n"
                "| 4. Migrate & validate | How do I execute and accept it? | A runbook and acceptance checklist |\n\n"
                "**Read this first.** V5-first recommendations preserve captured platform capabilities; "
                "V5 still needs guest OS, NVMe/MANA where applicable, encryption, application and quota checks. "
                "**100 means no scored platform penalty, not migration approval.**\n\n"
                "**Scope matters.** Lifecycle filters apply to fleet assessment, but regional pricing, "
                "SQL retirement detection and independent discovery/coverage have their own documented scope. "
                "The Plan quota scope can include all readable tenant subscriptions; it cannot reveal "
                "inaccessible subscriptions or pool their quota.\n\n"
                "**Evidence, not an all-clear.** Missing permissions or metadata, empty results and query "
                "errors must be investigated. SQL evidence does not prove runtime or HA health. "
                "Use the export buttons to maintain decisions, owners and approvals in your own tracker. "
                "This workbook does not save a completion state.",
                "workflow-start-map"))
        elif key in WORKFLOW_REPORTS:
            parameter, options = WORKFLOW_REPORTS[key]
            stage_items.append(navigation_item(f"workflow-views-{key}", parameter, options))
            for _, report in options:
                reports[report]["conditionalVisibility"]["parameterName"] = parameter
                stage_items.append(reports[report])
        elif key == "about":
            stage_items.extend(reports["about"]["content"]["items"])
        elif key == "migrate":
            stage_items.extend(route_guide_items("migrate"))
            stage_items.append(text_item(
                "### Choose workload acceptance criteria\n\n"
                "Use observed ownership and the application owner's assessment to choose a pattern. "
                "For AVD, confirm pooled versus personal in Independent service registrations. "
                "Selection changes guidance only; it is not a recorded migration decision.",
                "workflow-acceptance-intro"))
            stage_items.append(navigation_item(
                "workflow-acceptance-select", "AcceptancePattern",
                [(label, value) for value, (label, _) in WORKLOADS.items()]))
            stage_items.extend(conditional_text(
                f"workflow-acceptance-{value}", "AcceptancePattern", value, guidance)
                for value, (_, guidance) in WORKLOADS.items())
            stage_items.append(text_item(CLOSEOUT, "workflow-closeout"))
            stage_items.append(text_item(
                "### Execution and acceptance checklist\n\n"
                "| When | Customer action | Evidence / decision to record outside the workbook |\n"
                "| --- | --- | --- |\n"
                "| Before a pilot | Confirm owner, service dependencies, licensing, supportability and target eligibility | Owner-approved target and method; resolved or explicitly accepted unknowns |\n"
                "| Before a pilot | Test backup/restore and rollback; agree triggers, downtime and recovery objectives | A rehearsed recovery plan and go/no-go authority |\n"
                "| Pilot | Use a representative approved non-production workload where possible | Baseline CPU, memory, disk/network latency and application health |\n"
                "| Execute | Follow the supported VM, VMSS or managed-service runbook | Change record, migration logs and boot/driver/encryption evidence |\n"
                "| Validate | Test application transactions, performance, monitoring, HA/failover and backup | Compare with baseline and obtain application-owner acceptance |\n"
                "| Expand waves | Recheck restrictions, quota and actual capacity before each wave | Approved schedule; pause or roll back when agreed thresholds fail |\n"
                "| Close out | Retain recovery resources for the approved period, then retire only confirmed unused sources | Owner approval, inventory reconciliation and updated cost/licensing records |\n\n"
                "### Use the workload's supported migration method\n\n"
                "- **Standalone VMs:** choose supported resize versus rebuild/migrate after assessing generation, "
                "storage, encryption and application constraints. See the "
                "[VM modernization guide](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/retirement/retired-sizes-modernization-guide).\n"
                "- **VM scale sets:** use model-level changes and a tested rolling/instance replacement strategy; "
                "validate instance health and orchestration behavior. See "
                "[VMSS upgrade policies](https://learn.microsoft.com/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-upgrade-policy).\n"
                "- **AKS:** migrate through supported node-pool operations, with pod disruption, drain, storage "
                "and surge planning. **Do not resize managed backing VMs individually.** See "
                "[AKS node-pool resizing](https://learn.microsoft.com/azure/aks/resize-node-pool).\n"
                "- **Other managed services:** follow the owning service's pool/workspace/host migration process. "
                "Ownership conflicts require investigation before changing backing compute.\n"
                "- **SQL / SAP / stateful workloads:** engage application and database owners for licensing, "
                "data consistency, HA, backup/restore and vendor support. SQL registrations are opportunity "
                "signals, not automatic recommendations to move to a managed database.\n\n"
                "### Recheck and reconcile\n\n"
                "Refresh Inventory after changes and compare exports from the same scope. "
                "A resource disappearing from a retirement grid is **not proof of migration success**: "
                "it may have moved scope, changed size, been deleted, or become inaccessible. Confirm the "
                "destination, workload acceptance and source disposition independently.",
                "workflow-execution-checklist"))
        destinations = []
        if key == "about":
            destinations.append(("Return: Start here", "start"))
        elif index > 0:
            previous_label, previous_key = WORKFLOW_STAGES[index - 1]
            destinations.append((f"Previous: {previous_label}", previous_key))
        if key == "migrate":
            destinations.append(("Recheck: 1. Inventory", "inventory"))
        elif index < 4:
            next_label, next_key = WORKFLOW_STAGES[index + 1]
            destinations.append((f"Next: {next_label}", next_key))
        if key != "about":
            destinations.append(("Help: Methodology & references", "about"))
        stage_items.append(navigation_item(f"workflow-next-{key}", "WorkflowStage", destinations, style="paragraph"))
        result.append(group_item(f"stage-{key}", key, stage_items))
    return result


ASSESSMENT_CHECKS = {
    "TargetSelection": "A capability-preserving candidate exists in the captured catalogue; not live availability.",
    "GenerationSupport": "The actual OS disk generation is supported by this target; Not met means conversion/rebuild is needed.",
    "ConversionOS": "Windows OS eligibility for the selected Gen1-to-Trusted-Launch route; observed Server 2016 is Not met.",
    "ConversionEncryption": "Windows OS encryption prerequisite for that conversion route; detected ADE configuration is Not met.",
    "NvmeBoot": "NVMe image/disk/boot-driver readiness if this target requires it; a real target boot is still required.",
    "ManaNetworking": "Target MANA hardware, guest driver and traffic validation; image origin alone cannot pass this.",
    "Architecture": "Source and target CPU architecture match.",
    "MemoryCapacity": "The selected target preserves source memory capacity.",
    "LocalDisk": "Local-disk capacity, persistence assumptions and performance; known removal is Not met, otherwise review.",
    "CatalogueRegion": "The target is present in the captured regional snapshot; use Plan > Live target availability for current offerability.",
    "ZonePlacement": "Current zone compatibility and placement; zonal sources require live validation.",
    "AvailabilitySet": "Availability-set placement and resize-cluster constraints, when membership exists.",
    "ScaleSetModel": "Scale-set orchestration, upgrade policy and model-level migration, when applicable.",
    "DiskLimits": "Attached disk count, IOPS, throughput and target storage limits.",
    "NicLimits": "NIC count and target networking limits.",
    "Quota": "Regional/family quota for this path; the separate quota report models the default V5-first fleet, not V6 approval.",
    "Policy": "Subscription policy and deployment governance approval.",
    "AllocationCapacity": "Actual allocation and resize-cluster capacity; the SKU API does not guarantee this.",
    "Backup": "Recoverable backup/restore and conversion-route backup policy prerequisites.",
    "AsrConversion": "ASR prerequisites when the selected route requires generation conversion.",
    "ApplicationValidation": "Owner-approved application, performance, dependency and HA/failover tests.",
    "Rollback": "A documented and rehearsed recovery/rollback route.",
}


def assessment_key():
    return text_item(
        "### Assessment column and status key\n\n"
        "The compact grids show the resource, target, generation, NVMe and MANA checks, plus "
        "**ChecksNotMet** (observed mismatches across all 22 checks) and **ChecksUnknown** "
        "(missing evidence across all 22). Click **All 22 checks** for that VM's vertically "
        "listed statuses and evidence. Export retains every prerequisite as its own column. "
        "Zero mismatches is not approval when evidence remains Unknown.\n\n"
        "| Status | Meaning and example |\n| --- | --- |\n"
        "| Met | Evidence meets this specific check; e.g. the target supports the observed disk generation. Not migration approval. |\n"
        "| Not met | An observed mismatch or unmet prerequisite; e.g. Gen1 against a Gen2-only target. Convert, rebuild or choose another target. |\n"
        "| Unknown | Evidence or testing is missing; e.g. a driver/traffic test, quota or application acceptance. Neither a pass nor proof of failure. |\n"
        "| Not applicable | This chosen target/route does not trigger the check; e.g. Windows conversion encryption on a route with no conversion. |\n\n"
        "| Prerequisite field | What it checks |\n| --- | --- |\n"
        + "\n".join(f"| {name} | {description} |" for name, description in ASSESSMENT_CHECKS.items())
        + "\n\n**Evidence fields:** Prerequisites explains target-specific actions; Validation lists "
        "evidence and remaining work. VmGeneration comes from disk metadata or a labelled image hint; "
        "ConversionRequired/NvmeRequired describe the chosen target. OsEvidence, EncryptionEvidence "
        "and ImageProvenance are observations/hints, not guest certification. OsHintSource, NvmeOsHint, "
        "ManaOsHint and their reference/date fields identify the captured OS guidance. "
        "NvmeDriverValidation/ManaDriverValidation remain unverified until tested. "
        "ResourceId links back to the Azure resource.",
        "assessment-column-key")


def conditional_text(name, parameter, value, guidance):
    item = group_item(name, value, [text_item(guidance, name + "-text")])
    item["conditionalVisibility"]["parameterName"] = parameter
    return item


def route_guide_items(prefix):
    return [
        text_item("### Choose a migration route\n\n"
                  "This selector changes guidance, not resources, recommendations or approval. "
                  "A workload can need more than one operation; read each applicable route. "
                  "Resolve the per-resource evidence and gates before selecting a supported method.",
                  prefix + "-route-intro"),
        navigation_item(prefix + "-route-select", "MigrationRoute",
                        [(label, value) for value, (label, _) in ROUTES.items()]),
        *(conditional_text(prefix + "-route-" + value, "MigrationRoute", value, guidance)
          for value, (_, guidance) in ROUTES.items()),
    ]


def compatible_target_maps(data):
    sources, specs = {}, {}
    lifecycle = build_lookups(data)[0]
    for record in data["skus"]:
        if lifecycle.get(record["sku"], "").split("|")[0] not in ("RETIRE", "EOL"):
            continue
        candidates = []
        for generation in (5, 6, 7):
            targets = compatible_targets(record, generation, allow_unknown_temp_capacity=generation >= 6)
            targets.sort(key=lambda t: (t["vcpu"], t["memory_gb"], t["sku"]))
            candidates.extend(targets[:2])
            preferred = select_path_target(record, generation)[0]
            if preferred and preferred not in candidates:
                candidates.append(preferred)
        if candidates:
            sources[record["sku"]] = [target["sku"] for target in candidates]
        for target in candidates:
            name = target["sku"]
            specs[name] = [
                int(name.rsplit("_v", 1)[1]), target["cpu_arch"],
                target.get("cpu_vendor") or "Unknown - vendor certification required",
                target["vcpu"], target["memory_gb"], target.get("temp_disk_gb"),
                "Present - capacity unverified" if target.get("has_temp_disk") and target.get("temp_disk_gb") is None
                else "Present" if target.get("has_temp_disk") else "None",
                target["nvme_required"], target["hyperv_generations"], target.get("premium_io")]
    return sources, specs


def upgrade_path_panel(generation, lookups):
    sku_map, _, _, regions, region_ids = lookups
    query = base_query(
        sku_map, regions, region_ids, include_optional=False, use_lookup_parameters=True,
        lookup_parameters=PATH_LOOKUP_PARAMETERS[generation])
    query += os_eligibility_query()
    query += """
| extend ImageProvenance = case(
        isnotempty(ImageId), 'Custom/gallery image - verify guest drivers',
        isnotempty(ImagePublisher), 'Marketplace origin - guest changes unverified',
        'Unknown image lineage - verify guest drivers')
| extend OsEvidence = case(
        isnotempty(GuestOsName), strcat('Guest OS metadata: ', GuestOsName),
        OsType =~ 'Windows' and ImageSku contains '2016', 'Server 2016 image hint - current guest OS unverified',
        'Guest OS version unverified')
| extend EncryptionEvidence = iff(DiskAdeConfigured == true,
        'OS disk encryption configuration detected - guest state unverified',
        'Guest BitLocker/ADE state unverified')
| extend ConversionOsCheck = case(
        OsType !~ 'Windows' or NeedsGen2 == 0, '',
        GuestOsName contains '2016', '; Server 2016 detected: OS upgrade/rebuild before documented Trusted Launch conversion',
        isempty(GuestOsName) and ImageSku contains '2016', '; Server 2016 image hint: confirm guest version; upgrade/rebuild if still 2016 before documented Trusted Launch conversion',
        '; Verify Windows OS eligibility for the chosen conversion route')
| extend ConversionEncryptionCheck = case(
        OsType !~ 'Windows' or NeedsGen2 == 0, '',
        DiskAdeConfigured == true, '; Encryption config detected: address Windows OS encryption before documented Trusted Launch conversion',
        '; Guest encryption unknown: check BitLocker/ADE before conversion')
| extend Prerequisites = iff(isempty(RecommendedTarget),
        'No compatible target in this generation in the captured catalogue - manual selection required',
        strcat('Generation ', VmGeneration, ': ', Blockers,
            ConversionOsCheck, ConversionEncryptionCheck,
            iff(NeedsGen2 == 1, '; Convert/rebuild to Gen2; validate partition layout and chosen route; Backup/ASR prerequisites apply to Trusted Launch conversion', ''),
            iff(PenNvme > 0 and isnotempty(DiskControllers) and DiskControllers !contains 'NVMe',
                '; Current OS disk does not advertise NVMe - validate image/disk/boot-driver compatibility', '')))
| extend Validation = strcat('Catalogue region: ', TargetInRegion, '. ', OsEvidence, '. ',
        EncryptionEvidence, '. ', ImageProvenance,
        '. ', NvmeOsHint, '. ', ManaOsHint,
        '. Unverified: MANA/traffic, target policy/capacity, storage IOPS/NIC limits, application/rollback. ',
        'Use Plan > Capacity & quota with the matching target scenario; the default V5-first result is not a V6/V7-path quota approval.',
        iff(WarnCodes has 'l', ' Target local-disk capacity unknown: confirm before choosing this target.', ''))
| extend Resource = strcat(Name, ' [', VmSize, ']'),
         Target = iff(isempty(RecommendedTarget), 'Manual selection required', RecommendedTarget),
         ConversionRequired = iff(isempty(RecommendedTarget) or VmGeneration == 'Unknown', tobool(''), NeedsGen2 == 1),
         NvmeRequired = iff(isempty(RecommendedTarget), tobool(''), PenNvme > 0)
| extend TargetSelection = iff(isempty(RecommendedTarget), 'Not met', 'Met'),
         GenerationSupport = case(isempty(RecommendedTarget) or VmGeneration == 'Unknown', 'Unknown',
             NeedsGen2 == 1, 'Not met', 'Met'),
         ConversionOS = case(isempty(RecommendedTarget), 'Unknown',
             isempty(OsType) and WarnCodes has 'g' and VmGeneration != 'V2', 'Unknown',
             not(WarnCodes has 'g') or VmGeneration == 'V2' or OsType !~ 'Windows', 'Not applicable',
             VmGeneration == 'Unknown', 'Unknown',
             GuestOsName contains '2016', 'Not met', 'Unknown'),
         ConversionEncryption = case(isempty(RecommendedTarget), 'Unknown',
             isempty(OsType) and WarnCodes has 'g' and VmGeneration != 'V2', 'Unknown',
             not(WarnCodes has 'g') or VmGeneration == 'V2' or OsType !~ 'Windows', 'Not applicable',
             VmGeneration == 'Unknown', 'Unknown',
             DiskAdeConfigured == true, 'Not met', 'Unknown'),
         NvmeBoot = case(isempty(RecommendedTarget), 'Unknown',
             PenNvme == 0, 'Not applicable',
             isnotempty(DiskControllers) and DiskControllers !contains 'NVMe', 'Not met', 'Unknown'),
         ManaNetworking = 'Unknown',
         Architecture = case(isempty(RecommendedTarget), 'Unknown', PenArch > 0, 'Not met', 'Met'),
         MemoryCapacity = case(isempty(RecommendedTarget) or isnull(toreal(TargetMemoryGB)) or isnull(CurrentMemoryGB), 'Unknown',
             PenMem > 0, 'Not met', 'Met'),
         LocalDisk = case(isempty(RecommendedTarget) or WarnCodes has 'l', 'Unknown',
             PenTemp > 0, 'Not met', 'Unknown'),
         CatalogueRegion = case(TargetInRegion == 'Yes', 'Met', TargetInRegion == 'No', 'Not met', 'Unknown'),
         ZonePlacement = iff(isempty(Zone), 'Not applicable', 'Unknown'),
         AvailabilitySet = iff(isempty(AvSetId), 'Not applicable', 'Unknown'),
         ScaleSetModel = iff(IsVmss, 'Unknown', 'Not applicable'),
         DiskLimits = 'Unknown', NicLimits = 'Unknown',
         Quota = 'Unknown', Policy = 'Unknown', AllocationCapacity = 'Unknown',
         Backup = 'Unknown',
         AsrConversion = case(isempty(RecommendedTarget), 'Unknown',
             not(WarnCodes has 'g') or VmGeneration == 'V2', 'Not applicable', 'Unknown'),
         ApplicationValidation = 'Unknown', Rollback = 'Unknown'
"""
    query += "\n| extend ChecksNotMet = " + " + ".join(
        f"iff({name} == 'Not met', 1, 0)" for name in ASSESSMENT_CHECKS)
    query += ",\n         ChecksUnknown = " + " + ".join(
        f"iff({name} == 'Unknown', 1, 0)" for name in ASSESSMENT_CHECKS)
    query += """
| extend Review = 'All 22 checks'
| project Resource, Target, GenerationSupport, NvmeBoot, ManaNetworking,
          ChecksNotMet, ChecksUnknown, Review, TargetSelection, ConversionOS,
          ConversionEncryption, Architecture, MemoryCapacity,
          LocalDisk, CatalogueRegion, ZonePlacement, AvailabilitySet, ScaleSetModel,
          DiskLimits, NicLimits, Quota, Policy, AllocationCapacity, Backup, AsrConversion,
          ApplicationValidation, Rollback,
          Prerequisites, Validation, ResourceId,
          VmGeneration, ConversionRequired, NvmeRequired, ImageProvenance, OsEvidence, EncryptionEvidence,
          OsHintSource, NvmeOsHint, ManaOsHint, NvmeDriverValidation, ManaDriverValidation,
          NvmeOsReference, ManaOsReference, OsReferenceChecked
| order by Resource asc"""
    guidance = (
        "Prefer a catalogued non-NVMe v5 target. Gen1 conversion is required only "
        "if that selected target is Gen2-only; do not assume decryption or an OS "
        "upgrade is mandatory for every v5 resize."
        if generation == 5 else
        f"Evaluate a catalogued v{generation} target independently. Gen2-only and NVMe requirements "
        "come from that target's metadata, not the version label alone. Rehearse boot "
        "from the actual legacy image/disk before rollout.")
    checklist = (
        "**Before a v5 resize**\n\n"
        "- Confirm the target supports the actual VM generation and existing controller.\n"
        "- Validate MANA/accelerated networking on target hardware independently of NVMe.\n"
        "- Check OS/image support, ADE restrictions, disk/NIC limits and local-disk capacity/performance.\n"
        "- Check subscription quota, restrictions, policy, zone/cluster availability and downtime.\n"
        "- Take recoverable backups; test applications and rollback.\n\n"
        "Gen1, Server 2016 and ADE are not blanket v5 blockers."
        if generation == 5 else
        f"**Before a v{generation} migration**\n\n"
        "- For a Gen2-only target, convert or rebuild Gen1 guests; confirm supported OS and partition/EFI layout.\n"
        "- For [Gen1-to-Trusted-Launch conversion](https://learn.microsoft.com/azure/virtual-machines/trusted-launch-existing-vm-gen-1), "
        "check Server 2016 exclusion, Windows OS encryption, Backup policy and ASR prerequisites. Rebuilding is a separate route.\n"
        "- If required, validate NVMe image/disk capability, boot-critical driver and an actual converted-disk boot.\n"
        "- Validate MANA hardware, driver and traffic separately; custom image origin is not proof of a missing driver.\n"
        "- Confirm local-disk capacity/performance, disk/NIC limits, quota/policy/zone/cluster availability, applications and rollback.")
    hidden = ("ResourceId", "Prerequisites", "Validation", "VmGeneration", "ConversionRequired", "NvmeRequired",
              "ImageProvenance", "OsEvidence", "EncryptionEvidence", "OsHintSource",
              "NvmeOsHint", "ManaOsHint", "NvmeDriverValidation", "ManaDriverValidation",
              "NvmeOsReference", "ManaOsReference", "OsReferenceChecked") + tuple(
                  name for name in ASSESSMENT_CHECKS
                  if name not in ("GenerationSupport", "NvmeBoot", "ManaNetworking"))
    return {
        "type": 12,
        "name": f"path-v{generation}-panel",
        "customWidth": "100",
        "content": {
            "version": "NotebookGroup/1.0", "groupType": "editable",
            "items": [
                text_item(f"### V{generation} upgrade path\n\n{guidance}", f"path-v{generation}-intro"),
                arg_query(
                    query, f"path-v{generation}-grid", size=1, export=True,
                    title=f"V{generation} targets - select All 22 checks for per-VM details",
                    grid=grid_settings(
                        {"Resource": "20%", "Target": "19%", "GenerationSupport": "10%",
                         "NvmeBoot": "9%", "ManaNetworking": "9%", "ChecksNotMet": "9%",
                         "ChecksUnknown": "9%", "Review": "15%"},
                        [fmt("Resource", 1, formatOptions={"linkColumn": "ResourceId",
                                                         "linkTarget": "Resource",
                                                         "linkIsContextBlade": True}),
                         fmt("Review", 1, formatOptions={"linkTarget": "GenericDetails",
                                                         "linkIsContextBlade": True})]
                        + [fmt(name, 18, formatOptions={"thresholdsOptions": "colors",
                             "thresholdsGrid": [
                                 {"operator": ">", "thresholdValue": 0, "representation": color,
                                  "text": "{0}{1}"},
                                 {"operator": "Default", "representation": "gray", "text": "{0}{1}"}]})
                           for name, color in (("ChecksNotMet", "red"), ("ChecksUnknown", "orange"))]
                        + [fmt(name, 5) for name in hidden],
                        filter=True)),
                text_item(
                    "**Status legend:** Met = observed/captured evidence meets this specific check; "
                    "Not met = observed mismatch; Unknown = validation evidence missing; "
                    "Not applicable = this target/route does not trigger the check. "
                    "Use **All 22 checks** for vertically listed statuses/evidence, or export "
                    "for every prerequisite column. See the column key below. Catalogue matches "
                    "are not guest certification or a live capacity guarantee. The OS/encryption "
                    "checks describe the documented Trusted Launch conversion route, not every rebuild.\n\n"
                    + checklist, f"path-v{generation}-checklist"),
            ],
        },
    }


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def discovery_items():
    parameters = {"type": 9, "name": "discovery-parameters", "content": {
        "version": "KqlParameterItem/1.0", "style": "pills", "parameters": [{
            "version": "KqlParameterItem/1.0", "name": "DiscoveryWorkload",
            "label": "Workload signal", "type": 2, "isRequired": True, "value": "All",
            "jsonData": json.dumps([{"value": value, "label": value} for value in (
                "All", "AKS", "Databricks", "AVD", "SQL", "SAP", "Batch",
                "ARO", "Managed", "Unclassified")])}]
        + [{"version": "KqlParameterItem/1.0", "name": name, "type": 1,
            "value": "", "isHiddenWhenLocked": True}
           for name in ("BatchAccountId", "BatchSelectedWorkload")]}}
    services = arg_query(
        service_inventory_query(), "discovery-services", size=1, export=True,
        title="Services, including registrations without visible backing compute (select a Batch account for live pools)",
        grid=grid_settings({"Name": "28ch", "Workload": "18ch", "ResourceGroup": "24ch"},
                           [fmt("ResourceId", 5)], filter=True))
    services["content"]["exportedParameters"] = [
        {"fieldName": field, "parameterName": parameter, "parameterType": 1}
        for field, parameter in (("ResourceId", "BatchAccountId"), ("Workload", "BatchSelectedWorkload"))]
    batch_request = {
        "version": "ARMEndpoint/1.0", "method": "GETARRAY", "data": None, "headers": [],
        "path": "{BatchAccountId}/pools?api-version=2024-07-01",
        "urlParams": [], "batchDisabled": False,
        "transformers": [{"type": "jsonpath", "settings": {
            "tablePath": "$", "columns": [
                {"path": path, "columnid": column} for column, path in (
                    ("Pool", "$.name"), ("VmSize", "$.properties.vmSize"),
                    ("DedicatedNow", "$.properties.currentDedicatedNodes"),
                    ("SpotNow", "$.properties.currentLowPriorityNodes"),
                    ("DedicatedTarget", "$.properties.scaleSettings.fixedScale.targetDedicatedNodes"),
                    ("SpotTarget", "$.properties.scaleSettings.fixedScale.targetLowPriorityNodes"),
                    ("Autoscale", "$.properties.scaleSettings.autoScale.formula"))]}}],
    }
    return [
        parameters,
        text_item(
            "## Workload ownership and observed evidence\n\n"
            "Identify the owning service before choosing a resize or rebuild workflow. "
            "**No application complexity or migration approval is inferred from a platform score.** "
            "Owner associations, SQL registrations and SAP hints are discovery evidence, not proof "
            "of running software, healthy HA, stateless nodes or vendor certification. Use the "
            "table filter for resource groups and individual resources; export for full evidence.\n\n"
            "**Why Batch may be absent from the VM ownership grid:** it only contains VM/VMSS "
            "resources visible in the selected subscriptions. In **Batch service allocation mode**, "
            "pool VMs run in Microsoft-managed subscriptions, not your Compute inventory. "
            "Use **Service-owned pool configurations** below and **Live Batch pools (ARM)**. "
            "An absent VM row does not mean Batch is unowned or undiscovered.\n\n"
            "The compute detail includes Flexible members and scale-set models: **do not sum "
            "these rows for fleet capacity**. The quota and coverage inventories count models once. "
            "Missing NIC/disk visibility is Unknown, not a passed check. Identity and Marketplace "
            "plans are rebuild dependencies, not blanket resize blockers.\n\n"
            "OS hints use a deliberately limited subset of the official NVMe/MANA lists, checked "
            "18 November 2026. Other distributions/versions remain **Review**, not unsupported. "
            "Guest metadata takes precedence over old image labels; neither proves driver or boot readiness. "
            "Windows Server 2019 shared-disk NVMe compatibility needs separate review.",
            "discovery-intro"),
        arg_query(compute_evidence_query(), "discovery-compute", size=1, export=True,
                  title="Observed subscription VM / VMSS ownership and prerequisites",
                  grid=grid_settings(
                      {"Name": "22ch", "Workload": "24ch", "MigrationMethod": "65ch",
                       "CatalogueCoverage": "22ch", "ResourceGroup": "22ch"},
                      [fmt("ResourceId", 5)] + [fmt(field, 5) for field in (
                          "SubscriptionId", "OwnerIds", "AvdHostPoolIds", "SqlGroupIds",
                          "NvmeOsReference", "ManaOsReference", "OsReferenceChecked",
                          "NvmeDriverValidation", "ManaDriverValidation", "AdeDiskRows",
                          "ApplianceEvidence", "PlacementEvidence", "DiskVisibilityComplete",
                          "NicVisibilityComplete", "ProvisionedDiskIops", "ProvisionedDiskMBps")], filter=True)),
        text_item(
            "### Service-owned pool configurations\n\n"
            "This is the ownership view for **Batch service-mode compute**, including pools with "
            "zero nodes. Each Batch pool belongs to its Batch account; use the **Batch pool/task "
            "migration workflow**, not a direct VM resize. AKS pools are also shown as service-owned "
            "configuration. These are **pool records, not additional VM/VMSS rows**; do not add them "
            "to fleet totals or subscription Compute quota demand.\n\n"
            "Configured sizes and lifecycle classifications remain relevant when nodes are stopped. "
            "**Autoscaler maxima, surge and Batch demand are not automatically added to the existing "
            "Compute quota plan**; validate service-specific quotas and peak capacity separately. "
            "**Resource Graph may omit Batch pools even when ARM lists them.** Empty/missing pool "
            "profiles do not prove that the service has no nodes. Select a Batch account in "
            "**Independent service registrations** to inspect its pools directly through ARM below.",
            "discovery-pool-intro"),
        arg_query(configured_pools_query(), "discovery-pools", size=1, export=True,
                  title="Service-owned AKS / Batch pools, lifecycle and migration method (not VM inventory)",
                  grid=grid_settings({"Service": "28ch", "Pool": "20ch", "VmSize": "22ch"},
                                     [fmt("ResourceId", 5)], filter=True)),
        text_item(
            "### Independent service registrations\n\n"
            "These registrations remain visible without observable backing VMs. Counts are not "
            "additive to compute inventory. A service's location need not be its VM location. "
            "AVD host-pool type is shown here; SQL groups do not prove active Always On membership. "
            "Serverless/terminated Databricks compute and guest application roles are not enumerated.",
            "discovery-service-intro"),
        services,
        text_item(
            "### Live Batch pools (ARM)\n\n"
            "**Select a Batch account row above** to read its pool configurations directly from "
            "Azure Resource Manager, independently of Resource Graph pool indexing. This read-only "
            "inspection requires pool read permission. Empty output or an API error is **not proof "
            "of zero pools**; check the selected account and permissions.\n\n"
            "Use the **Batch pool/task migration workflow**, not a direct VM resize. These records "
            "are not additional subscription VMs and are not additive to fleet totals. Dedicated "
            "and Spot counts show current and fixed-scale targets; blank targets can indicate "
            "autoscaling, whose formula is shown separately. Configured sizes are evidence, not "
            "migration eligibility; this direct inspection does not apply the ARG lifecycle map.",
            "discovery-batch-live-intro"),
        {"type": 3, "name": "discovery-batch-live", "conditionalVisibility": {
            "parameterName": "BatchSelectedWorkload", "comparison": "isEqualTo", "value": "Batch"},
         "content": {"version": "KqlItem/1.0", "queryType": 12, "query": json.dumps(batch_request),
                     "title": "Live Batch pools (ARM) - {BatchAccountId}",
                     "size": 1, "visualization": "table", "showExportToExcel": True,
                     "noDataMessage": "Unknown: no pool rows returned. Check account selection, permissions and the ARM request; do not infer zero pools.",
                     "gridSettings": grid_settings(
                         {"Pool": "22ch", "VmSize": "22ch", "Autoscale": "40ch"}, [], filter=True)}},
    ]


def coverage_query():
    return inventory_query() + """
| extend CatalogueRecord = tostring(parse_json('{LifecycleSkuMap}')[VmSize])
| extend CatalogueCoverage = iff(isempty(CatalogueRecord), 'Not assessed - SKU absent from captured catalogue',
         strcat('Captured classification: ', tostring(split(CatalogueRecord, '|')[0])))
| summarize Resources = count(), Instances = sum(Instances) by VmSize, CatalogueCoverage
| order by CatalogueCoverage asc, Instances desc"""


def live_access_query():
    return """resources
| take 1
| project SkuJson = tostring(dynamic({LiveSkuRecord:json})),
          CheckedSubscription = '{LiveSubscription}', CheckedRegion = tolower('{LiveRegion}'),
          CheckedZone = '{LiveZone}', CheckedTarget = '{LiveTarget}'
| extend Sku = parse_json(SkuJson)
| extend LocationRows = iff(array_length(Sku.locationInfo) > 0, Sku.locationInfo, dynamic([{}]))
| mv-expand LocationRow = LocationRows limit 2000
| summarize AdvertisedZoneEntries = countif(
        tostring(LocationRow.location) =~ CheckedRegion and
        array_index_of(LocationRow.zones, CheckedZone) >= 0)
    by SkuJson, CheckedSubscription, CheckedRegion, CheckedZone, CheckedTarget
| extend Sku = parse_json(SkuJson), RegionalPlacement = CheckedZone =~ 'Regional'
| extend RecordMatches = tostring(Sku.name) =~ CheckedTarget and isnotempty(CheckedTarget)
                            and tostring(Sku.resourceType) =~ 'virtualMachines'
                            and isnotempty(CheckedSubscription) and isnotempty(CheckedRegion)
                            and isnotempty(CheckedZone),
         RegionAdvertised = coalesce(array_index_of(parse_json(tolower(tostring(Sku.locations))), CheckedRegion) >= 0, false),
         RestrictionsKnown = isnotnull(array_length(Sku.restrictions)) and array_length(Sku.restrictions) <= 2000,
         HasRestrictions = coalesce(array_length(Sku.restrictions) > 0, false)
| extend RestrictionRows = iff(HasRestrictions, Sku.restrictions, dynamic([{}]))
| mv-expand Restriction = RestrictionRows limit 2000
| extend RestrictionType = tostring(Restriction.type),
         InfoLocations = parse_json(tolower(tostring(Restriction.restrictionInfo.locations))),
         ValueLocations = parse_json(tolower(tostring(Restriction['values'])))
| extend ScopeLocations = iff(array_length(InfoLocations) > 0, InfoLocations,
                              iff(RestrictionType =~ 'Location', ValueLocations, dynamic(null)))
| extend ScopeKnown = coalesce(array_length(ScopeLocations) > 0, false),
         ScopeMatches = coalesce(array_index_of(ScopeLocations, CheckedRegion) >= 0, false),
         MalformedLocationScope = RestrictionType =~ 'Location' and (
             (isnotempty(tostring(Restriction.restrictionInfo.locations)) and isnull(array_length(InfoLocations))) or
             (isnotempty(tostring(Restriction['values'])) and isnull(array_length(ValueLocations)))),
         ConflictingLocations = coalesce(RestrictionType =~ 'Location' and array_length(InfoLocations) > 0
             and array_length(ValueLocations) > 0
             and ((array_index_of(InfoLocations, CheckedRegion) >= 0) !=
                  (array_index_of(ValueLocations, CheckedRegion) >= 0)), false),
         ZonesKnown = coalesce(array_length(Restriction.restrictionInfo.zones) > 0, false),
         ZoneMatches = coalesce(array_index_of(Restriction.restrictionInfo.zones, CheckedZone) >= 0, false)
| extend RegionBlocked = HasRestrictions and RestrictionType =~ 'Location'
                            and ScopeKnown and ScopeMatches and not(ConflictingLocations) and not(MalformedLocationScope),
         ZoneBlocked = HasRestrictions and RestrictionType =~ 'Zone' and not(RegionalPlacement)
                            and ScopeKnown and ScopeMatches and ZonesKnown and ZoneMatches,
         UncertainRestriction = HasRestrictions and (
             RestrictionType !in~ ('Location', 'Zone') or
             (RestrictionType =~ 'Location' and (not(ScopeKnown) or ConflictingLocations or MalformedLocationScope)) or
             (RestrictionType =~ 'Zone' and not(RegionalPlacement) and
                 (not(ScopeKnown) or (ScopeMatches and not(ZonesKnown)))))
| summarize RegionalBlocks = countif(RegionBlocked), ZonalBlocks = countif(ZoneBlocked),
            UncertainRestrictions = countif(UncertainRestriction),
            QuotaBlocks = countif((RegionBlocked or ZoneBlocked) and tostring(Restriction.reasonCode) =~ 'QuotaId')
    by CheckedSubscription, CheckedRegion, CheckedZone, CheckedTarget,
       RecordMatches, RegionAdvertised, RestrictionsKnown, AdvertisedZoneEntries, RegionalPlacement
| extend AccessStatus = case(
        not(RecordMatches), 'Unknown',
        RegionalBlocks > 0 or ZonalBlocks > 0, 'Restricted',
        not(RestrictionsKnown) or UncertainRestrictions > 0 or not(RegionAdvertised)
            or (not(RegionalPlacement) and AdvertisedZoneEntries == 0), 'Unknown',
        'Not restricted')
| extend Reason = case(
        not(RecordMatches), 'The current SKU check did not return a matching record. No access decision is available.',
        RegionalBlocks > 0, 'This subscription has an access restriction for the selected SKU in this region.',
        ZonalBlocks > 0, 'This subscription has an access restriction for the selected SKU in this zone.',
        not(RestrictionsKnown) or UncertainRestrictions > 0, 'Restriction information is missing or cannot be safely interpreted.',
        not(RegionAdvertised), 'The selected region is not advertised by this SKU record; access cannot be confirmed.',
        not(RegionalPlacement) and AdvertisedZoneEntries == 0, 'The selected zone is not advertised by this SKU record; access cannot be confirmed.',
        'No applicable region or zone access restriction was returned.')
| extend NextStep = case(
        AccessStatus == 'Restricted' and QuotaBlocks > 0, 'Request a quota increase for this SKU/family and region; see Plan > Capacity & quota.',
        AccessStatus == 'Restricted', 'Open a region/zone access support request for this subscription, target SKU and placement.',
        AccessStatus == 'Not restricted', 'No access ticket indicated. Continue with quota, policy and migration checks.',
        'Retry the check and verify permissions/placement. If unresolved, contact Azure support; do not treat this as clear.'),
         Help = iff(AccessStatus == 'Restricted', 'Open support request',
                    iff(AccessStatus == 'Unknown', 'Troubleshooting', '')),
         HelpUrl = case(AccessStatus == 'Restricted',
             'https://portal.azure.com/#view/Microsoft_Azure_Support/HelpAndSupportBlade/~/overview',
             AccessStatus == 'Unknown', 'https://learn.microsoft.com/azure/azure-resource-manager/troubleshooting/error-sku-not-available',
             '')
| project AccessStatus, Reason, NextStep, Help, HelpUrl,
          CheckedSubscription, CheckedRegion, CheckedZone, CheckedTarget"""


def eligibility_items(base):
    source = arg_query(base + """
| extend PathRecord = tostring(case('{EligibilityGeneration}' == '5',
         parse_json('{V5PathSkuMap}')[VmSize], '{EligibilityGeneration}' == '6',
         parse_json('{V6PathSkuMap}')[VmSize], parse_json('{V7PathSkuMap}')[VmSize]))
| extend SelectedTargetSku = tostring(split(PathRecord, '|')[3]), SourceSubscriptionId = SubscriptionId,
         SourceRegion = Region,
         PlacementOptions = iff(isnull(PlacementZones) or array_length(PlacementZones) == 0,
                                dynamic(['Regional']), PlacementZones)
| mv-expand SourceZone = PlacementOptions limit 2000
| extend SourceZone = tostring(SourceZone),
         ZoneInstances = iff(array_length(PlacementZones) > 1, tolong(''), tolong(Instances))
| summarize AffectedResources = count(), KnownInstances = sum(ZoneInstances),
            MultiZoneModels = countif(array_length(PlacementZones) > 1),
            UnknownInstanceResources = countif(isnull(ZoneInstances) or ZoneInstances < 0)
    by SourceSubscriptionId, SourceRegion, SourceZone, SelectedTargetSku
| extend AffectedInstances = iff(UnknownInstanceResources > 0, tolong(''), KnownInstances)
| project SourceSubscriptionId, SourceRegion, SourceZone, SelectedTargetSku,
          AffectedResources, AffectedInstances, MultiZoneModels, UnknownInstanceResources
| order by SourceSubscriptionId asc, SourceRegion asc, SourceZone asc, SelectedTargetSku asc""",
        "eligibility-sources", size=1, export=True,
        title="Select one subscription / region / zone / target SKU combination",
        grid=grid_settings({"SourceSubscriptionId": "27%", "SourceRegion": "12%", "SourceZone": "10%",
                            "SelectedTargetSku": "25%", "AffectedResources": "13%", "AffectedInstances": "13%"},
                           [fmt("MultiZoneModels", 5), fmt("UnknownInstanceResources", 5)], filter=True,
                           labelSettings=[
                               {"columnId": "SourceSubscriptionId", "label": "Subscription"},
                               {"columnId": "SourceRegion", "label": "Region"},
                               {"columnId": "SourceZone", "label": "Zone / regional"},
                               {"columnId": "SelectedTargetSku", "label": "Target SKU"},
                               {"columnId": "AffectedResources", "label": "Resources"},
                               {"columnId": "AffectedInstances", "label": "Instances (blank = unknown)"}]))
    source["content"]["exportedParameters"] = [
        {"fieldName": field, "parameterName": parameter, "parameterType": 1}
        for field, parameter in (("SourceSubscriptionId", "LiveSubscription"), ("SourceRegion", "LiveRegion"),
                                 ("SelectedTargetSku", "LiveTarget"), ("SourceZone", "LiveZone"))]
    arm = {
        "version": "ARMEndpoint/1.0", "method": "GETARRAY", "data": None, "headers": [],
        "path": "/subscriptions/{LiveSubscription}/providers/Microsoft.Compute/skus"
                "?api-version=2021-07-01&$filter=location eq '{LiveRegion}'",
        "urlParams": [], "batchDisabled": False,
        "transformers": [{"type": "jsonpath", "settings": {
            "tablePath": "$[?(@.resourceType=='virtualMachines' && @.name=='{LiveTarget}')]",
            "columns": [
                {"path": path, "columnid": column} for column, path in (
                    ("SkuRecord", "$"), ("Target", "$.name"), ("Family", "$.family"), ("Locations", "$.locations"),
                    ("LocationAndZones", "$.locationInfo"), ("Restrictions", "$.restrictions"),
                    ("Capabilities", "$.capabilities"))]}}],
    }
    merge_id = "e0dc6ed2-412d-49ca-8259-335a03ce4188"
    record_parameter = {
        "version": "KqlParameterItem/1.0", "name": "LiveSkuRecord", "type": 1,
        "isHiddenWhenLocked": True, "value": "", "queryType": 7,
        "query": json.dumps({
            "version": "Merge/1.0",
            "merges": [{"id": merge_id, "mergeType": "table", "leftTable": "eligibility-live"}],
            "projectRename": [{"originalName": "[eligibility-live].SkuRecord",
                               "mergedName": "SkuRecord", "fromId": merge_id}],
        }),
    }
    summary = arg_query(
        live_access_query(), "eligibility-status", size=1, export=True,
        title="Does this subscription need SKU region/zone access?",
        grid=grid_settings(
            {"AccessStatus": "15%", "Reason": "30%", "NextStep": "40%", "Help": "15%"},
            [fmt("AccessStatus", 18, formatOptions={"thresholdsOptions": "colors",
                 "thresholdsGrid": [
                     {"operator": "==", "thresholdValue": status, "representation": color,
                      "text": "{0}{1}"}
                     for status, color in (("Restricted", "red"), ("Not restricted", "green"),
                                           ("Unknown", "orange"))]
                 + [{"operator": "Default", "representation": "orange", "text": "Unknown"}]}),
             fmt("Help", 1, formatOptions={"linkTarget": "Url", "linkColumn": "HelpUrl"})]
            + [fmt(name, 5) for name in ("HelpUrl", "CheckedSubscription", "CheckedRegion",
                                        "CheckedZone", "CheckedTarget")],
            labelSettings=[{"columnId": "AccessStatus", "label": "SKU access"}]),
        extra={"noDataMessage": "Unknown: the live access check did not return a result. Retry and check permissions; this is not a green result."})
    raw = {
        "type": 3, "name": "eligibility-live", "conditionalVisibility": {
            "parameterName": "LiveEvidence", "comparison": "isEqualTo", "value": "raw"},
        "content": {"version": "KqlItem/1.0", "queryType": 12, "query": json.dumps(arm),
                    "title": "Advanced: raw Compute SKU evidence",
                    "size": 1, "visualization": "table", "showExportToExcel": True,
                    "noDataMessage": "Unknown: no matching target SKU returned. Check selection, API permissions and region.",
                    "gridSettings": grid_settings(
                        {"Target": "24ch", "Restrictions": "80ch"}, [fmt("SkuRecord", 5)], filter=True)},
    }
    return [
        text_item(
            "## Live target availability\n\n"
            "**Select a subscription / region / zone / target SKU combination.** "
            "Reselect after changing the path.\n\n"
            "**Loading:** access results may take a few seconds; wait before retrying an initial "
            "**Unknown**. Persistent Unknown is not a pass.\n\n"
            "**Green - Not restricted:** no applicable access restriction. "
            "**Red - Restricted:** follow the support/quota action. "
            "**Amber - Unknown:** missing, failed or unadvertised evidence; never a pass. "
            "Empty results are not a pass.\n\n"
            "**Green is SKU access only**, not a quota, Azure Policy, physical-capacity or migration approval. "
            "**Result detail** shows capabilities, the key or raw evidence.\n\n"
            "**Next:** **Plan > Capacity & quota**, with the matching scenario. "
            "This placement selection does not filter fleet quota demand or change recommendations.",
            "eligibility-intro"),
        {"type": 9, "name": "eligibility-parameters", "content": {
            "version": "KqlParameterItem/1.0", "style": "pills", "parameters": [{
                "version": "KqlParameterItem/1.0", "name": "EligibilityGeneration", "type": 2,
                "label": "Inspect upgrade path", "isRequired": True, "value": "5",
                "jsonData": json.dumps([{"value": str(g), "label": f"V{g}"} for g in (5, 6, 7)])},
                {"version": "KqlParameterItem/1.0", "name": "LiveEvidence", "type": 2,
                 "label": "Result detail", "isRequired": True, "value": "summary",
                 "jsonData": json.dumps([{"value": "summary", "label": "Customer summary"},
                                         {"value": "capabilities", "label": "Resource capability checks"},
                                         {"value": "key", "label": "Placement key & interpretation"},
                                         {"value": "raw", "label": "Advanced raw evidence"}])}]
            + [{"version": "KqlParameterItem/1.0", "name": name, "type": 1,
                "value": "", "isHiddenWhenLocked": True}
               for name in ("LiveSubscription", "LiveRegion", "LiveTarget", "LiveZone")]}},
        conditional_text("eligibility-key", "LiveEvidence", "key",
            "### Placement key & interpretation\n\n"
            "**Scope:** each row groups affected resources in the workbook's selected subscriptions "
            "by subscription / region / zone / target SKU. **Regional** means no zone specified, "
            "not support in every zone. Multi-zone scale-set resources repeat across zone rows; "
            "their per-zone instance counts stay Unknown (blank). Missing/invalid model capacity also "
            "keeps the combination's count Unknown. Do not sum these rows as fleet capacity.\n\n"
            "**Restricted:** use the result's support link with the displayed placement. "
            "A quota-coded restriction needs a quota increase instead of a region/zone access request. "
            "Restrictions for other regions/zones do not block this placement. "
            "**Unknown:** retry and verify permissions/placement; a missing target requires manual selection.\n\n"
            "The same read-only Compute SKU response feeds the summary, optional capability checks "
            "and raw evidence. It does not attempt allocation, reserve capacity or update catalogue "
            "recommendations/prerequisite statuses. "
            "See [SKU availability and restrictions]"
            "(https://learn.microsoft.com/rest/api/compute/resource-skus/list) for API details."),
        source,
        text_item(
            "Current selection: target **{LiveTarget}**, subscription **{LiveSubscription}**, "
            "region **{LiveRegion}**, zone / regional **{LiveZone}**.",
            "eligibility-selection"),
        {"type": 12, "name": "eligibility-result", "conditionalVisibility": {
            "parameterName": "LiveTarget", "comparison": "isNotEqualTo", "value": ""},
         "content": {"version": "NotebookGroup/1.0", "groupType": "editable", "items": [
             raw,
             {"type": 9, "name": "eligibility-record-parameter", "content": {
                 "version": "KqlParameterItem/1.0", "style": "pills", "parameters": [record_parameter]}},
             summary,
             {"type": 12, "name": "eligibility-capabilities", "conditionalVisibility": {
                 "parameterName": "LiveEvidence", "comparison": "isEqualTo", "value": "capabilities"},
              "content": {"version": "NotebookGroup/1.0", "groupType": "editable", "items": [
             text_item(
                 "### Per-resource target capability details\n\n"
                 "Attached/model counts are not guest measurements; VMSS instance visibility stays Unknown. "
                 "Disk totals require complete indexed disk evidence: **ceilings, not observed demand**. "
                 "Cached/uncached limits are **not additive**. Quota uses `vCPUs`, not constrained "
                 "`vCPUsAvailable`; CPU architecture is not manufacturer. Blank means Unknown. "
                 "Select **Details** for limits and placement evidence. Count checks do not approve "
                 "performance, zone/PPG/host/reservation placement or application readiness.",
                 "eligibility-fit-intro"),
             arg_query(target_fit_query(compute_evidence_query(filter_workload=False)) + "\n| extend Details = 'Details'",
                       "eligibility-fit", size=1, export=True,
                       title="Resource counts versus live target limits - select Details for capability evidence",
                       grid=grid_settings(
                           {"Resource": "20ch", "DataDisks": "10ch", "MaxDataDisks": "12ch",
                            "DiskCountCheck": "18ch", "Nics": "8ch", "MaxNics": "10ch",
                            "NicCountCheck": "18ch", "Details": "10ch"},
                           [fmt("Details", 1, formatOptions={"linkTarget": "GenericDetails",
                                                            "linkIsContextBlade": True})]
                           + [fmt(name, 5) for name in (
                               "Source", "Target", "ResourceId", "QuotaVcpus", "EffectiveVcpus",
                               "CpuArchitecture", "CpuVendor", "ProvisionedDiskIops", "ProvisionedDiskMBps",
                               "TargetUncachedIops", "TargetUncachedMBps", "TargetCachedIops", "TargetCachedMBps",
                               "Placement", "PerformanceCheck", "CheckedAt")], filter=True)),
              ]}},
         ]}},
    ]


def reservation_items():
    raw = {
        "type": 3, "name": "reservation-live", "conditionalVisibility": {
            "parameterName": "ReservationEvidence", "comparison": "isEqualTo", "value": "raw"},
        "content": {
            "version": "KqlItem/1.0", "queryType": 12, "query": json.dumps({
                "version": "ARMEndpoint/1.0", "method": "GET", "data": None, "headers": [],
                "path": "{ReservationPagePath}", "urlParams": [], "batchDisabled": False,
                "transformers": [{"type": "jsonpath", "settings": {
                    "tablePath": "$", "columns": [{"path": "$", "columnid": "Page"}]}}],
            }),
            "title": "Advanced: reservation API page", "size": 1, "visualization": "table",
            "gridSettings": grid_settings({"Page": "100%"}),
            "noDataMessage": "Unknown: no reservation response. Check API errors and reservation-level permissions.",
        },
    }
    next_page = navigation_item("reservation-next-page", "ReservationPagePath",
                               [("Next reservation page", "{ReservationNextPagePath}")], style="paragraph")
    next_page["conditionalVisibility"] = {
        "parameterName": "ReservationNextPagePath", "comparison": "isNotEqualTo", "value": ""}
    return [
        text_item(
            "## Purchased reservation exposure\n\n"
            "Actual reservation records visible to you in the **current tenant**, not inferred from VMs. "
            "This scope is independent of the VM subscription/lifecycle filters and includes records "
            "without a deployed VM. **Reservation access is separate from subscription Reader access**; "
            "[check permissions](https://learn.microsoft.com/azure/cost-management-billing/reservations/view-reservations).\n\n"
            "**Browse every API page** using Next reservation page, even when a page has no impacted matches. "
            "The default includes impacted and unassessed VM SKUs, including expired/cancelled historical "
            "records. Select Details for term, renewal, applied scope and IDs. "
            "**An empty or access-limited result does not prove no reservations exist.**\n\n"
            "Exposure is a planning signal, not lost value, utilization or VM benefit attribution. "
            "Series cutoffs are not customer expiry dates; pricing signals do not reprice existing RI terms.",
            "reservation-intro"),
        {"type": 9, "name": "reservation-controls", "content": {
            "version": "KqlParameterItem/1.0", "style": "pills", "parameters": [
                {"version": "KqlParameterItem/1.0", "name": "ReservationPagePath", "type": 1,
                 "value": FIRST_PAGE, "isHiddenWhenLocked": True, "isGlobal": True},
                *[{"version": "KqlParameterItem/1.0", "name": name, "type": 2, "label": label,
                   "isRequired": True, "value": default,
                   "jsonData": json.dumps([{"value": value, "label": text} for value, text in choices])}
                  for name, label, default, choices in (
                      ("ReservationView", "Show reservations", "affected",
                       [("affected", "Impacted + unassessed VM SKUs"), ("all", "All visible VM reservations")]),
                      ("ReservationEvidence", "Result detail", "summary",
                       [("summary", "Customer summary"), ("raw", "Advanced raw evidence")]))],
            ]}},
        raw,
        {"type": 9, "name": "reservation-page-parameter", "content": {
            "version": "KqlParameterItem/1.0", "parameters": [
                merge_parameter("ReservationPageRecord", "reservation-live", "Page")]}},
        arg_query(reservation_coverage_query(), "reservation-coverage", size=1,
                  title="Reservation visibility and page coverage",
                  grid=grid_settings({"Evidence": "65%", "RowsInPage": "10%", "Scope": "25%"},
                                     [fmt("NextPagePath", 5)])),
        {"type": 9, "name": "reservation-next-parameter", "content": {
            "version": "KqlParameterItem/1.0", "parameters": [
                merge_parameter("ReservationNextPagePath", "reservation-coverage", "NextPagePath")]}},
        arg_query(reservation_inventory_query(REGIONAL_PRICE_INCREASES, REGIONAL_PRICE_EXCLUDED_SERIES,
                                              REGIONAL_PRICE_V7_EXCLUSION_PATTERN),
                  "reservation-inventory", size=1, export=True, title="Purchased VM reservations - current API page",
                  grid=grid_settings(
                      {"ReservationName": "22ch", "ReservedSku": "22ch", "Region": "16ch",
                       "Quantity": "9ch", "Expiry": "13ch", "State": "15ch", "Exposure": "30ch", "Details": "10ch"},
                      [fmt("Details", 1, formatOptions={"linkTarget": "GenericDetails", "linkIsContextBlade": True})]
                      + [fmt(name, 5) for name in (
                          "Term", "AutoRenew", "TermState", "AppliedScope", "AppliedScopes",
                          "AppliedScopeProperties", "PurchaseDate", "Archived", "Lifecycle", "Series",
                          "RetirementDate", "SeriesRiCutoff", "SeriesPriceSignal", "RegionalPriceSignal", "ReservationId")],
                      filter=True),
                  extra={"noDataMessage": "No matching VM reservations on this API page. Check coverage/errors, view All visible VM reservations and continue through remaining pages; this is not proof of no RI exposure."}),
        next_page,
        navigation_item("reservation-first-page", "ReservationPagePath",
                        [("Return to first reservation page", FIRST_PAGE)], style="paragraph"),
    ]


def build(data):
    sku_map, spec_map, url_map, tgt_regions, region_ids = build_lookups(data)
    path_lookups = {generation: build_lookups(data, path_generation=generation)
                    for generation in (5, 6, 7)}
    shared_lookups = dict(zip(BASE_LOOKUP_PARAMETERS, (sku_map, tgt_regions, region_ids)))
    with open(os.path.join(REFS, "quota-families.json"), encoding="utf-8-sig") as f:
        quota_metadata = json.load(f)
    shared_lookups["QuotaFamilyMap"] = quota_metadata["families"]
    shared_lookups["QuotaCoreMap"] = quota_metadata["vcpus"]
    shared_lookups["CompatibleTargetMap"], shared_lookups["TargetSpecMap"] = compatible_target_maps(data)
    for generation, lookup in path_lookups.items():
        shared_lookups.update(zip(PATH_LOOKUP_PARAMETERS[generation],
                                  (lookup[0], lookup[3], lookup[4])))
    base = base_query(sku_map, tgt_regions, region_ids, use_lookup_parameters=True)
    base_impacted = base + "\n| where Lifecycle != 'OPTIONAL'"

    prereq_bag = kql_bag({
        WARN_SHORT.get(k, k):
            "|".join([v["title"], v["sev"], v["what"], v["why"], v["how"], v["doc"]])
        for k, v in PREREQ.items()
    })
    spec_bag = kql_bag(spec_map)
    url_bag = kql_bag(url_map)

    items = []

    # ---------------- header ----------------
    items.append(text_item(
        f"# {WORKBOOK_TITLE}\n\n"
        "This workbook inventories the virtual machines and scale sets in the "
        "subscriptions you select, identifies which sizes are affected by announced "
        "Azure VM retirements and lifecycle changes, recommends a target size for each, "
        "and scores captured platform compatibility, not application migration readiness.\n\n"
        "**Target policy: v5 first.** Retirement recommendations prefer a catalogued "
        "non-NVMe v5 target that preserves architecture, CPU, memory, local disk "
        "(including known capacity) and "
        "premium-storage support. Newer targets remain alternatives. If no suitable "
        "v5 candidate exists, the newer target is explicitly marked for manual review. "
        "Optional modernization is separate from retirement remediation.\n\n"
        "V5 does not eliminate MANA, guest OS, encryption, quota or application checks. "
        "A high score is not a migration certification. **100 means no scored platform penalty; "
        "guest, application, HA, backup and rollback checks may still be Unknown.** "
        "Use Inventory > Workloads & owners to identify service ownership and stateful workload evidence.\n\n"
        "The platform score uses your **actual** fleet attributes - the real Hyper-V "
        "generation of each OS disk, the region and zone each VM runs in, and whether it "
        "sits in an availability set or scale set - not from size metadata alone.\n\n"
        "> All data stays in your tenant. The workbook runs read-only Azure Resource Graph "
        "and selected-row Compute SKU API queries; it makes no resource changes.",
        "header"))

    items.append({
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": [
                {
                    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"workflow:{name}")),
                    "version": "KqlParameterItem/1.0", "name": name, "type": 1,
                    "isRequired": True, "isHiddenWhenLocked": True, "value": value,
                    "isGlobal": name in ("WorkflowStage", "MigrationRoute", "AcceptancePattern"),
                }
                for name, value in [("WorkflowStage", "start"), ("MigrationRoute", "parallel"),
                                    ("AcceptancePattern", "unknown")] + [
                    (parameter, options[0][1])
                    for parameter, options in WORKFLOW_REPORTS.values()
                ]
            ] + [
                {
                    "id": "b0a1f2c3-1111-4a11-9c01-000000000001",
                    "version": "KqlParameterItem/1.0",
                    "name": "Subscriptions",
                    "label": "Subscriptions",
                    "type": 6,
                    "isRequired": True,
                    "multiSelect": True,
                    "quote": "'",
                    "delimiter": ",",
                    "typeSettings": {"additionalResourceOptions": ["value::all"],
                                     "includeAll": True, "showDefault": False},
                    "value": ["value::all"],
                },
                {
                    "id": "b0a1f2c3-2222-4a22-9c02-000000000002",
                    "version": "KqlParameterItem/1.0",
                    "name": "LifecycleFilter",
                    "label": "Lifecycle status",
                    "type": 2,
                    "isRequired": True,
                    "multiSelect": True,
                    "quote": "",
                    "delimiter": ",",
                    "jsonData": json.dumps([
                        {"value": "RETIRE", "label": "Retirement announced", "selected": True},
                        {"value": "EOL", "label": "End of life", "selected": True},
                        {"value": "OPTIONAL", "label": "Optional modernization"},
                    ]),
                    "value": ["RETIRE", "EOL"],
                    "timeContext": {"durationMs": 86400000},
                    "typeSettings": {"additionalResourceOptions": [], "showDefault": False},
                },
            ],
            "style": "pills",
        },
        "name": "parameters",
    })

    # Store each large literal once. Workbooks expands these before sending KQL
    # to ARG; repeating them in every chart exceeds the saved-workbook limit.
    items.append({
        "type": 9,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": [
                {
                    "id": f"b0a1f2c3-4444-4a44-9c04-{i:012d}",
                    "version": "KqlParameterItem/1.0",
                    "name": name,
                    "type": 1,
                    "isRequired": True,
                    "isHiddenWhenLocked": True,
                    "value": kql_bag(value),
                }
                for i, (name, value) in enumerate(shared_lookups.items(), 1)
            ],
            "style": "pills",
        },
        "name": "lookup-parameters",
    })

    items.append(navigation_item("tabs", "WorkflowStage", WORKFLOW_STAGES))

    items.append(group_item("grp-routes", "routes", [
        text_item("## Choose the supported operation, not just a target SKU\n\n"
                  "Review **Details** for observed ADE, suspected appliance, placement and ownership "
                  "signals. Missing evidence is Unknown, not cleared. Guest OS/encryption and vendor "
                  "certification require customer confirmation. The global lifecycle display filter "
                  "does not hide ownership/dependency evidence here.",
                  "routes-intro"),
        arg_query(compute_evidence_query(filter_workload=False) + "\n| extend Details = 'Details'",
                  "routes-evidence", size=1, export=True,
                  title="Workload route evidence - all observed compute, including unassessed sizes",
                  grid=grid_settings(
                      {"Name": "20ch", "Workload": "24ch", "MigrationMethod": "65ch", "Details": "10ch"},
                      [fmt("Details", 1, formatOptions={"linkTarget": "GenericDetails",
                                                       "linkIsContextBlade": True})]
                      + [fmt(name, 5) for name in (
                          "ResourceGroup", "SubscriptionId", "Region", "VmSize", "CatalogueCoverage",
                          "AcceleratedNetworking", "ExpectedNics", "DataDiskCount", "SharedDiskEvidence",
                          "EphemeralOsDisk", "UnmanagedDiskEvidence", "Hibernation", "SecurityType",
                          "SystemAssignedIdentity", "MarketplacePlan", "AdeEvidence", "AdeDiskRows",
                          "ApplianceEvidence", "PlacementEvidence", "DiskVisibilityComplete",
                          "NicVisibilityComplete", "ProvisionedDiskIops", "ProvisionedDiskMBps",
                          "OsHintSource", "NvmeOsHint", "ManaOsHint", "NvmeDriverValidation",
                          "ManaDriverValidation", "OsReferenceChecked", "NvmeOsReference", "ManaOsReference",
                          "AssociationEvidence", "OwnerIds", "AvdHostPoolIds", "SqlGroupIds",
                          "MigrationApproval", "InventoryRole", "ResourceId")], filter=True)),
        *route_guide_items("assess"),
        text_item(COMMON_GATES, "routes-common-gates"),
    ]))
    items.append(group_item("grp-alternatives", "alternatives", [
        text_item("## Small compatible target shortlist\n\n"
                  "Up to two closest captured candidates per generation plus that generation's "
                  "preferred path, not every possible Azure size. The **default remains V5-first**. "
                  "Select Details for architecture versus CPU vendor, quota versus effective CPUs, "
                  "memory, local-disk and controller tradeoffs. Candidate filters preserve captured "
                  "CPU architecture, CPU/memory, local-disk presence and known capacity and premium IO. "
                  "V6/V7 unknown local-disk capacity remains an explicit validation gate. "
                  "No target record means no assessed candidate, never a suffix-invented SKU.\n\n"
                  "Vendor and effective CPUs are Unknown if the catalogue does not supply them; "
                  "inspect the live target SKU record and product/vendor certification. "
                  "Use **Plan > Live target availability** for the selected target's "
                  "count/performance ceilings. This is not utilization-based right-sizing, "
                  "service-supported node-type certification or a region/zone/quota approval.",
                  "alternatives-intro"),
        arg_query(alternatives_query() + "\n| extend Details = 'Details'", "target-alternatives",
                  size=1, export=True, title="Captured compatible candidates for retiring/EOL resources",
                  grid=grid_settings(
                      {"Resource": "22ch", "Source": "22ch", "Target": "25ch", "Generation": "10ch",
                       "QuotaVcpus": "10ch", "MemoryGB": "12ch", "Details": "10ch"},
                      [fmt("Details", 1, formatOptions={"linkTarget": "GenericDetails",
                                                       "linkIsContextBlade": True})]
                      + [fmt(name, 5) for name in ("CpuArchitecture", "CpuVendor", "EffectiveVcpus",
                          "LocalDiskGB", "LocalDisk", "NvmeRequired", "HyperVGenerations", "PremiumIO",
                          "Decision", "ResourceId", "Region", "SubscriptionId")], filter=True)),
    ]))
    items.append(group_item("grp-paths", "paths", [
        text_item(
            "## Compare upgrade paths\n\n"
            "The same retiring resources are assessed against **independent V5, V6 and V7 targets**. "
            "No suitable catalogue match means manual selection, not a fallback to another generation. "
            "These panels distinguish observed configuration from prerequisites and **unverified guest readiness**. "
            "They do not resize a VM or certify a migration. Export any grid for the full evidence columns. "
            "Continue to **Plan > Live target availability**, then **Capacity & quota**.",
            "paths-intro"),
        upgrade_path_panel(5, path_lookups[5]),
        upgrade_path_panel(6, path_lookups[6]),
        upgrade_path_panel(7, path_lookups[7]),
        assessment_key(),
    ]))
    items.append(group_item("grp-discovery", "discovery", discovery_items()))
    items.append(group_item("grp-reservations", "reservations", reservation_items()))
    items.append(group_item("grp-availability", "availability", eligibility_items(base_query(
        sku_map, tgt_regions, region_ids, include_optional=False, use_lookup_parameters=True,
        include_placement_zones=True))))
    items.append(group_item("grp-quota", "quota", quota_planning_items(
        base, region_ids, quota_metadata["families"])))

    # ---------------- TAB 1: Overview ----------------
    ov = []
    ov.append(text_item(
        "### Platform score and scored platform changes\n\n"
        "**Platform score** starts at 100, subtracts the selected target's scored platform "
        "penalties and stops at 0. Higher means fewer **scored platform differences**, not "
        "a count of checks passed, application complexity or probability of migration success. "
        "**100 is not migration approval.** Unknown guest drivers, applications, HA, backup "
        "and rollback can remain unverified without reducing the score. Inventory uses the "
        "**default V5-first recommendation**; the live inspector's path selector does not rescore it.\n\n"
        "| Score | Scored platform changes | Example |\n| --- | --- | --- |\n"
        "| 85-100 | Few scored platform changes - validation required | No penalty = 100; NVMe validation alone = 85. |\n"
        "| 60-84 | Moderate scored platform changes | Target absent from the source region snapshot: 100 - 30 = 70. |\n"
        "| 35-59 | Substantial scored platform changes | Gen1 to a Gen2-only target: 100 - 45 = 55. |\n"
        "| 0-34 | Major scored platform changes | Gen1 conversion plus region mismatch: 100 - 45 - 30 = 25. |\n\n"
        "The distribution shows **populated bands only**. Seeing only Few means every matching "
        "resource currently scores 85-100, not that other bands are missing. Gen1, Server 2016 "
        "and ADE can still score highly against a compatible V5 target. Change the target/path "
        "and the checks may differ; do not manufacture a worse target to fill a band.\n\n"
        "**Penalty key:** architecture change 50; Gen1 conversion 45; captured region mismatch 30; "
        "memory reduction 25; temp disk removed 20; unknown generation on a Gen2-only target 20; "
        "NVMe 15; narrow regional footprint 10; zone coverage warning on a zonal source 10; "
        "vCPU change 5; availability-set membership 5; VMSS 5. MANA validation deducts 0 "
        "and must still be tested. See Methodology for evaluation rules.",
        "ov-score-key"))

    ov.append(arg_query(
        base + """
| summarize Impacted = countif(Lifecycle != 'OPTIONAL'),
            Instances = sumif(Instances, Lifecycle != 'OPTIONAL'),
            Retiring = countif(Lifecycle == 'RETIRE'),
            EndOfLife = countif(Lifecycle == 'EOL'),
            Blocked = countif(Lifecycle != 'OPTIONAL' and ReadinessScore < 60),
            ScoreSum = sumif(ReadinessScore, Lifecycle != 'OPTIONAL'),
            SoonestDays = min(iff(DaysToRetirement >= 0, DaysToRetirement, 99999))
| extend Score = iff(Impacted == 0, 100, toint(round(todouble(ScoreSum) / todouble(Impacted), 0)))
| extend SoonestDays = iff(SoonestDays == 99999, -1, SoonestDays)
| extend M = pack_array('Impacted resources', 'Instances affected', 'Retirement announced',
                        'End of life', 'Need prerequisite work', 'Fleet platform score',
                        'Days to next retirement')
| mv-expand Metric = M to typeof(string)
| extend Value = case(
        Metric == 'Impacted resources', Impacted,
        Metric == 'Instances affected', Instances,
        Metric == 'Retirement announced', Retiring,
        Metric == 'End of life', EndOfLife,
        Metric == 'Need prerequisite work', Blocked,
        Metric == 'Fleet platform score', Score,
        SoonestDays)
| extend Detail = case(
        Metric == 'Fleet platform score', case(Score >= 85, 'Few scored differences; validation required',
                                                Score >= 60, 'Validation needed',
                                                Score >= 35, 'Prerequisite work required',
                                                'Significant re-architecture'),
        Metric == 'Days to next retirement', iff(Value < 0, 'No dated retirements', 'days remaining'),
        Metric == 'Need prerequisite work', 'platform score below 60',
        'resources')
| extend Ord = case(
        Metric == 'Impacted resources', 1,
        Metric == 'Instances affected', 2,
        Metric == 'Retirement announced', 3,
        Metric == 'End of life', 4,
        Metric == 'Need prerequisite work', 5,
        Metric == 'Fleet platform score', 6,
        7)
| order by Ord asc
| project Metric, Value, Detail""",
        "ov-tiles", visualization="tiles", size=4, title="Fleet summary",
        tile={"titleContent": {"columnMatch": "Metric", "formatter": 1},
              "leftContent": {"columnMatch": "Value", "formatter": 12,
                              "formatOptions": {"palette": "auto"},
                              "numberFormat": {"unit": 17, "options": {"maximumFractionDigits": 0}}},
              "subtitleContent": {"columnMatch": "Detail", "formatter": 1},
              "showBorder": True, "size": "fixed"}))

    ov.append(arg_query(
        base_impacted + """
| summarize Instances = sum(Instances) by Urgency
| order by Instances desc""",
        "ov-urgency", visualization="piechart", size=1, width="50",
        title="Impacted instances by urgency"))

    ov.append(arg_query(
        base_impacted + """
| summarize Resources = count(), Instances = sum(Instances) by VmGeneration
| order by Instances desc""",
        "ov-gen", visualization="piechart", size=1, width="50",
        title="Impacted resources by actual VM generation"))

    ov.append(arg_query(
        base_impacted + """
| summarize Resources = count(), Instances = sum(Instances), AvgReadiness = round(avg(todouble(ReadinessScore)), 0) by Complexity
| order by AvgReadiness asc""",
        "ov-complexity", visualization="table", size=1,
        title="Scored platform-change distribution - not workload complexity",
        grid=grid_settings(
            {"Complexity": "37%", "Resources": "21%", "Instances": "21%",
             "AvgReadiness": "21%"},
            [
             fmt("AvgReadiness", 8, formatOptions={"palette": "redGreen", "min": 0, "max": 100})],
            labelSettings=[{"columnId": "AvgReadiness", "label": "Avg platform score"},
                           {"columnId": "Complexity", "label": "Scored platform changes"}])))

    ov.append(arg_query(
        base_impacted + """
| summarize Instances = sum(Instances) by Series, PriceUpdate2027, RiStatus
| where PriceUpdate2027 == 'Yes' or RiStatus startswith 'Series'
| order by Instances desc""",
        "ov-commercial", size=1,
        title="Series commercial signals - pricing and RI cutoffs",
        grid=grid_settings(
            {"Series": "17%", "PriceUpdate2027": "24%", "RiStatus": "42%",
             "Instances": "17%"},
            [fmt("PriceUpdate2027", 18, formatOptions={"thresholdsOptions": "icons",
                 "thresholdsGrid": [
                     {"operator": "==", "thresholdValue": "Yes", "representation": "2",
                      "text": "Applies 1 Feb 2027"},
                     {"operator": "Default", "representation": "success",
                      "text": "Not affected"}]}),
             fmt("RiStatus", 18, formatOptions={"thresholdsOptions": "colors",
                 "thresholdsGrid": [
                     {"operator": "contains", "thresholdValue": "cutoff passed",
                      "representation": "orange", "text": "{0}{1}"},
                     {"operator": "Default", "representation": "blue", "text": "{0}{1}"}]})],
            labelSettings=[
                {"columnId": "PriceUpdate2027", "label": "Pricing update"},
                {"columnId": "RiStatus", "label": "Series RI cutoff - customer coverage unverified"},
            ])))

    ov.append(text_item(
        "### What commercial impact means\n\n"
        "These are **series-level planning signals**, not your current bill or reservation inventory.\n\n"
        "- **Pricing update:** the captured catalogue flags legacy series with a 1 February 2027 "
        "price update. Confirm the notification and applicable rate in Service Health/your agreement.\n"
        "- **Series RI cutoff:** a published purchase/renewal cutoff is recorded for the series "
        "(1 July 2026 in the captured catalogue). **Passed** means that date is past; "
        "**Upcoming** means it has not passed; **No cutoff in captured catalogue** means no date "
        "is recorded, not guaranteed RI availability. Affected terms differ by series; see Methodology.\n"
        "- **Coverage not assessed:** this summary does not read your reservations, remaining "
        "terms, expiry dates or negotiated discounts. Use **Inventory > Reservation exposure** "
        "for visible purchased records. A cutoff does not mean an existing RI "
        "expired or the VM is now billed at PAYG.\n"
        "- **Regional price exposure** is a separate report under Inventory > Retirement & pricing. "
        "Modern VMs can appear there without retiring. Do not combine the two signals as a price forecast.",
        "ov-commercial-key"))

    ov.append(arg_query(
        base_impacted + """
| summarize Resources = count(), Instances = sum(Instances), AvgReadiness = round(avg(todouble(ReadinessScore)), 0),
            NextRetirement = min(RetiresOn)
    by VmSize, Series, ActionCategory, RecommendedTarget
| order by Instances desc
| take 25""",
        "ov-topsizes", size=1, export=True, title="Top affected sizes",
        grid=grid_settings(
            {"VmSize": "11%", "Series": "11%", "ActionCategory": "20%",
             "RecommendedTarget": "12%", "Resources": "11%", "Instances": "11%",
             "AvgReadiness": "11%", "NextRetirement": "13%"},
            [fmt("AvgReadiness", 8, formatOptions={"palette": "redGreen", "min": 0, "max": 100}),
             date_col("NextRetirement")],
            labelSettings=[{"columnId": "VmSize", "label": "Current size"},
                           {"columnId": "ActionCategory", "label": "Lifecycle status"},
                           {"columnId": "RecommendedTarget", "label": "Recommended target"},
                           {"columnId": "AvgReadiness", "label": "Avg platform score"},
                           {"columnId": "NextRetirement", "label": "Next retirement"}])))

    items.append(group_item("grp-overview", "overview", ov))

    # ---------------- TAB 2: Impacted resources ----------------
    imp = []
    imp.append(text_item(
        "### VM retirement / end of life\n"
        "Every VM and scale set whose current size carries an announced retirement or "
        "end-of-life status. **Platform score** is 0-100 for scored configuration differences, "
        "not application readiness. **100 is not migration approval.** The listed checks are "
        "not exhaustive: guest drivers, service ownership, HA, backup and rollback still need evidence.",
        "imp-intro"))
    imp.append(arg_query(
        base_impacted + """
| where '{LifecycleFilter}' has Lifecycle
| project Name, ResourceGroup, Region, VmSize, Instances, VmGeneration,
          RecommendedTarget, TargetInRegion, RetiresOn, ReadinessScore,
          Blockers, MigrationApproval, ResourceId, Lifecycle, OfficialLifecycleStage
| order by ReadinessScore asc, RetiresOn asc""",
        "imp-grid", size=1, export=True, title="Retiring virtual machines and scale sets",
        grid=grid_settings(
            # Sized in ch to the actual content. Percentage widths bottom out at
            # roughly 130px per column, which leaves short values like "V1" or
            # "eastus2" swimming in white space; ch units size to the text.
            {"Name": "18ch", "ResourceGroup": "17ch", "Region": "11ch",
             "VmSize": "18ch", "Instances": "12ch", "VmGeneration": "8ch",
             "RecommendedTarget": "21ch", "TargetInRegion": "14ch",
             "RetiresOn": "13ch", "ReadinessScore": "12ch", "Blockers": "32ch"},
            [
                fmt("Name", 1, formatOptions={"linkColumn": "ResourceId",
                                              "linkTarget": "Resource",
                                              "linkIsContextBlade": True}),
                date_col("RetiresOn"),
                fmt("ReadinessScore", 8, formatOptions={"palette": "redGreen",
                                                        "min": 0, "max": 100}),
                fmt("TargetInRegion", 18, formatOptions={"thresholdsOptions": "icons",
                    "thresholdsGrid": [
                        {"operator": "==", "thresholdValue": "No", "representation": "failed",
                         "text": "Not available"},
                        {"operator": "==", "thresholdValue": "Unverified",
                         "representation": "unknown", "text": "Unverified"},
                        {"operator": "Default", "representation": "success",
                         "text": "Available"}]}),
                fmt("ResourceId", 5),
                fmt("Lifecycle", 5), fmt("OfficialLifecycleStage", 5),
            ],
            filter=True,
            sortBy=[{"itemKey": "ReadinessScore", "sortOrder": 1}],
            labelSettings=[
                {"columnId": "ResourceGroup", "label": "Resource group"},
                {"columnId": "VmSize", "label": "Current size"},
                {"columnId": "VmGeneration", "label": "Gen"},
                {"columnId": "RecommendedTarget", "label": "Recommended target"},
                {"columnId": "TargetInRegion", "label": "In region"},
                {"columnId": "RetiresOn", "label": "Retires on"},
                {"columnId": "ReadinessScore", "label": "Platform score"},
            ])))
    imp.append(text_item(
        "### Potential regional price exposure\n\n"
        "This is **independent of retirement** and the Lifecycle status filter: modern VMs "
        "can appear here even when they are not retiring. A resource can appear in both sections. "
        "The retirement summary tiles remain retirement-only.\n\n"
        "**Unverified pricing scope.** The source is a [public Microsoft Learn Q&A post "
        "reproducing a customer notification](" + REGIONAL_PRICE_SOURCE + "), not a formal public "
        "Microsoft announcement. It reports regional changes effective **1 February 2027**: "
        "North Europe 17%; France Central/South 11%; West Europe and Norway East/West 9%; "
        "Southeast Asia 8%. Confirm the authoritative Service Health advisory and agreement "
        "before customer communication or financial decisions.\n\n"
        "The table includes VMs/scale sets in those seven regions, including **reported exclusions** "
        "for context. A reported exclusion applies only to this regional update, not every price "
        "change. Unknown series are not assumed exempt. Percentages describe the reported region "
        "adjustment, **not an applied per-resource increase or an invoice forecast**. This section "
        "does not inventory Storage accounts/meters and is separate from the legacy-series "
        "pricing signal elsewhere in the workbook.",
        "regional-price-intro"))
    imp.append(arg_query(
        regional_price_query(data), "regional-price-grid", size=1, export=True,
        title="VMs in regions with reported pricing changes - potential exposure",
        grid=grid_settings(
            {"Name": "18ch", "ResourceGroup": "18ch", "Region": "15ch", "VmSize": "20ch",
             "Instances": "10ch", "Exposure": "45ch", "AnnouncedRegionalPercent": "17ch",
             "EffectiveDate": "13ch"},
            [fmt("Name", 1, formatOptions={"linkColumn": "ResourceId", "linkTarget": "Resource",
                                          "linkIsContextBlade": True}),
             date_col("EffectiveDate"), fmt("ResourceId", 5),
             fmt("Source", 1, formatOptions={"linkTarget": "Url"})],
            filter=True,
            labelSettings=[
                {"columnId": "VmSize", "label": "Current size"},
                {"columnId": "ResourceGroup", "label": "Resource group"},
                {"columnId": "AnnouncedRegionalPercent", "label": "Reported regional %"},
                {"columnId": "EffectiveDate", "label": "Reported effective date"},
            ])))
    items.append(group_item("grp-impacted", "impacted", imp))

    sql = [text_item(
        "## SQL modernization opportunities on retiring VMs\n\n"
        "These **retiring/end-of-life VMs** have SQL VM registration evidence, a SQL IaaS Agent "
        "extension with publisher **Microsoft.SqlServer.Management** and handler **SqlIaaSAgent**, "
        "or both. VM names and SQL-looking images alone are not evidence. Each VM appears once; "
        "extension/registration provisioning states are retained, including failed or unknown states. "
        "Registration edition, license type, management mode and group associations are shown. "
        "A registered group does not verify active Always On membership or health. Filtering is retirement-only "
        "and independent of the global Lifecycle status selector.\n\n"
        "**Discovery signal, not certification.** Extension or registration presence does not establish that "
        "SQL Server is installed, running, in use or suitable for a managed database service. "
        "A stopped VM remains an assessment candidate; its power state is shown when available. "
        "VMs without either visible signal are **not proven SQL-free**. Limited permissions, "
        "ARG indexing, registration mode and unsupported/unrecognized handlers can cause omissions. "
        "Flexible scale-set member VMs are evaluated individually here; scale-set models and "
        "Uniform instances without individual ARG VM records are not included.\n\n"
        "Confirm engine/version/edition, databases, dependencies (SQL Agent jobs, linked servers, "
        "SSIS/SSRS and cross-database features), HA/DR, performance, licensing and downtime. "
        "Assess **Azure SQL Managed Instance**, **Azure SQL Database**, or supported SQL Server "
        "on modern VMs based on evidence, not extension presence alone.\n\n"
        "[SQL IaaS Agent registration](https://learn.microsoft.com/azure/azure-sql/virtual-machines/windows/sql-agent-extension-manually-register-single-vm) "
        "| [SQL migration guidance](https://learn.microsoft.com/azure/azure-sql/migration-guides/)",
        "sql-intro")]
    sql.append(arg_query(
        sql_modernization_query(sku_map, tgt_regions, region_ids), "sql-modernization-grid",
        size=1, export=True, title="Retiring VMs with SQL extension and/or registration evidence",
        grid=grid_settings(
            {"Name": "20ch", "ResourceGroup": "18ch", "Region": "13ch", "VmSize": "20ch",
             "RetiresOn": "13ch", "PowerState": "12ch", "ExtensionStates": "18ch",
             "Opportunity": "42ch", "Assessment": "55ch"},
            [fmt("Name", 1, formatOptions={"linkColumn": "ResourceId", "linkTarget": "Resource",
                                          "linkIsContextBlade": True}),
             date_col("RetiresOn"), fmt("ResourceId", 5),
             fmt("Guidance", 1, formatOptions={"linkTarget": "Url"})],
            filter=True,
            labelSettings=[{"columnId": "VmSize", "label": "Retiring size"},
                           {"columnId": "ExtensionStates", "label": "Extension state"},
                           {"columnId": "SqlRuntime", "label": "SQL runtime evidence"}])))
    items.append(group_item("grp-sql", "sql", sql))

    # ---------------- TAB 3: Target mapping ----------------
    mapping = []
    mapping.append(text_item(
        "### Recommended target mapping\n"
        "One row per affected size in your fleet. **Recommended target** is the default "
        "same-or-better replacement. **Memory-preserving target** is the alternative when "
        "the default reduces RAM. Always right-size against 30-90 days of real utilisation "
        "rather than the current nameplate.",
        "map-intro"))
    map_base = f"""
| where '{{LifecycleFilter}}' has Lifecycle
| extend SpecNotes = tostring(parse_json('{spec_bag}')[Series])
| extend AnnouncementUrl = tostring(parse_json('{url_bag}')[SeriesGroup])
| summarize Resources = count(), Instances = sum(Instances),
            Regions = strcat_array(make_set(Region, 20), ', '),
            AvgReadiness = round(avg(todouble(ReadinessScore)), 0)
    by VmSize, Series, SeriesGroup, ActionCategory, RetirementDate, CurrentVcpu, CurrentMemoryGB,
       RecommendedTarget, TargetVcpu, TargetMemoryGB, MemoryPreservingTarget, MpVcpu, MpMemoryGB,
       AlternateTargets, SizeConsiderations, SpecNotes, RiEnded, RiStatus, PriceUpdate2027, AnnouncementUrl
| extend Current = strcat(CurrentVcpu, ' vCPU / ', CurrentMemoryGB, ' GB')
| extend Target = strcat(TargetVcpu, ' vCPU / ', TargetMemoryGB, ' GB')
| extend MemoryPreserving = iff(isempty(MemoryPreservingTarget), '-',
            strcat(MemoryPreservingTarget, '  (', MpVcpu, ' vCPU / ', MpMemoryGB, ' GB)'))"""

    mapping.append(arg_query(
        base + map_base + """
| project VmSize, ActionCategory, RetirementDate, Instances, Current,
          RecommendedTarget, Target, MemoryPreserving, AvgReadiness, AnnouncementUrl
| order by Instances desc""",
        "map-grid", size=1, export=True, title="Recommended targets",
        grid=grid_settings(
            {"VmSize": "12%", "ActionCategory": "11%", "RetirementDate": "9%",
             "Instances": "6%", "Current": "11%", "RecommendedTarget": "14%",
             "Target": "11%", "MemoryPreserving": "21%", "AvgReadiness": "5%"},
            [fmt("VmSize", 1, formatOptions={"linkColumn": "AnnouncementUrl",
                                             "linkTarget": "Url"}),
             date_col("RetirementDate"),
             fmt("AvgReadiness", 8, formatOptions={"palette": "redGreen", "min": 0, "max": 100}),
             fmt("AnnouncementUrl", 5)],
            filter=True,
            labelSettings=[
                {"columnId": "VmSize", "label": "Current size"},
                {"columnId": "ActionCategory", "label": "Lifecycle status"},
                {"columnId": "RetirementDate", "label": "Retires on"},
                {"columnId": "Current", "label": "Current spec"},
                {"columnId": "RecommendedTarget", "label": "Recommended target"},
                {"columnId": "Target", "label": "Target spec"},
                {"columnId": "MemoryPreserving", "label": "Memory-preserving target"},
                {"columnId": "AvgReadiness", "label": "Avg platform score"},
            ])))
    mapping.append(text_item(
        "#### Alternates, series notes and commercial impact\n"
        "The same sizes again, with the other targets worth evaluating, what changes at the "
        "series level, and the commercial position: whether the separate legacy-series 1 February 2027 pricing update "
        "applies to the series, and the series-wide RI purchase/renewal cutoff.\n\n"
        "> Reserved Instance purchases and renewals for these series ended **1 July 2026**. "
        "**This is not a customer's reservation expiry date or evidence of current pay-as-you-go billing.** "
        "See Inventory > Reservation exposure for visible purchased records. Matching benefit coverage, "
        "other discounts and billing are not inspected. "
        "([Azure update 560948](https://azure.microsoft.com/updates?id=560948)). "
        "For the rates that apply to your agreement, see the official "
        "[Windows](https://azure.microsoft.com/pricing/details/virtual-machines/windows/) and "
        "[Linux](https://azure.microsoft.com/pricing/details/virtual-machines/linux/) VM "
        "pricing pages, and your own Azure Service Health notification.",
        "map-detail-intro"))
    mapping.append(arg_query(
        base + map_base + """
| extend AlternateTargets = replace_string(AlternateTargets, ',', ', ')
| project VmSize, Series, AlternateTargets, SizeConsiderations, SpecNotes,
          PriceUpdate2027, RiEnded, RiStatus
| order by VmSize asc""",
        "map-detail", size=1, export=True, title="Alternates, series detail and commercial impact",
        grid=grid_settings(
            {"VmSize": "10%", "Series": "6%", "AlternateTargets": "16%",
             "SizeConsiderations": "20%", "SpecNotes": "20%",
             "PriceUpdate2027": "10%", "RiEnded": "8%", "RiStatus": "10%"},
            [date_col("RiEnded"),
             fmt("PriceUpdate2027", 18, formatOptions={"thresholdsOptions": "icons",
                 "thresholdsGrid": [
                     {"operator": "==", "thresholdValue": "Yes", "representation": "2",
                      "text": "1 Feb 2027"},
                     {"operator": "Default", "representation": "success",
                      "text": "Not affected"}]})],
            filter=True,
            labelSettings=[
                {"columnId": "VmSize", "label": "Current size"},
                {"columnId": "AlternateTargets", "label": "Alternate targets"},
                {"columnId": "SizeConsiderations", "label": "Considerations"},
                {"columnId": "SpecNotes", "label": "Series notes"},
                {"columnId": "PriceUpdate2027", "label": "Pricing update"},
                {"columnId": "RiEnded", "label": "Series RI purchase/renewal cutoff"},
                {"columnId": "RiStatus", "label": "Cutoff status - coverage not assessed"},
            ])))
    items.append(group_item("grp-mapping", "mapping", mapping))

    # ---------------- TAB 4: Prerequisites ----------------
    pre = []
    pre.append(text_item(
        "### Prerequisites and architecture work\n"
        "Some size transitions are a simple resize. Others require work **before** the "
        "resize can succeed - converting a Gen1 VM to Gen2, validating NVMe and MANA driver "
        "support in the OS image, or removing a dependency on the local temp disk. Each row "
        "below is a prerequisite that applies to your fleet, with the number of resources "
        "affected.",
        "pre-intro"))
    pre.append(arg_query(
        base_impacted + f"""
| extend Codes = split(WarnCodes, ',')
| extend Codes = array_concat(Codes,
        iff(TargetInRegion == 'No', pack_array('target_not_in_region'), dynamic([])),
        iff(isnotempty(AvSetId), pack_array('availability_set'), dynamic([])),
        iff(IsVmss, pack_array('scale_set'), dynamic([])),
        pack_array('quota_headroom'))
| mv-expand Code = Codes to typeof(string)
| where isnotempty(Code)
| where not(Code == 'g' and VmGeneration == 'V2')
| where Code != 's'
| extend D = split(tostring(parse_json('{prereq_bag}')[Code]), '|')
| where array_length(D) > 1
| summarize Resources = count(), Instances = sum(Instances),
            Sizes = strcat_array(make_set(VmSize, 12), ', ')
    by Prerequisite = tostring(D[0]), Severity = tostring(D[1]),
       WhatToDo = tostring(D[2]), Why = tostring(D[3]), How = tostring(D[4]), Guidance = tostring(D[5])
| extend SevRank = case(Severity == 'Blocking', 1, Severity == 'Validate', 2, 3)
| order by SevRank asc, Instances desc
| project-away SevRank""",
        "pre-grid", size=1, export=True, title="Prerequisites triggered by your fleet",
        grid=grid_settings(
            {"Prerequisite": "16%", "Severity": "7%", "Resources": "5%",
             "Instances": "5%", "WhatToDo": "21%", "Why": "18%", "How": "18%",
             "Sizes": "10%"},
            [fmt("Prerequisite", 1, formatOptions={"linkColumn": "Guidance",
                                                   "linkTarget": "Url"}),
             fmt("Severity", 18, formatOptions={"thresholdsOptions": "colors",
                 "thresholdsGrid": [
                     {"operator": "==", "thresholdValue": "Blocking",
                      "representation": "redBright", "text": "{0}{1}"},
                     {"operator": "==", "thresholdValue": "Validate",
                      "representation": "orange", "text": "{0}{1}"},
                     {"operator": "Default", "representation": "blue", "text": "{0}{1}"}]}),
             fmt("Guidance", 5)],
            filter=True,
            labelSettings=[
                {"columnId": "WhatToDo", "label": "What to do"},
                {"columnId": "Sizes", "label": "Sizes affected"},
            ])))
    items.append(group_item("grp-prereq", "prereq", pre))

    # ---------------- TAB 5: Migration plan ----------------
    plan = []
    plan.append(text_item(
        "### Suggested migration sequence\n"
        "Waves are ordered to bank low-risk wins first and to leave enough runway for the "
        "workloads that need prerequisite work. Front-load anything already Generation 2 and "
        "NVMe-capable; start the Gen1 conversion track in parallel because it has the longest "
        "lead time.\n\n"
        "| Wave | Scope | Why it goes here |\n| --- | --- | --- |\n"
        "| 1 | Few scored platform changes, non-production tag | Validate ownership and rehearse; not a no-risk claim |\n"
        "| 2 | Few scored platform changes, production or unverified | Assess application, HA, backup and rollback before scheduling |\n"
        "| 3 | Moderate scored platform changes | Validate NVMe/MANA and temp-disk dependencies |\n"
        "| 4 | Substantial scored platform changes | Plan conversion, memory or region changes |\n"
        "| 5 | Major scored platform changes | Assess redesign requirements and dependencies |\n\n"
        "These are **platform triage suggestions**, not approved migration waves or application complexity ratings.\n\n"
        "**How environment is determined.** Most fleets do not tag every VM, so the "
        "workbook resolves environment through a cascade and reports which signal it "
        "used:\n\n"
        "| Order | Signal | Notes |\n| ---: | --- | --- |\n"
        "| 1 | **Resource tag** | `Environment`, `Env`, `Tier`, `Stage` or `Usage`, any casing |\n"
        "| 2 | **Resource group tag** | Same keys on the parent resource group |\n"
        "| 3 | **Subscription tag** | Same keys on the subscription |\n"
        "| 4 | **Dev/Test subscription offer** | A Visual Studio or Enterprise Dev/Test subscription cannot host production under the Azure offer terms |\n"
        "| 5 | **Naming convention** | `dev`, `test`, `qa`, `uat`, `sandbox`, `staging` in the subscription or resource group name. Only ever read as non-production, never as production, because a name is weaker evidence than a tag |\n\n"
        "Values are normalised, so `non-prod`, `non_prod` and `nonprod` read alike. "
        "**Anything the cascade cannot resolve is sequenced as production**, not "
        "non-production, because the cost of guessing wrong is a production outage "
        "during what was sold as a no-risk pilot.\n\n"
        "The **Environment** and **Signal** columns below show the verdict and its "
        "source. If most rows read *Unverified*, the fastest fix is one "
        "`Environment` tag on each resource group - that alone resolves the whole "
        "estate through step 2.",
        "plan-intro", width="50"))
    plan.append(arg_query(
        base_impacted + """
| where DaysToRetirement >= 0
| extend Quarter = strcat(format_datetime(RetiresOn, 'yyyy'), ' Q', toint((getmonth(RetiresOn) - 1) / 3) + 1)
| summarize Instances = sum(Instances) by Quarter
| order by Quarter asc""",
        "plan-burndown", visualization="barchart", size=1, width="50",
        title="Instances to migrate by retirement quarter"))
    plan.append(arg_query(
        base_impacted + f"""
// Environment is resolved through a cascade, because most fleets do not tag
// every VM. The most specific signal wins, and the source is reported so the
// the signal it used is visible.
//
// A note on why this is not a tag-bag scan: the earlier `tostring(Tags) has
// 'prod'` test was wrong in both directions. KQL `has` matches whole terms, so
// "Production" did not match the term "prod" and a production VM was reported
// as non-production, while "non-prod" tokenised to ["non","prod"] and matched,
// and an unrelated tag such as Product=prod-catalog matched too.
| extend EnvSelf = {ENV_TAGS.format(col='Tags')}
| extend RgKey = tolower(strcat(SubscriptionId, '/', ResourceGroup))
| join kind=leftouter (
    resourcecontainers
    | where type =~ 'microsoft.resources/subscriptions/resourcegroups'
    | project RgKey = tolower(strcat(subscriptionId, '/', name)),
              EnvRg = {ENV_TAGS.format(col='tags')}
  ) on RgKey
| join kind=leftouter (
    resourcecontainers
    | where type =~ 'microsoft.resources/subscriptions'
    | project SubscriptionId = subscriptionId,
              EnvSub = {ENV_TAGS.format(col='tags')},
              QuotaId = tolower(tostring(properties.subscriptionPolicies.quotaId)),
              SubName = tolower(name)
  ) on SubscriptionId
// A Visual Studio or Enterprise Dev/Test subscription cannot host production
// under the Azure offer terms, so the offer itself is a reliable signal.
| extend IsDevTestOffer = (QuotaId has 'devtest' or QuotaId startswith 'msdn')
// Last resort: many estates encode environment in the subscription or resource
// group name. Only read as non-production, never as production, because a name
// is weaker evidence than a tag.
| extend NameHint = strcat(' ', replace_regex(strcat(SubName, ' ', tolower(ResourceGroup)), '[^a-z0-9]', ' '), ' ')
| extend NameSaysNonProd = (NameHint has ' dev ' or NameHint has ' test ' or NameHint has ' qa '
        or NameHint has ' uat ' or NameHint has ' sandbox ' or NameHint has ' nonprod '
        or NameHint has ' staging ' or NameHint has ' demo ')
| extend EnvSource = case(
        isnotempty(EnvSelf), 'Resource tag',
        isnotempty(EnvRg), 'Resource group tag',
        isnotempty(EnvSub), 'Subscription tag',
        IsDevTestOffer, 'Dev/Test subscription offer',
        NameSaysNonProd, 'Naming convention',
        'No signal')
| extend EnvRaw = case(
        isnotempty(EnvSelf), EnvSelf,
        isnotempty(EnvRg), EnvRg,
        isnotempty(EnvSub), EnvSub,
        IsDevTestOffer or NameSaysNonProd, 'devtest',
        '')
// Normalise so non-prod, non_prod and nonprod collapse to one token.
| extend EnvNorm = replace_regex(EnvRaw, '[^a-z0-9]', '')
| extend Environment = case(
        isempty(EnvNorm), 'Unverified - no signal',
        EnvNorm startswith 'non' or EnvNorm startswith 'pre'
            or EnvNorm in ('dev','devtest','development','test','testing','qa','uat','stg',
                           'stage','staging','sandbox','sbx','demo','poc','lab',
                           'sit','training'), 'Non-production',
        EnvNorm in ('prod','production','prd','live','prdn'), 'Production',
        'Unverified - unrecognised value')
// Only a confirmed non-production signal earns the wave 1 pilot slot. Anything
// unverified is sequenced as production, because the cost of being wrong is a
// production outage during what was sold as a no-risk pilot.
| extend Wave = case(
        ReadinessScore >= 85 and Environment == 'Non-production', 'Wave 1 - fewer platform changes, non-production tag',
        ReadinessScore >= 85, 'Wave 2 - fewer platform changes, production or unverified',
        ReadinessScore >= 60, 'Wave 3 - platform validation',
        ReadinessScore >= 35, 'Wave 4 - prerequisites required',
        'Wave 5 - re-architecture')
| summarize Resources = count(), Instances = sum(Instances),
            EarliestRetirement = min(RetiresOn),
            AvgReadiness = round(avg(todouble(ReadinessScore)), 0),
            Sizes = strcat_array(make_set(VmSize, 10), ', ')
    by Wave, Environment, EnvSource
| order by Wave asc""",
        "plan-waves", size=4, export=True, title="Migration waves",
        grid=grid_settings(
            {"Wave": "18%", "Environment": "14%", "EnvSource": "14%", "Resources": "9%",
             "Instances": "9%", "EarliestRetirement": "12%", "AvgReadiness": "9%",
             "Sizes": "15%"},
            [fmt("AvgReadiness", 8, formatOptions={"palette": "redGreen", "min": 0, "max": 100}),
             date_col("EarliestRetirement"),
             fmt("Environment", 18, formatOptions={"thresholdsOptions": "colors",
                 "thresholdsGrid": [
                     {"operator": "==", "thresholdValue": "Production",
                      "representation": "orange", "text": "{0}{1}"},
                     {"operator": "==", "thresholdValue": "Non-production",
                      "representation": "green", "text": "{0}{1}"},
                     {"operator": "Default", "representation": "gray", "text": "{0}{1}"}]})],
            labelSettings=[
                {"columnId": "EnvSource", "label": "Signal"},
                {"columnId": "EarliestRetirement", "label": "Earliest retirement"},
                {"columnId": "AvgReadiness", "label": "Avg platform score"},
                {"columnId": "Sizes", "label": "Sizes in wave"},
            ])))
    plan.append(arg_query(
        base_impacted + """
| where ReadinessScore < 60
| project Name, ResourceGroup, Region, VmSize, VmGeneration, RecommendedTarget,
          ReadinessScore, Blockers, RetiresOn, DaysToRetirement, ResourceId
| order by DaysToRetirement asc, ReadinessScore asc""",
        "plan-longpole", size=1, export=True, title="Long-pole workloads - start these first",
        grid=grid_settings(
            {"Name": "12%", "ResourceGroup": "10%", "Region": "9%", "VmSize": "10%",
             "VmGeneration": "8%", "RecommendedTarget": "12%", "ReadinessScore": "8%",
             "Blockers": "14%", "RetiresOn": "9%", "DaysToRetirement": "8%"},
            [fmt("Name", 1, formatOptions={"linkColumn": "ResourceId",
                                           "linkTarget": "Resource",
                                           "linkIsContextBlade": True}),
             fmt("ReadinessScore", 8, formatOptions={"palette": "redGreen",
                                                     "min": 0, "max": 100}),
             date_col("RetiresOn"),
             fmt("ResourceId", 5)],
            filter=True,
            labelSettings=[
                {"columnId": "ResourceGroup", "label": "Resource group"},
                {"columnId": "VmSize", "label": "Current size"},
                {"columnId": "VmGeneration", "label": "Gen"},
                {"columnId": "RecommendedTarget", "label": "Recommended target"},
                {"columnId": "ReadinessScore", "label": "Platform score"},
                {"columnId": "RetiresOn", "label": "Retires on"},
                {"columnId": "DaysToRetirement", "label": "Days left"},
                {"columnId": "ResourceId", "label": "Portal"},
            ])))
    items.append(group_item("grp-plan", "plan", plan))

    # ---------------- TAB 6: About ----------------
    about = []
    about.append(text_item(
        "### How this workbook works\n\n"
        "**Data source.** Read-only Azure Resource Graph queries and a selected-row Compute SKU "
        "API inspection across the subscriptions you "
        "select. Nothing is written, nothing leaves your tenant, and no agent or Log Analytics "
        "workspace is required. You need `Reader` on the subscriptions in scope.\n\n"
        "**Lifecycle status.**\n\n"
        "| Status | Meaning |\n| --- | --- |\n"
        "| Retirement announced | The size has a published retirement date. VMs still running "
        "that size on the date will stop. Action is mandatory. |\n"
        "| End of life | The series has entered the End of Life lifecycle stage with a published "
        "retirement date. Plan the migration inside the notice window. |\n"
        "| Optional modernization | Previous-generation or current-generation sizes with no "
        "announced retirement. Moving is a price-performance decision, not a compliance one. |\n\n"
        "**Platform score (legacy export field `ReadinessScore`).** Every impacted resource starts "
        "at 100 and loses points for scored platform differences. **100 is not migration approval**; "
        "unknown drivers, application dependencies, HA, backup and rollback do not become passed checks. "
        "Workload complexity is assessed separately by the application/service owner.\n\n"
        "| Factor | Penalty | Evaluated against |\n| --- | ---: | --- |\n"
        "| Arm64 architecture change | 50 | Target CPU architecture |\n"
        "| Gen1 to Gen2 conversion | 45 | **Actual `hyperVGeneration` of the OS disk** |\n"
        "| Target size not offered in this region | 30 | VM region vs target availability |\n"
        "| Memory reduction at same vCPU | 25 | Source vs target memory |\n"
        "| Local temp disk removed | 20 | Source has temp disk, target does not |\n"
        "| Unconfirmed VM generation | 20 | OS disk metadata unavailable |\n"
        "| NVMe boot validation | 15 | Selected target requires NVMe controller |\n"
        "| MANA/accelerated networking validation | 0 | Hardware-dependent, independent of storage controller |\n"
        "| Narrow target region footprint | 10 | Target region count |\n"
        "| Availability zone parity | 10 | Only when the VM uses a zone |\n"
        "| vCPU count change | 5 | No size exists at the current vCPU count |\n"
        "| Availability set membership | 5 | Cluster-pinned resize constraint |\n"
        "| Scale set | 5 | Model-level SKU change |\n\n"
        "A VM already running Generation 2 takes **no** Gen1 penalty even when its target is "
        "Gen2-only. That is the difference between scoring the size and scoring your fleet.\n\n"
        "**Legacy-series pricing changes.** A pricing update takes effect **1 February 2027** "
        "for selected v1 and v2 size series. The workbook flags which of your series are in "
        "scope; it does not restate the rate change, because the applicable figures are "
        "communicated to each tenant through its own Azure Service Health notification "
        "and depend on the agreement. For authoritative rates see the official "
        "[Windows](https://azure.microsoft.com/pricing/details/virtual-machines/windows/) and "
        "[Linux](https://azure.microsoft.com/pricing/details/virtual-machines/linux/) VM "
        "pricing pages. This flag is separate from the regional exposure section.\n\n"
        "**Regional pricing exposure.** Inventory > Retirement & pricing separately shows VMs in "
        "seven regions with publicly reproduced notification changes. Its figures are not a "
        "formal public announcement, contractual rates or a bill forecast. Confirm Service "
        "Health scope and your agreement; regional exclusions do not exempt a series from "
        "other pricing changes. Modern VMs may have potential regional exposure without retiring.\n\n"
        "**Reserved Instances.** Purchases and renewals ended **1 July 2026** for one-year "
        "reservations on Av2, Amv2, Bv1, D, Ds, Dv2, Dsv2, F, Fs, Fsv2, G, Gs, Ls and Lsv2, "
        "and for both one- and three-year reservations on Dv3, Dsv3, Ev3 and Esv3. Existing "
        "reservation records are available separately in Inventory > Reservation exposure; "
        "billing coverage is not assessed. The cutoff is **not the customer's "
        "reservation expiry**, and does not establish pay-as-you-go billing or the absence of other discounts "
        "([Azure update 560948](https://azure.microsoft.com/updates?id=560948)).\n\n"
        "**Cloud coverage.** The two changes do not have the same scope:\n\n"
        "| | Azure public | 21Vianet | Sovereign / Government |\n| --- | :---: | :---: | :---: |\n"
        "| v3 retirement | Applies | Exempt | Exempt |\n"
        "| Legacy-series 1 Feb 2027 pricing update | Applies | Applies | Exempt |\n\n"
        "This table does not establish the scope of the separate regional notification.\n\n"
        "This workbook does not detect cloud type. If you are running in Azure Government, "
        "a sovereign cloud, or 21Vianet, read the rows above before acting on a flag.\n\n"
        "**Capacity growth restrictions.** Separate from retirement. Existing subscriptions "
        "can keep deploying End of Life series within already-approved quota, subject to "
        "capacity, but additional quota is not approved and new subscriptions cannot deploy "
        "them at all. Restrictions on the v4 D and E families were **lifted** in the "
        "28 September 2026 notification, so quota requests for those families can be "
        "submitted again through the standard process, subject to capacity. Note that "
        "Microsoft's modernization guidance for the v3 series still points at v5, v6 and v7 - "
        "the lifting of v4 restrictions removes a constraint, it does not make v4 the "
        "recommended destination.\n\n"
        "**Caveats.**\n"
        "- Target recommendations are a starting point. Right-size against 30-90 days of real "
        "CPU, memory, disk and network utilisation before committing.\n"
        "- Region availability is checked against a captured size catalogue. Confirm with "
        "`az vm list-skus --location <region> --resource-type virtualMachines --size <target> --all` "
        "before you schedule a wave.\n"
        "- Reserved instance and savings plan coverage is not evaluated here. Review it alongside "
        "the migration plan, because savings plans flex across families while reservations do not.\n"
        "- Per-core licensed software (SQL Server, Oracle, SAP) must be re-checked whenever the "
        "vCPU count changes.\n\n"
        "**References**\n"
        "- [VM size series retirements, capacity restrictions and modernization guidance](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions)\n"
        "- [VM lifecycle overview](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/lifecycle-overview)\n"
        "- [End of Life VM size series](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/end-of-life-sizes-list)\n"
        "- [Retired VM sizes modernization guide](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/retirement/retired-sizes-modernization-guide)\n"
        "- [Modernize to the v5 VM series](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/sizes-v5-modernization-overview)\n"
        "- [Modernize to the v6 and v7 VM series](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/sizes-v6-v7-modernization-overview)\n"
        "- [Enhancing the Azure Virtual Machine lifecycle](https://azure.microsoft.com/blog/enhancing-microsoft-azure-virtual-machine-lifecycle/)\n"
        "- [Generation 2 VMs](https://learn.microsoft.com/azure/virtual-machines/generation-2)\n"
        "- [NVMe on Azure VMs](https://learn.microsoft.com/azure/virtual-machines/nvme-overview)\n"
        "- [MANA accelerated networking](https://learn.microsoft.com/azure/virtual-network/accelerated-networking-mana-overview)\n"
        "- [Resize a virtual machine](https://learn.microsoft.com/azure/virtual-machines/sizes/resize-vm)\n"
        "- [Manage Reserved VM Instances](https://learn.microsoft.com/azure/cost-management-billing/reservations/manage-reserved-vm-instance)\n"
        "- [Azure updates - retirements](https://azure.microsoft.com/updates/?updateType=retirements)\n",
        "about-text"))
    about.append(arg_query(
        base + """
| summarize Resources = count(), Instances = sum(Instances) by Lifecycle, ActionCategory
| order by Instances desc""",
        "about-coverage", size=4,
        title="Coverage - resources matched to the lifecycle catalogue",
        grid=grid_settings(
            {"Lifecycle": "26%", "ActionCategory": "32%", "Resources": "21%",
             "Instances": "21%"},
            labelSettings=[{"columnId": "ActionCategory", "label": "Lifecycle status"}])))
    about.append(arg_query(
        coverage_query(),
        "about-allsizes", size=4,
        title="All sizes in scope - explicit captured-catalogue coverage",
        grid=grid_settings({"VmSize": "24ch", "CatalogueCoverage": "50ch", "Resources": "12ch", "Instances": "12ch"},
                           labelSettings=[{"columnId": "VmSize", "label": "Size"}])))
    items.append(group_item("grp-about", "about", about))

    workbook = {
        "version": "Notebook/1.0",
        "items": workflow_layout(items),
        "styleSettings": {},
        "fallbackResourceIds": ["Azure Monitor"],
        "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json",
    }
    start = next(item for item in workbook["items"] if item.get("name") == "stage-start")
    start["content"]["items"].insert(1, text_item(
        "### Self-contained, with explicit evidence boundaries\n\n"
        "**Deploys one workbook resource only.** No Log Analytics workspace, custom tables, "
        "storage account, Function, automation, agent or customer data collection is required. "
        "Lookups and guidance are embedded; the deployed workbook does not download catalogues "
        "or run community tools. It reads built-in Azure Resource Graph tables and authenticated "
        "read-only Compute/Batch/Reservations ARM APIs using your permissions. It is not offline: Azure "
        "control-plane access is required. Documentation links are optional references, not data feeds.\n\n"
        f"**Captured catalogue:** {data['_meta']['generated']} (subscription-view snapshot, not today's offerability). "
        "**Lifecycle overrides:** 28 September 2026 announcements for v3 EOL, pricing and restrictions. "
        f"**Quota family/CPU map:** {quota_metadata['capturedAtUtc']}. "
        "ARG inventory/quota/Advisor are indexed at refresh and may lag; live ARM is requested "
        "when a placement is selected. Neither proves installed guest drivers or workload readiness.\n\n"
        "| Official lifecycle vocabulary | Interpretation, separate from workbook actions |\n"
        "| --- | --- |\n"
        "| Current | Supported modern series; not an urgency signal by itself |\n"
        "| Extended | Supported series in a later lifecycle stage; not the same as retired |\n"
        "| End of life | Announced retirement path; still distinct from retirement effective date |\n"
        "| Retired | Retirement effective; verify current official date/scope and cloud exemptions |\n\n"
        "**Internal actions:** RETIRE means a captured retirement announcement; it does not mean "
        "already retired. EOL is the captured end-of-life group. OPTIONAL does **not** assert "
        "Current versus Extended. Unmapped stages remain unverified. Reconcile exported findings "
        "with the [official lifecycle sources](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/lifecycle-overview), "
        "Advisor candidates under Catalogue coverage, and your tenant's "
        "[Service Health notices](https://portal.azure.com/#view/Microsoft_Azure_Health/AzureHealthBrowseBlade/~/serviceIssues) "
        "before approving a wave. An absent Advisor row is not an all-clear.",
        "workflow-self-contained"))
    for item in walk_workbook_items(workbook["items"]):
        if item.get("name", "").startswith("workflow-evidence-"):
            item["content"]["json"] += (
                f" **Captured as of:** catalogue {data['_meta']['generated']}; "
                f"quota family/CPU map {quota_metadata['capturedAtUtc']}.")
    coverage = next(item for item in walk_workbook_items(workbook["items"])
                    if item.get("name") == "grp-coverage")
    coverage["content"]["items"].extend([
        text_item("### Reconcile native Advisor evidence\n\n"
                  "Read-only, built-in `AdvisorResources`: retirement-text candidates associated "
                  "with Compute resources, not an authoritative or exhaustive VM-size retirement "
                  "feed. Includes other Compute retirement recommendations for owner review. "
                  "Empty/missing results do not prove absence; investigate indexing, permissions, "
                  "Service Health notices and official lifecycle dates. Does not change catalogue classifications.",
                  "advisor-intro"),
        arg_query(advisor_query(), "advisor-retirement", size=1, export=True,
                  title="Indexed Advisor Compute retirement candidates - reconcile, do not auto-approve",
                  grid=grid_settings({"ResourceId": "40ch", "Problem": "45ch", "Recommendation": "60ch"},
                                     [fmt(name, 5) for name in (
                                         "Category", "LastUpdated", "ExtendedEvidence", "Evidence", "EvaluatedAt")])),
    ])
    for item in walk_workbook_items(workbook["items"]):
        if item.get("type") == 3 and item["content"].get("visualization") == "table":
            item["customWidth"] = "100"
        content = item.get("content", {})
        for record in [content, *content.get("parameters", [])]:
            if "jsonData" in record and record["jsonData"] != "{QuotaGroupDiscovery:$.groups}":
                record["jsonData"] = json.dumps(json.loads(record["jsonData"]), separators=(",", ":"))
            if record.get("queryType") in (7, 12) and "query" in record:
                record["query"] = json.dumps(json.loads(record["query"]), separators=(",", ":"))
    return workbook


def walk_workbook_items(items):
    for item in items:
        yield item
        yield from walk_workbook_items(item.get("content", {}).get("items", []))


def arm_template(workbook):
    serialized = json.dumps(workbook, separators=(",", ":"))
    if len(serialized.encode("utf-8")) >= WORKBOOK_SIZE_LIMIT:
        raise ValueError("Workbook exceeds the saved-workbook size limit")
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "workbookDisplayName": {
                "type": "string",
                "defaultValue": WORKBOOK_TITLE,
                "metadata": {"description": "Name shown in the Workbooks gallery."},
            },
            "workbookId": {
                "type": "string",
                "defaultValue": "[newGuid()]",
                "metadata": {"description": "Unique id for the workbook resource."},
            },
        },
        "resources": [{
            "name": "[parameters('workbookId')]",
            "type": "microsoft.insights/workbooks",
            "location": "[resourceGroup().location]",
            "apiVersion": "2022-04-01",
            "kind": "shared",
            "properties": {
                "displayName": "[parameters('workbookDisplayName')]",
                "serializedData": serialized,
                "version": workbook["version"],
                "sourceId": "azure monitor",
                "category": "workbook",
            },
        }],
        "outputs": {
            "workbookId": {"type": "string",
                           "value": "[resourceId('microsoft.insights/workbooks', parameters('workbookId'))]"}
        },
    }


def main():
    import argparse
    global ONLY_SIZES
    ap = argparse.ArgumentParser()
    ap.add_argument("--only-sizes", help="File with one VM size per line. Restricts "
                                         "the embedded lookup (preview builds).")
    ap.add_argument("--suffix", default="", help="Suffix for output file names.")
    args = ap.parse_args()

    if args.only_sizes:
        with open(args.only_sizes, encoding="utf-8") as f:
            ONLY_SIZES = {ln.strip() for ln in f if ln.strip()}
        print(f"PREVIEW BUILD - lookup restricted to {len(ONLY_SIZES)} sizes")

    data = load()
    wb = build(data)
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WORKBOOK_DIR, exist_ok=True)
    sfx = args.suffix

    wb_path = os.path.join(OUT, f"azure-vm-retirement-workbook{sfx}.json")
    with open(wb_path, "w", encoding="utf-8") as f:
        json.dump(wb, f, separators=(",", ":"))

    arm_path = os.path.join(OUT, f"deploy-workbook{sfx}.json")
    with open(arm_path, "w", encoding="utf-8") as f:
        json.dump(arm_template(wb), f, indent=2)

    # Published copies. The "Deploy to Azure" button resolves a raw
    # githubusercontent URL, so the ARM template has to live at a stable path
    # in the repo rather than only under out/.
    published = []
    if not sfx:
        pub_arm = os.path.join(WORKBOOK_DIR, "azuredeploy.json")
        with open(pub_arm, "w", encoding="utf-8") as f:
            json.dump(arm_template(wb), f, indent=2)
        pub_wb = os.path.join(WORKBOOK_DIR, "vm-lifecycle-readiness.workbook")
        with open(pub_wb, "w", encoding="utf-8") as f:
            json.dump(wb, f, separators=(",", ":"))
        published = [pub_arm, pub_wb]

    sku_map, spec_map, url_map, tgt_regions, region_ids = build_lookups(data)
    tiers = {}
    for rec in sku_map.values():
        t = rec.split("|")[0]
        tiers[t] = tiers.get(t, 0) + 1

    # Emit the raw base query so it can be validated directly against ARG.
    q_path = os.path.join(OUT, "base-query.kql")
    with open(q_path, "w", encoding="utf-8") as f:
        f.write(base_query(sku_map, tgt_regions, region_ids))

    print(f"SKUs in lookup      : {len(sku_map)}  {tiers}")
    print(f"Target region map   : {len(tgt_regions)} targets / {len(region_ids)} regions")
    print(f"Spec notes          : {len(spec_map)} series, {len(url_map)} announcement urls")
    print(f"Base query          : {os.path.getsize(q_path)/1024:.0f} KB")
    print(f"Workbook            : {os.path.getsize(wb_path)/1024:.0f} KB -> {wb_path}")
    print(f"ARM template        : {os.path.getsize(arm_path)/1024:.0f} KB -> {arm_path}")
    for p in published:
        print(f"Published           : {os.path.getsize(p)/1024:.0f} KB -> {p}")


if __name__ == "__main__":
    main()
