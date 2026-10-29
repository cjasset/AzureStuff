# Audit or deny retired and retiring VM sizes

## Purpose

Prevent new technical debt and identify existing virtual machines that use retired or announced-retirement Azure VM sizes. This custom Azure Policy supports a gradual rollout from **Audit** to **Deny**, without maintaining an allowlist of every supported replacement size.

The retirement baseline comes from Microsoft's [VM size series retirements, capacity growth restrictions, and modernization guidance](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions), reviewed **October 28, 2026**.

This is a manually maintained public Azure snapshot. It does not retrieve the Learn page or detect new retirements automatically. Announced retirements are included immediately, even when their retirement dates are years away.

## Files

| File | Purpose |
|---|---|
| [Audit-Deny-Retired-VM-SKUs.json](./Audit-Deny-Retired-VM-SKUs.json) | Custom policy definition properties, parameters, and rule. |

## Effects and resource coverage

| Effect | Behavior |
|---|---|
| `Audit` (default) | Reports matching resources as noncompliant without blocking deployment. |
| `Deny` | Rejects matching creates and updates evaluated by Azure Policy. |
| `Disabled` | Turns off evaluation. |

The policy evaluates:

- `Microsoft.Compute/virtualMachines`, using `hardwareProfile.vmSize`.
- `Microsoft.Compute/virtualMachineScaleSets`, using the parent `sku.name`.
- Flexible scale set members represented as standalone VMs.

Uniform scale set instances are covered through the parent SKU, not a separate child-instance rule.

**Deny is not create-only.** Evaluated updates, redeployments, and scale operations involving a matching SKU can also fail. It does not stop or resize running VMs. Review exemptions and migration plans before enabling Deny. Tag-only PATCH requests have special Azure Policy evaluation behavior.

This intentionally goes beyond Microsoft's capacity restrictions, which can still allow existing subscriptions to deploy affected sizes within approved quota. It does not reproduce quota or capacity-allocation decisions.

## Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `effect` | `Audit` | Select `Audit`, `Deny`, or `Disabled`. |
| `disallowedSkuPatterns` | Maintained retirement baseline | Match whole retired/retiring series and specific affected sizes. |
| `additionalDisallowedVmSkus` | `[]` | Add exact sizes beyond the baseline through a `VMSKUs`-strong-typed portal picker or JSON/IaC. |

A resource matches if its size matches **either** a baseline pattern **or** an additional exact SKU. Both comparisons are case-insensitive.

### Baseline patterns

The baseline uses Azure Policy `matchInsensitively`, **not** glob or regex syntax:

- `#` matches exactly one digit.
- `?` matches exactly one letter.
- `.` matches exactly one character.
- All other characters, including `*`, are literal.

For example, `Standard_D#s_v3` matches `Standard_D2s_v3`, but not `Standard_D2s_v5`. Patterns describe name shapes, not a catalog of valid Azure sizes; Azure Compute still validates SKU existence.

Keep the pattern list at **100 entries or fewer**, the value-count iteration limit. Overriding `disallowedSkuPatterns` replaces its entire default array; it does not append entries.

### Additional SKU picker: supplements, never replaces, the baseline

> **Retired SKUs may no longer appear in the portal picker. Use the picker only for additional restrictions above and beyond the retirement baseline. Do not reconstruct, replace, or remove the baseline based on picker results.**

`additionalDisallowedVmSkus` uses `"strongType": "VMSKUs"` to provide a portal VM size picker. It accepts exact SKU names, not patterns or family names, and defaults to an empty list to preserve existing behavior.

- A SKU missing from the picker is not evidence that it is permitted or supported.
- Keep baseline patterns even after their corresponding sizes disappear from the catalog.
- If a newly retired SKU is absent from the picker, update the baseline or supply its exact name through assignment parameter JSON or IaC.
- `strongType` is a portal selection hint, not automatic retirement detection or a live retirement-status validator.
- Updating the additional list replaces that list; retain previous selections that are still required.
- Removing an additional selection does not exempt a size that also matches a baseline pattern.

Example assignment parameters for an **illustrative organizational restriction**; this does not assert that `Standard_D2s_v5` is retired:

```json
{
  "effect": { "value": "Audit" },
  "additionalDisallowedVmSkus": { "value": ["Standard_D2s_v5"] }
}
```

Omitting `disallowedSkuPatterns` retains the definition's retirement baseline.

## Retirement coverage

| Listed retirement date | Series or exact sizes covered |
|---|---|
| 2025-09-30 | NCv3, including NC24rs |
| 2026-06-30 | DCsv2, including the unusually named `Standard_DC8_v2` |
| 2026-09-01 | DCas_cc_v5, DCads_cc_v5, ECas_cc_v5, ECads_cc_v5 |
| 2026-09-30 | NVv3, NVv4 |
| 2027-03-31 | Only M192idms_v2, M192ids_v2, M192ims_v2, M192is_v2 |
| 2027-05-31 | NP, HC, HBv2 |
| 2028-05-01 | D, Ds, Dv2, Dsv2, Ls |
| 2028-11-15 | Av2/Amv2, B v1, F, Fs, Fsv2, G, Gs, Lsv2 |
| 2029-10-31 | DCsv3, DCdsv3 |
| 2029-11-15 | Dv3, Dsv3, Ev3, Esv3, including E64i_v3/E64is_v3 |

The source still labels NVv3/NVv4 "Announced" despite their listed dates being in the past. They are included regardless of that wording. Dates are reference information, not time-based evaluation conditions.

The baseline includes constrained-core naming variants and avoids blocking whole modern families: Bsv2, NCasT4_v3, DCasv5, ECasv5, and non-listed Mv2 sizes do not match the defaults.

## Exclusions and limitations

- **Dedicated Hosts:** Dsv3-Type1/Type2 and Esv3-Type1/Type2 are host SKUs, not VM sizes. `Microsoft.Compute/hostGroups/hosts` is excluded.
- **Sovereign clouds:** Review applicability before assignment. The source explicitly excludes sovereign clouds from the Dv3/Dsv3/Ev3/Esv3 retirement.
- **Other historical retirements:** This is not a comprehensive policy for every historical retirement. Older sizes absent from the source, such as Av1, are not added.
- **Higher-level services:** AKS and similar service resources are not directly evaluated. Review their backing VM/VMSS coverage and managed-resource scopes separately.
- **Maintenance:** Review the source periodically and update the policy baseline when Microsoft changes the list. Assignment-specific baseline overrides also need maintenance.

## Create and assign

Run the following examples from **this directory** in PowerShell with Azure CLI installed. Replace the subscription ID and confirm the intended scope. Requires `az login` and permissions to create policy definitions and assignments. Nothing is deployed simply by adding these files to the repository.

```powershell
$ErrorActionPreference = 'Stop'
$subscriptionId = '<subscription-id>'
$scope = "/subscriptions/$subscriptionId"
$definition = Get-Content -LiteralPath '.\Audit-Deny-Retired-VM-SKUs.json' -Raw | ConvertFrom-Json
$prefix = Join-Path ([System.IO.Path]::GetTempPath()) ("retired-vm-skus-{0}" -f [guid]::NewGuid())
$rulesPath = "$prefix-rules.json"
$paramsPath = "$prefix-params.json"
$metadataPath = "$prefix-metadata.json"
try {
    $definition.policyRule | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $rulesPath -Encoding Ascii
    $definition.parameters | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $paramsPath -Encoding Ascii
    $definition.metadata | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $metadataPath -Encoding Ascii
    $definitionId = az policy definition create --subscription $subscriptionId --name 'audit-deny-retired-vm-skus' --display-name $definition.displayName --description $definition.description --mode $definition.mode --rules "@$rulesPath" --params "@$paramsPath" --metadata "@$metadataPath" --query id -o tsv
    if ($LASTEXITCODE -ne 0) { throw 'Policy definition creation failed.' }
} finally {
    foreach ($path in @($rulesPath, $paramsPath, $metadataPath)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -ErrorAction Stop }
    }
}
az policy assignment create --subscription $subscriptionId --name 'retired-vm-skus' --display-name 'Audit retired and retiring VM SKUs' --scope $scope --policy $definitionId --enforcement-mode Default
if ($LASTEXITCODE -ne 0) { throw 'Policy assignment creation failed.' }
```

Omitting assignment parameters uses the definition defaults: Audit, the retirement baseline, and no additional sizes. To configure additional sizes, supply a parameter JSON file with `--params '@<your-parameters-file>.json'` on the assignment command.

After reviewing Audit compliance results, exemptions, and deployment impact, switch to Deny:

```powershell
$denyParameters = Join-Path ([System.IO.Path]::GetTempPath()) ("retired-vm-skus-{0}.json" -f [guid]::NewGuid())
try {
    '{"effect":{"value":"Deny"}}' | Set-Content -LiteralPath $denyParameters -Encoding Ascii
    az policy assignment update --subscription $subscriptionId --name 'retired-vm-skus' --scope $scope --params "@$denyParameters" --enforcement-mode Default
    if ($LASTEXITCODE -ne 0) { throw 'Policy assignment update failed.' }
} finally {
    if (Test-Path -LiteralPath $denyParameters) { Remove-Item -LiteralPath $denyParameters -ErrorAction Stop }
}
```

The Deny example assumes the default baseline and no additional sizes. If the assignment has custom parameter values, include those values in the update parameter file rather than relying on an effect-only update to preserve them.

For management group definitions, use `--management-group` when creating the definition and the corresponding management group scope for assignment.

## Rollout checklist

1. Create the definition at the intended scope. Azure validates syntax and aliases when creating it.
2. Assign Audit; wait for compliance results or request a policy scan.
3. Confirm an available announced-retirement size is noncompliant and a supported replacement is compliant, for both VM and VMSS.
4. In a controlled scope, switch to Deny. Verify a matching size fails with `RequestDisallowedByPolicy` referring to this assignment, and a supported size passes policy evaluation. A quota, capacity, or SKU retirement error alone is not proof of policy enforcement.
5. Confirm resizing away from an affected size remains permitted.
