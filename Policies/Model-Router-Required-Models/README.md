# Require explicit routing models for model router deployments

## Purpose

Require Microsoft Foundry model router deployments to specify a **non-empty routing model list** instead of relying on the default model set.

Policy definition: [Block-EmptyModel-Routers.json](./Block-EmptyModel-Routers.json).

This is an explicit-configuration guardrail. It complements approved-model governance; it is not an approved-model allowlist and should not be presented as evidence of a bypass in Microsoft's built-in policy.

## Use case

An organization may allow model router but require application teams to deliberately select and review the underlying models used for routing. A default deployment does not demonstrate that explicit selection.

Microsoft documents that model router's default deployment routes across the full supported model set, and that selecting no models uses the default set. An explicit subset gives teams control over cost, compliance, and performance characteristics. Newly introduced models are not added to an explicit inclusion list unless the team adds them.

Use this policy when your organizational standard is:

> Every model router deployment must declare its intended routing models, even when the default model set would otherwise be allowed.

## Relationship to the built-in approved-models policy

Microsoft's built-in **Foundry model deployments should only use approved models** governs permitted model publishers and asset IDs. Microsoft explicitly documents that model router honors this policy for its routing selections across the Foundry portal, REST API, Azure CLI, and ARM templates.

The two controls answer different questions:

| Control | Question |
|---|---|
| Built-in approved-models policy | Are the models permitted by organizational approval rules? |
| This custom policy | Does the deployment explicitly declare at least one routing model? |

The published built-in definition does not contain a `routing.models` non-empty-array check. However, that alone does **not** prove missing or empty lists bypass approved-model enforcement: Foundry applies service-side governance as well.

Assign both controls when you require approved models **and** explicit routing configuration. A non-empty list that includes an unapproved model can pass this custom check while still being rejected by the built-in.

**Review current built-ins first.** Microsoft's model-router governance documentation also describes preview policies for additional routing standards, including required routing rules. Check the Azure Policy Definitions catalog for an equivalent control before adopting or retaining this custom policy. Availability and coverage can evolve.

## What the policy evaluates

All three conditions must be true:

1. The resource type is `Microsoft.CognitiveServices/accounts/deployments`.
2. The deployment's model name is `model-router`.
3. The Azure Policy array alias `Microsoft.CognitiveServices/accounts/deployments/routing.models[*]` selects zero routing model entries.

The zero-count check targets missing, null, or empty routing model collections. It uses an array alias and `count`, rather than calling `length()` on a potentially missing value.

Ordinary non-router model deployments and unrelated resource types are outside the rule.

## Effects

| Effect | Behavior |
|---|---|
| `Deny` (default) | Rejects matching deployment requests evaluated by Azure Policy. |
| `Audit` | Reports matching deployments without blocking them. |
| `Disabled` | Turns off evaluation. |

**The definition defaults to Deny.** Start with an explicit Audit assignment to understand impact before enabling enforcement.

Example assignment parameter JSON:

```json
{
  "effect": {
    "value": "Audit"
  }
}
```

## Expected outcomes

| Deployment configuration | Result from this policy |
|---|---|
| Model router without a routing model collection | Matches the rule |
| Model router with a null routing model collection | Matches the rule |
| Model router with `routing.models: []` | Matches the rule |
| Model router with one or more valid routing model entries | Does not match the empty-list rule |
| Model router with an explicitly listed but unapproved model | Not rejected by this presence check; use approved-model governance |
| A non-router deployment | Outside the rule |

Whether a request is accepted overall also depends on the Foundry API, model availability, other assigned policies, and permissions.

## Scope and limitations

- **Presence, not approval:** It does not validate model names, publishers, versions, eligibility, or approval status.
- **Not full schema validation:** Foundry remains responsible for validating routing entry structure and whether the selected models are supported.
- **One entry is sufficient:** It does not require the two or more models recommended for model-router failover.
- **Default deployments are intentionally restricted:** Even if every model in the default set is approved, a missing or empty explicit list matches this policy.
- **Updates can be affected:** Deny applies to matching requests evaluated by Azure Policy, not just initial creation. Check existing deployment workflows before enforcement.
- **No automatic remediation:** It does not populate model lists, modify existing deployments, or stop inference on existing deployments.
- **Alias and API support matter:** Confirm that the routing array alias is available and maps correctly for the deployment API versions used in your environment. Do not equate a locally valid JSON file with verified Azure enforcement.
- **Deploy-time governance only:** This does not inspect prompts, responses, or individual inference-time routing decisions.

## Assignment guidance

Create a custom Azure Policy definition from the JSON and assign it at the resource group, subscription, or management group scope covering the relevant Foundry account deployments.

Start with `Audit`, review the affected deployments, configure explicit model subsets, and then use `Deny` if the impact matches your organizational standard. Keep the approved-models built-in assigned separately where approval restrictions are required.

The JSON contains definition properties, including `parameters` and `policyRule`; it is not an assignment document or a complete ARM deployment template. Creating or assigning this policy does not deploy any models.

## References

Documentation and built-in definition reviewed October 29, 2026:

- [Govern model router deployments with Azure Policy](https://learn.microsoft.com/en-us/azure/foundry/how-to/model-router-policy)
- [How to use model router: defaults and model subsets](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/model-router)
- [Model router concepts: subsets and automatic failover](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-router)
- [Built-in approved-models policy definition](https://github.com/Azure/azure-policy/blob/master/built-in-policies/policyDefinitions/Cognitive%20Services/AllowDeployRegistryModels_Audit.json)
- [Azure Policy array authoring guidance](https://learn.microsoft.com/en-us/azure/governance/policy/how-to/author-policies-for-arrays)
