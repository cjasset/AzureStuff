"""Offline regression coverage for saved-workbook deployment."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import build_workbook as builder
from validate_queries import SUBSTITUTIONS, run_graph_query, structure_check, walk, walk_items


def find_item(workbook, name):
    return next(item for item in walk_items(workbook["items"]) if item["name"] == name)


class WorkbookDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = builder.load()
        cls.workbook = builder.build(cls.data)

    def test_tables_default_to_100_rows_without_changing_queries_or_heights(self):
        original = builder.grid_settings
        self.assertEqual(original({}, rows=25)["rowLimit"], 25)
        with patch("build_workbook.grid_settings",
                   side_effect=lambda *args, **kwargs: original(*args, **dict(kwargs, rows=10))):
            previous = builder.build(self.data)
        previous_items = {item["name"]: item for item in walk_items(previous["items"])}
        tables = 0
        for item in walk_items(self.workbook["items"]):
            if item.get("type") != 3:
                continue
            expected = copy.deepcopy(item)
            if item["content"].get("visualization") == "table":
                tables += 1
                self.assertEqual(item["content"]["gridSettings"]["rowLimit"], 100, item["name"])
                expected["content"]["gridSettings"]["rowLimit"] = 10
            self.assertEqual(expected, previous_items[item["name"]], item["name"])
        self.assertGreater(tables, 0)

    def test_tables_use_full_width_and_responsive_columns_without_changing_formatters(self):
        for item in walk_items(self.workbook["items"]):
            if item.get("type") != 3 or item["content"].get("visualization") != "table":
                continue
            self.assertEqual(item["customWidth"], "100", item["name"])
            self.assertNotIn("maxWidth", item.get("styleSettings", {}), item["name"])
            for rule in item["content"]["gridSettings"]["formatters"]:
                width = rule.get("formatOptions", {}).get("customColumnWidthSetting")
                if width:
                    self.assertTrue(width.endswith("fr"), (item["name"], rule))
        grid = builder.grid_settings({"Name": "30ch", "Status": "20%"},
                                     [builder.fmt("Status", 18, formatOptions={"palette": "auto"})])
        self.assertEqual(grid["formatters"][0]["formatOptions"]["customColumnWidthSetting"], "30fr")
        status = grid["formatters"][1]
        self.assertEqual(status["formatter"], 18)
        self.assertEqual(status["formatOptions"], {"palette": "auto", "customColumnWidthSetting": "20fr"})
        for name in ("ov-urgency", "ov-gen"):
            self.assertEqual(find_item(self.workbook, name)["customWidth"], "50")
        for item in walk_items(self.workbook["items"]):
            for parameter in item.get("content", {}).get("parameters", []):
                if "jsonData" in parameter:
                    self.assertEqual(parameter["jsonData"], json.dumps(
                        json.loads(parameter["jsonData"]), separators=(",", ":")))

    def test_inplace_is_default_and_both_approaches_are_explained_beside_controls(self):
        parameters = find_item(self.workbook, "quota-parameters")["content"]["parameters"]
        mode = next(p for p in parameters if p["name"] == "QuotaMigrationMode")
        self.assertEqual(mode["value"], "inplace")
        self.assertEqual(SUBSTITUTIONS["{QuotaMigrationMode}"], "inplace")
        self.assertEqual({o["value"] for o in json.loads(mode["jsonData"])}, {"inplace", "sidebyside"})
        intro = find_item(self.workbook, "quota-intro")["content"]["json"]
        for phrase in ("In-place (default)", "resize existing VMs", "positive core growth",
                       "full target-family cores", "verified source allocation",
                       "does not confirm that resizing is supported",
                       "Side-by-side", "keeping sources allocated", "both fleets"):
            self.assertIn(phrase, intro)

    def test_graph_validation_retries_throttling_and_preserves_exhausted_failure(self):
        failure = subprocess.CompletedProcess([], 1, "", 'ERROR: {"code": "RateLimiting"}')
        success = subprocess.CompletedProcess([], 0, '{"data":[]}', "")
        with patch("validate_queries.subprocess.run", side_effect=[failure, success]) as run, \
                patch("validate_queries.time.sleep") as sleep:
            self.assertIs(run_graph_query(["az", "graph"]), success)
            self.assertEqual(run.call_count, 2)
            sleep.assert_called_once_with(30)
        with patch("validate_queries.subprocess.run", return_value=failure) as run, \
                patch("validate_queries.time.sleep") as sleep:
            self.assertIs(run_graph_query(["az", "graph"]), failure)
            self.assertEqual(run.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [30, 60])

    def test_graph_validation_does_not_retry_authentication_or_query_errors(self):
        for code in ("AuthenticationFailed", "InvalidQuery"):
            failure = subprocess.CompletedProcess([], 1, "", json.dumps({"code": code}))
            with patch("validate_queries.subprocess.run", return_value=failure) as run, \
                    patch("validate_queries.time.sleep") as sleep:
                self.assertIs(run_graph_query(["az", "graph"]), failure)
                run.assert_called_once()
                sleep.assert_not_called()

    def test_workflow_has_six_stages_and_starts_with_guidance(self):
        links = find_item(self.workbook, "tabs")["content"]["links"]
        self.assertEqual([(link["linkLabel"], link["subTarget"]) for link in links], [
            ("Start here", "start"), ("1. Inventory", "inventory"), ("2. Assess", "assess"),
            ("3. Plan", "planning"), ("4. Migrate & validate", "migrate"),
            ("Methodology & references", "about")])
        roots = [item for item in self.workbook["items"] if item["type"] == 12]
        self.assertEqual([item["conditionalVisibility"]["value"] for item in roots],
                         [link["subTarget"] for link in links])
        parameters = find_item(self.workbook, "parameters")["content"]["parameters"]
        self.assertEqual(next(p["value"] for p in parameters if p["name"] == "WorkflowStage"), "start")
        self.assertTrue(next(p["isGlobal"] for p in parameters if p["name"] == "WorkflowStage"),
                        "Footer controls must update the stage visibility and tab selector above them.")
        self.assertNotIn("SelectedTab", {p["name"] for p in parameters},
                         "Legacy report-tab state must not select a nonexistent workflow stage.")

    def test_workflow_report_selectors_are_independent_and_stage_scoped(self):
        expected = {
            "inventory": ("InventoryView", ["overview", "impacted", "reservations", "discovery", "coverage"]),
            "assess": ("AssessView", ["paths", "routes", "prereq", "sql"]),
            "planning": ("PlanView", ["availability", "quota", "mapping", "alternatives", "plan"]),
        }
        parameters = {p["name"]: p for p in find_item(self.workbook, "parameters")["content"]["parameters"]}
        for stage, (parameter, keys) in expected.items():
            group = find_item(self.workbook, f"stage-{stage}")
            reports = [item for item in group["content"]["items"] if item["type"] == 12]
            self.assertEqual([item["conditionalVisibility"]["value"] for item in reports], keys)
            self.assertTrue(all(item["conditionalVisibility"]["parameterName"] == parameter for item in reports))
            self.assertEqual(parameters[parameter]["value"], keys[0])
            self.assertTrue(parameters[parameter]["isHiddenWhenLocked"])
            self.assertFalse(parameters[parameter].get("isGlobal", False))
            links = find_item(self.workbook, f"workflow-views-{stage}")["content"]["links"]
            self.assertEqual([link["subTarget"] for link in links], keys)
            self.assertTrue(all(link["cellValue"] == parameter for link in links))

    def test_workflow_guidance_and_next_actions_exist_for_every_stage(self):
        next_steps = {"start": "inventory", "inventory": "assess", "assess": "planning",
                      "planning": "migrate", "migrate": "inventory"}
        for _, stage in builder.WORKFLOW_STAGES:
            guide = find_item(self.workbook, f"workflow-guide-{stage}")["content"]["json"]
            for label in ("**Goal:**", "**What to do:**", "**Before moving on:**",
                          "**Outcome / interpretation:**"):
                self.assertIn(label, guide)
            links = find_item(self.workbook, f"workflow-next-{stage}")["content"]["links"]
            self.assertEqual(find_item(self.workbook, f"workflow-next-{stage}")["content"]["style"], "paragraph",
                             "Footer actions must not behave as auto-selecting tabs.")
            if stage in next_steps:
                self.assertIn(next_steps[stage], [link["subTarget"] for link in links])
            self.assertTrue(all(link["cellValue"] == "WorkflowStage" for link in links))

    def test_migration_stage_is_a_customer_checklist_not_an_executor(self):
        stage = find_item(self.workbook, "stage-migrate")
        self.assertFalse(any(item["type"] == 3 for item in walk_items([stage])))
        guide = find_item(self.workbook, "workflow-guide-migrate")["content"]["json"]
        self.assertIn("does not execute migrations, track completion", guide)
        checklist = find_item(self.workbook, "workflow-execution-checklist")["content"]["json"]
        for phrase in ("backup/restore", "rollback", "Baseline", "HA/failover",
                       "application-owner acceptance", "Do not resize managed backing VMs individually",
                       "not proof of migration success"):
            self.assertIn(phrase, checklist)

    def test_empty_results_are_not_an_all_clear_or_proof_sql_is_absent(self):
        sql = find_item(self.workbook, "sql-modernization-grid")["content"]["noDataMessage"]
        self.assertIn("does not prove SQL is absent", sql)
        inventory = find_item(self.workbook, "imp-grid")["content"]["noDataMessage"]
        self.assertIn("empty result is not migration approval", inventory)
        self.assertIn("subscription access", inventory)

    def test_navigation_validator_rejects_broken_nested_links_defaults_and_roots(self):
        workbook = copy.deepcopy(self.workbook)
        find_item(workbook, "workflow-views-assess")["content"]["links"][0]["subTarget"] = "missing"
        self.assertTrue(any("navigation/group mismatch" in error for error in structure_check(workbook)))
        workbook = copy.deepcopy(self.workbook)
        parameters = find_item(workbook, "parameters")["content"]["parameters"]
        next(p for p in parameters if p["name"] == "InventoryView")["value"] = "missing"
        self.assertIn("navigation parameter InventoryView has no valid default", structure_check(workbook))
        workbook = copy.deepcopy(self.workbook)
        find_item(workbook, "stage-inventory")["conditionalVisibility"]["parameterName"] = "InventoryView"
        self.assertIn("top-level group stage-inventory must be a workflow stage", structure_check(workbook))
        workbook = copy.deepcopy(self.workbook)
        parameters = find_item(workbook, "parameters")["content"]["parameters"]
        next(p for p in parameters if p["name"] == "WorkflowStage")["isGlobal"] = False
        self.assertIn("WorkflowStage must be global for footer navigation", structure_check(workbook))

    def test_workflow_item_names_and_link_ids_are_unique(self):
        items = list(walk_items(self.workbook["items"]))
        names = [item["name"] for item in items]
        self.assertEqual(len(names), len(set(names)))
        link_ids = [link["id"] for item in items if item["type"] == 11
                    for link in item["content"]["links"]]
        self.assertEqual(len(link_ids), len(set(link_ids)))

    def test_catalogue_coverage_is_in_inventory_not_hidden_in_methodology(self):
        coverage = find_item(self.workbook, "grp-coverage")
        self.assertEqual(coverage["conditionalVisibility"]["parameterName"], "InventoryView")
        self.assertEqual({item["name"] for item in coverage["content"]["items"] if item["type"] == 3},
                         {"about-coverage", "about-allsizes", "advisor-retirement"})
        self.assertFalse(any(item["type"] == 3 for item in walk_items([find_item(self.workbook, "stage-about")])))

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
        for generation in (5, 6, 7):
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
        self.assertEqual(len(parameters), 16)
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
        self.assertEqual(len(queries), 36)
        for name, query in queries:
            expanded = builder.expand_lookups(query, self.workbook)
            if name == "group-quota-evidence":
                self.assertEqual(query, builder.group_evidence_query())
            elif name.startswith("reservation-"):
                self.assertIn("{ReservationPageRecord:json}", query)
                self.assertNotIn("resources\n", query)
            elif name == "eligibility-status":
                self.assertEqual(query, builder.live_access_query())
            elif name == "eligibility-fit":
                self.assertTrue(expanded.startswith(builder.expand_lookups(
                    builder.compute_evidence_query(filter_workload=False), self.workbook)))
            elif name == "routes-evidence":
                self.assertTrue(expanded.startswith(builder.expand_lookups(
                    builder.compute_evidence_query(filter_workload=False), self.workbook)))
            elif name == "target-alternatives":
                self.assertTrue(expanded.startswith(builder.expand_lookups(builder.alternatives_query(), self.workbook)))
            elif name == "advisor-retirement":
                self.assertEqual(query, builder.advisor_query())
            elif name == "about-allsizes":
                self.assertEqual(expanded, builder.expand_lookups(builder.coverage_query(), self.workbook))
            elif name.startswith("discovery-"):
                self.assertNotIn("| where isnotempty(Rec)", expanded)
                self.assertIn("{DiscoveryWorkload}", expanded)
                if name in ("discovery-services", "discovery-pools"):
                    self.assertIn("| union batchresources", expanded)
                if name == "discovery-pools":
                    self.assertIn("[tolower(VmSize)]", expanded)
                    self.assertIn("toupper(tostring(split(CatalogueRecord", expanded)
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
            elif name == "eligibility-sources":
                expected = builder.base_query(sku_map, regions, region_ids,
                                              include_optional=False, include_placement_zones=True)
                self.assertTrue(expanded.startswith(expected), name)
            else:
                self.assertTrue(expanded.startswith(original), name)
            for parameter in builder.LOOKUP_PARAMETERS:
                self.assertNotIn("{" + parameter + "}", expanded)
        discovery = builder.discovery_items()
        names = [item["name"] for item in discovery]
        self.assertEqual(names[names.index("discovery-compute") + 1:names.index("discovery-services")],
                         ["discovery-pool-intro", "discovery-pools", "discovery-service-intro"])
        intro = find_item(self.workbook, "discovery-intro")["content"]["json"]
        self.assertIn("Batch service allocation mode", intro)
        self.assertIn("Microsoft-managed subscriptions", intro)
        pool_intro = find_item(self.workbook, "discovery-pool-intro")["content"]["json"]
        self.assertIn("not additional VM/VMSS rows", pool_intro)
        self.assertIn("zero nodes", pool_intro)
        self.assertIn("Resource Graph may omit Batch pools", pool_intro)
        services = find_item(self.workbook, "discovery-services")["content"]
        self.assertEqual(services["exportedParameters"], [
            {"fieldName": "ResourceId", "parameterName": "BatchAccountId", "parameterType": 1},
            {"fieldName": "Workload", "parameterName": "BatchSelectedWorkload", "parameterType": 1}])
        live = find_item(self.workbook, "discovery-batch-live")
        self.assertEqual(live["conditionalVisibility"], {
            "parameterName": "BatchSelectedWorkload", "comparison": "isEqualTo", "value": "Batch"})
        request = json.loads(live["content"]["query"])
        self.assertEqual(request["method"], "GETARRAY")
        self.assertEqual(request["path"], "{BatchAccountId}/pools?api-version=2024-07-01")
        self.assertTrue(live["content"]["noDataMessage"].startswith("Unknown:"))
        columns = request["transformers"][0]["settings"]["columns"]
        self.assertEqual({column["columnid"] for column in columns}, {
            "Pool", "VmSize", "DedicatedNow", "SpotNow", "DedicatedTarget", "SpotTarget", "Autoscale"})

    def test_structure_is_valid(self):
        self.assertEqual(structure_check(self.workbook), [])

    def test_retirement_and_regional_prices_are_independent_sections(self):
        group = find_item(self.workbook, "grp-impacted")
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
        group = find_item(self.workbook, "grp-sql")
        self.assertEqual(group["conditionalVisibility"]["value"], "sql")

    def test_upgrade_paths_are_three_stacked_full_width_panels_in_one_tab(self):
        group = find_item(self.workbook, "grp-paths")
        self.assertEqual(group["conditionalVisibility"]["value"], "paths")
        panels = [i for i in group["content"]["items"] if i["name"].startswith("path-v")]
        self.assertEqual([p["name"] for p in panels], ["path-v5-panel", "path-v6-panel", "path-v7-panel"])
        self.assertEqual([p["customWidth"] for p in panels], ["100", "100", "100"])
        for panel in panels:
            self.assertEqual(len(panel["content"]["items"]), 3)
            query = panel["content"]["items"][1]["content"]["query"]
            self.assertIn("ConversionOsCheck", query)
            self.assertIn("ConversionEncryptionCheck", query)
            self.assertIn("Guest BitLocker/ADE state unverified", query)
            self.assertIn("Manual selection required", query)
            self.assertIn("Custom/gallery image - verify guest drivers", query)
            self.assertNotIn("| project ReadinessScore", query)

    def test_inventory_explains_every_platform_score_band_and_penalty(self):
        key = find_item(self.workbook, "ov-score-key")["content"]["json"]
        for text in ("85-100", "60-84", "35-59", "0-34", "populated bands only",
                     "Few scored platform changes", "Moderate scored platform changes",
                     "Substantial scored platform changes", "Major scored platform changes",
                     "100 is not migration approval", "MANA validation deducts 0"):
            self.assertIn(text, key)
        for value in set(builder.PENALTY.values()):
            self.assertIn(str(value), key)

    def test_inventory_commercial_key_separates_signals_from_customer_billing(self):
        key = find_item(self.workbook, "ov-commercial-key")["content"]["json"]
        for text in ("1 February 2027", "1 July 2026", "Passed", "Upcoming",
                     "No cutoff in captured catalogue", "Coverage not assessed",
                     "this summary does not read your reservations", "Reservation exposure", "Regional price exposure"):
            self.assertIn(text, key)

    def test_compact_assessment_keeps_all_checks_in_details_and_export(self):
        self.assertEqual(len(builder.ASSESSMENT_CHECKS), 22)
        key = find_item(self.workbook, "assessment-column-key")["content"]["json"]
        for column in builder.ASSESSMENT_CHECKS:
            self.assertIn(f"| {column} |", key)
        for generation in (5, 6, 7):
            grid = find_item(self.workbook, f"path-v{generation}-grid")["content"]
            formatters = {f["columnMatch"]: f for f in grid["gridSettings"]["formatters"]}
            visible = {name for name, f in formatters.items() if f["formatter"] != 5}
            self.assertEqual(visible, {"Resource", "Target", "GenerationSupport", "NvmeBoot",
                                       "ManaNetworking", "ChecksNotMet", "ChecksUnknown", "Review"})
            self.assertEqual(formatters["Review"]["formatOptions"]["linkTarget"], "GenericDetails")
            self.assertEqual(grid["exportToExcelOptions"], "all")

    def test_assessment_counts_include_every_check_not_just_visible_columns(self):
        for generation in (5, 6, 7):
            query = find_item(self.workbook, f"path-v{generation}-grid")["content"]["query"]
            for name in builder.ASSESSMENT_CHECKS:
                self.assertIn(f"iff({name} == 'Not met', 1, 0)", query)
                self.assertIn(f"iff({name} == 'Unknown', 1, 0)", query)

    def test_live_availability_groups_all_placement_dimensions_without_guessing_zone_counts(self):
        source = find_item(self.workbook, "eligibility-sources")["content"]["query"]
        self.assertIn("PlacementZones = zones", source)
        self.assertIn("by SourceSubscriptionId, SourceRegion, SourceZone, SelectedTargetSku", source)
        self.assertIn("mv-expand SourceZone = PlacementOptions limit 2000", source)
        self.assertIn("dynamic(['Regional'])", source)
        self.assertIn("iff(UnknownInstanceResources > 0, tolong(''), KnownInstances)", source)
        self.assertIn("countif(isnull(ZoneInstances) or ZoneInstances < 0)", source)
        self.assertNotIn("| project Name, VmSize, SelectedTargetSku", source)
        self.assertNotIn("{LifecycleFilter}", source)

    def test_live_selection_explanation_is_downstream_of_row_exports(self):
        planning = find_item(self.workbook, "stage-planning")
        assess = find_item(self.workbook, "stage-assess")
        self.assertIn("eligibility-live", {item["name"] for item in walk_items(planning["content"]["items"])})
        self.assertNotIn("eligibility-live", {item["name"] for item in walk_items(assess["content"]["items"])})
        items = find_item(self.workbook, "grp-availability")["content"]["items"]
        availability_names = {item["name"] for item in walk_items(items)}
        quota_items = find_item(self.workbook, "grp-quota")["content"]["items"]
        quota_names = {item["name"] for item in walk_items(quota_items)}
        self.assertNotIn("quota-parameters", availability_names)
        self.assertFalse(any(name.startswith("eligibility-") for name in quota_names))
        names = [item["name"] for item in items]
        self.assertLess(names.index("eligibility-parameters"), names.index("eligibility-sources"))
        self.assertLess(names.index("eligibility-sources"), names.index("eligibility-selection"))
        self.assertLess(names.index("eligibility-selection"), names.index("eligibility-result"))
        quota_queries = [item["content"] for item in walk_items(quota_items)
                         if item.get("content", {}).get("queryType") == 1
                         and item["name"].startswith("quota-")]
        self.assertEqual(len(quota_queries), 4)
        for content in quota_queries:
            self.assertEqual(content["crossComponentResources"], ["{QuotaAllSubscriptions}"])
            self.assertNotIn("{Live", content["query"])
        intro = find_item(self.workbook, "eligibility-intro")["content"]["json"]
        self.assertNotIn("{LiveTarget}", intro)
        self.assertIn("Live target availability", intro)
        self.assertIn("**Green is SKU access only**", intro)
        self.assertIn("not a quota, Azure Policy, physical-capacity or migration", intro)
        self.assertIn("does not filter fleet quota demand", intro)
        self.assertLessEqual(len(intro.split()), 160)
        self.assertLessEqual(len(find_item(self.workbook, "quota-intro")["content"]["json"].split()), 160)

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
        group = find_item(self.workbook, "grp-availability")
        live = find_item(self.workbook, "eligibility-live")
        source = next(i for i in group["content"]["items"] if i["name"] == "eligibility-sources")
        request = json.loads(live["content"]["query"])
        self.assertEqual(request["method"], "GETARRAY")
        self.assertIn("@.name=='{LiveTarget}'", request["transformers"][0]["settings"]["tablePath"])
        self.assertIn("$.restrictions", [c["path"] for c in request["transformers"][0]["settings"]["columns"]])
        self.assertTrue(live["content"]["noDataMessage"].startswith("Unknown:"))
        self.assertEqual(live["conditionalVisibility"]["parameterName"], "LiveEvidence")
        result = find_item(self.workbook, "eligibility-result")
        self.assertEqual(result["conditionalVisibility"]["parameterName"], "LiveTarget")
        self.assertEqual(len(source["content"]["exportedParameters"]), 4)
        self.assertIn("{V5PathSkuMap}", source["content"]["query"])
        self.assertIn("{V6PathSkuMap}", source["content"]["query"])

    def test_access_summary_reuses_one_live_lookup_without_a_second_customer_click(self):
        parameters = find_item(self.workbook, "eligibility-record-parameter")["content"]["parameters"]
        record = parameters[0]
        self.assertEqual(record["name"], "LiveSkuRecord")
        self.assertEqual(record["queryType"], 7)
        self.assertTrue(record["isHiddenWhenLocked"])
        merge = json.loads(record["query"])
        self.assertEqual(merge["merges"][0]["leftTable"], "eligibility-live")
        self.assertEqual(merge["projectRename"][0]["originalName"], "[eligibility-live].SkuRecord")
        result = find_item(self.workbook, "eligibility-result")["content"]["items"]
        self.assertEqual([item["name"] for item in result],
                         ["eligibility-live", "eligibility-record-parameter", "eligibility-status",
                          "eligibility-capabilities"])
        capabilities = find_item(self.workbook, "eligibility-capabilities")
        self.assertEqual(capabilities["conditionalVisibility"],
                         {"parameterName": "LiveEvidence", "comparison": "isEqualTo", "value": "capabilities"})
        self.assertEqual([item["name"] for item in capabilities["content"]["items"]],
                         ["eligibility-fit-intro", "eligibility-fit"])
        arms = [item for item in walk_items(self.workbook["items"]) if item.get("content", {}).get("queryType") == 12]
        self.assertEqual({item["name"] for item in arms},
                         {"eligibility-live", "discovery-batch-live", "reservation-live",
                          "group-quota-membership", "group-quota-pool"})

    def test_access_summary_has_red_green_unknown_and_raw_is_opt_in(self):
        content = find_item(self.workbook, "eligibility-status")["content"]
        formatter = next(f for f in content["gridSettings"]["formatters"] if f["columnMatch"] == "AccessStatus")
        rules = formatter["formatOptions"]["thresholdsGrid"]
        self.assertEqual({rule["thresholdValue"]: rule["representation"] for rule in rules if "thresholdValue" in rule},
                         {"Restricted": "red", "Not restricted": "green", "Unknown": "orange"})
        self.assertTrue(content["noDataMessage"].startswith("Unknown:"))
        controls = find_item(self.workbook, "eligibility-parameters")["content"]["parameters"]
        self.assertEqual(next(p["value"] for p in controls if p["name"] == "LiveEvidence"), "summary")
        choices = json.loads(next(p["jsonData"] for p in controls if p["name"] == "LiveEvidence"))
        self.assertEqual({choice["value"] for choice in choices}, {"summary", "capabilities", "key", "raw"})
        key = find_item(self.workbook, "eligibility-key")
        self.assertEqual(key["conditionalVisibility"],
                         {"parameterName": "LiveEvidence", "comparison": "isEqualTo", "value": "key"})
        self.assertEqual(find_item(self.workbook, "eligibility-live")["conditionalVisibility"]["value"], "raw")

    def test_access_classifier_preserves_placement_scope_unknowns_and_quota_actions(self):
        query = builder.live_access_query()
        for expression in ("RestrictionType =~ 'Location'", "RestrictionType =~ 'Zone' and not(RegionalPlacement)",
                           "ConflictingLocations", "not(RestrictionsKnown)", "UncertainRestrictions > 0",
                           "not(RegionAdvertised)", "AdvertisedZoneEntries == 0", "QuotaBlocks > 0",
                           "{LiveSkuRecord:json}", "Open a region/zone access support request",
                           "No access ticket indicated"):
            self.assertIn(expression, query)

    def test_access_validator_rejects_missing_raw_payload_or_lookup_binding(self):
        workbook = copy.deepcopy(self.workbook)
        parameter = find_item(workbook, "eligibility-record-parameter")["content"]["parameters"][0]
        parameter["query"] = "{}"
        self.assertTrue(any("native Merge parameter" in error for error in structure_check(workbook)))
        workbook = copy.deepcopy(self.workbook)
        raw = find_item(workbook, "eligibility-live")["content"]
        request = json.loads(raw["query"])
        columns = request["transformers"][0]["settings"]["columns"]
        columns[:] = [column for column in columns if column["columnid"] != "SkuRecord"]
        raw["query"] = json.dumps(request)
        self.assertTrue(any("complete unmodified SkuRecord" in error for error in structure_check(workbook)))

    def test_prerequisite_statuses_preserve_explicit_unknowns(self):
        queries = []
        walk(self.workbook, queries)
        for generation in (5, 6, 7):
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
        group = find_item(self.workbook, "grp-quota")
        controls = next(i for i in group["content"]["items"] if i["name"] == "quota-parameters")
        parameters = controls["content"]["parameters"]
        self.assertEqual(len(parameters), 5)
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
        group = find_item(workbook, "grp-paths")
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
        group = find_item(self.workbook, "grp-quota")
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
        key = find_item(self.workbook, "quota-key")
        self.assertEqual(key["conditionalVisibility"],
                         {"parameterName": "QuotaDetails", "comparison": "isEqualTo", "value": "key"})
        controls = find_item(self.workbook, "quota-parameters")["content"]["parameters"]
        detail = next(p for p in controls if p["name"] == "QuotaDetails")
        self.assertEqual(detail["value"], "none")
        self.assertIn("key", {choice["value"] for choice in json.loads(detail["jsonData"])})

    def test_quota_headroom_is_not_pooled_with_migration_checks(self):
        self.assertIn("Counts['cores']", builder.quota_headroom_query("resources"))
        self.assertIn("FamilyAvailable < RegionalAvailable", builder.quota_headroom_query("resources"))
        self.assertNotIn("quota_headroom", builder.quota_summary_query("resources"))
        workbook = copy.deepcopy(self.workbook)
        group = find_item(workbook, "grp-quota")
        summary = next(i for i in group["content"]["items"] if i["name"] == "quota-summary")
        summary["content"]["crossComponentResources"] = ["{Subscriptions}"]
        self.assertTrue(any("quota-summary: ARG query not scoped" in p for p in structure_check(workbook)))

    def test_oversized_workbook_cannot_generate_deployment_template(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            builder.arm_template({"version": "Notebook/1.0",
                                  "items": [], "padding": "x" * builder.WORKBOOK_SIZE_LIMIT})


if __name__ == "__main__":
    unittest.main()
