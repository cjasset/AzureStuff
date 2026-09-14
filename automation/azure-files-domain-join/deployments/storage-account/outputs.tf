output "resource_group_id" {
  description = "Resource ID of this storage deployment's resource group."
  value       = azurerm_resource_group.storage.id
}

output "storage_account_id" {
  description = "Resource ID of the Azure Files storage account."
  value       = module.storage_account_domain_join.storage_account_id
}

output "file_share_id" {
  description = "Resource ID of the SMB file share."
  value       = module.storage_account_domain_join.file_share_id
}

output "automation_job_id" {
  description = "Resource ID of the submitted domain-join job."
  value       = module.storage_account_domain_join.automation_job_id
}

output "automation_job_status" {
  description = "Job status returned when Azure accepted the request."
  value       = module.storage_account_domain_join.automation_job_status
}

output "automation_job_portal_url" {
  description = "Azure portal URL for the submitted domain-join job."
  value       = module.storage_account_domain_join.automation_job_portal_url
}