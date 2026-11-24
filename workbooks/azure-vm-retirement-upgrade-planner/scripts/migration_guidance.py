"""Embedded customer-operated migration guidance and read-only evidence queries."""

ROUTES = {
    "resize": ("Same-VM resize", """### Same-VM resize

- Confirm the exact source/target pair supports the existing generation, controller,
  security/encryption configuration, local-disk topology and supported OS. A matching
  vCPU/memory size alone does not establish a supported resize.
- Plan restart/deallocation, IP/address consequences, availability-set/host constraints,
  extensions and licensed core changes. Enabled hibernation is not proof of a hibernated
  guest: check runtime state and resume/clear it as required by the supported operation.
- Recheck family/regional quota and actual allocation capacity. A return to the source
  size is not guaranteed once deallocated, especially near retirement.
- Recovery: rehearsed backup/restore and an approved alternative destination if the
  old family cannot be reallocated. A resize-back is not a universal rollback.

[Supported resize procedure](https://learn.microsoft.com/azure/virtual-machines/sizes/resize-vm)
"""),
    "parallel": ("Parallel replacement", """### Parallel replacement

Preferred modernization pattern where workload architecture permits it: build a
supported replacement, move state, validate it, then cut over. **A fresh OS disk does
not inherit application state from the old OS disk.**

- Inventory OS-disk-only data, local databases, certificates/private keys, licenses,
  scheduled tasks, service accounts, application configuration and identity permissions.
- Recreate networking, private DNS, routes, NSGs, load-balancer probes, IP forwarding,
  IP preservation requirements, backup, monitoring and security integrations.
- Use full side-by-side target quota, including temporary surge and service quotas.
  Update images and restore data using a supported, tested application procedure.
- Recovery: retain a recoverable source for the agreed period; define how writes made
  after cutover are reconciled. Record the system of record, cutback trigger, authority,
  RPO/RTO and last safe recovery time before changing traffic.

[Modernization planning](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/sizes-v6-v7-modernization-plan)
"""),
    "service": ("Service-owned replacement", """### Service-owned replacement

Use the owning service's supported size/node-type list and replacement API/runbook.
Compute SKU offerability alone does not certify an AKS, Batch, Databricks or AVD target.
Resolve conflicting associations before proceeding. **Do not resize managed backing
VMs individually.** AVD session hosts still require the appropriate customer-managed
host/image procedure, with pooled and personal persistence treated differently.

- AKS/VMSS: test surge, upgrade policy, disruption budgets, storage, scheduling and drain.
- Batch: create/validate a replacement pool; move job/pool references and autoscale
  formulas; let tasks complete or explicitly recover/checkpoint them before deletion.
- Databricks: use a supported node type/runtime for job and all-purpose clusters,
  pools and policies; validate restart/recreation, jobs, queries and connections.
- AVD: verify host-pool type and persistence/assignment requirements, not only VM size.
- Recovery: service-level restoration, pool/cluster recreation and application state
  recovery must be rehearsed; a backing VM snapshot alone is not a service rollback.

Select the workload acceptance checklist under **Migrate & validate**.
"""),
    "gen1": ("Gen1 to Trusted Launch", """### Gen1 to Trusted Launch - distinct in-place operation

This is not a synonym for SKU resize or SCSI-to-NVMe conversion. Confirm the current
documented eligibility, Windows/Linux route, boot layout/EFI space, supported OS,
security and disk settings, Backup policy and ASR prerequisites before using it.
Windows Server 2016 and Windows OS encryption restrictions on the documented
conversion route must be resolved; a separate rebuild route has different prerequisites.

**A Trusted Launch conversion cannot be undone by simply resizing back or changing
the controller.** Rehearse recovery from a suitable pre-conversion backup. Gen1 image
references can remain after conversion: reconcile image, patching and reimage behavior.
Platform support does not make a community automation script Microsoft-supported.

[Gen1 upgrade prerequisites and recovery](https://learn.microsoft.com/azure/virtual-machines/trusted-launch-existing-vm-gen-1)
"""),
    "nvme": ("SCSI to NVMe", """### SCSI to NVMe - distinct in-place operation

Changing the disk controller and resizing are distinct from Gen1/Trusted Launch
conversion. Confirm the exact VM/security/OS combination is supported by the
documented operation; do not assume any v6/v7 suffix proves compatibility.

- Verify actual image/disk NVMe capability, boot-critical driver, initramfs where
  applicable and successful boot from the actual converted disk. Image publisher
  and supported OS metadata are hints, not proof of installed/current guest drivers.
- Validate MANA and real network traffic independently of NVMe.
- Inspect fstab, LVM, mount units, application/backup scripts and device-path
  assumptions; use stable identifiers. Confirm data disks mount correctly.
- Temporary/local NVMe can be raw or reinitialized after deallocation. Test its
  initialization plus pagefile, swap, tempdb and scratch placement; it is not durable data.
- Recovery: use only the operation's documented reversal prerequisites; otherwise
  recover from the rehearsed backup. A controller switch is not a universal rollback.

[NVMe guidance](https://learn.microsoft.com/azure/virtual-machines/nvme-overview) |
[Supported modernization operations](https://learn.microsoft.com/azure/virtual-machines/sizes/lifecycle/sizes-v6-v7-modernization-modernize)
"""),
}

WORKLOADS = {
    "unknown": ("Choose workload", """### Owner/classification required

Unclassified is **not stateless**. Identify the owner, dependencies and data authority,
then choose the appropriate acceptance pattern. These are customer checklists, not
automatically recorded pass/fail results. Combine patterns for mixed workloads.
"""),
    "standalone": ("Standalone app", """### Standalone application - done means

- Real application transactions, data integrity and client/DNS/LB cutover succeed.
- Latency, throughput, disk/network behavior and availability meet the agreed baseline.
- OS-disk state, certificates, tasks, licenses, identities and permissions are preserved.
- The application owner accepts the destination; recovery writes and source disposition
  are reconciled before the rollback window closes.
"""),
    "pools": ("AKS / VMSS / Batch pools", """### Pools - done means

- Workloads/tasks run successfully on supported target nodes and persistent data is intact.
- Old nodes/pools are drained using the service procedure; no jobs, selectors, taints,
  autoscale profiles, pool references or VMSS model/images still pin the retired size.
- AKS PDBs, scheduling, node/pod/network/storage health and upgrade/surge behavior pass.
- Batch tasks complete or recover correctly, replacement job/pool bindings and autoscale
  formulas work; zero allocated nodes alone does not prove a retired pool is remediated.
- Source pools are removed only after owner acceptance and approved recovery retention.
"""),
    "avd-pooled": ("AVD pooled", """### AVD pooled - done means

- Replacement hosts are Available and actually serve test/user sessions.
- Profiles/FSLogix, apps, authentication, user data and performance meet acceptance.
- Drain mode and session handling are verified; old hosts no longer receive sessions.
- Host-pool/image/scaling configuration no longer creates the retired size; remove
  old hosts only after sessions drain and recovery/owner approval.
"""),
    "avd-personal": ("AVD personal", """### AVD personal - done means

- Persistent user/OS-disk application state and personal assignments are explicitly
  preserved or migrated, not assumed portable like a pooled desktop.
- The assigned user can sign in to the correct replacement; apps, profiles, data,
  licenses, identity and performance pass.
- Reassignment, recovery and old-host retirement are owner-approved; do not delete
  the old persistent desktop merely because the replacement host is Available.
"""),
    "databricks": ("Databricks", """### Databricks - done means

- Supported target node types/runtime are applied to relevant clusters, job definitions,
  pools and policies; no old node-type pinning remains.
- Representative jobs, queries, libraries, authentication and external connections pass.
- Local scratch/shuffle behavior, performance and cost are accepted; durable data
  and recovery do not depend on lost local disks.
- Old clusters/pools are retired through Databricks after owner acceptance. A terminated
  old configuration is still a configuration that can restart on the retired type.
"""),
    "stateful": ("SQL / SAP / stateful", """### Stateful / SQL / SAP - done means

- Replication, quorum, data consistency, backup/restore and HA/failover pass under load.
- Client endpoints and authentication work; application/database owners accept RPO/RTO
  and baseline performance, including storage latency, tempdb/log and licensing effects.
- Vendor support and cluster membership are validated; the old node is removed cleanly
  after failover and recovery retention. SQL registration alone proves none of this.
"""),
    "appliance": ("Vendor appliance", """### Vendor appliance - done means

- Vendor certifies the exact product/version, target family, image/boot/security mode,
  NIC count/order, accelerated networking/MANA drivers and deployment/HA topology.
- Data-plane traffic, routing, IP forwarding, policy enforcement, management access,
  licensing and HA/failover pass representative tests.
- Old appliances leave the traffic/HA path cleanly; supported configuration restore
  and recovery have been tested. Generic boot success is not appliance certification.
"""),
}

COMMON_GATES = """### Gates for every route

**ADE has a separate retirement on 15 September 2028**, independent of VM-size
retirement. Determine OS versus data-disk encryption and Windows versus Linux
scope using guest and disk evidence. An absent ADE extension does not prove
decryption or absence of guest encryption.

ADE to encryption at host is **not an in-place conversion**: use the documented
new-disk/new-VM migration route. Linux ADE-encrypted OS disks cannot simply be
decrypted. Disk encryption history/UDE can persist after decryption, snapshots and
Copy; do not assume a copied/decrypted disk qualifies for encryption at host.
Follow the documented supported migration procedure, including its Upload versus
Copy distinctions, and validate data, keys, recovery and target encryption.
[ADE migration guidance](https://learn.microsoft.com/azure/virtual-machines/disk-encryption-migrate)

**Vendor certification is a gate.** Known network/storage/backup appliance image
publishers are only *suspected appliance* signals; a Marketplace image is not
automatically an appliance, and unrecognized/custom images are not automatically
cleared. Confirm the exact vendor product/version, target family, NIC/driver and
HA certification before choosing a route.

**Placement is more than region offerability.** Review exact zones, availability
sets, Dedicated Host/host groups, proximity placement groups (PPG), capacity
reservation groups and service-specific topology. A region-supported SKU does not
establish that a host, PPG, reservation or zone can accommodate it.

Guest-only gates stay **Unknown**: signed/current storage, backup and security filter
drivers; application data/state; encryption; boot/MANA/NVMe behavior; and recovery.
Do not install agents or deploy telemetry infrastructure just to use this workbook.
"""

CLOSEOUT = """### Complete before closing the change

Restore/test agents, backup policies and restore points, security, monitoring,
extensions and alerting. Reconcile Hybrid Benefit/BYOL and reservation/savings-plan
coverage with licensing owners; retail pricing is not actual commercial coverage.
Update Terraform/Bicep, image pipelines, autoscale profiles and operational runbooks
so the retired size is not recreated. Record evidence, owner acceptance, source
disposition and the explicit end of the recovery window in your own change tracker.

Recheck the same subscription/scope and both old **configuration** and live instances.
A disappeared retirement row, deallocated VM or empty query is not proof of success.
"""


def advisor_query():
    return """advisorresources
| where type =~ 'microsoft.advisor/recommendations'
| extend ResourceId = tostring(properties.resourceMetadata.resourceId),
         Problem = tostring(properties.shortDescription.problem),
         Recommendation = tostring(properties.shortDescription.solution)
| where tolower(ResourceId) contains '/microsoft.compute/'
| where strcat(Problem, ' ', Recommendation, ' ', tostring(properties.extendedProperties)) contains 'retir'
| project ResourceId, Problem, Recommendation, Category = tostring(properties.category),
          LastUpdated = tostring(properties.lastUpdated), ExtendedEvidence = properties.extendedProperties,
          Evidence = 'Indexed Advisor retirement candidate - reconcile with official lifecycle and Service Health',
          EvaluatedAt = now()
| order by ResourceId asc"""


def alternatives_query():
    return """resources
| where type in~ ('microsoft.compute/virtualmachines', 'microsoft.compute/virtualmachinescalesets')
| extend IsVmss = type =~ 'microsoft.compute/virtualmachinescalesets'
| where IsVmss or isempty(tostring(properties.virtualMachineScaleSet.id))
| extend VmSize = iff(IsVmss, tostring(sku.name), tostring(properties.hardwareProfile.vmSize))
| extend Targets = parse_json('{CompatibleTargetMap}')[VmSize]
| mv-expand Target = Targets limit 10
| extend Spec = parse_json('{TargetSpecMap}')[tostring(Target)]
| project Resource = name, Source = VmSize, Target = tostring(Target),
          Generation = toint(Spec[0]), CpuArchitecture = tostring(Spec[1]),
          CpuVendor = tostring(Spec[2]), QuotaVcpus = toint(Spec[3]),
          EffectiveVcpus = 'Unknown - inspect live vCPUsAvailable; not quota CPUs',
          MemoryGB = toreal(Spec[4]), LocalDiskGB = toreal(Spec[5]),
          LocalDisk = tostring(Spec[6]), NvmeRequired = tobool(Spec[7]),
          HyperVGenerations = tostring(Spec[8]), PremiumIO = tobool(Spec[9]),
          Decision = 'Captured compatible candidate - not guest, vendor, quota or placement approval',
          ResourceId = id, Region = location, SubscriptionId = subscriptionId
| order by Resource asc, Generation asc, QuotaVcpus asc, MemoryGB asc"""


def target_fit_query(compute_query):
    return compute_query + """
| where SubscriptionId =~ '{LiveSubscription}' and Region =~ '{LiveRegion}'
| extend SelectedPath = case('{EligibilityGeneration}' == '5', parse_json('{V5PathSkuMap}')[VmSize],
        '{EligibilityGeneration}' == '6', parse_json('{V6PathSkuMap}')[VmSize],
        parse_json('{V7PathSkuMap}')[VmSize])
| where tostring(split(tostring(SelectedPath), '|')[3]) =~ '{LiveTarget}' and isnotempty('{LiveTarget}')
| extend Sku = dynamic({LiveSkuRecord:json})
| extend RecordMatches = tostring(Sku.name) =~ '{LiveTarget}' and tostring(Sku.resourceType) =~ 'virtualMachines',
         CapabilityRows = iff(array_length(Sku.capabilities) > 0 and array_length(Sku.capabilities) <= 2000,
             Sku.capabilities, dynamic([{}]))
| mv-expand Capability = CapabilityRows limit 2000
| summarize Caps = make_bag(pack(tostring(Capability.name), tostring(Capability.value))),
         DiskLimitRecords = countif(tostring(Capability.name) == 'MaxDataDiskCount'),
         NicLimitRecords = countif(tostring(Capability.name) == 'MaxNetworkInterfaces'),
         Context = take_any(pack('Name', Name, 'Source', VmSize, 'IsVmss', InventoryRole == 'Scale-set model',
             'DataDisks', DataDiskCount, 'Nics', ExpectedNics, 'DiskVisibilityComplete', DiskVisibilityComplete,
             'NicVisibilityComplete', NicVisibilityComplete, 'ProvisionedDiskIops', ProvisionedDiskIops,
             'ProvisionedDiskMBps', ProvisionedDiskMBps, 'Placement', PlacementEvidence,
             'RecordMatches', RecordMatches))
    by ResourceId
| extend DataDisks = toint(Context.DataDisks), Nics = toint(Context.Nics),
         MaxDataDisks = iff(tobool(Context.RecordMatches) and DiskLimitRecords == 1, toint(Caps.MaxDataDiskCount), toint('')),
         MaxNics = iff(tobool(Context.RecordMatches) and NicLimitRecords == 1, toint(Caps.MaxNetworkInterfaces), toint(''))
| extend DiskCountCheck = case(isnull(MaxDataDisks) or MaxDataDisks < 0 or isnull(DataDisks)
             or not(tobool(Context.DiskVisibilityComplete)) or tobool(Context.IsVmss), 'Unknown',
             DataDisks > MaxDataDisks, 'Not met', 'Met - count only'),
         NicCountCheck = case(isnull(MaxNics) or MaxNics < 1 or isnull(Nics)
             or not(tobool(Context.NicVisibilityComplete)) or tobool(Context.IsVmss), 'Unknown',
             Nics > MaxNics, 'Not met', 'Met - count only')
| project Resource = tostring(Context.Name), Source = tostring(Context.Source), Target = '{LiveTarget}',
          DataDisks, MaxDataDisks, DiskCountCheck, Nics, MaxNics, NicCountCheck,
          QuotaVcpus = iff(tobool(Context.RecordMatches), toint(Caps.vCPUs), toint('')),
          EffectiveVcpus = iff(tobool(Context.RecordMatches), toint(Caps.vCPUsAvailable), toint('')),
          CpuArchitecture = iff(tobool(Context.RecordMatches), tostring(Caps.CpuArchitectureType), ''),
          CpuVendor = iff(tobool(Context.RecordMatches), tostring(Caps.CpuManufacturer), ''),
          ProvisionedDiskIops = tolong(Context.ProvisionedDiskIops),
          ProvisionedDiskMBps = toreal(Context.ProvisionedDiskMBps),
          TargetUncachedIops = iff(tobool(Context.RecordMatches), tolong(Caps.UncachedDiskIOPS), tolong('')),
          TargetUncachedMBps = iff(tobool(Context.RecordMatches), toreal(Caps.UncachedDiskBytesPerSecond) / 1000000.0, toreal('')),
          TargetCachedIops = iff(tobool(Context.RecordMatches), tolong(Caps.CachedDiskIOPS), tolong('')),
          TargetCachedMBps = iff(tobool(Context.RecordMatches), toreal(Caps.CachedDiskBytesPerSecond) / 1000000.0, toreal('')),
          Placement = tostring(Context.Placement), PerformanceCheck = 'Unknown - validate workload demand and cache paths',
          CheckedAt = now()
| order by Resource asc"""
