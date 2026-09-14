variable "subscription_id" {
  description = "Subscription containing the Automation platform and existing worker VM."
  type        = string
}

variable "resource_group_name" {
  description = "Name of the existing resource group for the Automation platform."
  type        = string
}

variable "location" {
  description = "Azure region for the Automation account and runbook."
  type        = string
}

variable "automation_account_name" {
  description = "Name of the Automation account."
  type        = string
}

variable "runbook_name" {
  description = "Name of the published domain-join runbook."
  type        = string
  default     = "Join-AzureFilesToDomain"
}

variable "runbook_content" {
  description = "Rendered PowerShell content for the domain-join runbook."
  type        = string
}

variable "hybrid_worker_group_name" {
  description = "Name of the Hybrid Runbook Worker group."
  type        = string
}

variable "worker_vm_resource_group_name" {
  description = "Resource group containing the existing Windows worker VM."
  type        = string
}

variable "worker_vm_name" {
  description = "Name of the existing Windows worker VM."
  type        = string
}

variable "bootstrap_script_content" {
  description = "PowerShell used by the Custom Script Extension to install worker prerequisites."
  type        = string
}

variable "key_vault_name" {
  description = "Name of the existing RBAC-enabled Key Vault containing AD DS credentials."
  type        = string
}

variable "key_vault_resource_group_name" {
  description = "Resource group containing the existing Key Vault."
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

variable "tags" {
  description = "Tags applied to platform resources that support tags."
  type        = map(string)
  default     = {}
}