locals {
  runbook_parameters = {
    SubscriptionId                      = var.subscription_id
    KeyVaultName                        = var.key_vault_name
    DomainUsernameSecretName            = var.domain_username_secret_name
    DomainPasswordSecretName            = var.domain_password_secret_name
    StorageAccountResourceGroupName     = var.resource_group_name
    StorageAccountName                  = azurerm_storage_account.files.name
    SamAccountName                      = var.sam_account_name
    DomainFqdn                          = trimspace(var.domain_fqdn)
    DomainAccountType                   = var.domain_account_type
    OrganizationalUnitDistinguishedName = var.organizational_unit_distinguished_name
  }
}

resource "azurerm_storage_account" "files" {
  name                            = var.storage_account_name
  resource_group_name             = var.resource_group_name
  location                        = var.location
  account_tier                    = "Standard"
  account_replication_type        = var.storage_account_replication_type
  account_kind                    = "StorageV2"
  https_traffic_only_enabled      = true
  min_tls_version                 = "TLS1_2"
  shared_access_key_enabled       = true
  default_to_oauth_authentication = true
  local_user_enabled              = false
  allow_nested_items_to_be_public = false
  public_network_access_enabled   = var.storage_public_network_access_enabled

  share_properties {
    retention_policy {
      days = 7
    }

    smb {
      versions                        = ["SMB3.1.1"]
      authentication_types            = ["Kerberos"]
      kerberos_ticket_encryption_type = ["AES-256"]
      channel_encryption_type         = ["AES-256-GCM"]
    }
  }

  tags = merge(var.tags, {
    SecurityControl = "ignore"
  })

  lifecycle {
    ignore_changes = [azure_files_authentication]
  }
}

resource "azapi_resource" "file_share" {
  type      = "Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01"
  name      = var.file_share_name
  parent_id = "${azurerm_storage_account.files.id}/fileServices/default"

  body = {
    properties = {
      accessTier       = "TransactionOptimized"
      enabledProtocols = "SMB"
      shareQuota       = var.file_share_quota_gb
    }
  }

  schema_validation_enabled = true
}

resource "azurerm_role_assignment" "automation_storage_contributor" {
  scope                = azurerm_storage_account.files.id
  role_definition_name = "Contributor"
  principal_id         = var.automation_account_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "worker_storage_contributor" {
  scope                = azurerm_storage_account.files.id
  role_definition_name = "Contributor"
  principal_id         = var.worker_vm_principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "worker_resource_group_reader" {
  scope                = var.resource_group_id
  role_definition_name = "Reader"
  principal_id         = var.worker_vm_principal_id
  principal_type       = "ServicePrincipal"
}

resource "time_sleep" "rbac" {
  create_duration = var.readiness_wait_duration

  triggers = {
    automation_storage_contributor_id = azurerm_role_assignment.automation_storage_contributor.id
    worker_storage_contributor_id     = azurerm_role_assignment.worker_storage_contributor.id
    worker_resource_group_reader_id   = azurerm_role_assignment.worker_resource_group_reader.id
  }
}

resource "random_uuid" "job" {
  keepers = {
    automation_account_id = var.automation_account_id
    runbook_name          = var.runbook_name
    hybrid_worker_group   = var.hybrid_worker_group_name
    parameters_sha256     = sha256(jsonencode(local.runbook_parameters))
    rbac_readiness_id     = time_sleep.rbac.id
    shared_key_access     = tostring(azurerm_storage_account.files.shared_access_key_enabled)
    invocation_token      = var.invocation_token
  }
}

resource "azapi_resource_action" "runbook_job" {
  type        = "Microsoft.Automation/automationAccounts/jobs@2024-10-23"
  resource_id = "${var.automation_account_id}/jobs/${random_uuid.job.result}"
  action      = ""
  method      = "PUT"
  when        = "apply"

  body = {
    properties = {
      runbook = {
        name = var.runbook_name
      }
      parameters = local.runbook_parameters
      runOn      = var.hybrid_worker_group_name
    }
  }

  response_export_values = ["properties.creationTime", "properties.provisioningState", "properties.status"]

  timeouts {
    create = "75m"
  }

  depends_on = [
    azapi_resource.file_share,
    time_sleep.rbac,
  ]
}