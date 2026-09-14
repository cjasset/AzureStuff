locals {
  worker_vm_id = format(
    "/subscriptions/%s/resourceGroups/%s/providers/Microsoft.Compute/virtualMachines/%s",
    var.subscription_id,
    var.worker_vm_resource_group_name,
    var.worker_vm_name,
  )
}

data "azurerm_automation_account" "platform" {
  name                = var.automation_account_name
  resource_group_name = var.automation_account_resource_group_name
}

data "azapi_resource" "worker_vm" {
  type                   = "Microsoft.Compute/virtualMachines@2024-07-01"
  resource_id            = local.worker_vm_id
  response_export_values = ["identity.principalId"]

  lifecycle {
    postcondition {
      condition     = try(self.output.identity.principalId, null) != null
      error_message = "The shared worker VM must have a system-assigned managed identity. Deploy the platform first."
    }
  }
}

resource "azurerm_resource_group" "storage" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags

  lifecycle {
    precondition {
      condition     = lower(var.resource_group_name) != lower(var.automation_account_resource_group_name)
      error_message = "resource_group_name must be dedicated to this storage deployment and cannot match automation_account_resource_group_name."
    }
  }
}

module "storage_account_domain_join" {
  source = "../../modules/storage-account-domain-join"

  subscription_id                        = var.subscription_id
  resource_group_id                      = azurerm_resource_group.storage.id
  resource_group_name                    = azurerm_resource_group.storage.name
  location                               = azurerm_resource_group.storage.location
  automation_account_id                  = data.azurerm_automation_account.platform.id
  automation_account_principal_id        = data.azurerm_automation_account.platform.identity[0].principal_id
  runbook_name                           = var.runbook_name
  hybrid_worker_group_name               = var.hybrid_worker_group_name
  worker_vm_principal_id                 = data.azapi_resource.worker_vm.output.identity.principalId
  key_vault_name                         = var.key_vault_name
  domain_username_secret_name            = var.domain_username_secret_name
  domain_password_secret_name            = var.domain_password_secret_name
  domain_fqdn                            = var.domain_fqdn
  organizational_unit_distinguished_name = var.organizational_unit_distinguished_name
  sam_account_name                       = var.sam_account_name
  domain_account_type                    = var.domain_account_type
  storage_account_name                   = var.storage_account_name
  storage_account_replication_type       = var.storage_account_replication_type
  storage_public_network_access_enabled  = var.storage_public_network_access_enabled
  file_share_name                        = var.file_share_name
  file_share_quota_gb                    = var.file_share_quota_gb
  tags                                   = var.tags
  invocation_token                       = var.invocation_token
  readiness_wait_duration                = var.readiness_wait_duration
}