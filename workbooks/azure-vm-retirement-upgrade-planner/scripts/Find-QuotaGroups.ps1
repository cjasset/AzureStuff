[CmdletBinding()]
param([Parameter(Mandatory)][string]$OutputPath)

$ErrorActionPreference = 'Stop'

function Invoke-QuotaGroupPage([string]$Path) {
    Get-Command az -ErrorAction Stop | Out-Null
    # Capture native stderr in Windows PowerShell so CLI failures can be classified and retried.
    $ErrorActionPreference = 'Continue'
    $url = 'https://management.azure.com' + $Path
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $output = & az rest --method get --url $url --only-show-errors -o json 2>&1
        if ($LASTEXITCODE -eq 0) { return (($output -join "`n") | ConvertFrom-Json -ErrorAction Stop) }
        $message = $output -join "`n"
        if ($attempt -lt 2 -and $message -match 'TooManyRequests|429|InternalServerError|ServiceUnavailable|502|503|504') {
            Start-Sleep -Seconds (5 * ($attempt + 1))
            continue
        }
        throw "GET $Path failed: $message"
    }
}

function Get-QuotaGroupPages([string]$Path, [scriptblock]$ReadPage) {
    $seen = @{}
    while ($Path) {
        if ($seen.ContainsKey($Path)) { throw "Repeated pagination link: $Path" }
        $seen[$Path] = $true
        $page = & $ReadPage $Path
        if ($null -eq $page.value -or $page.value -isnot [array]) {
            throw "Invalid list response: $Path"
        }
        foreach ($row in $page.value) { $row }
        $next = [string]$page.nextLink
        if ($next) {
            if ($next.StartsWith('/')) {
                if ($next.StartsWith('//')) { throw 'Unsafe pagination link.' }
                $Path = $next
            }
            else {
                $uri = [uri]$next
                if ($uri.Scheme -ne 'https' -or $uri.Host -ne 'management.azure.com' -or
                    $uri.Port -ne 443 -or $uri.UserInfo) { throw 'Unsafe pagination link.' }
                $Path = $uri.PathAndQuery
            }
        }
        else { $Path = '' }
    }
}

function Get-QuotaGroupDiscovery([scriptblock]$ReadPage = ${function:Invoke-QuotaGroupPage}) {
    $groups = @{}
    $failures = [System.Collections.Generic.List[object]]::new()
    $managementGroups = @{}
    $enumerated = $false
    try {
        foreach ($mg in @(Get-QuotaGroupPages '/providers/Microsoft.Management/managementGroups?api-version=2020-05-01' $ReadPage)) {
            if (-not $mg.name -or $mg.id -notmatch '^/providers/Microsoft\.Management/managementGroups/[^/]+$') {
                throw 'Invalid management-group id/name.'
            }
            $managementGroups[[string]$mg.id] = $mg
        }
        $enumerated = $true
    }
    catch {
        $failures.Add(@{ scope = 'management-group enumeration'; error = $_.Exception.Message })
        Write-Warning $_.Exception.Message
    }
    foreach ($mg in $managementGroups.Values) {
        try {
            $path = "$($mg.id)/providers/Microsoft.Quota/groupQuotas?api-version=2025-09-01"
            $found = @(Get-QuotaGroupPages $path $ReadPage)
            foreach ($group in $found) {
                $expected = '^' + [regex]::Escape("$($mg.id)/providers/Microsoft.Quota/groupQuotas/") + '[^/]+$'
                if (-not $group.name -or $group.id -notmatch $expected) { throw 'Invalid quota-group id/name or owning scope.' }
            }
            foreach ($group in $found) {
                $groups[[string]$group.id] = [ordered]@{
                    value = [string]$group.id
                    label = "$($group.name) [$($mg.name)]"
                    selected = $false
                    group = [string]$mg.name
                    managementGroupId = [string]$mg.id
                }
            }
        }
        catch {
            $failures.Add(@{ scope = [string]$mg.id; error = $_.Exception.Message })
            Write-Warning $_.Exception.Message
        }
    }
    $options = @($groups.Values | Sort-Object value)
    if ($options.Count -eq 1) { $options[0].selected = $true }
    @{
        schemaVersion = 1; checkedAtUtc = [DateTime]::UtcNow.ToString('o')
        status = $(if ($enumerated -and $failures.Count -eq 0) { 'Completed' } else { 'Incomplete' })
        managementGroupsEnumerated = $enumerated
        managementGroupsScanned = $managementGroups.Count
        failedScopes = @($failures.ToArray())
        groups = $options
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $report = Get-QuotaGroupDiscovery
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
    Write-Host "$($report.status): $($report.groups.Count) groups across $($report.managementGroupsScanned) readable management groups. Saved $OutputPath"
    if ($report.status -ne 'Completed') { throw 'Discovery incomplete. Review failedScopes; discovered groups are not complete tenant coverage.' }
}
