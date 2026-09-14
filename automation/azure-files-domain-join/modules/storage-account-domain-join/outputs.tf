output "storage_account_id" {
  description = "Resource ID of the Azure Files storage account."
  value       = azurerm_storage_account.files.id
}

output "file_share_id" {
  description = "Resource ID of the SMB file share."
  value       = azapi_resource.file_share.id
}

output "automation_job_id" {
  description = "Resource ID of the submitted Automation job."
  value       = "${var.automation_account_id}/jobs/${random_uuid.job.result}"
}

output "automation_job_name" {
  description = "UUID used as the Automation job name."
  value       = random_uuid.job.result
}

output "automation_job_status" {
  description = "Job status returned when Azure accepted the request."
  value       = try(azapi_resource_action.runbook_job.output.properties.status, null)
}

output "automation_job_portal_url" {
  description = "Azure portal URL for the submitted Automation job."
  value       = "https://portal.azure.com/#resource${var.automation_account_id}/jobs/${random_uuid.job.result}"
}