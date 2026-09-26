"""Machine-readable and review-friendly scale run reports."""

from __future__ import annotations

import json
from pathlib import Path


def report_markdown(report: dict) -> str:
    lines = [
        "# Brain V3 scale run",
        "",
        f"Git: `{report['git_sha']}`  ",
        f"Working tree clean: `{report['working_tree_clean']}`  ",
        f"Embedding model: `{report['embedding']['model']}`; digest `{report['embedding']['model_digest']}`  ",
        f"Hardware: `{report['hardware']}`",
        "",
        (
            "P95 retrieval budget (configured): "
            f"{report['retrieval_budget_ms']} ms. This is an operational ceiling for retrieval; "
            "DR-024's interactive number remains a whole-turn time-to-first-audio budget."
        ),
        "",
        "| Backend | Memories | Status | Search P50/P95/P99 ms | Raw query P95 ms | Hit@5 | FG P95 no load / decay / delta ms | Build s | Graph 1-hop / 2-hop ms | Not run reason |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for cell in report["cells"]:
        metrics = cell.get("metrics", {})
        search = metrics.get("retrieval_ms", {})
        raw = metrics.get("raw_backend_query_ms", {})
        recall = metrics.get("recall", {})
        no_load = metrics.get("foreground_no_load_ms", {})
        during = metrics.get("foreground_during_decay_ms", {})
        graph = cell.get("graph_traversal", {})
        lines.append(
            "| {backend} | {size:,} | {status} | {p50}/{p95}/{p99} | {raw95} | {hit} | {no95}/{during95}/{delta} | {build} | {hop1}/{hop2} | {reason} |".format(
                backend=cell["backend"],
                size=cell["size"],
                status=cell["status"],
                p50=_fmt(search.get("p50")),
                p95=_fmt(search.get("p95")),
                p99=_fmt(search.get("p99")),
                raw95=_fmt(raw.get("p95")),
                hit=_fmt(recall.get("hit_at_5")),
                no95=_fmt(no_load.get("p95")),
                during95=_fmt(during.get("p95")),
                delta=_fmt(metrics.get("foreground_delta_p95_ms")),
                build=_fmt(cell.get("build_seconds")),
                hop1=_fmt(graph.get("1_hop_ms")),
                hop2=_fmt(graph.get("2_hop_ms")),
                reason=cell.get("reason", ""),
            )
        )
    lines.extend(["", "## Retrieval budget breakpoint", ""])
    for backend, finding in report["budget_breakpoints"].items():
        lines.append(f"- **{backend}**: {finding}")
    return "\n".join(lines) + "\n"


def _fmt(value):
    return "—" if value is None else f"{value:.2f}"


def write_report(report: dict, json_path: Path, markdown_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(report_markdown(report))


def budget_breakpoints(cells: list[dict], budget_ms: float) -> dict[str, str]:
    result = {}
    backends = sorted({cell["backend"] for cell in cells})
    for backend in backends:
        measured = sorted(
            (
                cell
                for cell in cells
                if cell["backend"] == backend and cell["status"] == "measured"
            ),
            key=lambda cell: cell["size"],
        )
        exceeded = next(
            (
                cell["size"]
                for cell in measured
                if (cell.get("metrics", {}).get("retrieval_ms", {}).get("p95") or 0)
                > budget_ms
            ),
            None,
        )
        if exceeded is not None:
            result[backend] = (
                f"P95 first exceeds {budget_ms:g} ms at {exceeded:,} memories"
            )
        elif measured:
            result[backend] = (
                f"not exceeded through {max(cell['size'] for cell in measured):,} measured memories; "
                "larger requested sizes remain unmeasured"
            )
        else:
            result[backend] = "no measured sizes"
    return result
