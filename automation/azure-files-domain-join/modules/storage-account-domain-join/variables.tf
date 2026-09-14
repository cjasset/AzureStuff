variable "subscription_id" {
  description = "Subscription containing the storage account."
  type        = string
}

variable "resource_group_id" {
  description = "Resource ID of the existing resource group for the storage account."
  type        = string
}

variable "resource_group_name" {
  description = "Name of the existing resource group for the storage account."
  type        = string
}

variable "location" {
  description = "Azure region for the storage account."
  type        = string
}

variable "automation_account_id" {
  description = "Resource ID of the shared Automation account."
  type        = string
}

variable "automation_account_principal_id" {
  description = "Principal ID of the Automation account system-assigned identity."
  type        = string
}

variable "runbook_name" {
  description = "Name of the shared published domain-join runbook."
  type        = string
}

variable "hybrid_worker_group_name" {
  description = "Name of the shared Hybrid Runbook Worker group."
  type        = string
}

variable "worker_vm_principal_id" {
  description = "Principal ID of the worker VM system-assigned identity."
  type        = string
}

variable "key_vault_name" {
  description = "Key Vault name passed to the shared runbook."
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

  validation {
    condition     = can(regex("^[^$]{1,20}$", var.sam_account_name))
    error_message = "sam_account_name must be 1-20 characters and must not end with $."
  }
}

variable "domain_account_type" {
  description = "AD DS object type created by AzFilesHybrid."
  type        = string
  default     = "ComputerAccount"

  validation {
    condition     = contains(["ComputerAccount", "ServiceLogonAccount"], var.domain_account_type)
    error_message = "domain_account_type must be ComputerAccount or ServiceLogonAccount."
  }
}

variable "storage_account_name" {
  description = "Globally unique storage account name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must contain 3-24 lowercase letters or numbers."
  }
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
  description = "Tags applied to the storage account."
  type        = map(string)
  default     = {}
}

variable "invocation_token" {
  description = "Change this value to intentionally submit another job when other inputs are unchanged."
  type        = string
  default     = "initial"
}

variable "readiness_wait_duration" {
  description = "Delay after account-specific RBAC creation before submitting the job."
  type        = string
  default     = "90s"
}