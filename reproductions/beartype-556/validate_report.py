"""Pin the contention evidence expected from the beartype#556 fixture."""

import json
import pathlib
import sys

document = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
routes = document["resolution_routes"]
routes_by_id = {route["id"]: route for route in routes}
assert any(
    route["module"].startswith("myproject")
    and route["kind"] == "captured_claim"
    and route["finder_type_name"] == "ScikitBuildRedirectingFinder"
    and "meta_path_short_circuit" in route["signals"]
    for route in routes
), routes
assert any(
    routes_by_id[comparison["left_route_ref"]]["module"].startswith("myproject")
    and comparison["loader_type_differs"] is True
    for comparison in document["route_comparisons"]
), document["route_comparisons"]
