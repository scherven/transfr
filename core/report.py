"""Formats a find_shortest_path() result into a human-readable report with
clickable openstreetmap.org links, for hand-verification of the algorithm's
output against the real map."""

from typing import Any, Dict


def _way_link(way_id: int) -> str:
    return f"https://www.openstreetmap.org/way/{way_id}"


def _relation_link(rel_id: int) -> str:
    return f"https://www.openstreetmap.org/relation/{rel_id}"


def _node_link(node_id: int) -> str:
    return f"https://www.openstreetmap.org/node/{node_id}"


def format_verification_report(
    station_name: str, ref_1: str, ref_2: str, result: Dict[str, Any]
) -> str:
    lines = [f"=== {station_name}: platform {ref_1} -> platform {ref_2} ==="]

    if not result.get("found"):
        lines.append(f"NOT FOUND -- reason: {result.get('reason')}")
        if "graph_ways" in result:
            lines.append(f"(searched a graph of {result['graph_ways']} ways / {result['graph_nodes']} nodes)")
        return "\n".join(lines)

    lines.append(f"station relation: {_relation_link(result['relation_id'])}")
    lines.append(f"platform {ref_1} edge way(s): " + ", ".join(_way_link(w) for w in result["edge_1_way_ids"]))
    lines.append(f"platform {ref_2} edge way(s): " + ", ".join(_way_link(w) for w in result["edge_2_way_ids"]))
    lines.append(
        f"walking time: {result['walking_time_seconds']}s "
        f"({result['walking_time_seconds'] / 60:.1f} min), "
        f"distance: {result['walking_distance_meters']}m"
    )
    lines.append(f"searched graph: {result['graph_ways']} ways / {result['graph_nodes']} nodes (full closure)")
    lines.append(f"path uses {len(result['way_path'])} way(s), in order:")
    for w in result["way_path"]:
        lines.append(f"  {_way_link(w)}")
    lines.append(f"start node: {_node_link(result['node_path'][0])}")
    lines.append(f"end node:   {_node_link(result['node_path'][-1])}")
    return "\n".join(lines)
