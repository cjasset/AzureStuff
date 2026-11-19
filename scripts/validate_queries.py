#!/usr/bin/env python3
"""Check deployment artifacts and run every generated query against live ARG.
Use --offline without Azure; live checks require az login + resource-graph ext."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time

from build_workbook import LOOKUP_PARAMETERS, WORKBOOK_SIZE_LIMIT, expand_lookups, lookup_values

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "out")
WB = os.path.join(OUT, "azure-vm-retirement-workbook.json")

# Workbook parameter placeholders are not resolvable outside the portal.
SUBSTITUTIONS = {
    "{LifecycleFilter}": "RETIRE,EOL,OPTIONAL",
    "{QuotaScope}": "selected",
    "{QuotaMigrationMode}": "sidebyside",
    "{DiscoveryWorkload}": "All",
    "{EligibilityGeneration}": "5",
}

def azure_json(arguments):
    result = subprocess.run(["az", *arguments, "-o", "json"],
                            capture_output=True, text=True, shell=os.name == "nt")
    if result.returncode:
        raise RuntimeError(f"Azure CLI failed: {(result.stderr or result.stdout).strip()}")
    return json.loads(result.stdout)


def run_graph_query(arguments):
    for attempt in range(3):
        result = subprocess.run(arguments, capture_output=True, text=True, shell=os.name == "nt")
        throttled = re.search(r'"code"\s*:\s*"(RateLimiting|TooManyRequests)"',
                              result.stderr or result.stdout)
        if result.returncode == 0 or not throttled or attempt == 2:
            return result
        delay = 30 * (attempt + 1)
        print(f"  WARN  ARG throttled the query; retrying in {delay}s "
              f"(attempt {attempt + 2}/3)", flush=True)
        time.sleep(delay)


def walk(node, found):
    if isinstance(node, dict):
        c = node.get("content")
        if isinstance(c, dict) and c.get("queryType") == 1 and "query" in c:
            found.append((node.get("name", "?"), c["query"]))
        for v in node.values():
            walk(v, found)
    elif isinstance(node, list):
        for v in node:
            walk(v, found)


def walk_items(items):
    for item in items:
        yield item
        yield from walk_items(item.get("content", {}).get("items", []))


def structure_check(wb):
    """Offline structural checks on the workbook JSON."""
    problems = []
    items = wb.get("items", [])

    all_items = list(walk_items(items))
    links = [i for i in all_items if i.get("type") == 11]
    destinations = set()
    for l in links:
        for link in l["content"]["links"]:
            destinations.add((link.get("cellValue"), link["subTarget"]))
            if link.get("linkTarget") != "parameter":
                problems.append(f"link {link.get('linkLabel')} is not a parameter link")

    navigation_parameters = {parameter for parameter, _ in destinations}
    groups = [i for i in all_items if i.get("type") == 12 and i.get("conditionalVisibility")]
    visibility = set()
    for g in groups:
        cv = g["conditionalVisibility"]
        if cv.get("parameterName") in navigation_parameters:
            visibility.add((cv["parameterName"], cv.get("value")))

    if destinations != visibility:
        problems.append("navigation/group mismatch: every destination must select an existing stage/report")
    for group in (i for i in items if i.get("type") == 12):
        if group.get("conditionalVisibility", {}).get("parameterName") != "WorkflowStage":
            problems.append(f"top-level group {group.get('name')} must be a workflow stage")

    params = []
    def collect_parameters(node):
        if isinstance(node, dict):
            c = node.get("content", {})
            if isinstance(c, dict):
                params.extend(p["name"] for p in c.get("parameters", []))
                params.extend(p["parameterName"] for p in c.get("exportedParameters", []))
            for value in node.values():
                collect_parameters(value)
        elif isinstance(node, list):
            for value in node:
                collect_parameters(value)
    collect_parameters(wb)
    declared = {p["name"]: p for item in all_items if item.get("type") == 9
                for p in item["content"].get("parameters", [])}
    for parameter in navigation_parameters:
        if parameter not in declared or (parameter, declared[parameter].get("value")) not in destinations:
            problems.append(f"navigation parameter {parameter} has no valid default")
    if not declared.get("WorkflowStage", {}).get("isGlobal"):
        problems.append("WorkflowStage must be global for footer navigation")
    for required in ("Subscriptions", "LifecycleFilter"):
        if required not in params:
            problems.append(f"missing parameter {required}")
    lookups = lookup_values(wb)
    for name in LOOKUP_PARAMETERS:
        if name not in lookups:
            problems.append(f"missing shared lookup {name}")
    if len(json.dumps(wb, separators=(",", ":")).encode("utf-8")) >= WORKBOOK_SIZE_LIMIT:
        problems.append("workbook exceeds the saved-workbook size limit")

    # Every ARG query must be scoped and must not reference undefined parameters.
    found = []
    walk(wb, found)
    known = set(params)
    import re
    for name, q in found:
        for ref in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", q):
            if ref not in known:
                problems.append(f"{name}: references unknown parameter {{{ref}}}")
        # A declared parameter used WITHOUT braces is never substituted by the
        # portal and fails at runtime with "Failed to resolve scalar expression".
        for p in params:
            for m in re.finditer(rf"(?<![{{\w]){re.escape(p)}(?![}}\w])", q):
                problems.append(
                    f"{name}: parameter '{p}' used without braces at offset {m.start()} "
                    f"- must be written as {{{p}}}")

    def check_scope(node):
        if isinstance(node, dict):
            c = node.get("content", {})
            if isinstance(c, dict) and c.get("queryType") == 12:
                request = json.loads(c["query"])
                if request.get("method") != "GETARRAY" or request.get("version") != "ARMEndpoint/1.0":
                    problems.append(f"{node.get('name')}: live eligibility must be a read-only ARM array request")
                if request.get("path") != (
                    "/subscriptions/{LiveSubscription}/providers/Microsoft.Compute/skus"
                    "?api-version=2021-07-01&$filter=location eq '{LiveRegion}'"
                ):
                    problems.append(f"{node.get('name')}: unexpected live eligibility endpoint/scope")
            if isinstance(c, dict) and c.get("queryType") == 1:
                expected = ["{QuotaAllSubscriptions}"] if node.get("name", "").startswith("quota-") else ["{Subscriptions}"]
                if c.get("crossComponentResources") != expected:
                    problems.append(f"{node.get('name')}: ARG query not scoped to {expected[0]}")
                if node.get("name", "").startswith("quota-") and "{QuotaScope}" not in c["query"]:
                    problems.append(f"{node.get('name')}: missing quota planning-scope filter")
            for value in node.values():
                check_scope(value)
        elif isinstance(node, list):
            for value in node:
                check_scope(value)
    check_scope(wb)

    return problems


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true",
                        help="Check structure and published deployment artifacts without Azure.")
    parser.add_argument("--subscription", help="Live query scope; defaults to the current CLI subscription.")
    parser.add_argument("--quota-scope", choices=("selected", "tenant"), default="selected")
    parser.add_argument("--migration-mode", choices=("sidebyside", "inplace"), default="sidebyside")
    parser.add_argument("--query", action="append", dest="query_names",
                        help="Validate only these named ARG queries; repeat for related queries.")
    args = parser.parse_args()
    wb = json.load(open(WB, encoding="utf-8"))

    problems = structure_check(wb)
    root = os.path.dirname(HERE)
    with open(os.path.join(root, "workbook", "azuredeploy.json"), encoding="utf-8") as f:
        template = json.load(f)
    resource = template["resources"][0]
    with open(os.path.join(root, "workbook", "vm-lifecycle-readiness.workbook"),
              encoding="utf-8") as f:
        published = json.load(f)
    if json.loads(resource["properties"]["serializedData"]) != wb or published != wb:
        problems.append("published workbook/template differ from the generated workbook")
    if resource["properties"]["version"] != wb["version"]:
        problems.append("ARM workbook version differs from the serialized workbook")
    if len(json.dumps(resource).encode("utf-8")) >= WORKBOOK_SIZE_LIMIT:
        problems.append("ARM workbook resource exceeds 1 MB")
    print("Structure and deployment artifact checks")
    for problem in problems:
        print(f"  FAIL  {problem}")
    if not problems:
        print("  PASS  size limits, artifact parity, versions, tabs, parameters and query scoping")
    if args.offline or problems:
        return 1 if problems else 0

    queries = []
    walk(wb, queries)
    if args.query_names:
        missing = set(args.query_names) - {name for name, _ in queries}
        if missing:
            parser.error(f"Unknown query names: {', '.join(sorted(missing))}")
        queries = [(name, q) for name, q in queries if name in args.query_names]
    print(f"\nFound {len(queries)} Azure Resource Graph queries\n")

    subscription = args.subscription or azure_json(["account", "show"])["id"]
    substitutions = dict(SUBSTITUTIONS)
    substitutions.update({"{Subscriptions}": f"'{subscription}'", "{QuotaScope}": args.quota_scope,
                          "{QuotaMigrationMode}": args.migration_mode})
    failures = 0
    for name, q in queries:
        q = expand_lookups(q, wb)
        for a, b in substitutions.items():
            q = q.replace(a, b)
        with tempfile.NamedTemporaryFile("w", suffix=".kql", delete=False,
                                         encoding="utf-8") as f:
            f.write(q)
            path = f.name
        try:
            r = run_graph_query(
                ["az", "graph", "query", "-q", f"@{path}", "--subscriptions", subscription,
                 "--first", "5", "-o", "json"])
            if r.returncode == 0:
                data = json.loads(r.stdout)
                cols = list(data["data"][0].keys()) if data["data"] else []
                print(f"  PASS  {name:<18} rows={data['count']:<4} cols={len(cols)}")
            else:
                failures += 1
                err = (r.stderr or r.stdout).strip().splitlines()
                msg = " | ".join(x.strip() for x in err if x.strip())
                print(f"  FAIL  {name:<18} {msg}")
        finally:
            os.unlink(path)

    print(f"\n{len(queries) - failures}/{len(queries)} queries passed")
    return 1 if (failures or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
