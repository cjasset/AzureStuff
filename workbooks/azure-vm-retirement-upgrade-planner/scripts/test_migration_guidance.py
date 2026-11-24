"""Guided decisions and standalone deployment/runtime contract regression tests."""
import copy
import json
import unittest

import build_workbook as builder
from migration_guidance import CLOSEOUT, COMMON_GATES, ROUTES, WORKLOADS
from validate_queries import self_containment_problems, structure_check, walk, walk_items


class GuidedMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = builder.load()
        cls.workbook = builder.build(cls.data)
        cls.items = {item["name"]: item for item in walk_items(cls.workbook["items"])}

    def test_single_workbook_resource_has_no_infrastructure_dependencies(self):
        template = builder.arm_template(self.workbook)
        self.assertEqual(self_containment_problems(self.workbook, template), [])
        self.assertEqual(len(template["resources"]), 1)
        self.assertEqual(template["resources"][0]["type"], "microsoft.insights/workbooks")
        for resource_type in ("Microsoft.OperationalInsights/workspaces", "Microsoft.Storage/storageAccounts",
                              "Microsoft.Web/sites", "Microsoft.Resources/deployments"):
            with self.subTest(resource_type=resource_type):
                broken = copy.deepcopy(template)
                broken["resources"].append({"type": resource_type})
                self.assertIn("self-contained: deploy exactly one workbook resource",
                              self_containment_problems(self.workbook, broken))

    def test_dependency_guard_rejects_law_remote_lookups_and_external_merges(self):
        for replacement in (
            {"queryType": 0, "query": "CustomMigration_CL | count"},
            {"queryType": 1, "resourceType": "microsoft.operationalinsights/workspaces", "query": "Heartbeat"},
            {"queryType": 1, "resourceType": "microsoft.resourcegraph/resources",
             "query": "externaldata(x:string)['https://example.invalid/map.json']"},
            {"queryType": 7, "query": json.dumps({"version": "Merge/1.0", "merges": [
                {"leftTable": "/external/table", "mergeType": "table"}]})},
        ):
            workbook = copy.deepcopy(self.workbook)
            workbook["items"].append({"type": 3, "name": "forbidden", "content": replacement})
            self.assertTrue(self_containment_problems(workbook))
        workbook = copy.deepcopy(self.workbook)
        workbook["items"].append(builder.text_item("![runtime asset](https://example.invalid/image.svg)", "remote"))
        self.assertIn("self-contained: remote/active content dependency", self_containment_problems(workbook))

    def test_control_plane_requests_are_only_the_approved_read_only_endpoints(self):
        for mutation in ({"method": "POST"}, {"path": "/external/endpoint"}):
            workbook = copy.deepcopy(self.workbook)
            item = next(i for i in walk_items(workbook["items"]) if i["name"] == "eligibility-live")
            request = json.loads(item["content"]["query"])
            request.update(mutation)
            item["content"]["query"] = json.dumps(request)
            self.assertTrue(structure_check(workbook))

    def test_route_and_acceptance_selections_have_complete_conditional_guidance(self):
        parameters = {p["name"]: p for p in self.items["parameters"]["content"]["parameters"]}
        for parameter, choices, prefixes in (
            ("MigrationRoute", ROUTES, ("assess-route-", "migrate-route-")),
            ("AcceptancePattern", WORKLOADS, ("workflow-acceptance-",)),
        ):
            self.assertTrue(parameters[parameter]["isGlobal"])
            self.assertIn(parameters[parameter]["value"], choices)
            for prefix in prefixes:
                for value in choices:
                    condition = self.items[prefix + value]["conditionalVisibility"]
                    self.assertEqual(condition["parameterName"], parameter)
                    self.assertEqual(condition["value"], value)
        self.assertEqual(set(ROUTES), {"resize", "parallel", "service", "gen1", "nvme"})
        self.assertIn("cannot be undone", ROUTES["gen1"][1])
        self.assertIn("not a universal rollback", ROUTES["nvme"][1])
        self.assertIn("writes made", ROUTES["parallel"][1])

    def test_ade_appliance_placement_and_guest_gates_cannot_be_mistaken_for_clearance(self):
        for phrase in ("15 September 2028", "OS versus data-disk", "Windows versus Linux",
                       "not an in-place conversion", "Linux ADE-encrypted OS", "UDE",
                       "Upload versus", "Marketplace image is not", "Unknown",
                       "Dedicated Host", "proximity placement groups", "capacity"):
            self.assertIn(phrase, COMMON_GATES)
        query = self.items["routes-evidence"]["content"]["query"]
        for phrase in ("AdeDiskRows", "ApplianceEvidence", "PlacementEvidence", "VM.properties.host.id",
                       "proximityPlacementGroup", "capacityReservationGroup", "VM.zones"):
            self.assertIn(phrase, query)
        self.assertNotIn("{DiscoveryWorkload}", query)
        self.assertNotIn("{LifecycleFilter}", query)

    def test_every_workload_has_real_done_criteria_and_iac_closeout(self):
        for pattern, phrase in (
            ("standalone", "transactions"), ("pools", "drained"), ("avd-pooled", "serve"),
            ("avd-personal", "assignments"), ("databricks", "job definitions"),
            ("stateful", "quorum"), ("appliance", "Data-plane"), ("unknown", "not stateless"),
        ):
            self.assertIn(phrase, WORKLOADS[pattern][1])
        for phrase in ("Terraform/Bicep", "Hybrid Benefit/BYOL", "restore points",
                       "extensions", "recovery window", "not proof of success"):
            self.assertIn(phrase, CLOSEOUT)

    def test_alternatives_are_real_compatible_targets_and_expose_uncertainty(self):
        sources, specs = builder.compatible_target_maps(self.data)
        records = {record["sku"]: record for record in self.data["skus"]}
        self.assertTrue(sources)
        self.assertIn(7, {spec[0] for spec in specs.values()})
        for source, targets in sources.items():
            record = records[source]
            self.assertLessEqual(len(targets), 9)
            for target in targets:
                generation = specs[target][0]
                candidates = builder.compatible_targets(record, generation, allow_unknown_temp_capacity=generation >= 6)
                self.assertIn(target, [candidate["sku"] for candidate in candidates])
                self.assertEqual(specs[target][1], record["current"]["cpu_arch"])
                self.assertGreaterEqual(specs[target][3], record["current"]["vcpu"])
                self.assertGreaterEqual(specs[target][4], record["current"]["memory_gb"])
        self.assertIn("Unknown", self.items["target-alternatives"]["content"]["query"])

    def test_v7_is_wired_to_assessment_live_placement_and_quota_without_suffix_fallback(self):
        self.assertIn("path-v7-grid", self.items)
        self.assertIn("{V7PathSkuMap}", self.items["eligibility-sources"]["content"]["query"])
        query = self.items["quota-demand"]["content"]["query"]
        self.assertIn("'{QuotaGeneration}' == '7'", query)
        self.assertIn("'{QuotaGeneration}' == 'default', RecommendedTarget", query)
        parameters = self.items["quota-parameters"]["content"]["parameters"]
        scenario = next(p for p in parameters if p["name"] == "QuotaGeneration")
        self.assertEqual(scenario["value"], "default")
        self.assertEqual([choice["value"] for choice in json.loads(scenario["jsonData"])],
                         ["default", "5", "6", "7"])
        record = copy.deepcopy(next(r for r in self.data["skus"] if r["sku"] == "Standard_D2s_v3"))
        record["targets"] = [t for t in record["targets"] if not t["sku"].endswith("_v7")]
        self.assertEqual(builder.select_path_target(record, 7), ({}, {}, {"path_target_unavailable"}))

    def test_live_capabilities_distinguish_counts_from_performance_and_quota_cpus(self):
        query = self.items["eligibility-fit"]["content"]["query"]
        for phrase in ("DiskLimitRecords == 1", "NicLimitRecords == 1", "DiskVisibilityComplete",
                       "NicVisibilityComplete", "isnull(DataDisks)", "DataDisks > MaxDataDisks",
                       "Nics > MaxNics", "Caps.vCPUsAvailable", "Caps.vCPUs", "Caps.CpuManufacturer",
                       "Caps.UncachedDiskBytesPerSecond", "Caps.CachedDiskBytesPerSecond",
                       "Unknown - validate workload demand", "ProvisionedDiskIops", "RecordMatches"):
            self.assertIn(phrase, query)
        self.assertNotIn("{DiscoveryWorkload}", query)
        text = self.items["eligibility-fit-intro"]["content"]["json"]
        self.assertIn("ceilings, not observed demand", text)
        self.assertIn("not additive", text)

    def test_native_advisor_and_freshness_do_not_create_telemetry_dependencies(self):
        query = self.items["advisor-retirement"]["content"]["query"]
        self.assertTrue(query.startswith("advisorresources\n"))
        self.assertNotIn("LogAnalytics", query)
        provenance = self.items["workflow-self-contained"]["content"]["json"]
        for phrase in ("No Log Analytics", "built-in Azure Resource Graph", "subscription-view snapshot",
                       "Current", "Extended", "OPTIONAL does **not** assert", "not offline",
                       self.data["_meta"]["generated"]):
            self.assertIn(phrase, provenance)
        queries = []
        walk(self.workbook, queries)
        self.assertEqual(len(queries), 35)


if __name__ == "__main__":
    unittest.main()
