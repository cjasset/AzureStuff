output "resource_group_id" {
  description = "Resource ID of the shared platform resource group."
  value       = azurerm_resource_group.platform.id
}

output "automation_account_id" {
  description = "Resource ID of the shared Automation account."
  value       = module.automation_platform.automation_account_id
}

output "automation_runbook_id" {
  description = "Resource ID of the shared domain-join runbook."
  value       = module.automation_platform.runbook_id
}

output "hybrid_worker_group_id" {
  description = "Resource ID of the shared Hybrid Runbook Worker group."
  value       = module.automation_platform.hybrid_worker_group_id
}

output "worker_vm_principal_id" {
  description = "Principal ID used by storage deployments for scoped RBAC."
  value       = module.automation_platform.worker_vm_principal_id
}