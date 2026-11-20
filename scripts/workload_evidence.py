"""Read-only workload evidence queries, independent of lifecycle scoring."""

NVME_OS_SOURCE = "https://learn.microsoft.com/azure/virtual-machines/enable-nvme-interface"
MANA_OS_SOURCE = "https://learn.microsoft.com/azure/virtual-network/accelerated-networking-overview"
OS_REFERENCE_DATE = "2026-11-18"


def os_eligibility_query():
    # Prefer observed guest metadata; an old image label must not override it.
    return r"""
| extend OsHintSource = iff(isnotempty(GuestOsName), 'Guest OS metadata', 'Image hint only - current guest unverified'),
         OsHintText = tolower(iff(isnotempty(GuestOsName), GuestOsName, strcat(ImagePublisher, ' ', ImageOffer, ' ', ImageSku)))
| extend NvmeOsHint = case(
        isempty(trim(' ', OsHintText)), 'Unknown - OS metadata missing',
        OsHintText matches regex @'windows.?server[^0-9]{0,30}(2019|2022|2025)($|[^0-9])', 'Listed OS family - validate image, generation and boot',
        OsHintText matches regex @'ubuntu[^0-9]{0,30}(18[._-]04|20[._-]04|22[._-]04|24[._-]04)($|[^0-9])', 'Listed OS family - validate image, kernel and boot',
        OsHintText matches regex @'debian[^0-9]{0,30}(11|12)($|[^0-9])', 'Listed OS family - validate image, kernel and boot',
        'Review official NVMe OS list - not classified by captured subset'),
         ManaOsHint = case(
        isempty(trim(' ', OsHintText)), 'Unknown - OS metadata missing',
        OsHintText matches regex @'windows.?server[^0-9]{0,30}(2016|2019|2022|2025)($|[^0-9])', 'Listed OS family - install/verify current MANA driver',
        OsHintText matches regex @'ubuntu[^0-9]{0,30}(22[._-]04|24[._-]04|26[._-]04)($|[^0-9])', 'Listed OS family - verify minimum kernel and MANA driver',
        'Review official MANA OS/kernel list - not classified by captured subset')
| extend NvmeDriverValidation = 'Unknown - guest driver and target boot not tested',
         ManaDriverValidation = 'Unknown - target hardware, driver and traffic not tested',
         NvmeOsReference = '""" + NVME_OS_SOURCE + """',
         ManaOsReference = '""" + MANA_OS_SOURCE + """',
         OsReferenceChecked = '""" + OS_REFERENCE_DATE + """'"""


def sql_evidence_query():
    return """resources
| where type in~ ('microsoft.compute/virtualmachines/extensions', 'microsoft.sqlvirtualmachine/sqlvirtualmachines')
| extend IsRegistration = type =~ 'microsoft.sqlvirtualmachine/sqlvirtualmachines'
| extend IsSqlExtension = type =~ 'microsoft.compute/virtualmachines/extensions'
    and tostring(properties.publisher) =~ 'Microsoft.SqlServer.Management'
    and (tostring(properties.type) =~ 'SqlIaaSAgent' or tostring(properties.typePropertiesType) =~ 'SqlIaaSAgent')
| where IsRegistration or IsSqlExtension
| extend ParentOffset = indexof(tolower(id), '/extensions/')
| extend VmId = iff(IsRegistration, tolower(tostring(properties.virtualMachineResourceId)),
                   iff(ParentOffset > 0, tolower(substring(id, 0, ParentOffset)), ''))
| where isnotempty(VmId)
| extend ExtensionState = iff(isempty(tostring(properties.provisioningState)), 'Unknown', tostring(properties.provisioningState))
| summarize SqlExtensionCount = countif(IsSqlExtension),
            SqlExtensions = strcat_array(make_set_if(tostring(split(name, '/')[-1]), IsSqlExtension, 20), ', '),
            ExtensionStates = strcat_array(make_set_if(ExtensionState, IsSqlExtension, 20), ', '),
            SqlRegistrationCount = countif(IsRegistration),
            SqlRegistrationIds = strcat_array(make_set_if(id, IsRegistration, 20), ', '),
            SqlRegistrationStates = strcat_array(make_set_if(ExtensionState, IsRegistration, 20), ', '),
            SqlEditions = strcat_array(make_set_if(tostring(properties.sqlImageSku), IsRegistration, 20), ', '),
            SqlLicenseTypes = strcat_array(make_set_if(tostring(properties.sqlServerLicenseType), IsRegistration, 20), ', '),
            SqlManagementModes = strcat_array(make_set_if(tostring(properties.sqlManagement), IsRegistration, 20), ', '),
            SqlGroupIds = strcat_array(make_set_if(tostring(properties.sqlVirtualMachineGroupResourceId), IsRegistration, 20), ', ')
    by VmId"""


def compute_evidence_query():
    # Fold child-resource evidence before joining owners to avoid ARG join limits.
    return """resources
| where type in~ ('microsoft.compute/virtualmachines', 'microsoft.compute/virtualmachinescalesets',
    'microsoft.network/networkinterfaces', 'microsoft.compute/disks',
    'microsoft.compute/virtualmachines/extensions', 'microsoft.sqlvirtualmachine/sqlvirtualmachines')
| extend IsVmss = type =~ 'microsoft.compute/virtualmachinescalesets',
         IsCompute = type in~ ('microsoft.compute/virtualmachines', 'microsoft.compute/virtualmachinescalesets'),
         IsNic = type =~ 'microsoft.network/networkinterfaces', IsDisk = type =~ 'microsoft.compute/disks',
         IsRegistration = type =~ 'microsoft.sqlvirtualmachine/sqlvirtualmachines',
         IsExtension = type =~ 'microsoft.compute/virtualmachines/extensions'
| extend IsSqlExtension = IsExtension and tostring(properties.publisher) =~ 'Microsoft.SqlServer.Management'
            and (tostring(properties.type) =~ 'SqlIaaSAgent' or tostring(properties.typePropertiesType) =~ 'SqlIaaSAgent'),
         IsAdeExtension = IsExtension and tostring(properties.publisher) in~ ('Microsoft.Azure.Security', 'Microsoft.Azure.Security.Edp')
            and tostring(properties.type) in~ ('AzureDiskEncryption', 'AzureDiskEncryptionForLinux'),
         IsSapExtension = IsExtension and tostring(properties.publisher) in~ ('Microsoft.AzureCAT.AzureEnhancedMonitoring', 'Microsoft.OSTCExtensions')
            and tostring(properties.type) in~ ('MonitorX64Windows', 'MonitorX64Linux', 'AzureEnhancedMonitorForLinux', 'AzureCATExtensionHandler')
| extend ComputeProfile = iff(IsVmss, properties.virtualMachineProfile, properties)
| extend Keys = case(IsCompute, array_concat(pack_array(id),
            coalesce(ComputeProfile.storageProfile.dataDisks, dynamic([])),
            pack_array(tostring(ComputeProfile.storageProfile.osDisk.managedDisk.id))),
        IsNic, pack_array(tostring(properties.virtualMachine.id)),
        IsRegistration, pack_array(tostring(properties.virtualMachineResourceId)),
        IsExtension, pack_array(tostring(split(tolower(id), '/extensions/')[0])),
        IsDisk, pack_array(id), dynamic([]))
| mv-expand Key = Keys limit 2000
| extend VmId = tolower(iff(isnotempty(tostring(Key.managedDisk.id)), tostring(Key.managedDisk.id), tostring(Key)))
| where isnotempty(VmId)
| extend IsSelf = IsCompute and tolower(id) == VmId
| summarize ComputeRows = countif(IsSelf),
        VM = take_anyif(pack('id', id, 'type', type, 'name', name, 'resourceGroup', resourceGroup, 'subscriptionId', subscriptionId,
            'location', location, 'properties', properties, 'sku', sku, 'tags', tags,
            'identity', identity, 'plan', plan, 'managedBy', managedBy), IsSelf),
        AttachedVmIds = make_set_if(tolower(id), IsCompute and not(IsSelf), 2000),
        NicRows = countif(IsNic), AcceleratedNicRows = countif(IsNic and tobool(properties.enableAcceleratedNetworking) == true),
        NicFlagRows = countif(IsNic and isnotnull(tobool(properties.enableAcceleratedNetworking))),
        DiskRows = countif(IsDisk), SharedDiskRows = countif(IsDisk and toint(properties.maxShares) > 1),
        AdeExtensionRows = countif(IsAdeExtension), SapExtensionRows = countif(IsSapExtension),
        SqlExtensionRows = countif(IsSqlExtension), SqlRegistrationRows = countif(IsRegistration),
        SqlGroupIds = make_set_if(tostring(properties.sqlVirtualMachineGroupResourceId), IsRegistration, 20)
    by VmId
| extend ComputeKeys = iff(ComputeRows > 0, pack_array(VmId), AttachedVmIds)
| mv-expand ComputeKey = ComputeKeys limit 2000
| summarize VM = take_anyif(VM, ComputeRows > 0), ComputeRows = sum(ComputeRows),
        NicRows = sum(NicRows), AcceleratedNicRows = sum(AcceleratedNicRows), NicFlagRows = sum(NicFlagRows),
        DiskRows = sum(DiskRows), SharedDiskRows = sum(SharedDiskRows), AdeExtensionRows = sum(AdeExtensionRows),
        SapExtensionRows = sum(SapExtensionRows), SqlExtensionRows = sum(SqlExtensionRows),
        SqlRegistrationRows = sum(SqlRegistrationRows), SqlGroupIds = take_anyif(SqlGroupIds, ComputeRows > 0)
    by VmId = tostring(ComputeKey)
| where ComputeRows == 1
| extend ResourceId = tostring(VM.id), Name = tostring(VM.name), ResourceGroup = tostring(VM.resourceGroup),
         SubscriptionId = tostring(VM.subscriptionId), Region = tostring(VM.location),
         IsVmss = tostring(VM.type) =~ 'microsoft.compute/virtualmachinescalesets',
         Profile = iff(tostring(VM.type) =~ 'microsoft.compute/virtualmachinescalesets', VM.properties.virtualMachineProfile, VM.properties)
| extend VmSize = iff(IsVmss, tostring(VM.sku.name), tostring(Profile.hardwareProfile.vmSize)),
         RgId = tolower(strcat('/subscriptions/', SubscriptionId, '/resourceGroups/', ResourceGroup)),
         ExpectedNics = array_length(Profile.networkProfile.networkInterfaces),
         DataDiskCount = array_length(Profile.storageProfile.dataDisks),
         ImagePublisher = tostring(Profile.storageProfile.imageReference.publisher),
         ImageOffer = tostring(Profile.storageProfile.imageReference.offer), ImageSku = tostring(Profile.storageProfile.imageReference.sku),
         GuestOsName = tostring(VM.properties.extended.instanceView.osName)
| extend ExpectedDisks = coalesce(DataDiskCount, 0) + iff(isnotempty(tostring(Profile.storageProfile.osDisk.managedDisk.id)), 1, 0)
| extend AcceleratedNetworking = case(IsVmss, 'Review scale-set NIC configurations',
            isnull(ExpectedNics) or NicRows != ExpectedNics or NicFlagRows != NicRows, 'Unknown - incomplete NIC evidence',
            strcat(AcceleratedNicRows, ' of ', ExpectedNics, ' NICs enabled')),
         SharedDiskEvidence = case(SharedDiskRows > 0, 'Detected - review clustering and migration sequencing',
            IsVmss or DiskRows != ExpectedDisks or ExpectedDisks == 0, 'Unknown - incomplete attached-disk evidence',
            'No sharing-enabled attached disks observed'),
         EphemeralOsDisk = case(tostring(Profile.storageProfile.osDisk.diffDiskSettings.option) =~ 'Local', 'Configured - rebuild/persistence review',
            isempty(tostring(Profile.storageProfile.osDisk)), 'Unknown', 'Not configured in indexed model'),
         UnmanagedDiskEvidence = iff(isnotempty(tostring(Profile.storageProfile.osDisk.vhd.uri)) or tostring(Profile.storageProfile.dataDisks) contains 'vhd',
            'Detected - managed-disk migration review', 'No unmanaged disk reference observed'),
         Hibernation = case(tobool(VM.properties.additionalCapabilities.hibernationEnabled) == true, 'Enabled - target support review',
            isnull(VM.properties.additionalCapabilities.hibernationEnabled), 'Unknown - not recorded', 'Disabled in indexed model'),
         SecurityType = iff(isempty(tostring(Profile.securityProfile.securityType)), 'Not recorded - verify effective security type', tostring(Profile.securityProfile.securityType)),
         SystemAssignedIdentity = iff(tostring(VM.identity.type) contains 'SystemAssigned', 'Present - preserve/re-authorize if rebuilding', 'No system-assigned identity recorded'),
         MarketplacePlan = strcat(tostring(VM.plan.publisher), ' / ', tostring(VM.plan.product)),
         AdeEvidence = iff(AdeExtensionRows > 0, 'Extension observed - guest encryption state unverified', 'Guest encryption state unverified'),
         InventoryRole = case(IsVmss, 'Scale-set model', isnotempty(tostring(Profile.virtualMachineScaleSet.id)), 'Flexible member detail - do not add to model capacity', 'Standalone VM')
| join kind=leftouter (
    resources
    | where type in~ ('microsoft.containerservice/managedclusters', 'microsoft.databricks/workspaces',
        'microsoft.solutions/applications', 'microsoft.redhatopenshift/openshiftclusters')
    | extend OwnerKind = case(type =~ 'microsoft.containerservice/managedclusters', 'AKS',
             type =~ 'microsoft.databricks/workspaces', 'Databricks',
             type =~ 'microsoft.redhatopenshift/openshiftclusters', 'ARO', 'Managed application')
    | extend RgId = tolower(case(OwnerKind == 'AKS', strcat('/subscriptions/', subscriptionId, '/resourceGroups/', tostring(properties.nodeResourceGroup)),
             OwnerKind == 'ARO', tostring(properties.clusterProfile.resourceGroupId), tostring(properties.managedResourceGroupId)))
    | project RgId, OwnerKind, OwnerId = tolower(id)
    | union (
        resourcecontainers
        | where type =~ 'microsoft.resources/subscriptions/resourcegroups' and isnotempty(tostring(managedBy))
        | extend OwnerId = tolower(tostring(managedBy))
        | project RgId = tolower(id), OwnerId, OwnerKind = case(
            OwnerId contains '/microsoft.containerservice/managedclusters/', 'AKS',
            OwnerId contains '/microsoft.databricks/workspaces/', 'Databricks',
            OwnerId contains '/microsoft.batch/', 'Batch', 'Managed service')
      )
    | where isnotempty(RgId) and isnotempty(OwnerId)
    | summarize OwnerIds = make_set(OwnerId, 20), OwnerKinds = make_set(OwnerKind, 20) by RgId
  ) on RgId
| join kind=leftouter (
    desktopvirtualizationresources
    | where type =~ 'microsoft.desktopvirtualization/hostpools/sessionhosts'
    | extend VmId = tolower(tostring(properties.resourceId))
    | where isnotempty(VmId)
    | summarize AvdHostPoolIds = make_set(tostring(split(tolower(id), '/sessionhosts/')[0]), 20) by VmId
  ) on VmId
| extend Workload = trim('; ', strcat(
        iff(array_length(OwnerKinds) > 0, strcat(strcat_array(OwnerKinds, '; '), '; '), ''),
        iff(isnotempty(tostring(VM.managedBy)) and coalesce(array_length(OwnerKinds), 0) == 0, 'Managed service (compute managedBy); ', ''),
        iff(array_length(AvdHostPoolIds) > 0, 'AVD; ', ''),
        iff(SqlExtensionRows > 0 or SqlRegistrationRows > 0, 'SQL; ', ''),
        iff(SapExtensionRows > 0 or tostring(Profile.licenseType) contains 'SLES_SAP' or ImageOffer contains 'sap', 'SAP signal; ', '')))
| extend Workload = iff(isempty(Workload), 'Unclassified - guest/workload assessment required', Workload),
         AssociationEvidence = strcat('Compute managedBy: ', tostring(VM.managedBy), '; Managed owners: ', tostring(OwnerIds), '; AVD host pools: ', tostring(AvdHostPoolIds),
            '; SQL extensions/registrations: ', SqlExtensionRows, '/', SqlRegistrationRows, '; SAP extensions: ', SapExtensionRows),
         MigrationMethod = case(array_length(OwnerIds) > 1, 'Multiple owner associations - resolve ownership before changes',
            tostring(OwnerKinds) contains 'AKS', 'AKS node-pool workflow; validate surge, PDBs, persistence and capacity',
            tostring(OwnerKinds) contains 'Databricks', 'Databricks-supported node type/runtime and cluster restart/recreation',
            array_length(AvdHostPoolIds) > 0, 'AVD host replacement; check pooled/personal type, sessions, profiles and persistence',
            tostring(OwnerKinds) contains 'Batch', 'Batch pool replacement; drain tasks and review autoscale/quotas',
            array_length(OwnerKinds) > 0 or isnotempty(tostring(VM.managedBy)), 'Use owning service/vendor migration workflow; do not directly resize managed compute',
            SqlExtensionRows > 0 or SqlRegistrationRows > 0 or Workload contains 'SAP', 'Stateful/vendor assessment before resize or rebuild; validate HA, licensing, backup and rollback',
            'Determine application owner/state; then validate resize versus rebuild and rollback'),
         MigrationApproval = 'Not assessed - application, HA, backup and rollback require evidence'
""" + os_eligibility_query() + """
| extend CatalogueRecord = tostring(parse_json('{LifecycleSkuMap}')[VmSize])
| extend CatalogueCoverage = iff(isempty(CatalogueRecord), 'Not assessed - SKU absent from captured catalogue', tostring(split(CatalogueRecord, '|')[0]))
| where '{DiscoveryWorkload}' == 'All' or Workload contains '{DiscoveryWorkload}'
| project Name, ResourceGroup, SubscriptionId, Region, VmSize, CatalogueCoverage, Workload, MigrationMethod,
    AcceleratedNetworking, ExpectedNics, DataDiskCount, SharedDiskEvidence, EphemeralOsDisk,
    UnmanagedDiskEvidence, Hibernation, SecurityType, SystemAssignedIdentity, MarketplacePlan, AdeEvidence,
    OsHintSource, NvmeOsHint, ManaOsHint, NvmeDriverValidation, ManaDriverValidation,
    OsReferenceChecked, NvmeOsReference, ManaOsReference, AssociationEvidence, OwnerIds, AvdHostPoolIds,
    SqlGroupIds, MigrationApproval, InventoryRole, ResourceId
| order by Workload asc, Name asc"""


def service_inventory_query():
    return """resources
| union batchresources
| where type in~ ('microsoft.containerservice/managedclusters', 'microsoft.databricks/workspaces',
    'microsoft.batch/batchaccounts', 'microsoft.batch/batchaccounts/pools', 'microsoft.solutions/applications',
    'microsoft.redhatopenshift/openshiftclusters', 'microsoft.desktopvirtualization/hostpools',
    'microsoft.sqlvirtualmachine/sqlvirtualmachines', 'microsoft.workloads/sapvirtualinstances',
    'microsoft.workloads/sapvirtualinstances/applicationinstances', 'microsoft.workloads/sapvirtualinstances/centralinstances',
    'microsoft.workloads/sapvirtualinstances/databaseinstances')
| extend Workload = case(type contains 'microsoft.containerservice/', 'AKS', type contains 'microsoft.databricks/', 'Databricks',
    type contains 'microsoft.batch/', 'Batch', type contains 'microsoft.desktopvirtualization/', 'AVD',
    type contains 'microsoft.sqlvirtualmachine/', 'SQL', type contains 'microsoft.workloads/', 'SAP',
    type contains 'microsoft.redhatopenshift/', 'ARO', 'Managed application')
| where '{DiscoveryWorkload}' == 'All' or Workload contains '{DiscoveryWorkload}'
| project Name = name, Workload, ResourceGroup = resourceGroup, SubscriptionId = subscriptionId,
    ServiceRegion = location, ServiceType = type, ResourceId = id,
    HostPoolType = tostring(properties.hostPoolType), NodeProvisioningMode = tostring(properties.nodeProvisioningProfile.mode),
    ManagedResourceGroup = coalesce(tostring(properties.managedResourceGroupId), tostring(properties.nodeResourceGroup)),
    LinkedVm = tostring(properties.virtualMachineResourceId), ConfiguredPools = properties.agentPoolProfiles,
    SqlEdition = tostring(properties.sqlImageSku), SqlLicenseType = tostring(properties.sqlServerLicenseType),
    SapVmDetails = properties.vmDetails,
    Evidence = 'Indexed service registration/configuration; backing compute and runtime not established'
| order by Workload asc, Name asc"""


def configured_pools_query():
    return """resources
| union batchresources
| where type in~ ('microsoft.containerservice/managedclusters', 'microsoft.batch/batchaccounts/pools')
| extend Workload = iff(type =~ 'microsoft.containerservice/managedclusters', 'AKS', 'Batch')
| where '{DiscoveryWorkload}' == 'All' or Workload contains '{DiscoveryWorkload}'
| extend Pools = iff(Workload == 'AKS', properties.agentPoolProfiles, pack_array(properties))
| mv-expand Pool = Pools limit 2000
| extend VmSize = tostring(Pool.vmSize)
| extend CatalogueRecord = tostring(parse_json(tolower('{LifecycleSkuMap}'))[tolower(VmSize)])
| project Service = name, Workload, ResourceGroup = resourceGroup, SubscriptionId = subscriptionId,
    Region = location, Pool = iff(Workload == 'AKS', tostring(Pool.name), name), VmSize,
    Lifecycle = iff(isempty(CatalogueRecord), 'Not assessed - no catalogue match', toupper(tostring(split(CatalogueRecord, '|')[0]))),
    ConfiguredNodes = toint(Pool['count']), Autoscaling = tostring(Pool.enableAutoScaling),
    MinimumNodes = toint(Pool.minCount), MaximumNodes = toint(Pool.maxCount),
    MaxSurge = tostring(Pool.upgradeSettings.maxSurge), OsSku = tostring(Pool.osSKU),
    OsDiskType = tostring(Pool.osDiskType), Priority = tostring(coalesce(Pool.scaleSetPriority, Pool.scaleSettings.fixedScale.targetLowPriorityNodes)),
    BatchCurrentDedicated = toint(Pool.currentDedicatedNodes), BatchCurrentLowPriority = toint(Pool.currentLowPriorityNodes),
    BatchTargetDedicated = toint(Pool.scaleSettings.fixedScale.targetDedicatedNodes),
    BatchTargetLowPriority = toint(Pool.scaleSettings.fixedScale.targetLowPriorityNodes),
    BatchAutoscaleFormula = tostring(Pool.scaleSettings.autoScale.formula),
    MigrationMethod = iff(Workload == 'AKS', 'Node-pool migration; validate PDBs, persistence, surge and autoscaler maximum',
        'Pool/task migration; validate image, task slots, drainage and autoscale demand'),
    Evidence = 'Configured pool, not observed running nodes; not additive to VM/VMSS inventory', ResourceId = id
| order by Workload asc, Service asc, Pool asc"""
