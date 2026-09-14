variable "subscription_id" {
  description = "Subscription containing the platform resources and worker VM."
  type        = string
}

variable "resource_group_name" {
  description = "Name of the resource group created for the shared Automation platform."
  type        = string
}

variable "location" {
  description = "Azure region for the shared Automation platform."
  type        = string
}

variable "automation_account_name" {
  description = "Name of the shared Automation account."
  type        = string
}

variable "runbook_name" {
  description = "Name of the shared domain-join runbook."
  type        = string
  default     = "Join-AzureFilesToDomain"
}

variable "hybrid_worker_group_name" {
  description = "Name of the shared Hybrid Runbook Worker group."
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
  description = "Tags applied to shared platform resources."
  type        = map(string)
  default     = {}
}