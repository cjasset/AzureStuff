# Azure VM Retirement & Upgrade Planner

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A self-service Azure Monitor Workbook that inventories your VM fleet,
flags every size affected by announced Azure VM retirements and lifecycle changes,
recommends a target size for each, scores platform compatibility against the fleet's
**actual** configuration, and separates observed prerequisites from unverified migration readiness.

**A score of 100 is not migration approval.** It means no scored platform penalty;
guest drivers, application dependencies, HA, backup and rollback may still be unverified.

Modelled on the Azure Advisor *Service Retirement* workbook, but VM-specific and
with target mapping plus readiness scoring that Advisor does not provide.

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fcjasset%2Fvm-retirement-workbook%2Fmain%2Fworkbook%2Fazuredeploy.json)

## Fork policy: v5 first

This fork of [the upstream workbook](https://github.com/krishna-sunkavalli/vm-retirement-workbook)
prefers a **catalogued, non-NVMe v5 target** for retirement remediation, rather than
defaulting directly to v6. Candidates must preserve CPU architecture, vCPU and
memory capacity, local-disk presence/known capacity and premium-storage support. Only existing
target records are eligible; SKU names are not invented by replacing a suffix.

The target specifications, memory-preserving target, regional lookup and readiness
warnings are derived from the selected target together. For example, DS1_v2 and
D2s_v3 now recommend **D2ds_v5**, which retains local storage and supports Gen1/Gen2.
They no longer inherit the Gen2-only/NVMe penalties of the original D2ads_v6 target.
V6 remains an explicit alternative, requiring its own boot-controller validation.

If no suitable v5 candidate exists in the captured catalogue, the newer target
remains visible with **manual review required**. Existing non-NVMe recommendations
such as modern B-series are retained. Optional modernization is separate and may
still suggest a newer generation for a VM that is not retiring.

**V5 is not a compatibility guarantee.** MANA/accelerated networking is evaluated
independently of NVMe and may apply to v5 SCSI hardware. Guest OS, ADE/encryption,
drivers, disk/NIC limits, storage performance, application behavior, quota,
subscription restrictions and availability still need independent validation.
The source catalogue is a historical subscription snapshot, not live eligibility.
The original screenshots and score examples below describe the upstream baseline,
not the regenerated v5-first recommendations.

![Overview tab](docs/images/overview.png)

<sub>Screenshots are from a four-VM demo tenant built to exercise the scoring: two
Gen1 VMs, one Gen2 VM and one v3 end-of-life VM.</sub>

---

## What you get

The default landing page is **Start here**. Follow the numbered stages, or jump
directly to a stage and use its report selector. Each stage explains its goal,
actions, interpretation, checkpoint and next step. Report selections are
independent, so returning to a stage retains its current report during the session.
The workflow uses a new navigation parameter, rather than reusing legacy
`SelectedTab` report values that could otherwise hide every stage after an upgrade.
`WorkflowStage` is declared **global** so footer Next, Previous, Recheck and Help
actions update the stage visibility and top tabs above them. Report selectors
remain local. A non-global parameter only propagates downstream in Workbooks and
cannot support these footer actions.

| Stage | Reports and outcome |
| --- | --- |
| **Start here** | Scope, permissions, workflow map, evidence limitations and expected outputs |
| **1. Inventory** | Fleet overview; Retirement & pricing; Workloads & owners; Catalogue coverage. Produce a scoped workload list with owners and explicit unassessed SKUs |
| **2. Assess** | V5 / V6 paths and live SKU inspector; Required actions; SQL opportunities. Produce candidate paths and a prerequisite/evidence backlog |
| **3. Plan** | Capacity & quota; Target specifications; Suggested waves. Produce quota actions, pilots, dependency-aware waves and a recovery plan |
| **4. Migrate & validate** | Customer-operated execution and acceptance checklist: pilot, backup/restore, rollback, application/performance/HA checks and source closeout. Links to supported service-specific methods |
| **Methodology & references** | Recommendation policy, scoring model, sources and limitations; always accessible without advancing the workflow |

This is **guidance, not an execution engine or completion tracker**. Maintain
owners, decisions and approvals in your own exported plan. A green check applies
only to its specific evidence; Unknown is not a pass. Empty grids and disappearing
resources do not prove absence, successful migration or application readiness.
The detailed report descriptions below retain their familiar names.

### Workload discovery and live eligibility

Compute associations use AKS node resource groups, Databricks/managed-application
managed resource groups, resource-group `managedBy`, and AVD session-host VM links.
SQL registration/extension and SAP extension/image/license signals are kept separate
from proof of running software. Multiple owner associations require review.
Unclassified compute is **not assumed stateless or low complexity**.

NIC configuration/counts, shared disks, ephemeral/unmanaged disks, hibernation,
security type, system-assigned identity and Marketplace plans identify assessment
or rebuild dependencies, not blanket resize blockers. Incomplete NIC/disk
visibility stays Unknown. Flexible member details and service/pool registrations
must **not** be summed with model capacity.

Independent service inventory retains services with no visible backing VMs.
AVD host-pool type and configured AKS/Batch pool demand are exposed. Autoscaler
maxima, surge, Batch service quotas and transient/serverless compute are **not**
automatically incorporated into the existing Compute VM/VMSS quota calculation.

OS eligibility hints use a deliberately limited subset of the official
[NVMe image list](https://learn.microsoft.com/azure/virtual-machines/enable-nvme-interface)
and [MANA OS/kernel guidance](https://learn.microsoft.com/azure/virtual-network/accelerated-networking-overview),
checked 18 November 2026. Other versions/distributions remain Review, not unsupported.
Observed guest OS takes precedence over image labels. Hints never mark boot,
driver or traffic validation Met. Windows Server 2019 shared-disk NVMe compatibility
requires separate review.

In **Upgrade paths**, choose V5/V6 and select a source row to inspect the exact
target's live subscription/current-region Compute SKU record. Full restriction
objects preserve type, reason, location and zone scope. Missing data or API
failure is Unknown, not a passed check or proof of unavailability. No SKU
restriction does not mean sufficient quota, policy approval or allocation capacity.
This inspector does not overwrite catalogue recommendations or prerequisite statuses.
Per-resource quota prerequisites remain Unknown: the separate quota tab evaluates
the default V5-first fleet model, not an arbitrary V6 path.

The About coverage table explicitly retains SKUs outside the captured catalogue
as **Not assessed**. Absence from the impacted table does not establish that an
unmapped resource is unaffected.

### Impacted resources

The **VM retirement / end of life** section respects the Lifecycle status filter
but always excludes optional modernization. The **Potential regional price exposure**
section is independent of that filter and the retirement catalogue: modern v5/v6
VMs can be listed without being classified as retiring. A resource can appear in
both sections. Overview summary counts continue to describe retirement, not the
union of financial and lifecycle signals.

Regional figures are **publicly reproduced notification information**, not a
confirmed tenant-specific increase: North Europe 17%, France Central/South 11%,
West Europe and Norway East/West 9%, Southeast Asia 8%, reportedly effective
1 February 2027. The [public Microsoft Learn Q&A source](https://learn.microsoft.com/en-us/answers/questions/6025662/is-there-any-official-announcement-for-azure-vm-in)
is a community post quoting a notification, **not a formal Microsoft announcement**.
Verify the authoritative Service Health advisory, in-scope meters, contract and
reservation treatment. The percentages are not an applied per-VM increase or a
bill forecast.

VMs in those regions are shown with either potential exposure, series/scope unknown,
or a **reported regional exclusion**. An exclusion is not a universal exemption
from other changes, particularly the separate legacy-series update. Unknown SKUs
remain unknown; only the explicitly named Intel v7 series are treated as reported
v7 exclusions, not every v7 family. Storage meters are not inventoried here.

### SQL modernization

The separate tab identifies retiring/end-of-life **VMs**, not SQL-looking names or
Marketplace images, joined by normalized VM ID to SQL VM registrations and/or
VM extension resources. Extension detection requires publisher
`Microsoft.SqlServer.Management` and handler `SqlIaaSAgent` (either ARG
handler-property representation). Both signals are aggregated to one VM row,
with registration/extension states, edition, license type, management mode and
group IDs preserved. Group registration does not prove active HA membership/health.
Stopped VMs and failed/unknown extension states remain assessment signals.

Flexible scale-set member VMs are evaluated individually for SQL discovery; the
scale-set model is not counted again. Uniform instances without individual ARG
VM/extension records are outside this query's coverage. Absence of a matching
extension or registration is **not proof SQL is absent**; permissions and ARG indexing also affect
coverage. The tab includes all retiring/end-of-life matches independently of the
global Lifecycle status selector.

Before recommending a destination, validate SQL instances/version/edition,
database compatibility, dependencies (jobs, linked servers, SSIS/SSRS), HA/DR,
storage performance, licensing and downtime. Evaluate Azure SQL Managed Instance,
Azure SQL Database or supported SQL on modern VMs based on that assessment.
The workbook does not inspect guest SQL services or certify a migration target.

### Original upstream screenshot

Sorted worst-readiness first, so the workloads that need the most lead time are at
the top. Note the two Gen1 VMs scoring 35 and 40 against the Gen2 VM at 85 — same
retirement, very different amount of work.

![Impacted resources tab](docs/images/impacted-resources.png)

### Target mapping

`Standard_DS1_v2` is 1 vCPU / 3.5 GB and its recommended target is 2 vCPU / 8 GB —
the kind of spec jump that changes per-core licensing, which is why the current and
target specs sit side by side.

![Target mapping tab](docs/images/target-mapping.png)

### Upgrade paths: V5 and V6 side by side

Select **Upgrade paths** to compare the same retiring resources in two half-width
panels. Each selects a target from that generation's captured catalogue; a missing
match is **manual selection required**, never a silent fallback to another generation.

- **V5:** a non-NVMe target preserving the source's required architecture, CPU,
  memory, local-disk presence/known capacity and premium-storage support. Do not
  force Gen1 conversion, an OS upgrade or decryption when the selected resize
  target does not require that conversion.
- **V6:** independently evaluated Gen2/NVMe requirements. Gen1 conversion or a
  rebuild, OS eligibility, image/disk/boot-driver compatibility and actual boot
  rehearsal are called out where applicable. Unknown target local-disk capacity
  stays unverified, not assumed equal.
- **Both:** MANA/accelerated networking is independent of the disk controller.
  Validate quota/restrictions/policy, zone/cluster availability, storage performance,
  disk/NIC limits, application behavior, recoverable backups and rollback.

The OS disk's ADE configuration is an observed **configuration signal**, not proof
of current guest BitLocker state. A Server 2016 image SKU is an **image hint**, not
proof that the guest has not since been upgraded. Custom/gallery lineage does not
prove a missing driver. These unknowns remain visible in both panels.

Windows OS/encryption and Backup/ASR prerequisites of the documented
Gen1-to-Trusted-Launch conversion are explicitly distinguished from an ordinary
resize or a rebuild. No guest inspection or migration is performed by the workbook.
Each panel exports the additional evidence columns; neither displays a synthetic
"ready" score while guest compatibility is unverified.

Each prerequisite has its own **Met / Not met / Unknown / Not applicable**
column: target selection, generation, conversion OS/encryption, NVMe boot, MANA,
architecture, memory, local disk, catalogue region, zone, availability set,
scale-set model, disk/NIC limits, quota, policy, allocation capacity, backup,
ASR conversion, application validation and rollback. Scroll each grid horizontally
or export it to inspect all columns. **Met** means only that the available evidence
satisfies that particular check; missing guest, placement, quota or application
evidence stays **Unknown**. The paths' quota column is not automatically certified
by the separate quota-planning assessment.

### Capacity / quota

The default view has only **five visible columns**, with a total followed by
one row per subscription:

| Column | Meaning |
| --- | --- |
| Scope | The total or subscription name; IDs remain available in exports |
| Current cores | Configured retirement/EOL source quota-vCPUs, not all workload usage; includes deallocated inventory |
| Target cores | Their default V5-first target quota-vCPUs in the current regions |
| Delta (+/-) | Target minus current; a signed inventory difference, **not** the quota request |
| Quota status | Red if any required regional/family quota is insufficient; orange for unknown coverage; green means quota only |

Use **Planning scope** to switch between the workbook's selected subscriptions
and all subscriptions you can read in the current tenant. The latter is not a
claim of access to every tenant subscription. Other tabs keep their original
subscription scope. Lifecycle display filters do not remove retirement/EOL demand.
Scale sets use model capacity and Flexible members are not double-counted.

- **Side-by-side:** the entire target fleet needs additional cores while sources
  remain allocated.
- **In-place regional:** sum positive target-minus-source core deltas, without
  granting negative credits for downsizing other resources.
- **In-place family:** positive deltas for same-family moves; full target cores
  for cross-family moves. This assumes the source allocation can be retained or
  released as planned. Deallocated or unknown source power states (including
  scale-set models without instance evidence) make the in-place check **Unknown**,
  with null shortfall. Use side-by-side demand when allocation or resize
  sequencing is uncertain.

Every affected subscription/region/family is checked **automatically** against
indexed Compute `QuotaResources` limits and usage, including the regional total
across all target families. Both constraints must pass. Totals retain every
individual shortfall; spare quota elsewhere cannot cancel one. Known failures
remain red even if other checks are unknown.

**Show detail** displays one optional table at a time:

- **Migration breakdown:** subscription, region, target family, current cores,
  target cores and delta. Extra arithmetic remains in exports, not the default UI.
- **Quota checks and shortfalls:** additional cores required by the chosen
  migration approach, available quota (limit minus all existing usage), shortfall
  and status. Shortfall is floored at zero; a blank means Unknown.
- **Tenant headroom / quota group discussion:** readable subscriptions with
  relevant target-family/region quota, even without affected source VMs.
  Available cores are capped by both family and regional headroom. This is
  unused quota **now**, not surplus after their planned migrations.

Headroom is a discussion lead, **not a shared pool**. Verify quota-group membership,
eligibility, limits and approvals before any reallocation. The workbook neither
inspects quota-group eligibility nor moves quota.

Missing CPU/family metadata, missing quota, or Spot workloads keep the relevant
estimate **Unknown**, not unlimited or zero shortfall. Duplicate quota metrics are
also Unknown. ARG indexing is eventually consistent: refresh and recheck using
Azure Quotas/CLI immediately before migration. The workbook does not request quota
increases. Quota **does not reserve physical capacity** or satisfy SKU/zone
restrictions, policy, boot/driver or application prerequisites. All-at-once estimates
are not a wave schedule. The SKU map uses actual `vCPUs`, not constrained-core
`vCPUsAvailable`.

### Prerequisites

Only the prerequisites that *this* fleet actually triggers, ranked Blocking first.
The Gen1-to-Gen2 conversion is the long pole and is surfaced as blocking.

![Prerequisites tab](docs/images/prerequisites.png)

### Migration plan

![Migration plan tab](docs/images/migration-plan.png)

### About

![About tab](docs/images/about.png)

## Interpreting the platform score

Most retirement tooling scores the **size**. This scores the **fleet**.

The workbook joins each VM to its managed OS disk and reads the real
`properties.hyperVGeneration`. A VM already running Generation 2 takes **no** Gen1
penalty even when its recommended target is Gen2-only, while a Gen1 VM heading for
the same target takes a 45-point hit and surfaces the BIOS-to-UEFI conversion as a
blocking prerequisite.

Validated against a live fleet: identical `RETIRE` sizes scored **95** on Gen2 VMs
and **35** on Gen1 VMs.

| Factor | Penalty | Evaluated against |
| --- | ---: | --- |
| Arm64 architecture change | 50 | Target CPU architecture |
| Gen1 to Gen2 conversion | 45 | Actual `hyperVGeneration` of the OS disk |
| Target size not offered in this region | 30 | VM region vs captured size catalogue |
| Memory reduction at same vCPU | 25 | Source vs target memory |
| Local temp disk removed | 20 | Source has temp disk, target does not |
| Unconfirmed VM generation | 20 | OS disk metadata unreadable |
| NVMe boot validation | 15 | Selected target requires NVMe controller |
| MANA/accelerated networking validation | 0 | Hardware-dependent, independent of storage controller |
| Narrow target region footprint | 10 | Target region count |
| Availability zone parity | 10 | Only when the VM uses a zone |
| vCPU count change | 5 | No target at the current vCPU count |
| Availability set membership | 5 | Cluster-pinned resize constraint |
| Scale set | 5 | Model-level SKU change |

Bands describe few (85+), moderate (60-84), substantial (35-59) or major
(under 35) **scored platform changes**, not application migration complexity.
The exported `ReadinessScore` field is retained for compatibility. Unknown
prerequisites do not become passed checks at 100. Suggested migration waves
remain platform triage suggestions requiring workload-owner approval.

---

## Resources vs instances
The two counts differ wherever a scale set is involved:

- **Resources** counts ARM objects. A scale set is one resource however large it is.
- **Instances** counts actual VMs. A scale set contributes `sku.capacity`.

A fleet of 12 resources and 400 instances means twelve pieces of work but four
hundred machines rebooting. Effort and change control size off Resources; quota,
cost and blast radius size off Instances.

### Flexible orchestration is counted once

A **Flexible** scale set surfaces in Azure Resource Graph twice over: the scale
set itself, carrying `sku.capacity`, *and* every member VM as its own
`microsoft.compute/virtualmachines` row. Counting both double-reports the fleet -
a 2-instance scale set reads as 3 resources and 4 instances.

The base query drops member VMs and lets the scale set represent them:

```kusto
| where IsVmss or isempty(tostring(properties.virtualMachineScaleSet.id))
```

**Uniform** scale sets never expose member VMs, so they were always counted
correctly. Flexible is the current Azure default, so this affects most modern
fleets.

---

## How environment is determined

Wave 1 is the no-risk pilot, so it only accepts workloads confirmed to be
non-production. Most fleets do not tag every VM, so environment is resolved
through a cascade and the workbook reports which signal it used:

| Order | Signal | Notes |
| ---: | --- | --- |
| 1 | Resource tag | `Environment`, `Env`, `Tier`, `Stage` or `Usage`, any casing |
| 2 | Resource group tag | Same keys on the parent resource group |
| 3 | Subscription tag | Same keys on the subscription |
| 4 | Dev/Test subscription offer | A Visual Studio or Enterprise Dev/Test subscription cannot host production under the Azure offer terms |
| 5 | Naming convention | `dev`, `test`, `qa`, `uat`, `sandbox`, `staging` in the subscription or resource group name. Read only as non-production, never as production, because a name is weaker evidence than a tag |

Values are normalised, so `non-prod`, `non_prod` and `nonprod` read alike.

**Anything the cascade cannot resolve is sequenced as production.** Guessing the
other way puts a production VM into a wave described as carrying no
business risk.

If a fleet has no environment signal at all, every low-complexity workload lands
in wave 2 and wave 1 is empty - which is the correct outcome, not a bug. One
`Environment` tag per resource group resolves the whole estate through step 2.

The **Environment** and **Signal** columns on the wave table show the verdict and
its source, so the classification is auditable rather than implicit.

### Worked example

A four-VM demo fleet where the resource group carries `Environment=Development`
and two VMs carry their own tag:

| VM | Own tag | Resolved | Signal |
| --- | --- | --- | --- |
| `legacy-ds2-gen2` | `Environment: Production` | Production | Resource tag |
| `legacy-d2s-v3` | `Environment: non-prod` | Non-production | Resource tag |
| `legacy-ds1-gen1` | *none* | Non-production | Resource group tag |
| `legacy-vmss-ds2v2` | *none* | Non-production | Resource group tag |

A resource tag always beats the group it sits in, and the two untagged VMs
inherit the group rather than falling through to *Unverified*.

### Why not just search the tags

An earlier version tested `tostring(Tags) has 'prod'`. That is wrong in both
directions, because KQL `has` matches whole terms:

| Tag | Old verdict | Why |
| --- | --- | --- |
| `Environment: Production` | Non-production | `"Production"` is not the term `"prod"` |
| `Environment: non-prod` | Production | tokenises to `[non, prod]` |
| `Product: prod-catalog` | Production | matched an unrelated tag |

The first row is the dangerous one: a production VM sequenced into wave 1.

---

## The queries

Every Azure Resource Graph query the workbook runs is documented verbatim in
[`docs/QUERIES.md`](docs/QUERIES.md) - 27 read-only ARG queries plus the selected-row ARM SKU inspector, with the shared base
pipeline shown once and each per-tab tail alongside it. Nothing is hidden: you
can audit exactly what executes against your tenant before you deploy.

## Sharing the results

| Need | How |
| --- | --- |
| **Export a table to Excel** | Detail grids carry a **Download to Excel** toolbar button. It exports *all* columns, not just the visible ones, so the prose columns that truncate on screen come through in full. |
| **Print / PDF** | Workbooks have no native print command. Use the browser's own **Print → Save as PDF** (`Ctrl+P`). Expand the tab you want first; only the visible tab prints, because the other tabs are hidden by conditional visibility. |
| **Share the live view** | The toolbar **Share** button copies a URL that carries your parameter selections. The recipient needs `Reader` on the same subscriptions. |
| **Pin to a dashboard** | Individual charts and grids can be pinned to an Azure dashboard from the item's **Pin** control. |

There is no built-in "export the whole workbook to PDF" - that is a long-standing
Workbooks gap rather than something this workbook can add. For a point-in-time
artifact you can share, the Excel export per grid is the reliable path.

---

## Deploying it

### Option A - Deploy to Azure (recommended)

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Fcjasset%2Fvm-retirement-workbook%2Fmain%2Fworkbook%2Fazuredeploy.json)

Pick a subscription and resource group, then **Review + create**. The workbook
appears under **Monitor → Workbooks**.

### Option B - Azure CLI

```bash
az group create --name rg-vm-lifecycle --location eastus

az deployment group create \
  --resource-group rg-vm-lifecycle \
  --template-file workbook/azuredeploy.json \
  --parameters workbookDisplayName="Azure VM Retirement & Upgrade Planner"
```

### Option C - Azure PowerShell

```powershell
New-AzResourceGroup -Name "rg-vm-lifecycle" -Location "eastus"

New-AzResourceGroupDeployment `
  -ResourceGroupName "rg-vm-lifecycle" `
  -TemplateFile "workbook/azuredeploy.json" `
  -workbookDisplayName "Azure VM Retirement & Upgrade Planner"
```

### Option D - paste the template (no resource created)

1. Azure portal → **Monitor** → **Workbooks** → **New**
2. Click the **</>** *Advanced Editor* button
3. Paste the contents of [`workbook/vm-lifecycle-readiness.workbook`](workbook/vm-lifecycle-readiness.workbook)
4. **Apply** → **Done Editing** → **Save**

### Permissions

To deploy, you need `Contributor` on the destination resource group (or a
custom role permitting deployments and `Microsoft.Insights/workbooks/write`).
`Reader` is sufficient to view queried resources, but not to deploy or save a
workbook. To use the workbook, you need `Reader` on every subscription in scope.
Quota checks use indexed Compute `QuotaResources` visible to the reader; absent
records are reported as Unknown. The workbook issues
**read-only** Azure Resource Graph queries, writes nothing,
needs no agent and no Log Analytics workspace, and no data leaves the tenant.

If a subscription grants read on `virtualMachines` but not on `disks`, the OS-disk
join returns nothing and those VMs show `VmGeneration = Unknown` with a 20-point
penalty and a "Confirm VM generation" prerequisite, rather than failing.

---

## Rebuilding

```bash
python scripts/build_workbook.py      # regenerate workbook + ARM template
python scripts/build_queries_doc.py   # regenerate docs/QUERIES.md
python scripts/validate_queries.py --offline  # size, structure and published artifact checks
python -m unittest discover -s scripts -p "test_*.py"  # offline regression tests
python scripts/validate_queries.py    # all 27 live ARG queries in the current CLI subscription
# Optional quota scope / migration approach (CLI authorization scope remains --subscription):
# python scripts/validate_queries.py --subscription <id> --quota-scope tenant --migration-mode inplace
```

The guided-workflow release uses an explicitly limited **metadata/API validation**
scope: 41 offline tests, all 27 live ARG queries, quota and pricing/SQL boundary
fixtures, workload-discovery fixtures and exact live target-SKU records. Fresh
SQL guest-runtime verification was not completed because the SQL lab VM's guest
agent did not report readiness during the earlier lab startup. No lab VM is
started for this workflow-only validation. Portal visual verification was
also not completed. SQL detection remains an assessment signal, not proof of a
running SQL service or application migration readiness.

`build_workbook.py` writes four files: the workbook and ARM template under
`out/` for local iteration, and published copies at `workbook/azuredeploy.json`
and `workbook/vm-lifecycle-readiness.workbook`. The published ARM template is
what the **Deploy to Azure** button resolves, so it must be committed for the
button to work.

Without `--offline`, `validate_queries.py` needs `az login` and the
`resource-graph` CLI extension. It
verifies that tab links match group visibility conditions, that every query is
scoped to `{Subscriptions}` and references only declared parameters, and that all
27 Resource Graph queries execute successfully. Live CLI validation is limited
to the current or explicitly supplied subscription; the workbook's tenant scope
uses the portal's readable subscriptions. Quota queries join indexed quota records
automatically, without selected-row parameters or per-family ARM requests. Azure Resource Graph throttles
aggressive callers, so an occasional single-query failure on a back-to-back run is
a rate limit rather than a regression - re-run to confirm.

### Source data

`references/sku-migration-map.json` is vendored into this repo so a fresh clone
builds without any sibling checkout. It carries 628 sizes - 137 retiring, 39
end-of-life, 452 optional - with targets, memory-preserving alternatives,
alternates and warning codes, and self-declares:

> PUBLIC - all lifecycle facts sourced from learn.microsoft.com. Contains NO
> pre-announcement pricing information.

`references/quota-families.json` captures 1,284 public Compute SKU family and
quota-vCPU mappings. It contains no subscription IDs, usage, quota limits or tenant
information. Refresh it from the Compute SKU API when adding sizes; preserve
the API's family casing for the workbook's case-sensitive JSONPath selection.
Live quota/usage is never embedded in the published artifacts.

Prerequisite copy and series lifecycle detail were originally separate reference
files; they are now inlined in `scripts/build_workbook.py` as the `PREREQ` and
series tables. `scripts/build_workbook.py` prefers the local `references/` folder
and falls back to a sibling `compute-fleet-modernization/references` checkout when
building from the authoring tree.

### Implementation notes

Azure Resource Graph supports neither `let` nor `datatable`, so the lifecycle
lookup is used in each query as a `parse_json('{...}')` dynamic literal indexed
by VM size. The lookup data is stored once in hidden workbook text parameters
and substituted before queries are sent to ARG. Warning codes use single
characters and region lists use hex indices into a shared region table.

Earlier builds repeated the lookups in every chart, exceeding the saved-workbook
size limit and failing with **"The workbook length is too large"** through both
ARM deployment and Advanced Editor save. The shared lookups preserve the full
628-size catalogue and all 27 ARG queries while keeping the saved workbook below
1 MB. Rebuilds fail if that limit is reached; use the current published template
or workbook rather than an older downloaded copy.

### Layout conventions

Modelled on the official Advisor *Azure Services Retirement* workbook
(`reference/official-service-retirement.workbook`):

- **Column widths are percentages**, not `ch`. Each grid's widths sum to 100 so
  the table fits the container instead of scrolling horizontally.
- **Workbooks enforces a ~130px minimum column width**, so any percentage below
  about 8% of a full-width grid is silently floored. The practical limit on a
  1600px canvas is **11-12 columns**; past that the grid scrolls sideways and the
  scrollbar eats the row height. Cut columns rather than shrinking percentages.
- **Query `size` sets the grid height**, and `gridSettings.rowLimit` only caps how
  many rows the query returns - it does not make the grid taller. Measured
  heights: `size: 4` ~126px (3 rows, too cramped), `size: 1` ~220px (~6 rows,
  the balance used here), `size: 2` ~600px (~18 rows, leaves dead space under
  short results).
- **Static dropdowns use `jsonData`**, not `query` + `queryType: 8`. The latter
  never resolves and leaves every dependent query showing *"This query could not
  run because some parameters are not set."*
- **Date columns** carry `dateFormat: {showUtcTime, formatName: shortDatePattern}`,
  otherwise they render as `4/30/2028, 7:00:00 AM`.
- **Resource and URL links** use formatter 1 with `linkColumn` pointing at a
  companion column that is itself hidden with formatter 5. That makes the resource
  name or size name the clickable element and removes a redundant link column.
- **Numeric columns carry no bar formatter.** Formatter 4 renders a left-aligned
  value with a coloured bar while plain numbers right-align, so mixing the two
  across `Resources` and `Instances` made the columns look misaligned. Only the
  readiness heat map (formatter 8, `redGreen`) keeps a colour treatment.

---

## Sources and scope

**Public information only.**

Included: announced retirement dates and migration targets; the v3
(Dv3/Dsv3/Ev3/Esv3) End-of-Life status with its 15 November 2029 retirement date;
a neutral flag naming the size series in scope for the 1 February 2027 pricing
update; and the Reserved Instance purchase and renewal end date of 1 July 2026
([Azure update 560948](https://azure.microsoft.com/updates?id=560948)).

That is a **series-wide purchase/renewal cutoff**, not a customer's reservation
expiry. The workbook does not read reservation terms, matching coverage, other
discounts or billing, and does not infer that a VM now bills at pay-as-you-go rates.

For the **legacy-series update**, deliberately excluded: the rate-change percentage. That increase is scoped by
size series, and the applicable figures depend on your agreement and reach each
tenant through its own Azure Service Health notification. Restating a single
percentage in a template that deploys into any tenant would be a generalized
bill-impact claim, so the workbook flags *which* series are in scope and points
at the official
[Windows](https://azure.microsoft.com/pricing/details/virtual-machines/windows/)
and [Linux](https://azure.microsoft.com/pricing/details/virtual-machines/linux/)
VM pricing pages for rates.

Separately, the regional exposure section includes the publicly reproduced
regional percentages, prominently labeled as unverified notification scope rather
than contractual rates or a bill-impact calculation. They must not be added to
the legacy-series pricing flag as if the two updates universally stack.

The v3 retirement and the lifecycle policy became public on **28 September 2026**
via the [Azure blog](https://azure.microsoft.com/blog/enhancing-microsoft-azure-virtual-machine-lifecycle/)
and the Learn lifecycle pages.

### Cloud coverage

The retirement and the **legacy-series** pricing update do not share the same scope.
This table does not establish the scope of the separate regional notification:

| | Azure public | 21Vianet | Sovereign / Government |
| --- | :---: | :---: | :---: |
| v3 retirement | Applies | Exempt | Exempt |
| 1 Feb 2027 pricing update | Applies | Applies | Exempt |

The workbook does not detect cloud type; Methodology & references carries this table so a
reader in a sovereign or government tenant can discount the flags.
