"""Offline regression coverage for saved-workbook deployment."""
import copy
import json
import unittest

import build_workbook as builder
from validate_queries import structure_check, walk


class WorkbookDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = builder.load()
        cls.workbook = builder.build(cls.data)

    def test_saved_workbook_and_arm_resource_fit_size_limit(self):
        resource = builder.arm_template(self.workbook)["resources"][0]
        self.assertLess(len(resource["properties"]["serializedData"].encode("utf-8")),
                        builder.WORKBOOK_SIZE_LIMIT)
        self.assertLess(len(json.dumps(resource).encode("utf-8")),
                        builder.WORKBOOK_SIZE_LIMIT)
        self.assertEqual(json.loads(resource["properties"]["serializedData"]), self.workbook)
        self.assertEqual(resource["properties"]["version"], self.workbook["version"])

    def test_customer_facing_name_is_consistent(self):
        title = "Azure VM Retirement & Upgrade Planner"
        template = builder.arm_template(self.workbook)
        self.assertEqual(template["parameters"]["workbookDisplayName"]["defaultValue"], title)
        header = next(item for item in self.workbook["items"] if item["name"] == "header")
        self.assertTrue(header["content"]["json"].startswith(f"# {title}\n"))

    def test_shared_parameters_preserve_complete_lookup(self):
        lookups = builder.lookup_values(self.workbook)
        sku_map, _, _, regions, region_ids = builder.build_lookups(self.data)
        expected = dict(zip(builder.BASE_LOOKUP_PARAMETERS, (sku_map, regions, region_ids)))
        for generation in (5, 6):
            path = builder.build_lookups(self.data, path_generation=generation)
            expected.update(zip(builder.PATH_LOOKUP_PARAMETERS[generation],
                                (path[0], path[3], path[4])))
        for name, value in expected.items():
            self.assertEqual(lookups[name], builder.kql_bag(value))
        parameters = [
            p for item in self.workbook["items"] if item.get("type") == 9
            for p in item["content"]["parameters"]
            if p["name"] in builder.LOOKUP_PARAMETERS
        ]
        self.assertEqual(len(parameters), 11)
        for parameter in parameters:
            self.assertEqual(parameter["type"], 1)
            self.assertTrue(parameter["isHiddenWhenLocked"])
            self.assertTrue(parameter["isRequired"])
            self.assertNotIn("query", parameter)

    def test_query_expansion_preserves_original_inventory_and_scoring(self):
        sku_map, _, _, regions, region_ids = builder.build_lookups(self.data)
        original = builder.base_query(sku_map, regions, region_ids)
        queries = []
        walk(self.workbook, queries)
        self.assertEqual(len(queries), 27)
        for name, query in queries:
            expanded = builder.expand_lookups(query, self.workbook)
            if name == "about-allsizes":
                self.assertEqual(expanded, builder.expand_lookups(builder.coverage_query(), self.workbook))
            elif name.startswith("discovery-"):
                self.assertNotIn("| where isnotempty(Rec)", expanded)
                self.assertIn("{DiscoveryWorkload}", expanded)
            elif name == "regional-price-grid":
                self.assertEqual(expanded, builder.regional_price_query(self.data))
            elif name == "sql-modernization-grid":
                expected = builder.base_query(sku_map, regions, region_ids,
                                              include_optional=False, include_flexible_members=True)
                self.assertTrue(expanded.startswith(expected), name)
            elif name.startswith("path-v"):
                generation = int(name[6])
                lookup = builder.build_lookups(self.data, path_generation=generation)
                expected = builder.base_query(lookup[0], lookup[3], lookup[4], include_optional=False)
                self.assertTrue(expanded.startswith(expected), name)
            else:
                self.assertTrue(expanded.startswith(original), name)
            for parameter in builder.LOOKUP_PARAMETERS:
                self.assertNotIn("{" + parameter + "}", expanded)

    def test_structure_is_valid(self):
        self.assertEqual(structure_check(self.workbook), [])

    def test_retirement_and_regional_prices_are_independent_sections(self):
        group = next(i for i in self.workbook["items"] if i["name"] == "grp-impacted")
        queries = {i["name"]: i["content"]["query"] for i in group["content"]["items"]
                   if i["type"] == 3}
        self.assertEqual(set(queries), {"imp-grid", "regional-price-grid"})
        self.assertIn("| where Lifecycle != 'OPTIONAL'", queries["imp-grid"])
        regional = queries["regional-price-grid"]
        self.assertNotIn("{LifecycleFilter}", regional)
        self.assertNotIn("{LifecycleSkuMap}", regional)
        self.assertNotIn("| where isnotempty(Rec)", regional)
        self.assertIn("series/scope unknown", regional)
        self.assertIn("not a universal exemption", regional)
        self.assertIn("not a formal public announcement", regional)
        self.assertEqual(builder.REGIONAL_PRICE_INCREASES, {
            "northeurope": 17, "francecentral": 11, "francesouth": 11,
            "westeurope": 9, "norwayeast": 9, "norwaywest": 9, "southeastasia": 8})
        self.assertNotIn("Dsv5", builder.REGIONAL_PRICE_EXCLUDED_SERIES)
        self.assertNotIn("Dsv6", builder.REGIONAL_PRICE_EXCLUDED_SERIES)

    def test_v7_exclusions_do_not_exempt_all_v7_skus(self):
        import re
        pattern = builder.REGIONAL_PRICE_V7_EXCLUSION_PATTERN
        for sku in ("standard_d2s_v7", "standard_d4lds_v7", "standard_e16-4ds_v7"):
            self.assertRegex(sku, pattern)
        for sku in ("standard_d2as_v7", "standard_e2ads_v7", "standard_d2s_v6"):
            self.assertIsNone(re.fullmatch(pattern, sku))

    def test_sql_candidates_require_extension_identity_and_retirement(self):
        queries = []
        walk(self.workbook, queries)
        query = dict(queries)["sql-modernization-grid"]
        self.assertIn("| where Lifecycle != 'OPTIONAL'", query)
        self.assertIn("| where not(IsVmss)", query)
        self.assertNotIn("isempty(tostring(properties.virtualMachineScaleSet.id))", query)
        self.assertIn("properties.publisher) =~ 'Microsoft.SqlServer.Management'", query)
        self.assertIn("properties.type) =~ 'SqlIaaSAgent'", query)
        self.assertIn("properties.typePropertiesType) =~ 'SqlIaaSAgent'", query)
        self.assertIn("SqlExtensionCount = countif(IsSqlExtension)", query)
        self.assertIn("microsoft.sqlvirtualmachine/sqlvirtualmachines", query)
        self.assertIn("SqlRegistrationCount = countif(IsRegistration)", query)
        self.assertIn("SqlLicenseTypes", query)
        self.assertIn("active HA membership/health unverified", query)
        self.assertIn("| join kind=inner", query)
        self.assertNotIn("{LifecycleFilter}", query)
        self.assertIn("does not prove SQL is running", query)
        self.assertNotIn("| where ExtensionState", query)
        group = next(i for i in self.workbook["items"] if i["name"] == "grp-sql")
        self.assertEqual(group["conditionalVisibility"]["value"], "sql")

    def test_upgrade_paths_are_two_half_width_panels_in_one_tab(self):
        group = next(i for i in self.workbook["items"] if i["name"] == "grp-paths")
        self.assertEqual(group["conditionalVisibility"]["value"], "paths")
        panels = [i for i in group["content"]["items"] if i["type"] == 12]
        self.assertEqual([p["name"] for p in panels], ["path-v5-panel", "path-v6-panel"])
        self.assertEqual([p["customWidth"] for p in panels], ["50", "50"])
        for panel in panels:
            self.assertEqual(len(panel["content"]["items"]), 3)
            query = panel["content"]["items"][1]["content"]["query"]
            self.assertIn("ConversionOsCheck", query)
            self.assertIn("ConversionEncryptionCheck", query)
            self.assertIn("Guest BitLocker/ADE state unverified", query)
            self.assertIn("Manual selection required", query)
            self.assertIn("Custom/gallery image - verify guest drivers", query)
            self.assertNotIn("| project ReadinessScore", query)

    def test_platform_score_is_not_migration_approval(self):
        queries = []
        walk(self.workbook, queries)
        query = dict(queries)["imp-grid"]
        self.assertIn("MigrationApproval = 'Not assessed", query)
        self.assertIn("Few scored platform changes - validation required", query)
        self.assertNotIn("Low - in-place resize", query)
        self.assertNotIn("low risk", dict(queries)["plan-waves"])

    def test_series_ri_cutoff_never_claims_customer_expiry_or_billing(self):
        queries = []
        walk(self.workbook, queries)
        for name, query in queries:
            self.assertNotIn("Expired - billing at pay-as-you-go", query, name)
        query = dict(queries)["map-detail"]
        self.assertIn("Series purchase/renewal cutoff passed; coverage not assessed", query)

    def test_coverage_retains_unmapped_and_counts_flexible_models_once(self):
        query = builder.coverage_query()
        self.assertIn("Not assessed - SKU absent", query)
        self.assertNotIn("| where isnotempty(Rec)", query)
        self.assertIn("IsVmss or isempty(tostring(properties.virtualMachineScaleSet.id))", query)

    def test_sql_evidence_has_registration_and_exact_extension_identity(self):
        query = builder.sql_evidence_query()
        self.assertIn("properties.virtualMachineResourceId", query)
        self.assertIn("properties.publisher) =~ 'Microsoft.SqlServer.Management'", query)
        self.assertIn("properties.typePropertiesType) =~ 'SqlIaaSAgent'", query)
        self.assertIn("by VmId", query)
        self.assertNotIn("| where ExtensionState", query)

    def test_discovery_separates_observations_from_readiness(self):
        query = builder.compute_evidence_query()
        for field in ("ExpectedNics", "DataDiskCount", "SharedDiskEvidence", "Hibernation",
                      "SecurityType", "SystemAssignedIdentity", "MarketplacePlan", "InventoryRole"):
            self.assertIn(field, query)
        self.assertIn("Unknown - incomplete NIC evidence", query)
        self.assertIn("Unknown - incomplete attached-disk evidence", query)
        self.assertIn("desktopvirtualizationresources", query)
        self.assertIn("nodeResourceGroup", query)
        self.assertIn("managedResourceGroupId", query)
        self.assertIn("Multiple owner associations", query)
        self.assertIn("Not assessed - application", query)

    def test_os_hints_never_claim_driver_validation(self):
        query = builder.os_eligibility_query()
        self.assertIn("iff(isnotempty(GuestOsName), GuestOsName", query)
        self.assertIn("not classified by captured subset", query)
        self.assertIn("Unknown - guest driver and target boot not tested", query)
        self.assertIn("Unknown - target hardware, driver and traffic not tested", query)
        self.assertNotIn("'TRUE'", query)

    def test_live_eligibility_is_exact_target_read_only_and_unknown_without_data(self):
        group = next(i for i in self.workbook["items"] if i["name"] == "grp-paths")
        live = next(i for i in group["content"]["items"] if i["name"] == "eligibility-live")
        source = next(i for i in group["content"]["items"] if i["name"] == "eligibility-sources")
        request = json.loads(live["content"]["query"])
        self.assertEqual(request["method"], "GETARRAY")
        self.assertIn("@.name=='{LiveTarget}'", request["transformers"][0]["settings"]["tablePath"])
        self.assertIn("$.restrictions", [c["path"] for c in request["transformers"][0]["settings"]["columns"]])
        self.assertTrue(live["content"]["noDataMessage"].startswith("Unknown:"))
        self.assertEqual(live["conditionalVisibility"]["parameterName"], "LiveTarget")
        self.assertEqual(len(source["content"]["exportedParameters"]), 4)
        self.assertIn("{V5PathSkuMap}", source["content"]["query"])
        self.assertIn("{V6PathSkuMap}", source["content"]["query"])

    def test_prerequisite_statuses_preserve_explicit_unknowns(self):
        queries = []
        walk(self.workbook, queries)
        for generation in (5, 6):
            query = dict(queries)[f"path-v{generation}-grid"]
            for column in ("TargetSelection", "GenerationSupport", "ConversionOS",
                           "ConversionEncryption", "NvmeBoot", "ManaNetworking",
                           "Architecture", "MemoryCapacity", "LocalDisk",
                           "CatalogueRegion", "ZonePlacement", "AvailabilitySet",
                           "ScaleSetModel", "DiskLimits", "NicLimits", "Quota", "Policy",
                           "AllocationCapacity", "Backup", "AsrConversion", "ApplicationValidation", "Rollback"):
                self.assertIn(column, query)
            self.assertIn("ManaNetworking = 'Unknown'", query)
            self.assertIn("Quota = 'Unknown'", query)
            self.assertIn("GuestOsName contains '2016', 'Not met'", query)
            self.assertIn("DiskAdeConfigured == true, 'Not met'", query)
            self.assertIn("'Not applicable'", query)

    def test_quota_demand_models_and_live_parameters_are_explicit(self):
        queries = []
        walk(self.workbook, queries)
        demand = dict(queries)["quota-demand"]
        check = dict(queries)["quota-check"]
        self.assertIn("| where Lifecycle != 'OPTIONAL'", demand)
        self.assertNotIn("{LifecycleFilter}", demand)
        self.assertIn("toint(sku.capacity)", demand)
        self.assertIn("SourceQuotaFamily == TargetQuotaFamily", demand)
        self.assertIn("TargetCores > SourceCores", demand)
        self.assertIn("UnresolvedResources", demand)
        self.assertIn("isnull(Limit) or isnull(Usage)", check)
        self.assertIn("Not met - quota shortfall", check)
        group = next(i for i in self.workbook["items"] if i["name"] == "grp-quota")
        controls = next(i for i in group["content"]["items"] if i["name"] == "quota-parameters")
        parameters = controls["content"]["parameters"]
        self.assertEqual(len(parameters), 4)
        self.assertEqual(parameters[0]["name"], "QuotaScope")
        self.assertEqual([o["value"] for o in json.loads(parameters[0]["jsonData"])], ["selected", "tenant"])
        self.assertIn("QuotaResources", check)
        self.assertIn("limit 2000", check)
        self.assertIn("MetricRecords != 1", check)
        self.assertIn("InPlaceUnverifiedResources > 0", check)
        families = json.loads(builder.lookup_values(self.workbook)["QuotaFamilyMap"])
        self.assertEqual(families["standard_d2s_v3"], "standardDSv3Family")
        self.assertEqual(families["standard_f2s_v2"], "standardFSv2Family")

    def test_path_targets_and_prerequisites_are_independent(self):
        for generation, target in ((5, "Standard_D2ds_v5"), (6, "Standard_D2ads_v6")):
            lookup = builder.build_lookups(self.data, path_generation=generation)[0]
            self.assertNotIn("Standard_D2s_v5", lookup)
            for source in ("Standard_DS1_v2", "Standard_D2s_v3"):
                fields = lookup[source].split("|")
                self.assertEqual(fields[3], target)
                warnings = fields[9].split(",")
                self.assertEqual("g" in warnings, generation == 6)
                self.assertEqual("n" in warnings, generation == 6)
                self.assertIn("h", warnings)
                if generation == 6:
                    self.assertIn("l", warnings)

    def test_path_without_generation_match_does_not_fall_back(self):
        record = copy.deepcopy(next(r for r in self.data["skus"]
                                    if r["sku"] == "Standard_D2s_v3"))
        for generation in (5, 6):
            with self.subTest(generation=generation):
                record["targets"] = [t for t in record["targets"]
                                     if not t["sku"].endswith(f"_v{generation}")]
                target, memory_target, codes = builder.select_path_target(record, generation)
                self.assertEqual(target, {})
                self.assertEqual(memory_target, {})
                self.assertEqual(codes, {"path_target_unavailable"})

    def test_v6_unknown_temp_capacity_is_flagged_not_assumed(self):
        record = copy.deepcopy(next(r for r in self.data["skus"]
                                    if r["sku"] == "Standard_D2s_v3"))
        target, _, codes = builder.select_path_target(record, 6)
        self.assertTrue(target["has_temp_disk"])
        self.assertIsNone(target["temp_disk_gb"])
        self.assertIn("target_local_disk_capacity_unverified", codes)
        for candidate in record["targets"]:
            if candidate["sku"].endswith("_v6"):
                candidate["temp_disk_gb"] = 1
        target, _, codes = builder.select_path_target(record, 6)
        self.assertEqual(target, {})

    def test_nested_upgrade_queries_must_be_subscription_scoped(self):
        workbook = copy.deepcopy(self.workbook)
        group = next(i for i in workbook["items"] if i["name"] == "grp-paths")
        panel = next(i for i in group["content"]["items"] if i["name"] == "path-v5-panel")
        panel["content"]["items"][1]["content"]["crossComponentResources"] = []
        self.assertIn("path-v5-grid: ARG query not scoped to {Subscriptions}", structure_check(workbook))

    def test_lab_retirement_targets_prefer_v5_and_recalculate_warnings(self):
        sku_map, _, _, _, _ = builder.build_lookups(self.data)
        for source in ("Standard_DS1_v2", "Standard_D2s_v3"):
            fields = sku_map[source].split("|")
            self.assertEqual(fields[3], "Standard_D2ds_v5")
            self.assertEqual(fields[4], "Standard_D2ds_v5")
            self.assertNotIn("g", fields[9].split(","))
            self.assertNotIn("n", fields[9].split(","))
            self.assertIn("h", fields[9].split(","))
            self.assertIn("Standard_D2ads_v6", fields[10].split(","))
        self.assertIn("v", sku_map["Standard_DS1_v2"].split("|")[9].split(","))
        self.assertNotIn("v", sku_map["Standard_D2s_v3"].split("|")[9].split(","))

    def test_v5_policy_does_not_mutate_reference_data(self):
        original = copy.deepcopy(self.data)
        builder.build_lookups(self.data)
        self.assertEqual(original, self.data)

    def test_promoted_targets_preserve_required_capabilities(self):
        for record in self.data["skus"]:
            preferred, memory_target, codes = builder.select_targets(record, True)
            if preferred.get("sku") == record.get("recommended_target"):
                continue
            current = record["current"]
            self.assertTrue(preferred["sku"].endswith("_v5"))
            self.assertFalse(preferred["nvme_required"])
            self.assertEqual(preferred["cpu_arch"], current["cpu_arch"])
            self.assertGreaterEqual(preferred["vcpu"], current["vcpu"])
            self.assertGreaterEqual(preferred["memory_gb"], current["memory_gb"])
            self.assertEqual(preferred, memory_target)
            self.assertNotIn("nvme_required", codes)
            if current.get("has_temp_disk"):
                self.assertTrue(preferred["has_temp_disk"])
            if current.get("temp_disk_gb") is not None:
                self.assertGreaterEqual(preferred["temp_disk_gb"], current["temp_disk_gb"])
            if current.get("premium_io"):
                self.assertTrue(preferred["premium_io"])

    def test_incompatible_v5_candidate_keeps_newer_target_with_review(self):
        record = copy.deepcopy(next(r for r in self.data["skus"]
                                    if r["sku"] == "Standard_D2s_v3"))
        original = next(t for t in record["targets"]
                        if t["sku"] == record["recommended_target"])
        compatible = next(t for t in record["targets"] if t["sku"] == "Standard_D2ds_v5")
        for field, value in (("memory_gb", 1), ("vcpu", 1),
                             ("cpu_arch", "Arm64"), ("has_temp_disk", False),
                             ("temp_disk_gb", 1), ("temp_disk_gb", None),
                             ("premium_io", False), ("nvme_required", True),
                             ("nvme_required", None), ("hyperv_generations", "")):
            with self.subTest(field=field):
                candidate = copy.deepcopy(compatible)
                candidate["sku"] = "Standard_Test_v5"
                candidate[field] = value
                record["targets"] = [original, candidate]
                selected, _, codes = builder.select_targets(record, True)
                self.assertEqual(selected, original)
                self.assertIn("v5_target_review", codes)

    def test_optional_modernization_is_not_rewritten_as_retirement(self):
        record = next(r for r in self.data["skus"]
                      if r["sku"] == "Standard_D2s_v5")
        selected, _, _ = builder.select_targets(record, False)
        self.assertEqual(selected["sku"], record["recommended_target"])

    def test_retirement_v6_recommendations_require_explicit_review(self):
        sku_map, _, _, _, _ = builder.build_lookups(self.data)
        for source, value in sku_map.items():
            fields = value.split("|")
            if fields[0] in ("RETIRE", "EOL") and fields[3].endswith(("_v6", "_v7")):
                self.assertIn("u", fields[9].split(","), source)

    def test_unknown_architecture_cannot_promote_a_v5_candidate(self):
        record = copy.deepcopy(next(r for r in self.data["skus"]
                                    if r["sku"] == "Standard_D2s_v3"))
        record["current"]["cpu_arch"] = None
        selected, _, codes = builder.select_targets(record, True)
        self.assertEqual(selected["sku"], record["recommended_target"])
        self.assertIn("v5_target_review", codes)

    def test_missing_shared_parameter_is_rejected(self):
        workbook = copy.deepcopy(self.workbook)
        item = next(i for i in workbook["items"] if i["name"] == "lookup-parameters")
        item["content"]["parameters"] = [p for p in item["content"]["parameters"]
                                         if p["name"] != "RegionIdMap"]
        self.assertIn("missing shared lookup RegionIdMap", structure_check(workbook))

    def test_quota_summary_is_compact_and_retains_shortfalls(self):
        group = next(i for i in self.workbook["items"] if i["name"] == "grp-quota")
        queries = {i["name"]: i for i in group["content"]["items"] if i["type"] == 3}
        summary = queries["quota-summary"]["content"]
        hidden = {f["columnMatch"] for f in summary["gridSettings"]["formatters"] if f.get("formatter") == 5}
        self.assertEqual(hidden, {"SubscriptionId", "FailedChecks", "UnknownChecks"})
        self.assertIn("sumif(CurrentCores, Metric == 'cores')", summary["query"])
        self.assertIn("pack_array(SubscriptionId, '__total__')", summary["query"])
        self.assertIn("FailedChecks > 0 and UnknownChecks > 0", summary["query"])
        colors = next(f for f in summary["gridSettings"]["formatters"] if f["columnMatch"] == "Quota status")
        self.assertEqual(colors["formatOptions"]["thresholdsGrid"][0]["representation"], "red")
        for name in ("quota-demand", "quota-check", "quota-headroom"):
            self.assertEqual(queries[name]["conditionalVisibility"]["parameterName"], "QuotaDetails")

    def test_quota_headroom_is_not_pooled_with_migration_checks(self):
        self.assertIn("Counts['cores']", builder.quota_headroom_query("resources"))
        self.assertIn("FamilyAvailable < RegionalAvailable", builder.quota_headroom_query("resources"))
        self.assertNotIn("quota_headroom", builder.quota_summary_query("resources"))
        workbook = copy.deepcopy(self.workbook)
        group = next(i for i in workbook["items"] if i["name"] == "grp-quota")
        summary = next(i for i in group["content"]["items"] if i["name"] == "quota-summary")
        summary["content"]["crossComponentResources"] = ["{Subscriptions}"]
        self.assertTrue(any("quota-summary: ARG query not scoped" in p for p in structure_check(workbook)))

    def test_oversized_workbook_cannot_generate_deployment_template(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            builder.arm_template({"version": "Notebook/1.0",
                                  "items": [], "padding": "x" * builder.WORKBOOK_SIZE_LIMIT})


if __name__ == "__main__":
    unittest.main()
