locals {
  runbook_content = replace(
    file("${path.root}/../../runbooks/Join-AzureFilesToDomain.ps1.tftpl"),
    "__JOIN_SCRIPT_BASE64__",
    filebase64("${path.root}/../../scripts/Invoke-AzureFilesDomainJoin.ps1"),
  )
}

resource "azurerm_resource_group" "platform" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

module "automation_platform" {
  source = "../../modules/automation-platform"

  subscription_id               = var.subscription_id
  resource_group_name           = azurerm_resource_group.platform.name
  location                      = azurerm_resource_group.platform.location
  automation_account_name       = var.automation_account_name
  runbook_name                  = var.runbook_name
  runbook_content               = local.runbook_content
  hybrid_worker_group_name      = var.hybrid_worker_group_name
  worker_vm_resource_group_name = var.worker_vm_resource_group_name
  worker_vm_name                = var.worker_vm_name
  bootstrap_script_content      = file("${path.root}/../../scripts/Configure-HybridWorker.ps1")
  key_vault_name                = var.key_vault_name
  key_vault_resource_group_name = var.key_vault_resource_group_name
  domain_username_secret_name   = var.domain_username_secret_name
  domain_password_secret_name   = var.domain_password_secret_name
  tags                          = var.tags
}