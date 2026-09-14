#requires -Version 5.1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$moduleRoot = Join-Path $env:ProgramFiles 'WindowsPowerShell\Modules'
$temporaryRoot = Join-Path $env:TEMP 'AzureFilesHybridWorkerBootstrap'
$zipPath = Join-Path $temporaryRoot 'AzFilesHybrid.zip'
$extractPath = Join-Path $temporaryRoot 'extracted'

try {
    Write-Output 'Installing the Active Directory PowerShell feature.'
    Import-Module ServerManager -ErrorAction Stop
    $feature = Get-WindowsFeature -Name RSAT-AD-PowerShell
    if (-not $feature.Installed) {
        Install-WindowsFeature -Name RSAT-AD-PowerShell -IncludeAllSubFeature -ErrorAction Stop | Out-Null
    }

    Write-Output 'Installing required Azure PowerShell modules for all users.'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -ErrorAction Stop | Out-Null
    Set-PSRepository -Name PSGallery -InstallationPolicy Trusted -ErrorAction Stop
    Install-Module -Name Az.Accounts -Scope AllUsers -Repository PSGallery -Force -AllowClobber -ErrorAction Stop
    Install-Module -Name Az.KeyVault -Scope AllUsers -Repository PSGallery -Force -AllowClobber -ErrorAction Stop
    Install-Module -Name Az.Resources -Scope AllUsers -Repository PSGallery -Force -AllowClobber -ErrorAction Stop
    Install-Module -Name Az.Storage -MinimumVersion 8.1.0 -Scope AllUsers -Repository PSGallery -Force -AllowClobber -ErrorAction Stop

    Write-Output 'Installing AzFilesHybrid 0.3.2 from the Microsoft Azure Files samples release.'
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
    Invoke-WebRequest `
        -Uri 'https://github.com/Azure-Samples/azure-files-samples/releases/download/v0.3.2/AzFilesHybrid.zip' `
        -OutFile $zipPath `
        -UseBasicParsing `
        -ErrorAction Stop
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force

    $manifest = Get-ChildItem -LiteralPath $extractPath -Filter 'AzFilesHybrid.psd1' -Recurse | Select-Object -First 1
    if (-not $manifest) {
        throw 'AzFilesHybrid.psd1 was not found in the downloaded release archive.'
    }

    $destination = Join-Path $moduleRoot 'AzFilesHybrid'
    Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item -LiteralPath $manifest.Directory.FullName -Destination $destination -Recurse -Force

    Import-Module ActiveDirectory -ErrorAction Stop
    Import-Module Az.Accounts -ErrorAction Stop
    Import-Module Az.KeyVault -ErrorAction Stop
    Import-Module Az.Resources -ErrorAction Stop
    Import-Module Az.Storage -MinimumVersion 8.1.0 -ErrorAction Stop
    Import-Module AzFilesHybrid -ArgumentList @{
        SkipPowerShellGetCheck = $true
        SkipAzPowerShellCheck  = $true
    } -ErrorAction Stop

    Write-Output 'Hybrid Worker prerequisites installed successfully.'
}
finally {
    Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
}
