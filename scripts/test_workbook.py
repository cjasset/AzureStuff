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

    def test_shared_parameters_preserve_complete_lookup(self):
        lookups = builder.lookup_values(self.workbook)
        sku_map, _, _, regions, region_ids = builder.build_lookups(self.data)
        for name, value in zip(builder.LOOKUP_PARAMETERS, (sku_map, regions, region_ids)):
            self.assertEqual(lookups[name], builder.kql_bag(value))
        parameters = [
            p for item in self.workbook["items"] if item.get("type") == 9
            for p in item["content"]["parameters"]
            if p["name"] in builder.LOOKUP_PARAMETERS
        ]
        self.assertEqual(len(parameters), 3)
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
        self.assertEqual(len(queries), 15)
        for name, query in queries:
            expanded = builder.expand_lookups(query, self.workbook)
            if name == "about-allsizes":
                self.assertEqual(expanded, query)
            else:
                self.assertTrue(expanded.startswith(original), name)
                for parameter in builder.LOOKUP_PARAMETERS:
                    self.assertNotIn("{" + parameter + "}", expanded)

    def test_structure_is_valid(self):
        self.assertEqual(structure_check(self.workbook), [])

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
        item["content"]["parameters"].pop()
        self.assertIn("missing shared lookup RegionIdMap", structure_check(workbook))

    def test_oversized_workbook_cannot_generate_deployment_template(self):
        with self.assertRaisesRegex(ValueError, "size limit"):
            builder.arm_template({"version": "Notebook/1.0",
                                  "items": [], "padding": "x" * builder.WORKBOOK_SIZE_LIMIT})


if __name__ == "__main__":
    unittest.main()
