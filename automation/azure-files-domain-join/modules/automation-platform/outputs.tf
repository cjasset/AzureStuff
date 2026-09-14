output "automation_account_id" {
  description = "Resource ID of the shared Automation account."
  value       = azurerm_automation_account.this.id
}

output "automation_account_principal_id" {
  description = "Principal ID of the Automation account system-assigned identity."
  value       = azurerm_automation_account.this.identity[0].principal_id
}

output "runbook_name" {
  description = "Name of the published domain-join runbook."
  value       = azurerm_automation_runbook.domain_join.name
}

output "runbook_id" {
  description = "Resource ID of the published domain-join runbook."
  value       = azurerm_automation_runbook.domain_join.id
}

output "hybrid_worker_group_name" {
  description = "Name of the shared Hybrid Runbook Worker group."
  value       = azurerm_automation_hybrid_runbook_worker_group.this.name
}

output "hybrid_worker_group_id" {
  description = "Resource ID of the shared Hybrid Runbook Worker group."
  value       = azurerm_automation_hybrid_runbook_worker_group.this.id
}

output "worker_vm_id" {
  description = "Resource ID of the registered worker VM."
  value       = local.worker_vm_id
}

output "worker_vm_principal_id" {
  description = "Principal ID of the worker VM system-assigned identity."
  value       = azapi_update_resource.worker_vm_identity.output.identity.principalId
}