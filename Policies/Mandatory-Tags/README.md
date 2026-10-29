# Audit mandatory tags on resource groups

## Purpose

Provide **coarse-grained verification of mandatory tags** on Azure resource groups. This policy checks for a configurable set of required tag names and non-empty values, without defining separate rules for each tag or validating the business meaning of its value.

Policy definition: [Tags-AuditMandatoryTagsFromArrayVerifyNoNullTags.json](./Tags-AuditMandatoryTagsFromArrayVerifyNoNullTags.json).

## Use case

Use this policy as an initial governance check when resource groups must carry a minimum set of ownership, cost allocation, or environment metadata.

For example, an organization may require:

- `CostCenter` to support cost allocation and reporting.
- `ProductionTier` to identify a workload's operating tier.

The policy helps identify resource groups with incomplete tagging so owners can correct them. Its Audit effect supports visibility and adoption without blocking resource group creation or updates.

This is a completeness check, not a comprehensive tagging standard. A more specific policy or other validation is needed to confirm that a cost center exists, that a tier is approved, or that a value follows an organizational naming convention.

## What it does

- Evaluates only `Microsoft.Resources/subscriptions/resourceGroups`.
- Counts required tag names present in the resource group's tags.
- Separately checks required tag values for empty strings.
- Audits the resource group when a required tag is missing or has an empty value.
- Uses the fixed `audit` effect; there is no effect parameter.

The definition uses `mode: All` and compares its counts against the number of entries in the `mandatoryTags` array.

## Configuration

| Parameter | Type | Default |
|---|---|---|
| `mandatoryTags` | Array of required tag names | `["CostCenter", "ProductionTier"]` |

Supply tag **names**, not tag/value pairs. Overriding this parameter replaces the entire default list.

Example assignment parameter JSON:

```json
{
  "mandatoryTags": {
    "value": [
      "CostCenter",
      "ProductionTier",
      "ApplicationOwner"
    ]
  }
}
```

Keep the array non-empty to require tagging. An empty array requires no tags.

## Examples using the defaults

| Resource group tags | Expected result |
|---|---|
| `CostCenter = CC123`, `ProductionTier = Production` | Compliant |
| `CostCenter = CC123`, no `ProductionTier` | Noncompliant |
| `CostCenter = ""`, `ProductionTier = Production` | Noncompliant |
| Both required tags plus additional tags | Compliant |
| Both required tags contain non-empty but unapproved values | Passes this coarse-grained check; business validity is not evaluated |

## Scope and limitations

- **Resource groups only:** Resources inside a tagged resource group are not evaluated by this policy. They do not automatically inherit resource group tags.
- **Audit only:** The policy reports noncompliance. It does not add tags, correct values, remediate existing resources, or provide a selectable Deny effect.
- **No value allowlist:** It does not check approved cost centers, tier names, email addresses, or other business rules.
- **No whitespace normalization:** Whitespace-only values are not trimmed and can satisfy the non-empty-string check.
- **Assignment scope matters:** Assign at a scope containing the resource groups you want to assess, such as a subscription or management group, and review exclusions and exemptions.

Start with the mandatory tag names your organization needs for basic reporting and accountability. Use additional policies for finer-grained validation, inheritance, or enforcement.
