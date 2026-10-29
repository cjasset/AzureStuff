# AzureStuff

Collection of projects and examples related to Azure.

## Policies

- [Require explicit routing models for model router deployments](Policies/Model-Router-Required-Models/README.md): require a non-empty model subset as an explicit-configuration guardrail alongside built-in approved-model governance.
- [Audit mandatory tags on resource groups](Policies/Mandatory-Tags/README.md): coarse-grained verification that required tags are present and have non-empty values, without validating business-specific values.
- [Audit or deny retired and retiring VM sizes](Policies/Retired-VM-SKUs/README.md): retirement guardrails for virtual machines and scale sets, with Audit/Deny effects, a maintained baseline, and an additive VM size picker.

## Guides

- [Windows VM series migration runbook](guides/azure-vm-no-temp-disk-migration/MIGRATION-RUNBOOK.md): migrate between sizes with and without local temporary storage using complete snapshot-derived disk sets, retained-source failback, private-IP preservation and graphical administration.
- [Quick cutover guide](guides/azure-vm-no-temp-disk-migration/README.md): abbreviated checklist, including Premium SSD v2 and SQL Server considerations.

## Automation

- [Azure Files AD DS domain join](automation/azure-files-domain-join): Terraform and Azure Automation proof of concept for deploying domain-joined Azure file shares through a Windows Hybrid Runbook Worker.
