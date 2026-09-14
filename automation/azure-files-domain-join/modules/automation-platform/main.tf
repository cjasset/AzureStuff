data "azurerm_key_vault" "credentials" {
  name                = var.key_vault_name
  resource_group_name = var.key_vault_resource_group_name
}

locals {
  worker_vm_id = format(
    "/subscriptions/%s/resourceGroups/%s/providers/Microsoft.Compute/virtualMachines/%s",
    var.subscription_id,
    var.worker_vm_resource_group_name,
    var.worker_vm_name,
  )

  username_secret_scope = "${data.azurerm_key_vault.credentials.id}/secrets/${var.domain_username_secret_name}"
  password_secret_scope = "${data.azurerm_key_vault.credentials.id}/secrets/${var.domain_password_secret_name}"

  existing_user_assigned_identities = try(data.azapi_resource.worker_vm.output.identity.userAssignedIdentities, {})
  worker_vm_identity = merge(
    {
      type = length(keys(local.existing_user_assigned_identities)) > 0 ? "SystemAssigned, UserAssigned" : "SystemAssigned"
    },
    length(keys(local.existing_user_assigned_identities)) > 0 ? {
      userAssignedIdentities = local.existing_user_assigned_identities
    } : {},
  )
}

data "azapi_resource" "worker_vm" {
  type                   = "Microsoft.Compute/virtualMachines@2024-07-01"
  resource_id            = local.worker_vm_id
  response_export_values = ["identity", "location", "properties.storageProfile.osDisk.osType"]

  lifecycle {
    postcondition {
      condition     = self.output.properties.storageProfile.osDisk.osType == "Windows"
      error_message = "worker_vm_name must identify a Windows Azure VM."
    }
  }
}

resource "azurerm_automation_account" "this" {
  name                          = var.automation_account_name
  location                      = var.location
  resource_group_name           = var.resource_group_name
  sku_name                      = "Basic"
  local_authentication_enabled  = false
  public_network_access_enabled = true

  identity {
    type = "SystemAssigned"
  }

  tags = var.tags
}

resource "azapi_update_resource" "worker_vm_identity" {
  type        = "Microsoft.Compute/virtualMachines@2024-07-01"
  resource_id = local.worker_vm_id

  body = {
    identity = local.worker_vm_identity
  }

  response_export_values = ["identity.principalId"]
}

resource "azurerm_automation_hybrid_runbook_worker_group" "this" {
  name                    = var.hybrid_worker_group_name
  resource_group_name     = var.resource_group_name
  automation_account_name = azurerm_automation_account.this.name
}

resource "random_uuid" "worker" {
  keepers = {
    automation_account_id = azurerm_automation_account.this.id
    worker_group_name     = azurerm_automation_hybrid_runbook_worker_group.this.name
    worker_vm_id          = lower(local.worker_vm_id)
  }
}

resource "azurerm_automation_hybrid_runbook_worker" "this" {
  worker_id               = random_uuid.worker.result
  resource_group_name     = var.resource_group_name
  automation_account_name = azurerm_automation_account.this.name
  worker_group_name       = azurerm_automation_hybrid_runbook_worker_group.this.name
  vm_resource_id          = lower(local.worker_vm_id)

  depends_on = [azapi_update_resource.worker_vm_identity]
}

resource "azurerm_virtual_machine_extension" "bootstrap" {
  name                       = "AzureFilesDomainJoinBootstrap"
  virtual_machine_id         = local.worker_vm_id
  publisher                  = "Microsoft.Compute"
  type                       = "CustomScriptExtension"
  type_handler_version       = "1.10"
  auto_upgrade_minor_version = true

  settings = jsonencode({
    commandToExecute = format(
      "powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"$bytes=[Convert]::FromBase64String('%s');$inputStream=[IO.MemoryStream]::new($bytes);$gzipStream=[IO.Compression.GzipStream]::new($inputStream,[IO.Compression.CompressionMode]::Decompress);$reader=[IO.StreamReader]::new($gzipStream);$script=$reader.ReadToEnd();$reader.Dispose();Invoke-Expression $script\"",
      base64gzip(var.bootstrap_script_content),
    )
  })
}

resource "azurerm_virtual_machine_extension" "hybrid_worker" {
  name                       = "HybridWorkerExtension"
  virtual_machine_id         = local.worker_vm_id
  publisher                  = "Microsoft.Azure.Automation.HybridWorker"
  type                       = "HybridWorkerForWindows"
  type_handler_version       = "1.1"
  auto_upgrade_minor_version = true
  automatic_upgrade_enabled  = true

  settings = jsonencode({
    AutomationAccountURL = azurerm_automation_account.this.hybrid_service_url
  })

  provision_after_extensions = [azurerm_virtual_machine_extension.bootstrap.name]

  depends_on = [
    azurerm_automation_hybrid_runbook_worker.this,
    azapi_update_resource.worker_vm_identity,
  ]
}

resource "azurerm_role_assignment" "key_vault_username" {
  scope                = local.username_secret_scope
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_automation_account.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "key_vault_password" {
  scope                = local.password_secret_scope
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_automation_account.this.identity[0].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_automation_runbook" "domain_join" {
  name                    = var.runbook_name
  location                = azurerm_automation_account.this.location
  resource_group_name     = var.resource_group_name
  automation_account_name = azurerm_automation_account.this.name
  log_progress            = true
  log_verbose             = true
  description             = "Retrieves AD DS credentials from Key Vault and registers Azure Files from a Windows Hybrid Worker."
  runbook_type            = "PowerShell"
  content                 = var.runbook_content
  tags                    = var.tags
}