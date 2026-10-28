# Azure VM migration: quick guide for sizes with or without a local temporary disk

**Keep the original VM. Migrate copies, not its attached disks.**

**Tools:** Azure portal; SSMS and Windows graphical tools over approved private access. No customer scripts, command-line tools or deployment templates.

**Provided as is, without warranty of any kind.** No guarantee of zero loss or a downtime duration. Obtain customer approval, prove independent backup recovery, and rehearse the [full runbook](./MIGRATION-RUNBOOK.md) using the customer's actual portal/software versions.

## Problem statement

Azure does not support a **direct in-place resize of a Windows VM** between a size without a local temporary disk and a size with one, in either direction. For example, **Easv5** (no local temporary disk) cannot be directly resized to **Ebdsv5** (with local temporary disk). Migrate the workload using a separate VM and snapshot-derived persistent disks instead.

**Example only:** `Standard_E16as_v5` (16 vCPUs / 128 GiB, no local temporary disk) to `Standard_E8bds_v5` (8 vCPUs / 64 GiB, 300-GiB local temporary disk, up to 16 data disks). Use the actual limits of the selected sizes; CPU/memory need not decrease.

**Scope:** standalone Windows VM, persistent managed OS/data disks, same region/subnet, SCSI. SQL Server steps apply only if installed; otherwise use application-owner-approved equivalent backup, quiescing and validation. Not an FCI/AG, multi-NIC, Storage Spaces/dynamic-disk, ephemeral OS disk or specialized encryption/placement procedure. Linux has different resize support; do not apply this Windows restriction universally.

## Before the outage

1. **Check eligibility/capacity:** record source/target local-disk availability, CPU/RAM, disk-count limit and throughput. Check actual target-family/regional quota, region/zone, SCSI, security/generation, licensing and workload headroom. Deallocated source restart capacity is not guaranteed.
2. **Inventory off VM:** source OS/every data disk, LUN/cache, letters/mounts, SQL/tempdb paths, NIC/IP/security/DNS, identities, management/backups and SQL tuning. Preserve source-compatible persistent tempdb paths for failback.
3. **Premium SSD v2:** data disks only; **None** host caching. Preserve sector size, capacity, IOPS/throughput, encryption and compatible zone. Use incremental snapshots. A 4-KiB-sector snapshot needs a compatible v2/Ultra restore, not conventional Premium SSD.
4. **Prove recovery and remove disappearing-disk dependencies:** take independently protected backups and prove isolated restore/integrity (SSMS for SQL). When moving **from local-disk to diskless**, first move the pagefile, all tempdb data/log files and scratch/startup dependencies to approved persistent storage through supported graphical tools, then reboot/validate source before snapshots. Temporary-disk contents are not captured. Define acceptance, failback boundary and retention.
5. **Prepare network/retention:** original service IP Static in Azure; Windows DHCP enabled. Reserve target provisional and source parking IPs. Portal creates a new target NIC; inventory settings to reproduce. Retain **source VM, SQL registration, original disks and NIC until the very end**.

## Cutover: keep writers gated

6. **Freeze:** stop/drain all application, job and integration writers. Record final committed identifiers, take final backups/logs as appropriate. For a smaller-memory SQL target, apply the approved lower max-memory budget in SSMS before shutdown. Set workload services Manual; stop SQL Agent/Engine cleanly if installed.
7. **Deallocate source:** portal **Stop**; require **Stopped (deallocated)**. Keep it stopped throughout snapshots and while target runs. **Do not delete it or detach its disks.**
8. **Snapshot ALL disks:** OS plus every data/log/backup/other persistent disk, from this one stable state. Record source > incremental snapshot manifest. Require successful provisioning; **Premium SSD v2 snapshot background copy must reach 100% before disk creation**.
9. **Create ALL new disks:** each final snapshot > **Create disk**, including OS. Preserve required type, size, sector size, encryption/zone and v2 IOPS/throughput. Require **restored Premium SSD v2 disk copy at 100%** before use; inspect progress/properties or read-only JSON View. Stop if readiness cannot be confirmed graphically.
10. **Create target from cloned OS:** **cloned OS disk > Create VM**, different Azure VM name, approved target size, compatible security/zone and **SCSI**. Attach every matching cloned data disk at its original LUN/cache; v2 cache **None**. New NIC, original subnet, **Public IP: None**. Never attach an original and its clone together. Keep workload services Manual until the complete set is verified.
11. **Transfer service IP:** with both VMs deallocated, reproduce NIC settings, park source NIC, immediately assign original service IP Static to target NIC. Verify unique ownership and service DNS resolution, not the parked source; Azure-provided names do not automatically transfer. Start target only. Cloned guest/domain identities must never run simultaneously.
12. **Validate/tune:** verify new disk IDs against the clone manifest, actual attached storage SKUs, **original permanent letters/mounts**, paths/ACLs, workload recovery/integrity and business totals. Never initialize/format migrated disks. Restore target identities, applicable SQL VM registration, security/monitoring and backup protection. For SQL, in SSMS review:
    - **Server Properties > Memory:** DBA-approved max/min server memory for **actual target RAM**, with OS/agent/other-instance headroom. The example has 64 GiB; that is not a universal target.
    - **Advanced > Parallelism:** MAXDOP against actual CPU/NUMA/workload; review cost threshold/overrides. Do not assume the source setting fits, or prescribe 8 for every target.
13. **Temporary-storage handling:** if target has compatible local storage and SQL tempdb will use it, portal **SQL virtual machines > Storage configuration > tempdb Configure**: all tempdb data/log on **ephemeral D:\SQLTemp**, **Manage tempdb database folders on restart** enabled. Verify paths/ACLs, sizing/growth and storage-loss recovery. If target has **no local disk**, verify persistent tempdb, pagefile and scratch paths; disable initialization that waits for a nonexistent resource disk. If graphical controls cannot complete this, stop for support. Durable data/logs/backups remain persistent; resolve any permanent D: conflict before using D: as a resource disk.
14. **Accept:** explicitly close the simple-failback window **before any new target business/test commit**. Verify private-client commit/readback, obtain sign-off, restore intended jobs/start modes and reopen writes. Monitor CPU/memory, paging, SQL waits/queries, tempdb, disk limits and backups.

## Failback

**Before any new target commits:** stop/deallocate target; return the service IP to the original source NIC; start the retained source VM on its original disks. Verify source-compatible tempdb/pagefile/scratch configuration, original letters, workload state, tuning and connectivity before reopening. No VM reconstruction or disk swap is needed.

**After new target commits:** old disks are stale. Freeze target again; take current verified backups and make tempdb/pagefile/scratch dependencies compatible with the original source size using persistent storage where required. Verify restart. Cleanly stop/deallocate. Snapshot **current OS + ALL current disks**, clone the complete set and wait for both v2 snapshot/disk copies to reach 100%. With both VMs off, retain/detach old source data disks, **Swap OS disk** on the retained source to the new current OS clone, attach all matching current data clones at the original LUNs, then reverse IP transfer. Validate **all new commits**, integrity and source tuning before reopening. Retain old disks separately; neither VM is deleted. See the full runbook for every gate.

**If current state cannot be copied:** use DBA-approved current backup/log recovery. Never simply start the stale source or revert old snapshots after new commits.

## Cleanup last

Keep the old VM deallocated through final acceptance and retention. Only then obtain separate deletion approval; preserve disks/NIC in the deletion review unless explicitly authorized otherwise. Disk/snapshot charges continue while VMs are deallocated. Never delete shared resources or the resource group as a shortcut.

## Reference

[Microsoft: Windows resize restrictions between sizes with and without local temporary disks](https://learn.microsoft.com/azure/virtual-machines/azure-vms-no-temp-disk).
