"""
qcs — Oracle EBS test automation CLI.

Commands:
  record   Run the AI recorder agent against live Oracle.
  gen      Generate a pytest suite from a recording.
    normalize Convert recording.jsonl into a normalized manifest.
  pages    Regenerate all Page Object files from the repository.
  flows    Regenerate all flow functions from the repository.
    repo-capture  Import a recording snapshot.db into the repository catalog.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path


# ── Subcommand implementations ────────────────────────────────────────────────

def cmd_record(args: argparse.Namespace) -> None:
    """qcs record <instructions.txt> --run-id <id> [--data <xlsx>] [--auto-name]"""
    from oracle_ai_agent import run_agent  # noqa: PLC0415
    from generator.build_pages import build_all as build_all_pages  # noqa: PLC0415
    from generator.build_test import generate_test  # noqa: PLC0415
    from qcs_replay.data import load_excel_rows  # noqa: PLC0415
    import config  # noqa: PLC0415

    instructions = Path(args.instructions).read_text(encoding="utf-8")
    data_columns: list[str] | None = None
    if args.data:
        rows = load_excel_rows(args.data)
        data_columns = list(rows[0].keys()) if rows else None

    asyncio.run(run_agent(instructions, args.run_id, data_columns,
                          auto_name=args.auto_name))

    rec_dir = config.RECORDINGS_DIR / args.run_id
    test_name = args.test_name or args.run_id
    out_dir = Path(args.out) if args.out else config.TESTS_DIR / test_name

    print("\n[Codegen] Regenerating page objects from repository …")
    page_paths = build_all_pages()
    print(f"[Codegen] Page objects regenerated: {len(page_paths)}")

    print(f"[Codegen] Generating replay suite: {test_name}")
    generate_test(rec_dir, out_dir, test_name)
    script_path = out_dir / f"test_{_snake(test_name)}.py"
    print(f"\nGenerated replay script: {script_path}")
    print(f"Run replay with: {sys.executable} -m pytest {out_dir} -q -s")


def _snake(name: str) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return re.sub(r"[^a-z0-9_]", "_", s.lower()).strip("_")


def cmd_gen_run(args: argparse.Namespace) -> None:
    """qcs gen run <recording_path> <test_name> [--out <dir>]"""
    from generator.build_test import generate_test  # noqa: PLC0415
    import config  # noqa: PLC0415

    recording_path = Path(args.recording_path)
    test_name = args.test_name
    out_dir   = Path(args.out) if args.out else config.TESTS_DIR / test_name
    generate_test(recording_path, out_dir, test_name)


def cmd_gen_validate(args: argparse.Namespace) -> None:
    """qcs gen validate <generated_test_dir>"""
    from generator.script_validator import validate_generated_dir  # noqa: PLC0415

    test_dir = Path(args.test_dir)
    results = validate_generated_dir(test_dir)
    if not results:
        print(f"No test_*.py files found under {test_dir}")
        return

    any_failures = False
    for result in results:
        print(result.summary())
        if not result.passed:
            any_failures = True

    n_files = len(results)
    n_fail  = sum(1 for r in results if not r.passed)
    n_reviews = sum(len(r.alias_reviews) for r in results)
    print(f"\n{n_files} file(s) checked — {n_fail} failure(s), {n_reviews} alias_review item(s)")
    if any_failures:
        sys.exit(1)


def cmd_normalize(args: argparse.Namespace) -> None:
    """qcs normalize <recording_path> [--out <manifest_path>]"""
    from qcs_manifest import normalize_recording  # noqa: PLC0415

    recording_path = Path(args.recording_path)
    out_path = Path(args.out) if args.out else None
    manifest_path = normalize_recording(recording_path, out_path)
    print(f"Normalized manifest written: {manifest_path}")


def cmd_pages(args: argparse.Namespace) -> None:
    """qcs pages [form_id ...]"""
    from generator.build_pages import generate_page_file, build_all  # noqa: PLC0415
    import config  # noqa: PLC0415

    if args.form_ids:
        for fid in args.form_ids:
            p = generate_page_file(fid)
            print(f"Generated: {p}")
    else:
        paths = build_all()
        print(f"Total: {len(paths)} page objects regenerated.")


def cmd_flows(args: argparse.Namespace) -> None:
    """qcs flows [flow_id ...]"""
    from generator.build_flows import generate_flow_file, build_all  # noqa: PLC0415

    if args.flow_ids:
        for fid in args.flow_ids:
            p = generate_flow_file(fid)
            print(f"Generated: {p}")
    else:
        paths = build_all()
        print(f"Total: {len(paths)} flow functions regenerated.")


def cmd_repo_validate(args: argparse.Namespace) -> None:
    """qcs repo validate — validate all repo_entries in the catalog."""
    from qcs_repo import store as repo_store  # noqa: PLC0415
    import config  # noqa: PLC0415

    repo_dir = getattr(args, "repo_dir", None)
    if repo_dir is None:
        repo_dir = config.REPO_DIR
    errors = repo_store.validate_repo(repo_dir)
    if errors:
        for e in errors:
            print(f"  ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print("Repository validation passed.")


def cmd_repo_capture(args: argparse.Namespace) -> None:
    """qcs repo-capture <form_id> <snapshot_db> [--screenshot <png>]"""
    from qcs_repo import store as repo_store  # noqa: PLC0415

    snapshot_db = Path(args.snapshot_db)
    if not snapshot_db.exists():
        raise FileNotFoundError(f"Snapshot DB not found: {snapshot_db}")
    screenshot = Path(args.screenshot) if args.screenshot else None
    elements = repo_store.load_snapshot_db(snapshot_db)
    repo_store.save_form_capture(
        args.form_id,
        elements,
        screenshot_path=screenshot,
        source=f"import:{snapshot_db.as_posix()}",
    )
    print(
        f"Imported {len(elements)} elements from {snapshot_db} "
        f"into {repo_store.repo_db_path().as_posix()}"
    )


def cmd_play(args: argparse.Namespace) -> None:
    """qcs play <instructions.txt> --run-id <id>"""
    from oracle_ai_agent.play import run_play  # noqa: PLC0415

    instructions = Path(args.instructions).read_text(encoding="utf-8")
    asyncio.run(run_play(instructions, args.run_id))


def cmd_aliases_validate(args: argparse.Namespace) -> None:
    """qcs aliases validate — validate all alias catalog files and report conflicts."""
    from generator.alias_catalog import AliasCatalog  # noqa: PLC0415
    import config  # noqa: PLC0415

    catalog_dir = Path(args.catalog_dir) if getattr(args, "catalog_dir", None) else config.BUSINESS_ALIASES_DIR
    catalog = AliasCatalog.load(catalog_dir)
    conflicts = catalog.validate()
    if not conflicts:
        print(f"Alias catalog OK — {len(catalog.form_files)} form(s) loaded from {catalog_dir}")
        return
    for conflict in conflicts:
        severity = "WARNING" if conflict.conflict_type == "alias_review_pending" else "ERROR"
        print(f"  {severity}: {conflict}", file=sys.stderr)
    n_errors = sum(
        1 for c in conflicts if c.conflict_type != "alias_review_pending"
    )
    if n_errors:
        sys.exit(1)


def cmd_aliases_report(args: argparse.Namespace) -> None:
    """qcs aliases report — print a full alias catalog summary and conflict report."""
    from generator.alias_catalog import AliasCatalog  # noqa: PLC0415
    import config  # noqa: PLC0415

    catalog_dir = Path(args.catalog_dir) if getattr(args, "catalog_dir", None) else config.BUSINESS_ALIASES_DIR
    catalog = AliasCatalog.load(catalog_dir)
    print(catalog.report())


def cmd_center(_args: argparse.Namespace) -> None:
    """qcs center — start the QCS Center FastAPI server."""
    try:
        from qcs_center.app import main as center_main  # noqa: PLC0415
    except ImportError:
        print(
            "ERROR: qcs_center dependencies not installed.\n"
            "Install with:  pip install -e '.[center]'",
            file=sys.stderr,
        )
        sys.exit(1)
    center_main()


def cmd_agent(args: argparse.Namespace) -> None:
    """qcs agent — start the QCS Agent long-poll worker."""
    try:
        from qcs_agent.main import main as agent_main  # noqa: PLC0415
        import qcs_agent.main as _am  # noqa: PLC0415
    except ImportError:
        print(
            "ERROR: qcs_agent dependencies not installed.\n"
            "Install with:  pip install -e '.[agent]'",
            file=sys.stderr,
        )
        sys.exit(1)
    # Forward CLI args by re-using sys.argv manipulation, or just call main()
    # which re-parses sys.argv from the agent's own argparse.
    agent_main()


# ── CLI parser ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="qcs",
        description="QCS Oracle EBS test automation CLI.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # record
    p_rec = sub.add_parser("record", help="Run the AI recorder against live Oracle EBS.")
    p_rec.add_argument("instructions", help="Path to English instructions .txt file.")
    p_rec.add_argument("--run-id", required=True, metavar="ID",
                       help="Unique run identifier (e.g. run_001).")
    p_rec.add_argument("--data", metavar="XLSX",
                       help="Optional Excel data file (provides placeholder column names).")
    p_rec.add_argument("--auto-name", action="store_true",
                       help="Skip interactive element-naming prompts; save as confirmed_by=ai.")
    p_rec.add_argument("--test-name", metavar="NAME",
                       help="Generated replay suite name (default: same as --run-id).")
    p_rec.add_argument("--out", metavar="DIR",
                       help="Generated replay output directory (default: generated_tests/<test-name>).")
    p_rec.set_defaults(func=cmd_record)

    # gen (subcommand group)
    p_gen = sub.add_parser("gen", help="Code generation commands: run, validate.")
    gen_sub = p_gen.add_subparsers(dest="gen_cmd", required=True)

    p_gen_run = gen_sub.add_parser(
        "run",
        help="Generate a pytest suite from a recording dir/jsonl/manifest.",
    )
    p_gen_run.add_argument(
        "recording_path",
        help="Path to recording run directory, recording.jsonl, or recording.manifest.json.",
    )
    p_gen_run.add_argument("test_name", help="Name for the generated test suite.")
    p_gen_run.add_argument("--out", help="Output directory (default: generated_tests/<test_name>).")
    p_gen_run.set_defaults(func=cmd_gen_run)

    p_gen_validate = gen_sub.add_parser(
        "validate",
        help="Validate generated test_*.py files for business readability.",
    )
    p_gen_validate.add_argument(
        "test_dir",
        help="Path to directory containing generated test_*.py files.",
    )
    p_gen_validate.set_defaults(func=cmd_gen_validate)

    # normalize
    p_norm = sub.add_parser(
        "normalize",
        help="Convert recording.jsonl into a normalized recording manifest.",
    )
    p_norm.add_argument(
        "recording_path",
        help="Path to recording run directory or recording.jsonl file.",
    )
    p_norm.add_argument(
        "--out",
        help="Output manifest path (default: <recording_dir>/recording.manifest.json).",
    )
    p_norm.set_defaults(func=cmd_normalize)

    # pages
    p_pages = sub.add_parser("pages", help="Regenerate all Page Object files from the repository.")
    p_pages.add_argument("form_ids", nargs="*", help="Specific form_ids to regenerate (default: all).")
    p_pages.set_defaults(func=cmd_pages)

    # flows
    p_flows = sub.add_parser("flows", help="Regenerate all flow functions from the repository.")
    p_flows.add_argument("flow_ids", nargs="*", help="Specific flow_ids to regenerate (default: all).")
    p_flows.set_defaults(func=cmd_flows)

    # aliases (with nested sub-commands)
    p_aliases = sub.add_parser("aliases", help="Alias catalog management commands.")
    aliases_sub = p_aliases.add_subparsers(dest="aliases_cmd", required=True)

    p_aliases_validate = aliases_sub.add_parser(
        "validate",
        help="Validate all alias catalog files and report conflicts.",
    )
    p_aliases_validate.add_argument(
        "--catalog-dir",
        dest="catalog_dir",
        help="Path to alias catalog directory (default: config/aliases).",
    )
    p_aliases_validate.set_defaults(func=cmd_aliases_validate)

    p_aliases_report = aliases_sub.add_parser(
        "report",
        help="Print a full alias catalog summary with all validation issues.",
    )
    p_aliases_report.add_argument(
        "--catalog-dir",
        dest="catalog_dir",
        help="Path to alias catalog directory (default: config/aliases).",
    )
    p_aliases_report.set_defaults(func=cmd_aliases_report)

    # repo (with nested sub-commands)
    p_repo = sub.add_parser("repo", help="Object repository management commands.")
    repo_sub = p_repo.add_subparsers(dest="repo_cmd", required=True)
    p_repo_validate = repo_sub.add_parser(
        "validate",
        help="Validate all repository entries and report errors.",
    )
    p_repo_validate.set_defaults(func=cmd_repo_validate)

    # repo-capture
    p_capture = sub.add_parser(
        "repo-capture",
        help="Import a recording snapshot.db into the repository element catalog.",
    )
    p_capture.add_argument("form_id", help="Repository form id, e.g. java_find_orders.")
    p_capture.add_argument("snapshot_db", help="Path to recordings/<run_id>/snapshot.db.")
    p_capture.add_argument("--screenshot", help="Optional form screenshot PNG to attach.")
    p_capture.set_defaults(func=cmd_repo_capture)

    # play
    p_play = sub.add_parser(
        "play",
        help="Computer-use play mode: login + form open deterministically, then GPT drives the UI.",
    )
    p_play.add_argument("instructions", help="Path to English instructions .txt file.")
    p_play.add_argument("--run-id", required=True, metavar="ID",
                        help="Unique run identifier (e.g. play_001).")
    p_play.set_defaults(func=cmd_play)

    # center
    p_center = sub.add_parser(
        "center",
        help="Start the QCS Center control-plane API (requires [center] extra).",
    )
    p_center.set_defaults(func=cmd_center)

    # agent
    p_agent = sub.add_parser(
        "agent",
        help="Start the QCS Agent worker on this machine (requires [agent] extra).",
    )
    p_agent.add_argument("--center-url", help="URL of QCS Center.")
    p_agent.add_argument("--token",      help="Bearer token (QCS_AGENT_TOKEN).")
    p_agent.add_argument("--name",       help="Unique agent name (default: COMPUTERNAME).")
    p_agent.add_argument("--tags",       help="Comma-separated capability tags.")
    p_agent.add_argument("--log-level",  default="INFO",
                         choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p_agent.set_defaults(func=cmd_agent)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
