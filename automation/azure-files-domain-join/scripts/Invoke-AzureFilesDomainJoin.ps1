#requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $SubscriptionId,

    [Parameter(Mandatory = $true)]
    [string] $StorageAccountResourceGroupName,

    [Parameter(Mandatory = $true)]
    [string] $StorageAccountName,

    [Parameter(Mandatory = $true)]
    [string] $SamAccountName,

    [Parameter(Mandatory = $true)]
    [string] $DomainFqdn,

    [Parameter(Mandatory = $false)]
    [ValidateSet('ComputerAccount', 'ServiceLogonAccount')]
    [string] $DomainAccountType = 'ComputerAccount',

    [Parameter(Mandatory = $false)]
    [string] $OrganizationalUnitDistinguishedName
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

Import-Module ActiveDirectory -ErrorAction Stop
Import-Module Az.Accounts -ErrorAction Stop
Import-Module Az.Resources -ErrorAction Stop
Import-Module Az.Storage -MinimumVersion 8.1.0 -ErrorAction Stop
Import-Module AzFilesHybrid -ArgumentList @{
    SkipPowerShellGetCheck = $true
    SkipAzPowerShellCheck  = $true
} -ErrorAction Stop

$domain = Get-ADDomain -Identity $DomainFqdn -ErrorAction Stop
Write-Output "Authenticated to AD DS domain '$($domain.DNSRoot)' as '$([Security.Principal.WindowsIdentity]::GetCurrent().Name)'."

Disable-AzContextAutosave -Scope Process | Out-Null
$login = Connect-AzAccount `
    -Identity `
    -Subscription $SubscriptionId `
    -Scope Process `
    -ErrorAction Stop
$azureContext = Set-AzContext -Context $login.Context -Scope Process -ErrorAction Stop

$activeContext = Get-AzContext -ErrorAction Stop
if ($activeContext.Subscription.Id -ne $SubscriptionId) {
    throw "Azure context is using subscription '$($activeContext.Subscription.Id)', not '$SubscriptionId'."
}

# AzFilesHybrid 0.3.2 does not accept DefaultProfile and relies on the active process context.
Get-AzResourceGroup -Name $StorageAccountResourceGroupName -ErrorAction Stop | Out-Null

$storageAccount = Get-AzStorageAccount `
    -ResourceGroupName $StorageAccountResourceGroupName `
    -Name $StorageAccountName `
    -DefaultProfile $azureContext `
    -ErrorAction Stop

$directoryServiceOptions = try {
    $storageAccount.AzureFilesIdentityBasedAuth.DirectoryServiceOptions
}
catch {
    $null
}

if ($directoryServiceOptions -eq 'AD') {
    $configuredSamAccountName = try {
        $storageAccount.AzureFilesIdentityBasedAuth.ActiveDirectoryProperties.SamAccountName
    }
    catch {
        $null
    }
    if ($configuredSamAccountName -and $configuredSamAccountName -ne $SamAccountName) {
        throw "Storage account is already configured for AD DS with SAM account '$configuredSamAccountName', not '$SamAccountName'."
    }

    Write-Output "Storage account '$StorageAccountName' is already registered with AD DS."
    exit 0
}

$joinParameters = @{
    ResourceGroupName  = $StorageAccountResourceGroupName
    StorageAccountName = $StorageAccountName
    SamAccountName      = $SamAccountName
    DomainAccountType   = $DomainAccountType
    ErrorAction         = 'Stop'
}

if (-not [string]::IsNullOrWhiteSpace($OrganizationalUnitDistinguishedName)) {
    $joinParameters.OrganizationalUnitDistinguishedName = $OrganizationalUnitDistinguishedName
}

Join-AzStorageAccount @joinParameters

$updatedStorageAccount = Get-AzStorageAccount `
    -ResourceGroupName $StorageAccountResourceGroupName `
    -Name $StorageAccountName `
    -DefaultProfile $azureContext `
    -ErrorAction Stop

$updatedDirectoryServiceOptions = try {
    $updatedStorageAccount.AzureFilesIdentityBasedAuth.DirectoryServiceOptions
}
catch {
    $null
}

if ($updatedDirectoryServiceOptions -ne 'AD') {
    throw "Join-AzStorageAccount completed, but AD DS authentication is not enabled on '$StorageAccountName'."
}

Write-Output "Azure Files AD DS registration completed for '$StorageAccountName'."
