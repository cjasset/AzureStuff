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
        self.assertEqual(len(parameters), 9)
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
        self.assertEqual(len(queries), 17)
        for name, query in queries:
            expanded = builder.expand_lookups(query, self.workbook)
            if name == "about-allsizes":
                self.assertEqual(expanded, query)
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

    def test_oversized_workbook_cannot_generate_deployment_template(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            builder.arm_template({"version": "Notebook/1.0",
                                  "items": [], "padding": "x" * builder.WORKBOOK_SIZE_LIMIT})


if __name__ == "__main__":
    unittest.main()
