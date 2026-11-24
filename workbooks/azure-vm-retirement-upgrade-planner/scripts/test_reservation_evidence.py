"""Reservation visibility, paging and lifecycle classification contracts."""
import copy
import json
import re
import unittest

import build_workbook as builder
import reservation_evidence as reservations
from validate_queries import structure_check, walk_items


class ReservationEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workbook = builder.build(builder.load())
        cls.items = {item["name"]: item for item in walk_items(cls.workbook["items"])}

    def test_inventory_has_a_dedicated_reservation_report(self):
        group = self.items["grp-reservations"]
        self.assertEqual(group["conditionalVisibility"],
                         {"parameterName": "InventoryView", "comparison": "isEqualTo", "value": "reservations"})
        self.assertNotIn("reservation-inventory", {
            item["name"] for item in walk_items(self.items["grp-overview"]["content"]["items"])})
        self.assertIn("Series commercial signals", self.items["ov-commercial"]["content"]["title"])

    def test_request_preserves_the_page_envelope_and_is_read_only(self):
        raw = self.items["reservation-live"]
        request = json.loads(raw["content"]["query"])
        self.assertEqual(request["method"], "GET")
        self.assertIsNone(request["data"])
        self.assertEqual(request["path"], "{ReservationPagePath}")
        self.assertEqual(request["transformers"][0]["settings"],
                         {"tablePath": "$", "columns": [{"path": "$", "columnid": "Page"}]})
        self.assertEqual(raw["conditionalVisibility"]["value"], "raw")

    def test_page_navigation_uses_global_state_and_native_merge_sources(self):
        controls = self.items["reservation-controls"]["content"]["parameters"]
        page = next(p for p in controls if p["name"] == "ReservationPagePath")
        self.assertTrue(page["isGlobal"])
        self.assertEqual(page["value"], reservations.FIRST_PAGE)
        next_page = self.items["reservation-next-page"]
        self.assertEqual(next_page["content"]["style"], "paragraph")
        self.assertEqual(next_page["content"]["links"][0]["subTarget"], "{ReservationNextPagePath}")
        self.assertEqual(next_page["conditionalVisibility"]["parameterName"], "ReservationNextPagePath")
        self.assertFalse(structure_check(self.workbook))

    def test_continuation_accepts_only_the_reservation_collection(self):
        pattern = re.search(r"@'([^']+)'", reservations.page_query()).group(1)
        for prefix in ("", "https://management.azure.com"):
            path = reservations.FIRST_PAGE + "&$skiptoken=opaque%2Btoken%3D%26next"
            self.assertEqual(re.fullmatch(pattern, prefix + path).group(1), path)
        for bad in ("https://example.com" + reservations.FIRST_PAGE,
                    reservations.FIRST_PAGE + "\n", "/subscriptions/other?api-version=1",
                    reservations.FIRST_PAGE + "#fragment"):
            self.assertIsNone(re.fullmatch(pattern, bad))

    def test_missing_partial_and_empty_results_are_not_all_clear(self):
        query = reservations.coverage_query()
        for value in ("not(RecordsKnown)", "array_length(Records) > 100", "isnotempty(NextLink)",
                      "Unknown", "Partial", "More pages", "not proof", "visibility is still limited"):
            self.assertIn(value, query)
        self.assertIn("No matching VM reservations on this API page",
                      self.items["reservation-inventory"]["content"]["noDataMessage"])

    def test_classification_is_case_insensitive_and_independent_of_deployed_vms(self):
        query = self.items["reservation-inventory"]["content"]["query"]
        for value in ("tolower('{LifecycleSkuMap}')", "tolower(ReservedSku)", "Fields[11]", "Fields[13]",
                      "Unassessed SKU", "Retirement / EOL", "RegionalPriceSignal", "Archived",
                      "Expired - historical record", "Quantity >= 0", "AppliedScopeProperties"):
            self.assertIn(value, query)
        self.assertNotIn("resources\n", query)
        self.assertNotIn("{LifecycleFilter}", query)
        self.assertNotIn("join", query)
        self.assertEqual(self.items["reservation-inventory"]["content"]["gridSettings"]["rowLimit"], 100)

    def test_permissions_terms_and_benefit_limits_are_visible(self):
        intro = self.items["reservation-intro"]["content"]["json"]
        for text in ("current tenant", "independent", "Reservation access is separate",
                     "Browse every API page", "expired/cancelled", "not lost value",
                     "not customer expiry", "do not reprice existing RI terms"):
            self.assertIn(text, intro)

    def test_validator_rejects_modified_endpoint_paging_and_merge_bindings(self):
        for change in ("method", "page", "merge", "navigation"):
            workbook = copy.deepcopy(self.workbook)
            items = {item["name"]: item for item in walk_items(workbook["items"])}
            if change == "method":
                request = json.loads(items["reservation-live"]["content"]["query"])
                request["method"] = "POST"
                items["reservation-live"]["content"]["query"] = json.dumps(request)
            elif change == "page":
                items["reservation-controls"]["content"]["parameters"][0]["value"] = "/subscriptions/other"
            elif change == "merge":
                items["reservation-next-parameter"]["content"]["parameters"][0]["query"] = "{}"
            else:
                items["reservation-next-page"]["content"]["links"][0]["subTarget"] = "/other"
            self.assertTrue(structure_check(workbook), change)
