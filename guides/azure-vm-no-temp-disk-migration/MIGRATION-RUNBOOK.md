# Runbook: migrate Windows VMs between series with and without a local temporary disk

**Method:** Retain the deallocated source VM and all its original disks. Snapshot the OS disk and every persistent data disk, create a complete new disk set from those snapshots, and build a separate target from those copies. Azure portal for infrastructure; SSMS and Windows graphical tools for guest administration. No customer scripts, command-line tools or deployment templates.

For an abbreviated checklist, see the [quick cutover guide](./README.md).

## Important notice

This document is provided **as is, without warranty of any kind**, including any warranty of fitness for a particular purpose, uninterrupted operation or absence of data loss. It is guidance, not a guarantee or a substitute for the customer's change approval, recoverable backups, environment-specific review and rehearsal. Existing contractual support terms are not changed by this document.

The customer is responsible for validating applicability, authorizing changes, protecting backups and encryption keys, and approving downtime and recovery. Stop and engage the appropriate Azure/SQL support team if a required setting or portal action is unavailable or differs from this guide. Do not substitute an unreviewed workaround.

Portal controls vary by VM, storage configuration and software version. Rehearse the complete graphical procedure, private-IP transfer and both failback boundaries on a representative non-production copy before approval. Do not promise a downtime duration.

## Problem statement

For **Windows VMs**, Azure does not support a **direct in-place resize** between a size without a local temporary disk and a size with one, in either direction. For example, an **Easv5** VM without local temporary storage cannot be directly resized to an **Ebdsv5** VM with local temporary storage. This is a resize restriction, not a prohibition on migrating the workload.

The workaround in this guide is to retain the deallocated source, snapshot its OS disk and **all persistent data disks**, create a complete new disk set, and deploy a separate compatible target VM. Transfer the private service IP under a write freeze, validate the workload, and retain the source until final acceptance.

**Example only:** `Standard_E16as_v5` (16 vCPUs, 128 GiB RAM, no local temporary disk) to `Standard_E8bds_v5` (8 vCPUs, 64 GiB RAM, 300-GiB local temporary disk, up to 16 data disks). Other sizes have different limits. A change in temporary-disk availability does not require reducing CPU or memory.

This Windows restriction does not apply universally to Linux: Azure supports Linux resizing between disk and diskless sizes subject to the applicable requirements. Local temporary storage is also distinct from an **ephemeral OS disk**, which is outside this managed-OS-disk procedure.

## 1. Scope, constraints and expected outcome

1. Use this guide only for a **standalone Windows VM using a persistent managed OS disk and managed data disks**, remaining in the same subscription, region and subnet. SQL Server is the reference workload: SQL/SSMS/SQL IaaS Agent steps apply only when SQL Server is installed. For other applications, the application owner must supply equivalent graphical backup, quiescing, startup, data-integrity and acceptance procedures.
2. This guide does not cover availability groups, failover cluster instances, shared disks, multiple NIC/IP configurations, Storage Spaces/dynamic-disk arrangements, cross-region migration, ASR-protected VMs or specialized encryption/host-placement configurations. Those require a separate reviewed procedure.
3. Record whether each selected size has local temporary storage, its capacity, CPU/RAM, disk-count limit and storage throughput. Keep the existing **SCSI** controller for this procedure; controller conversion/NVMe migration requires a separate plan.
4. Windows does not support a direct resize between these no-local-disk/local-disk categories. Build a separate target from snapshot-derived copies of the specialized OS disk and **every persistent data disk**. Do not detach, reuse or delete original source disks during forward migration.
5. The Azure portal creates a **new NIC** when creating a VM. It does not offer reuse of an existing NIC in that creation workflow. This guide transfers the original private IPv4 address to the new NIC and manually reapplies NIC-specific configuration. The original NIC is retained on a different, approved address.
6. There is a planned outage. The original IP is briefly unassigned during the controlled transfer. Freeze competing subnet allocations and coordinate with the network/IPAM owner; Azure does not atomically transfer the address between NICs.
7. Intended RPO is zero for committed transactions at the approved write freeze, conditional on preserving current disks, verified recovery and successful validation. There is **no unconditional no-data-loss guarantee**. In-flight/uncommitted transactions may roll back normally.
8. **Keep the original source VM, original NIC and every original disk until final acceptance and the agreed retention period end.** Keep it deallocated while the target runs. Before new target commits, failback can return the service IP and start that VM. After new commits, its old disks are stale: use Section 13's current-state failback.
9. Preserve actual customer drive letters and mount points. The following letters are examples, not directions to reletter production:

   | Purpose | Source | Target |
   |---|---|---|
   | Windows/SQL installation and system databases | Persistent C: | Cloned OS disk, C: |
   | User database data | Persistent F:, for example | Matching cloned data disk, F: |
   | User transaction logs | Persistent G:, for example | Matching cloned log disk, G: |
   | Backups and other durable files | Persistent H:, for example | Matching cloned disk, H: |
   | tempdb data and log, if SQL is installed | Source-compatible location | Target with local disk: optionally ephemeral D:\SQLTemp; target without local disk: approved persistent storage |

10. **Never place durable data, user transaction logs or backups on a local temporary disk.** Where the target has a local resource disk, this guide uses D: as its example letter. If a persistent source volume already uses D:, stop: preserving that letter and reserving D: for temporary storage conflict. Resolve through a separately approved prerequisite change. For a target without a local disk, do not assume any local D: volume will exist.
11. The cloned OS retains the guest computer/domain identity. Never boot source and target simultaneously, even with different IPs. Coordinate domain trust, SPNs, machine-bound certificates and licensing with their owners.

## 2. Prepare the change and prove graphical access

1. Assign an Azure operator, DBA, application owner, network/IPAM owner and failback decision maker.
2. Record the approved outage, decision deadline, acceptance criteria and escalation contacts. Include the extra time needed for snapshots, allocation, SQL recovery, integrity checking, NIC/IP transfer and any restart tests.
3. Sign into the [Azure portal](https://portal.azure.com). Use **Directories + subscriptions** to select the correct customer tenant/subscription. Verify the VM name, resource group, region and zone.
4. Confirm permissions to manage the VM/disks/NIC, SQL VM registration, snapshots, backups and any required role/key permissions.
5. Establish graphical guest access using an existing approved private route and private RDP/jump host. Confirm SSMS can administer SQL and that a separate private application/client connection works.
6. Do not add a public IP or expose SQL/RDP to the Internet. This procedure does not rely on Cloud Shell, Run Command, PowerShell, CLI, SQL query scripts or custom automation.
7. Confirm Windows Disk Management, SQL Server Configuration Manager, SSMS, Event Viewer and File Explorer are available. Confirm credentials are available through the approved secret store.
8. Inventory resource locks, Azure Policy, backup/ASR relationships and automation. Agree how to pause conflicting jobs or remediation and restore controls afterward. Do not casually remove required security controls.
9. Rehearse this graphical procedure with a representative non-production copy, including private-IP transfer and failback. If a necessary option is unavailable, stop before production cutover.

## 3. Check target eligibility and performance

1. In the portal, open **Quotas > Compute**, select the subscription/region, and check regional and **selected target-family** vCPU quotas. Ensure headroom for the target vCPU count and concurrent resources. In the example, this includes 8 target vCPUs in the Ebdsv5 family.
2. Check the selected target size in the region/zone using the portal's VM size picker in a preliminary creation review. Do not create a VM or assume that quota/listed size guarantees allocation at cutover.
3. Verify the target supports the existing OS generation, security type, SCSI controller, managed disk types, zone, NIC features and encryption. Count **all** data disks and compare with that target's limit; the example E8bds_v5 supports up to 16.
4. Stop for unsupported disk types, dependencies or placement constraints. Preserve the existing region/zone; do not silently change them to obtain capacity.
5. Review Azure Monitor and application/SQL performance over peak periods. Justify any CPU/RAM reduction using workload headroom; also check disk/network throughput and latency when moving between families.
6. Use the **SCSI**, not NVMe, performance specifications for the selected target. Do not assume another member of the same family has the example size's limits.
7. Have the DBA approve target max server memory, MAXDOP, parallelism and tempdb settings using Section 10. Record the original settings for failback.
8. Confirm SQL/Windows licensing. If guaranteed allocation is required, arrange a supported capacity solution with Azure before the outage; source-size failback allocation is also not guaranteed.
9. For **Premium SSD v2**, verify region/zone eligibility for both VMs and restored disks. In most zonal regions the VM and disks must share an availability zone. Review any nonzonal-to-zonal move separately; do not silently change placement.
10. Premium SSD v2 is **data-disk only**, not an OS-disk type, and its host cache setting must be **None**. Preserve capacity, logical sector size (512 or 4096 bytes), encryption, zone, IOPS and throughput. A 4096-byte-sector snapshot cannot be restored as a conventional Premium SSD; use compatible Premium SSD v2 disks.
11. Budget for source disks, target disks, snapshots and any current-state failback disk set. Deallocation stops compute billing, not disk/snapshot billing. Copy completion can materially extend downtime; incremental does not mean instantly usable.

## 4. Capture an off-VM inventory

Save screenshots, exported portal views where available, and a written checklist in the approved protected change record **outside this VM**. Do not store passwords/private keys in screenshots or the document.

1. **VM Overview/Properties/JSON View (read-only):** record resource ID, computer name, size, region/zone, availability set, generation, security/Trusted Launch settings, licensing, placement and boot diagnostics. JSON View is for inspection only, not a deployment artifact.
2. **VM > Disks:** record the OS disk resource ID and every data disk resource ID, LUN, size, SKU, caching, delete-with-VM setting and any disk-specific feature. Include non-SQL/backup/audit disks.
3. **Disk resources:** record encryption, Disk Encryption Set/key dependencies, zone and access settings. Establish authorized key recovery independently.
4. **VM > Networking / NIC resource:** record the original NIC ID, subnet, original private IP, allocation method, NSG, accelerated networking, DNS settings, IP forwarding if applicable, ASGs and load-balancer membership. A new NIC does not inherit these settings automatically.
5. **VM > Identity / Access control (IAM):** record managed identities and authorizations. The retained source keeps its original identity; the separate target's system-assigned identity has a **new principal ID**. An OS snapshot does not copy Azure RBAC assignments. Reassociate approved user-assigned identities and preplan target authorizations, especially Key Vault/encryption dependencies. Preserve source access for failback.
6. **VM > Extensions + applications**, monitoring/security pages and **SQL virtual machines**: record configuration and approved recovery/reinstallation procedures. Obtain authorized sources for protected extension settings. Keep source registration/configuration; provision target management separately.
7. **Backup center/vault:** record protection, policies and recent successful recovery points. Confirm the supported reprotection procedure for a recreated VM and continued access to old restore points.
8. In Windows **Disk Management**, record persistent disk identities available in disk properties, volumes, sizes, labels, partition layout, drive letters and folder mount points. Reconcile disks to portal LUNs using device Location information where necessary. Do not rely on Windows disk numbers staying the same.
9. In File Explorer, record important directories and their **Properties > Security** permissions. Confirm all durable files are on persistent disks.
10. In SSMS, record each database's **Properties > Files** paths, **Options** recovery model and current state. Include FILESTREAM, audit, backup and external application locations.
11. Record SQL instance/version/edition, service accounts/start modes, jobs/schedules, logins, certificates/TDE dependencies, linked servers and application authentication. Record existing tempdb file paths, count, sizes and growth.
12. Record database/application reconciliation totals and last committed business transaction identifiers through existing graphical reports. Decide how the application owner will prove continuity after the freeze; a healthy database icon alone is insufficient.

## 5. Prepare disks, network and independent recovery

### A. Protect resources and plan the IP transfer

1. Open the original NIC > **IP configurations** > primary configuration. Record the current private IP. Set allocation to **Static**, retaining that exact address, and select **Save**.
2. Keep Windows NIC IPv4 configuration on **Obtain an IP address automatically**. Azure supplies the static allocation through DHCP.
3. With the network owner, record three addresses in the same subnet:
   - **Original service IP:** the address applications must continue using.
   - **Target provisional IP:** used briefly by the new NIC before transfer.
   - **Original NIC parking IP:** an approved unused address to hold the retained old NIC.
4. Confirm address availability and freeze competing allocations for the transfer. Record NIC-specific configuration to reproduce. No IP may be assigned to two NICs simultaneously.
5. In **VM > Disks**, clear delete-with-VM options for the OS disk and every data disk where exposed; save and recheck. Confirm NIC retention through its applicable settings/deletion review.
6. **Do not delete the source VM, its SQL VM registration, NIC or disks during cutover.** Pause conflicting source automation. Never delete the resource group. Cleanup is a separate final approval after acceptance/retention. Also protect target disks/NIC against accidental delete-with-VM.
7. Leave permanent letters unchanged. If the target has a local disk and D: is its planned letter, reserve D: for it. If an empty optical drive occupies D:, use Disk Management to move **only that optical drive** to an unused letter.
8. Review pagefile settings through Windows **System > Advanced system settings > Performance > Settings > Advanced > Virtual memory**. Keep the source pagefile on valid persistent storage with sufficient free space. If changes require a reboot, schedule and verify it before cutover.

### B. Extra preparation when removing local temporary storage

1. If the source has a local temporary disk and the target does not, inventory **every** dependency on that disk: pagefile, SQL tempdb, application scratch/cache paths, service startup dependencies and folder-initialization settings.
2. Move the pagefile to persistent storage through the Windows dialog above. For example, set C: to **System managed size > Set**, remove the local-disk pagefile with **No paging file > Set**, and restart Windows. Verify the settings and adequate persistent free space.
3. If SQL Server is installed, move **all tempdb data/log files** to approved persistent storage using supported portal SQL VM storage configuration. Recreate required directories/permissions, disable or reconfigure local-disk folder initialization, restart SQL, and verify all paths in SSMS. If graphical controls cannot complete this relocation, stop for SQL support; do not boot a diskless target with tempdb paths pointing to a missing drive.
4. Reconfigure application scratch/cache paths and startup dependencies through their supported graphical tools. Persist required configuration and ensure the target can recreate disposable content without a resource disk.
5. Reboot and validate the source with these persistent settings before the final write freeze/snapshots. Its original disks must remain a bootable failback set after this preparation.
6. Temporary-disk contents are **not included** in the managed-disk snapshot set. If any data that must be retained is found there, stop and protect/migrate it to verified persistent storage before proceeding.

### C. Verify recoverable backups

1. In SSMS, right-click each user database > **Tasks > Back Up**. Take a Full backup, selecting **Copy-only backup** when appropriate to preserve the established differential base.
2. In backup **Media Options**, select checksum and verification options exposed by the SSMS version, including **Perform checksum before writing to media** and **Verify backup when finished**. Confirm successful completion.
3. Back up master, msdb and model using the corresponding supported graphical backup workflow. Confirm encryption certificates/private keys, backup encryption material and authorized recovery access are already protected under the customer's established process. If required key recovery cannot be proven through that process, stop.
4. Store verified copies on independently protected off-VM storage using the organization's approved graphical backup/storage tools. A backup on this VM's H: alone is not independent protection.
5. On an **isolated compatible SQL instance**, use SSMS **Databases > Restore Database** to restore the backups under test names with different target file paths. Confirm needed encryption material is available. Do not overwrite production.
6. Run a full integrity check on the restored databases through an SSMS **Maintenance Plan > Check Database Integrity Task**, selecting the restored databases and leaving **Physical only** unchecked. Execute it with the DBA, then inspect plan history/report output for success. SQL Agent must be available to execute the plan.
7. Record the restore, integrity results and application reconciliation evidence. Backup verification alone does not replace an actual test restore. Stop on any failure.
8. For FULL/BULK_LOGGED databases, confirm the existing log chain and the final transaction-log backup procedure in **Tasks > Back Up > Backup type: Transaction Log**. Do not change recovery models merely for this migration; SIMPLE databases do not support log backups.

## 6. Freeze writes, shut down and snapshot

1. Confirm all prerequisite sign-offs and the failback deadline. Record UTC start time when the application write freeze begins.
2. Place applications in maintenance mode through their normal management interface. Drain connections/transactions. Stop all ETL, integrations, scheduled tasks and other writers, not just the main application.
3. In SSMS **SQL Server Agent > Jobs**, record and disable job schedules/jobs as appropriate. Confirm no executions remain. In **Activity Monitor**, review sessions/processes and resolve active application work under DBA control. Activity Monitor alone does not prove that every external writer is gated.
4. Capture the final committed business identifiers/reconciliation totals through agreed application reports. Confirm no additional writes occur.
5. Take final transaction-log backups where applicable and verify/copy them off VM. Use the approved final backup procedure for SIMPLE databases.
6. With applications gated, apply the DBA-approved **max server memory** for a smaller-memory target before shutdown, if SQL is installed. This avoids starting a smaller target with a source-sized allocation. For a same/larger-memory target, review the budget and schedule any increase after validation. Record original settings and approved changes.
7. In **SQL Server Configuration Manager**, record Engine/Agent start modes, set both to **Manual** for every instance, then stop Agent followed by Engine. Also prevent application services from auto-starting.
8. Confirm services are Stopped, and review SQL error logs/Event Viewer for clean shutdown. Do not force-kill SQL.
9. In Azure portal > VM > **Stop**, confirm deallocation. Wait until Overview shows **Stopped (deallocated)**. A Windows shutdown alone is insufficient.
10. For the OS disk and **each data disk**, open its disk resource > **Create snapshot**. Choose an approved unique name/resource group, same region and compatible encryption/storage settings. Select **Incremental**; Premium SSD v2 requires incremental snapshots. Record a manifest mapping source disk ID, role, LUN, sector size and snapshot ID to this frozen set; select **Review + create > Create**.
11. Wait for successful provisioning of every snapshot. For Premium SSD v2, also require **background copy completion of 100% before creating disks**. Succeeded alone is insufficient. Inspect snapshot Overview/properties or read-only JSON View for `completionPercent`; refresh until complete. If progress cannot be confirmed graphically, stop and obtain support. Keep source access/encryption dependencies available.
12. Keep the VM deallocated throughout the snapshot sequence. With SQL stopped and disks stable, the sequential snapshots represent that stable state. Do not treat uncoordinated online snapshots as equivalent.
13. Confirm the source remains deallocated with its original OS/data disks attached, its NIC retained and its SQL VM registration intact. Obtain authorization to build the separate target.
14. Optional earlier incremental snapshots can pre-seed the chain and help estimate copy time, but they are not the cutover set. Never mix earlier OS/data snapshots with the final frozen set. Each incremental snapshot reconstructs its full disk; do not assemble delta files manually or delete snapshot families during migration.

## 7. Create the complete cloned disk set and separate target

1. Keep the source deallocated. Open **Snapshots > final OS snapshot > Create disk**. Use a unique target disk name, the original region/compatible zone, OS type, generation and security/encryption configuration. Use the original appropriate OS-disk storage type, **not Premium SSD v2**.
2. Repeat **Create disk** for **every** final data-disk snapshot, including backup/audit/non-database disks. Record source disk > final snapshot > target disk in the manifest.
3. For Premium SSD v2 copies, select **Premium SSD v2**, retain sector size/capacity, and explicitly configure the recorded IOPS/throughput and compatible encryption/zone. Do not silently accept new defaults. Stop if the portal cannot express a compatible restore.
4. Wait for successful provisioning and for **every Premium SSD v2 restored disk's background copy to reach 100%**. Inspect copy progress/properties or read-only JSON View (`completionPercent`). Attaching earlier may be permitted but can degrade I/O; this runbook intentionally waits.
5. Verify target disk resource IDs are **new** and source disks remain attached to the original VM. Never attach an original and its clone to the same VM: their guest partition identities can collide.
6. Open **Disks > cloned OS disk > Create VM**. Use this specialized OS copy, not a fresh marketplace installation. Do not generalize/sysprep it. Choose a **different Azure VM resource name**; the cloned guest computer name stays the same.
7. In **Basics**, select the original subscription/resource group, original region/compatible zone/security and the **approved target size** (for example, Standard_E8bds_v5). In **Disks**, attach **every matching cloned data disk** at its original LUN. Preserve supported cache settings; **None for Premium SSD v2**.
8. If the creation wizard cannot attach every data disk at the correct LUN, create with SQL/application services still Manual, immediately **Stop (deallocate)** the new VM, then use **VM > Disks > Attach existing disks** to attach the complete list before starting SQL. No application write access is allowed during this interval.
9. Where the creation experience exposes disk controller type, choose **SCSI**. Verify the resulting VM configuration using **JSON View (read-only)**, including its storage profile. If SCSI cannot be selected/confirmed, stop and obtain support; do not assume the target uses it.
10. In **Networking**, select the original VNet/subnet, create a **new NIC**, choose **Public IP: None**, and select the approved NSG. Do not add public RDP/SQL access.
11. Preserve compatible licensing, diagnostics and other required settings. Review the entire disk/network/security summary and create the VM.
12. The portal may start the VM automatically. SQL/application services must remain gated/Manual. Confirm target provisioning and boot health before continuing.

## 8. Transfer the private IP and reproduce networking

1. Keep writers gated. Stop/deallocate the target before network changes. Confirm the source is deallocated; its original NIC stays attached to it.
2. On the new NIC, record its provisional address. If necessary, set its primary private IPv4 allocation to **Static** at the network-owner-approved provisional IP; save.
3. Reapply the original NIC's compatible NSG, DNS servers, accelerated networking, IP forwarding, ASG and load-balancer backend settings through the respective portal blades. Maintain the intended private routing/security posture.
4. Reconfirm the original service IP is still owned only by the retained original NIC and that the parking IP is available.
5. Open **original NIC > IP configurations > primary configuration**. Change its static address to the approved **parking IP**, then **Save**. Confirm the service IP is no longer assigned there. Do not delete the original NIC.
6. Immediately open **new NIC > IP configurations > primary configuration**. Set **Static**, enter the **original service IP**, and **Save**.
7. Verify the new NIC now owns exactly the recorded service IP, in the original subnet, with no public IP. Verify the original NIC owns only its parking IP.
8. If assignment fails or another resource has acquired the service IP, stop. Keep writers gated and resolve with the network owner; do not proceed with a different service address.
9. Verify all NIC-specific dependencies now point to the **new NIC** where required. An unchanged IP does not preserve NIC ID, MAC address or interface-bound configuration/licensing. Verify service DNS records/aliases resolve to the service IP, not the parked source. Azure-provided DNS tied to the retained source VM name can still follow its parked NIC; do not assume the cloned Windows name transfers that Azure record. Resolve any required naming change with the DNS/application owner before cutover, or stop if the required endpoint cannot be preserved.
10. Start the target. Via private guest access, open Windows **Network Connections > adapter Status > Details** and verify address, DNS and gateway. Keep DHCP enabled. Review any hidden/old-adapter dependencies with the network owner.
11. Test connectivity from the **actual private application/client network**, not just the guest itself. Resolve routing, firewall, DNS, domain trust and service-account/SPN issues before reopening writes.

## 9. Verify persistent disks, SQL and integrations

1. Keep SQL Engine/Agent and application writers stopped until the storage checks pass.
2. Compare **VM > Disks** to the snapshot/clone manifest: **new** OS/data disk IDs tied to the correct final snapshots, complete disk count, original LUNs, supported caching/encryption. Reopen each disk resource after attachment and verify the **actual OS and data-disk storage SKUs**, not merely the earlier disk-creation selection. Confirm the approved target size/SCSI, security/zone, sector sizes and Premium SSD v2 IOPS/throughput/copy completion.
3. In Windows **Disk Management**, verify every persistent volume is Healthy and online with its original letter/mount point, label, size and partition layout. Guest mappings are copied with the OS/data set; Azure resource IDs and hardware identifiers can change. Reconcile to the manifest, not identical Azure/SCSI IDs. Never initialize/format a cloned disk. If letters/mounts differ, keep SQL stopped and positively identify volumes before an approved correction.
4. For a target with local storage, positively identify its resource disk (D: in this guide) and distinguish it from persistent disks. For a target without local storage, verify no service, pagefile, tempdb or application path still depends on a missing resource disk. Resolve letter conflicts while workload services stay stopped; stop if any durable volume is missing or cannot be reconciled.
5. In File Explorer, verify data/log/backup/audit paths and service-account permissions. Check all non-SQL durable files as well.
6. Restore required identities and authorizations through portal **Identity/IAM** and relevant service access controls. Validate key access before allowing dependent SQL features to start.
7. Start SQL Engine through SQL Server Configuration Manager, leaving Agent/writers stopped. Wait for database recovery; inspect SQL error logs and Event Viewer for errors.
8. In SSMS, confirm all expected databases are online. Check **Properties > Files/Options** against the saved paths/recovery models; validate logins, permissions, certificates, linked servers and application dependencies.
9. Perform the agreed full database integrity checks using **Maintenance Plans > Check Database Integrity Task**, with **Physical only** unchecked. If Agent is required, temporarily start it only after confirming unrelated jobs are disabled; inspect execution history and stop it again as appropriate.
10. Compare final pre-freeze business identifiers, counts and reconciliation totals using the same application reports. Have the application owner verify continuity. Integrity checks alone do not prove every business transaction is present.
11. If necessary, use portal **SQL virtual machines > Register** to register the existing Azure VM with the SQL IaaS Agent extension. Select the correct existing VM and license/edition; verify successful registration/extension health. If the portal does not offer registration for this VM, stop and obtain support rather than using a script.
12. Restore saved automated backup/patching, monitoring/security, assessment and Key Vault settings through their portal pages. Do not run automatic data/log storage creation or repartition existing disks.
13. Reestablish VM/SQL backup protection through the existing vault's supported recreation/reprotection workflow. Confirm old restore points remain available and obtain a new successful recovery point under the agreed acceptance plan.

## 10. Tune SQL for the target CPU and memory

These are **DBA-approved, workload-specific settings**, not universal values. Record before/after screenshots for every SQL instance. Cloning the OS reproduces old settings; Azure does not necessarily resize them appropriately.

### A. Max/min server memory

1. In SSMS Object Explorer, right-click the server > **Properties > Memory**.
2. Record **Minimum server memory (in MB)** and **Maximum server memory (in MB)**. Recalculate the maximum for the target's actual physical RAM, leaving headroom for Windows, monitoring/security/backup agents, other instances and SQL allocations outside this setting. This option is not a hard cap on all SQL process memory.
3. For illustration only, **49,152 MB (48 GiB)** would leave 16 GiB outside that setting on a 64-GiB machine. It is **not a prescribed value**; a multi-instance or agent-heavy VM may require a lower maximum.
4. Do not mechanically scale the old value, leave an oversized source allocation, assign all target RAM to SQL, or give each instance the full budget. Allocate a combined budget across instances.
5. Review minimum memory as well; do not set it equal to maximum without a justified design. An existing minimum must remain compatible with the new maximum.
6. For a smaller-memory target, apply the approved maximum after the write freeze and **before shutdown** (Section 6). Reopen the target's Memory page and verify its budget before workload. Increase memory for a larger target only under DBA approval after validating actual available RAM. These options generally do not require a SQL restart.

### B. MAXDOP and cost threshold for parallelism

1. Confirm SQL sees the expected target CPU count using SSMS server properties/reports and SQL startup error-log entries. Review actual NUMA/soft-NUMA topology with the DBA; do not infer it solely from the Azure size name.
2. In SSMS, right-click the server > **Properties > Advanced > Parallelism**.
3. Record **Max Degree of Parallelism** and **Cost Threshold for Parallelism**.
4. Select MAXDOP using Microsoft's topology guidance and workload evidence. With one node and eight or fewer logical processors, the guideline is not to exceed the logical processor count; on multi-node layouts, respect processors per NUMA node. **MAXDOP 8 is not an automatic recommendation** for every 8-vCPU workload; 4 or another lower value may be appropriate.
5. Do not blindly retain a source-sized MAXDOP or change to 0 without review. Apply the DBA-approved value, select **OK**, reopen the page and verify it. MAXDOP changes generally do not require restarting SQL.
6. Review cost threshold alongside MAXDOP, but avoid an unrelated arbitrary increase during cutover. Retain the baseline unless a reviewed change is justified.
7. Account for database-scoped, Resource Governor and query-level overrides. Changing the server property does not override all limits; use supported SSMS properties/reports and DBA review. Do not introduce CPU affinity or worker-thread changes merely because the VM size/family changed.

### C. Tempdb capacity and follow-up

1. Use Section 11 to select a compatible tempdb location: approved local temporary storage with managed directory recreation, or persistent storage when local storage is unavailable or not selected.
2. Have the DBA approve data-file count, equal initial data-file sizes, fixed-MB growth, log size and capacity headroom alongside any pagefile/other platform use of the local disk.
3. Review tempdb file count using actual logical processors and contention. For the example eight-processor target, eight equally sized data files are an initial guideline, not an instruction to add/remove files blindly. Larger CPU counts do not imply one file per CPU without limit. Preserve a safe working configuration until approved; do not shrink files routinely.
4. Use **SSMS Activity Monitor**, existing **Query Store reports** (if already enabled), SQL/Windows logs, Windows Task Manager/Resource Monitor and Azure Monitor to compare CPU, paging, available memory, grants/waits, query duration, tempdb contention and disk latency/throttling.
5. Define thresholds, a peak-period observation window and who can adjust settings or initiate failback. Do not call tuning complete based only on an idle VM. Preserve source settings for failback to the original size.

## 11. Configure tempdb for the target's storage capabilities

This section applies only to SQL Server. For other workloads, validate their approved scratch/cache locations and initialization instead.

### A. Target has a local temporary disk and will use it for tempdb

1. Confirm the selected target actually has compatible local temporary storage. Positively identify D: as that resource disk and verify capacity/permanent volumes. Keep writers/jobs gated. A target that needs striping/initialization of multiple local devices requires a separate storage plan.
2. In portal **SQL virtual machines > the VM > Storage configuration**, select **Configure** beside tempdb.
3. Choose **Yes** for **Configure tempdb data files**, set the intended location to **D:\SQLTemp**, and review count, initial sizes and growth with the DBA.
4. Choose **Yes** for **Manage tempdb database folders on restart**, so SQL IaaS Agent manages the required folder and permissions after temporary storage is lost.
5. Review every proposed change. Do not select creation/extension of the persistent data/log storage pools. Save/apply only the approved tempdb configuration.
6. Verify that **both tempdb data and log files** will use D:\SQLTemp through the available configuration and subsequent SSMS verification. If the portal cannot express/complete the required relocation or folder management, stop and obtain support. This guide does not substitute a custom startup script or claim SSMS can edit an existing file's path on its Files page.
7. Follow the portal's service-restart requirement. Through SQL Server Configuration Manager, stop Agent, cleanly restart Engine, and wait for recovery while writers remain gated.
8. In SSMS, expand **Databases > System Databases > tempdb > Properties > Files**. Confirm every data/log path, count, size and growth setting. In File Explorer, verify files exist on D: and folder **Properties > Security** grants the actual SQL service identity appropriate access.
9. Validate application workflows that use temporary objects through the approved graphical application test. Confirm no user database data/logs/backups were moved to D:.
10. Rehearse local-temp-storage loss/recreation and managed startup behavior in non-production. A normal restart may preserve the disk and alone is not proof of recovery after disk loss. Schedule any production restart/deallocation test explicitly; it is an additional interruption.
11. Restore service start modes only when the chosen **managed** folder-recreation mechanism is healthy and its startup ordering is proven. Do not rely on a one-time manual folder creation.
12. If managed tempdb configuration fails, keep writes gated and either return to the known working persistent tempdb location through supported portal configuration or initiate the failback/escalation plan. Do not accept an unbootable SQL instance.

### B. Target has no local temporary disk, or tempdb will remain persistent

1. Use the approved persistent tempdb location, with sufficient capacity, throughput and SQL service-account permissions. For a local-disk-to-diskless migration, Section 5B must already have removed local-disk dependencies **before snapshots**.
2. In SSMS **tempdb > Properties > Files**, verify every data/log path points to an existing persistent volume. Do not attempt to edit an existing physical path through this Files page; use the supported storage configuration/restart procedure for relocation.
3. Verify the pagefile and application scratch paths also use valid storage. Disable or reconfigure any initialization that waits for a nonexistent D: resource disk.
4. Restart SQL under the write gate, validate database recovery and an approved temporary-object application workflow, and review persistent-disk capacity/latency before reopening writes.

## 12. Acceptance, reopen and monitor

1. Obtain DBA/application/network sign-off that permanent disks/letters/mounts, database recovery, integrity/reconciliation, identity access, original service IP, private client authentication and target tuning all pass.
2. **Before the first target business commit, explicitly close the simple-failback window with the decision maker.** An approved test commit also counts. Through the actual private client/application GUI, commit/read back the agreed transaction and record its identifier. Thereafter use current-state failback, not the stale original disks alone.
3. Restore intended services, jobs, schedules and integrations in a controlled order. Avoid duplicate writers and respect the managed tempdb startup configuration.
4. Reopen application writes. Record UTC end time and actual outage. Monitor new commits, connections, application errors and latency.
5. Once new writes are accepted, **do not restore pre-cutover snapshots as an ordinary rollback**; they exclude the new commits.
6. Observe the agreed peak-load interval and successful jobs/backups. Review memory/MAXDOP/tempdb decisions against actual load and adjust under the approved change process.
7. Verify backup protection/new recovery points, monitoring/alerts, security settings and paused automation are restored. Confirm no public IP/access was introduced.
8. Retain the **deallocated original VM, every original disk, original NIC**, snapshots, inventories and independent backups for the approved period. Record charges and cleanup ownership. Do not automatically delete anything at sign-off; use the final cleanup approval below.

## 13. Failback and recovery

### A. Simple failback: before any new target business commits

1. Keep writers/jobs gated. Confirm no target business commits occurred, including validation transactions. If uncertain, use B instead.
2. Cleanly stop target Agent/Engine and deallocate the target. Leave all its disks intact.
3. Keep the source deallocated during IP transfer. In portal, park the target NIC at its approved provisional IP; return the original service IP as **Static** to the original source NIC. Verify unique ownership, original subnet/security/DNS and no public IP.
4. Start the **retained source VM with its original disks**. Use its recorded source-compatible tempdb/pagefile/application settings, not assumptions about target temporary storage. Start workload services under the gate and validate every letter/mount, database state, integrity, reconciliation and private-client access.
5. Review source memory/MAXDOP/start modes and management/backup health. Obtain owner sign-off before reopening writes. Do not boot target while source is running.
6. If the IP was never moved, omit only the unnecessary transfer. Deallocation releases compute capacity, so source restart allocation is still not guaranteed.

### B. After target commits: bring current state back to the retained source

Starting the source on its untouched old disks **would lose visibility of new commits**. This branch retains both VM resources and uses fresh copies of the complete current target state.

1. Freeze target writers again, drain transactions, capture the **latest** committed identifiers/totals and take fresh verified independent backups/log backups where applicable. Keep the original source deallocated.
2. Make the current OS configuration compatible with the **original source size** before cloning it back. Put the pagefile and all tempdb/application scratch dependencies on approved persistent storage where required; a diskless source has no resource D:. For SQL, use supported portal storage configuration to move **all** tempdb data/log files, ensure directories/ACLs exist, disable/reconfigure local-disk initialization, restart SQL and verify all persistent paths in SSMS. If graphical relocation cannot be completed, stop for support.
3. Set Engine/Agent/application services to Manual, cleanly stop SQL and deallocate target. Snapshot its **current OS and every current data disk** as a new, complete frozen set. Do not use the older cutover snapshots or mix generations.
4. Repeat Sections 6-7's snapshot/disk-copy gates: Premium SSD v2 incremental snapshots at 100%, then new disks from every current snapshot with preserved sector size, zone, encryption, IOPS/throughput and restored-disk copy at 100%. Record the return manifest.
5. Record the retained source VM's current disk list. Keep both VMs deallocated. In source **VM > Disks**, detach each old source data disk **without deleting it**. Save; verify those original disks remain available for recovery.
6. On the retained source VM's **Disks** blade, select **Swap OS disk** and the **new current-state cloned OS disk**. Verify compatible Windows generation/security/encryption, the required matching OS-disk size, and the same Disk Encryption Set where applicable. If target OS size/encryption changed since cutover, stop for a reviewed compatibility plan. Confirm the old OS disk will be retained; do not use target's still-attached OS disk. If portal swap is unavailable/incompatible, stop and engage support.
7. Attach **all matching current-state cloned data disks** to source at their original LUNs; **None** caching for Premium SSD v2. Save and reconcile the complete manifest before booting. Never attach old and cloned copies together.
8. Reverse the IP transfer with both VMs deallocated: park target NIC, restore the original service IP to the **original source NIC**, verify ownership/security/DNS. Neither VM resource is deleted.
9. Start source; verify original letters/mounts, current SQL paths and **all post-cutover committed identifiers**, full integrity, authentication, integrations and source-appropriate memory/MAXDOP. Verify source-managed identity/SQL registration/backup protection against the swapped OS. Reopen only after owner sign-off.
10. Keep target deallocated, all old source disks separately retained and the current snapshot/clone set protected. No cutover snapshot rollback is a substitute for this current-state transfer. Copy/allocation/recovery time extends the failback outage.

### C. Current target disks or SQL are unusable

1. Keep writers gated and engage DBA/Azure recovery owners. Do not improvise deletion or mix disk generations.
2. Before any new target commits, A can use the retained source. After commits, recover the required current state from independently verified current backup/log chains through SSMS graphical restore workflows under DBA direction.
3. Prove the recovery boundary and reconcile all required business transactions. If commits cannot be recovered, report the actual gap; do not claim zero loss or start a stale source as though it were current.

## 14. Final customer acceptance record

Record pass/fail, evidence location, owner and UTC time for:

- Every source > snapshot > cloned disk mapping, complete disk count, LUN/cache, volume letter/mount, sector size and Premium SSD v2 performance/copy completion.
- Same private service IP on the new NIC; intended DNS/NSG/ASG/load-balancer behavior; no public IP.
- SQL database state, full integrity checks, business reconciliation and a new private-client committed transaction.
- Target max/min server memory, MAXDOP, parallelism review and tempdb settings.
- Target-compatible pagefile/scratch configuration; for SQL, every tempdb path verified on approved local or persistent storage. Local tempdb requires healthy managed folder recreation and non-production loss/restart evidence.
- Restored identities, SQL VM management, security/monitoring and backup protection.
- Actual outage, failback decision point, known exceptions, retained recovery resources and cleanup ownership.

### Final cleanup: only after acceptance and the retention period

1. Obtain written authorization that the rollback window has closed, independent recovery is proven and source disks/snapshots are no longer required. Confirm which VM is the accepted production owner of the IP and latest commits.
2. Keep the other VM deallocated. Remove only its explicitly approved management/backup relationships through supported portal workflows; do not affect the accepted VM.
3. Only now may the old VM be deleted. In its deletion review, **uncheck every disk/NIC/associated-resource deletion selection** unless separately approved for disposal. Verify the accepted target and shared network resources are not selected.
4. Dispose of old disks, snapshots and unused NICs only through a separately reconciled retention/encryption/security review. Do not delete the resource group as a shortcut. Record retained evidence and final ownership.

## Microsoft references

Portal labels and features can change. Review the current documentation and the customer's exact versions before execution.

- [Windows resize restrictions for VMs without a local temporary disk](https://learn.microsoft.com/azure/virtual-machines/azure-vms-no-temp-disk)
- [Ebdsv5/Ebsv5 capabilities and separate SCSI/NVMe performance tables](https://learn.microsoft.com/azure/virtual-machines/sizes/memory-optimized/ebdsv5-ebsv5-series)
- [Create a Windows VM from an existing specialized OS disk](https://learn.microsoft.com/azure/virtual-machines/attach-os-disk)
- [Portal limitation on existing NIC reuse at VM creation](https://learn.microsoft.com/azure/virtual-network/virtual-network-network-interface-vm)
- [Configure NIC private IP addresses](https://learn.microsoft.com/azure/virtual-network/ip-services/virtual-network-network-interface-addresses)
- [SQL VM storage and managed tempdb folder configuration](https://learn.microsoft.com/azure/azure-sql/virtual-machines/windows/storage-configuration)
- [SQL IaaS Agent registration](https://learn.microsoft.com/azure/azure-sql/virtual-machines/windows/sql-agent-extension-manually-register-single-vm)
- [SQL Server memory configuration](https://learn.microsoft.com/sql/database-engine/configure-windows/server-memory-server-configuration-options)
- [MAXDOP configuration and topology guidance](https://learn.microsoft.com/sql/database-engine/configure-windows/configure-the-max-degree-of-parallelism-server-configuration-option)
- [SSMS database file properties and existing-path limitations](https://learn.microsoft.com/sql/relational-databases/databases/database-properties-files-page)
- [Incremental snapshots, Premium SSD v2 sector restrictions and background copy](https://learn.microsoft.com/azure/virtual-machines/disks-incremental-snapshots)
- [Premium SSD v2 deployment, availability and caching limitations](https://learn.microsoft.com/azure/virtual-machines/disks-deploy-premium-v2)
- [Swap a Windows VM OS disk](https://learn.microsoft.com/azure/virtual-machines/windows/os-disk-swap)
