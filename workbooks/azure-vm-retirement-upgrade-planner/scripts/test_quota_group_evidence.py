"""Guided quota-group scope, read-only and conservative evidence contracts."""
import copy
import json
import unittest

import build_workbook as builder
import quota_group_evidence as groups
from validate_queries import structure_check, walk_items


class QuotaGroupEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbook = builder.build(builder.load())
        cls.items = {i["name"]: i for i in walk_items(cls.workbook["items"])}
        cls.parameters = {p["name"]: p for i in cls.items.values()
                          for p in i.get("content", {}).get("parameters", [])}

    def test_inspector_is_optional_and_independent(self):
        self.assertEqual(self.items["quota-group-inspector"]["conditionalVisibility"]["value"], "groups")
        query = self.items["group-quota-evidence"]["content"]["query"]
        self.assertNotIn("QuotaResources", query)
        self.assertNotIn("QuotaScope", query)
        self.assertNotIn("LifecycleFilter", query)

    def test_discovery_uses_native_paged_lists(self):
        for name in ("QuotaGroupManagementGroup", "QuotaGroupId"):
            request = json.loads(self.parameters[name]["query"])
            self.assertEqual((request["method"], request["path"]), groups.ENDPOINTS[name])
            self.assertEqual(request["method"], "GETARRAY")
            settings = request["transformers"][0]["settings"]
            self.assertEqual(settings["tablePath"], "$")
            self.assertEqual(settings["columns"],
                             [{"columnid": "value", "path": "$.id"},
                              {"columnid": "label", "path": "$.name"}])

    def test_records_preserve_envelopes_and_native_sources(self):
        for name, source in (("QuotaGroupMemberRecord", "group-quota-membership"),
                             ("QuotaGroupPoolRecord", "group-quota-pool")):
            request = json.loads(self.items[source]["content"]["query"])
            self.assertEqual(request["method"], "GET")
            self.assertEqual(request["transformers"][0]["settings"]["columns"],
                             [{"columnid": "Record", "path": "$"}])
            merge = json.loads(self.parameters[name]["query"])
            self.assertEqual(merge["merges"][0]["leftTable"], source)
        self.assertFalse(structure_check(self.workbook))

    def test_membership_is_explicit_and_scope_matched(self):
        query = groups.evidence_query()
        for expression in ("Member.properties.subscriptionId", "== ExpectedMember",
                           "MemberState =~ 'Succeeded'", "== ExpectedPool", "PoolState =~ 'Succeeded'"):
            self.assertIn(expression, query)
        self.assertNotIn("managementGroupAncestorsChain", query)

    def test_partial_missing_and_duplicate_values_remain_unknown(self):
        query = groups.evidence_query()
        for expression in ("Pool.properties.nextLink", "Pool.nextLink", "ValuesTruncated",
                           "MetricRecords == 1", "AllocationRecords <= 1",
                           "AllocationsShape != 'array'", "InvalidValues == 0",
                           "InvalidAllocations == 0", "Unknown -"):
            self.assertIn(expression, query)

    def test_pool_does_not_change_subscription_checks(self):
        for name in ("quota-summary", "quota-check", "quota-demand", "quota-headroom"):
            self.assertNotIn("QuotaGroup", self.items[name]["content"]["query"])
        self.assertIn("Already allocated to subscription", groups.evidence_query())
        self.assertIn("Allocation required; then refresh", groups.evidence_query())
        text = self.items["group-quota-intro"]["content"]["json"]
        self.assertIn("not a tenant-wide scan", text)
        self.assertIn("Already allocated quota is not added again", text)

    def test_new_endpoints_and_methods_are_strictly_guarded(self):
        for name in ("QuotaGroupId", "group-quota-pool"):
            workbook = copy.deepcopy(self.workbook)
            items = {i["name"]: i for i in walk_items(workbook["items"])}
            target = (next(p for p in items["group-quota-controls"]["content"]["parameters"]
                           if p["name"] == name) if name == "QuotaGroupId" else items[name]["content"])
            request = json.loads(target["query"])
            request["method"] = "POST"
            target["query"] = json.dumps(request)
            self.assertTrue(any("read-only" in e for e in structure_check(workbook)))
        for name in ("QuotaGroupManagementGroup", "QuotaGroupId"):
            workbook = copy.deepcopy(self.workbook)
            items = {i["name"]: i for i in walk_items(workbook["items"])}
            target = next(p for p in items["group-quota-controls"]["content"]["parameters"]
                          if p["name"] == name)
            request = json.loads(target["query"])
            request["transformers"][0]["settings"]["tablePath"] = "$.value[*]"
            target["query"] = json.dumps(request)
            self.assertTrue(any("array-root" in e for e in structure_check(workbook)))

    def test_raw_evidence_is_opt_in_and_verified_is_not_green(self):
        self.assertEqual(self.parameters["QuotaGroupEvidence"]["value"], "summary")
        self.assertEqual(self.items["group-quota-pool"]["conditionalVisibility"]["value"], "raw")
        content = self.items["group-quota-evidence"]["content"]
        self.assertNotIn("green", json.dumps(content["gridSettings"]))
        self.assertEqual(self.parameters["QuotaGroupSubscription"]["type"], 6)
