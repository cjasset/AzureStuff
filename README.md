# AzureStuff

Collection of projects and examples related to Azure.

## Guides

- [Windows VM series migration runbook](guides/azure-vm-no-temp-disk-migration/MIGRATION-RUNBOOK.md): migrate between sizes with and without local temporary storage using complete snapshot-derived disk sets, retained-source failback, private-IP preservation and graphical administration.
- [Quick cutover guide](guides/azure-vm-no-temp-disk-migration/README.md): abbreviated checklist, including Premium SSD v2 and SQL Server considerations.

## Automation

- [Azure Files AD DS domain join](automation/azure-files-domain-join): Terraform and Azure Automation proof of concept for deploying domain-joined Azure file shares through a Windows Hybrid Runbook Worker.
