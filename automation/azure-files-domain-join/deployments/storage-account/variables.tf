variable "subscription_id" {
  description = "Subscription containing the storage account and shared platform."
  type        = string
}

variable "resource_group_name" {
  description = "Name of the resource group created for this storage account."
  type        = string
}

variable "location" {
  description = "Azure region for this storage account."
  type        = string
}

variable "automation_account_resource_group_name" {
  description = "Resource group containing the shared Automation account."
  type        = string
}

variable "automation_account_name" {
  description = "Name of the shared Automation account."
  type        = string
}

variable "runbook_name" {
  description = "Name of the shared published domain-join runbook."
  type        = string
  default     = "Join-AzureFilesToDomain"
}

variable "hybrid_worker_group_name" {
  description = "Name of the shared Hybrid Runbook Worker group."
  type        = string
}

variable "worker_vm_resource_group_name" {
  description = "Resource group containing the shared worker VM."
  type        = string
}

variable "worker_vm_name" {
  description = "Name of the shared worker VM."
  type        = string
}

variable "key_vault_name" {
  description = "Name of the Key Vault configured for the shared runbook."
  type        = string
}

variable "domain_username_secret_name" {
  description = "Name of the Key Vault secret containing the delegated AD DS username."
  type        = string
}

variable "domain_password_secret_name" {
  description = "Name of the Key Vault secret containing the delegated AD DS password."
  type        = string
}

variable "domain_fqdn" {
  description = "AD DS DNS domain name."
  type        = string
}

variable "organizational_unit_distinguished_name" {
  description = "Distinguished name of the OU for the storage identity."
  type        = string
}

variable "sam_account_name" {
  description = "SAM account name for the AD DS object, without a trailing dollar sign."
  type        = string
}

variable "domain_account_type" {
  description = "AD DS object type created by AzFilesHybrid."
  type        = string
  default     = "ComputerAccount"
}

variable "storage_account_name" {
  description = "Globally unique storage account name."
  type        = string
}

variable "storage_account_replication_type" {
  description = "Storage account replication type."
  type        = string
  default     = "LRS"
}

variable "storage_public_network_access_enabled" {
  description = "Allow storage public endpoint access."
  type        = bool
  default     = false
}

variable "file_share_name" {
  description = "Name of the SMB file share."
  type        = string
  default     = "labshare"
}

variable "file_share_quota_gb" {
  description = "Quota of the SMB file share in GiB."
  type        = number
  default     = 100
}

variable "tags" {
  description = "Tags applied to this storage deployment."
  type        = map(string)
  default     = {}
}

variable "invocation_token" {
  description = "Change this value to intentionally submit another job."
  type        = string
  default     = "initial"
}

variable "readiness_wait_duration" {
  description = "Delay after account-specific RBAC creation before submitting the job."
  type        = string
  default     = "90s"
}