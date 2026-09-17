"""Command-line interface.

    unifaculty run --university stanford --profile config/profile.yaml -v
    unifaculty run --all --llm mock            # dry run, no API key needed
    unifaculty robots https://www.fb12.uni-frankfurt.de/100453263/Stellenportal
    unifaculty doctor
    unifaculty universities
    unifaculty summary runs/<run_id>
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import platform
import sys
from pathlib import Path

from unifaculty import PRODUCT_TOKEN, REPO_URL, __version__
from unifaculty.compliance import MIN_DELAY_FLOOR
from unifaculty.config import ConfigError, load_dotenv, load_profile, load_settings, load_universities

EXIT_OK, EXIT_CONFIG, EXIT_RUNTIME = 0, 2, 3


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--settings", type=Path, default=Path("config/settings.yaml"),
                   help="settings YAML (default: config/settings.yaml; built-in defaults if missing)")
    p.add_argument("-v", "--verbose", action="count", default=0, help="-v debug, -vv trace on the console")
    p.add_argument("-q", "--quiet", action="store_true", help="only warnings and errors on the console")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="unifaculty", description="Find professors with open, funded PhD/postdoc "
                                     "positions — politely, with an LLM filter and detailed logs.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="crawl universities and write CSVs")
    _add_common(run)
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("-u", "--university", action="append", help="university slug (repeatable)")
    target.add_argument("--all", action="store_true", help="every file in config/universities/")
    run.add_argument("-p", "--profile", type=Path, default=Path("config/profile.yaml"), help="research profile YAML")
    run.add_argument("--llm", "--provider", dest="provider",
                     choices=["gemini", "openrouter", "anthropic", "ollama", "openai_compat", "mock"],
                     help="override llm.provider (mock = offline dry run)")
    run.add_argument("--model", help="override llm.model")
    run.add_argument("--fetcher", choices=["curl_cffi", "httpx"], help="override crawl.fetcher")
    run.add_argument("--max-pages", type=int, help="lower the page cap for this run")
    run.add_argument("--prefilter", choices=["strict", "loose", "off"], help="override crawl.prefilter")
    run.add_argument("--department-tag", action="append", help="only crawl sections with this tag (repeatable)")
    run.add_argument("--trace", action="store_true", help="store full prompts and raw LLM replies; TRACE in log files")
    run.add_argument("--debug-dump", action="store_true", help="save raw HTML of every fetched page under runs/<id>/pages/")
    run.add_argument("--no-cache", action="store_true", help="ignore cached LLM answers")

    robots = sub.add_parser("robots", help="explain what robots.txt says about a URL")
    _add_common(robots)
    robots.add_argument("url")
    robots.add_argument("--fetcher", choices=["curl_cffi", "httpx"], help="override crawl.fetcher")

    doctor = sub.add_parser("doctor", help="check your environment and configuration")
    _add_common(doctor)
    doctor.add_argument("-p", "--profile", type=Path, default=Path("config/profile.yaml"))

    unis = sub.add_parser("universities", help="list configured universities")
    _add_common(unis)

    summary = sub.add_parser("summary", help="print a run's summary.json and top reject reasons")
    summary.add_argument("run_dir", type=Path)
    return parser


# --------------------------------------------------------------------------- commands
def cmd_run(args: argparse.Namespace) -> int:
    from unifaculty.fetcher import build_fetcher
    from unifaculty.llm.factory import ProviderConfigError, build_provider
    from unifaculty.logs import close_logging, get_logger, log_event, setup_logging
    from unifaculty.pipeline import run_university, write_summary

    load_dotenv()
    settings = load_settings(args.settings)
    if args.provider:
        settings.llm.provider = args.provider
        if not args.model:
            settings.llm.model = None
    if args.model:
        settings.llm.model = args.model
    if args.fetcher:
        settings.crawl.fetcher = args.fetcher
    if args.prefilter:
        settings.crawl.prefilter = args.prefilter
    profile = load_profile(args.profile)
    if args.department_tag:
        profile.department_tags = args.department_tag
    universities = load_universities(Path(settings.universities_dir))
    slugs = sorted(universities) if args.all else args.university
    missing = [s for s in slugs if s not in universities]
    if missing:
        raise ConfigError(f"unknown university slug(s): {', '.join(missing)}. Known: {', '.join(sorted(universities))}")

    label = "all" if args.all else "-".join(slugs)
    ctx = setup_logging(Path(settings.runs_dir), label, verbosity=args.verbose, quiet=args.quiet,
                        trace=args.trace, debug_dump=args.debug_dump)
    log = get_logger("cli")
    log_event(log, logging.INFO, "run.start", version=__version__, python=platform.python_version(),
              universities=slugs, provider=settings.llm.provider, model=settings.llm.model,
              fetcher=settings.crawl.fetcher, prefilter=settings.crawl.prefilter,
              target_fields=profile.target_fields, degree_levels=profile.target_degree_levels)
    try:
        provider = build_provider(settings.llm)
    except ProviderConfigError as exc:
        log_event(log, logging.ERROR, "run.provider_error", error=str(exc))
        close_logging()
        return EXIT_CONFIG

    from unifaculty.ratelimit import RequestsPerMinute

    rpm = RequestsPerMinute(settings.llm.requests_per_minute)  # shared across universities
    results = []
    try:
        for slug in slugs:
            uni = universities[slug]
            if args.max_pages:
                uni.max_pages = max(1, min(uni.max_pages, args.max_pages))
            fetcher = build_fetcher(settings.crawl.fetcher, settings.crawl.impersonate, settings.crawl.timeout_seconds,
                                    settings.crawl.max_bytes, uni.languages)
            try:
                results.append(run_university(uni, profile, settings, ctx, fetcher, provider,
                                              use_cache=not args.no_cache, rpm=rpm))
            finally:
                fetcher.close()
            if results[-1].interrupted:
                break
    except RuntimeError as exc:
        log_event(log, logging.ERROR, "run.failed", error=str(exc), exc_info=True)
        close_logging()
        return EXIT_RUNTIME
    finally:
        provider.close()

    summary_path = write_summary(ctx, results, profile, settings, provider, settings.crawl.fetcher)
    for r in results:
        log_event(log, logging.INFO, "run.university_done", university=r.slug, kept=r.kept, rejected=r.rejected,
                  pages=r.pages_fetched, llm_calls=r.llm_calls, csv=r.csv_path, blocked_hosts=r.blocked_hosts or None,
                  duration_s=r.duration_s)
    log_event(log, logging.INFO, "run.done", run_dir=str(ctx.run_dir), summary=str(summary_path))
    close_logging()
    _print_table(results, ctx.run_dir)
    return EXIT_OK


def _print_table(results, run_dir: Path) -> None:
    print()
    print(f"{'university':<22}{'pages':>7}{'llm':>6}{'kept':>6}{'dropped':>9}  csv")
    for r in results:
        print(f"{r.slug:<22}{r.pages_fetched:>7}{r.llm_calls:>6}{r.kept:>6}{r.rejected:>9}  {r.csv_path}")
        for host, reason in r.blocked_hosts.items():
            print(f"{'':<22}stopped {host}: {reason}")
    print(f"\nlogs: {run_dir}  (run.log, events.jsonl, llm/, summary.json)")


def cmd_robots(args: argparse.Namespace) -> int:
    from unifaculty.compliance import effective_delay
    from unifaculty.fetcher import build_fetcher
    from unifaculty.robots import RobotsCache
    from unifaculty.urls import normalize_url

    settings = load_settings(args.settings)
    if args.fetcher:
        settings.crawl.fetcher = args.fetcher
    url = normalize_url(args.url)
    if not url:
        print(f"not an http(s) URL: {args.url}", file=sys.stderr)
        return EXIT_CONFIG
    fetcher = build_fetcher(settings.crawl.fetcher, settings.crawl.impersonate, settings.crawl.timeout_seconds,
                            settings.crawl.max_bytes, ["en"])

    def fetch_text(robots_url):
        result = fetcher.get(robots_url)
        return result.status, result.text if 200 <= result.status < 300 else "", result.error

    try:
        cache = RobotsCache(fetch_text)
        policy = cache.policy_for(url)
        decision = policy.check(url)
    finally:
        fetcher.close()
    print(json.dumps({
        "url": url, "robots_status": policy.status, "mode": policy.mode, "group": policy.group_label,
        "allowed": decision.allowed, "matched_rule": decision.rule, "reason": decision.reason,
        "crawl_delay": policy.crawl_delay, "effective_delay_seconds": effective_delay(None, policy.crawl_delay),
        "sitemaps": policy.sitemaps[:5], "product_token": PRODUCT_TOKEN,
    }, indent=2))
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    from unifaculty.llm.factory import DEFAULT_MODELS, KEY_ENV
    from unifaculty.secretscan import is_git_tracked, looks_like_placeholder, scan_repo

    from_dotenv = set(load_dotenv())
    ok = True

    def line(status: str, label: str, detail: str = "") -> None:
        print(f"  [{status:^4}] {label}{(' — ' + detail) if detail else ''}")

    print(f"unifaculty {__version__}  ({REPO_URL})")
    py_ok = sys.version_info >= (3, 10)
    ok &= py_ok
    line("ok" if py_ok else "FAIL", "python", platform.python_version())
    for module, required in [("curl_cffi", False), ("bs4", True), ("lxml", True), ("httpx", True),
                             ("pydantic", True), ("yaml", True)]:
        present = importlib.util.find_spec(module) is not None
        if required:
            ok &= present
        line("ok" if present else ("FAIL" if required else "warn"), f"module {module}",
             "" if present else ("required" if required else "optional — use --fetcher httpx without it"))
    if importlib.util.find_spec("curl_cffi"):
        try:
            import curl_cffi
            line("ok", "curl_cffi version", getattr(curl_cffi, "__version__", "?"))
        except Exception as exc:  # pragma: no cover
            line("warn", "curl_cffi import", str(exc))
    try:
        settings = load_settings(args.settings)
        line("ok", "settings", str(args.settings) if Path(args.settings).exists() else "built-in defaults")
        provider = settings.llm.provider
        envs = KEY_ENV.get(provider, ())
        key_env = next((e for e in envs if os.environ.get(e)), None)
        has_key = not envs or key_env is not None
        ok &= has_key
        model = settings.llm.model or DEFAULT_MODELS[provider]
        if not envs:
            line("ok", f"LLM {provider}", f"model {model}; no key needed")
        elif not key_env:
            line("FAIL", f"LLM {provider}", f"model {model}; set {' or '.join(envs)} in .env")
        else:
            # Never print any part of the key — not even the last characters.
            source = ".env" if key_env in from_dotenv else "environment variable"
            line("ok", f"LLM {provider}", f"model {model}; {key_env} set via {source}")
            if looks_like_placeholder(os.environ[key_env]):
                line("warn", f"{key_env}", "looks like a placeholder, not a real key")
        unis = load_universities(Path(settings.universities_dir))
        line("ok", "universities", ", ".join(sorted(unis)))
    except ConfigError as exc:
        ok = False
        line("FAIL", "config", str(exc))
    try:
        profile = load_profile(args.profile)
        line("ok", "profile", f"{', '.join(profile.target_fields)} ({', '.join(profile.target_degree_levels)})")
    except ConfigError as exc:
        ok = False
        copy_cmd = ("Copy-Item config\\profile.example.yaml config\\profile.yaml" if os.name == "nt"
                    else "cp config/profile.example.yaml config/profile.yaml")
        line("FAIL", "profile", f"{exc}  (run: {copy_cmd})")
    # --- secret hygiene -------------------------------------------------------------------------
    repo = Path.cwd()
    env_tracked = is_git_tracked(repo, ".env") if Path(".env").exists() else False
    if env_tracked:
        ok = False
        line("FAIL", ".env is tracked by git", "run `git rm --cached .env`, commit, and REVOKE the key at the provider")
    elif env_tracked is None and Path(".env").exists():
        line("warn", ".env", "not inside a git repository — could not confirm it is ignored")
    else:
        line("ok", ".env not tracked by git" if Path(".env").exists() else "no .env file", "")
    findings = scan_repo(repo) if (repo / ".git").exists() else []
    if findings:
        ok = False
        for finding in findings[:10]:
            line("FAIL", "possible secret in repo", f"{finding}  (revoke it, then remove it)")
    elif (repo / ".git").exists():
        line("ok", "no key-shaped strings in files git would commit")
    line("info", "politeness floor", f"{MIN_DELAY_FLOOR}s per host, robots.txt always enforced")
    for d in ("output", "runs", ".cache"):
        Path(d).mkdir(exist_ok=True)
        writable = os.access(d, os.W_OK)
        ok &= writable
        line("ok" if writable else "FAIL", f"writable ./{d}")
    print("\nall good" if ok else "\nfix the FAIL lines above")
    return EXIT_OK if ok else EXIT_CONFIG


def cmd_universities(args: argparse.Namespace) -> int:
    settings = load_settings(args.settings)
    for slug, uni in sorted(load_universities(Path(settings.universities_dir)).items()):
        print(f"{slug}: {uni.name} ({uni.country}) — domains {', '.join(uni.allowed_domains)}; "
              f"max_pages {uni.max_pages}; seeds verified {uni.seeds_verified or 'n/a'}")
        for s in uni.sections:
            print(f"    - {s.name} [{s.kind}; tags: {', '.join(s.tags) or '-'}]")
            for seed in s.seeds:
                print(f"        {seed}")
    return EXIT_OK


def cmd_summary(args: argparse.Namespace) -> int:
    path = args.run_dir / "summary.json"
    if not path.exists():
        print(f"no summary.json in {args.run_dir}", file=sys.stderr)
        return EXIT_CONFIG
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"run {data['run_id']}  ({data['duration_s']}s, llm {data['llm']['provider']}/{data['llm']['model']}, "
          f"fetcher {data['fetcher']})")
    print(f"totals: {data['totals']}")
    for uni in data["universities"]:
        stats = uni["stats"]
        print(f"\n{uni['slug']}: kept {uni['kept']}, rejected {uni['rejected']}, pages {uni['pages_fetched']}, "
              f"llm calls {uni['llm_calls']}")
        print(f"  http status: {stats.get('http_status')}")
        if uni.get("blocked_hosts"):
            print(f"  stopped hosts: {uni['blocked_hosts']}")
        top = list(stats.get("reject_reasons", {}).items())[:8]
        if top:
            print("  top reasons: " + ", ".join(f"{k}={v}" for k, v in top))
        print(f"  csv: {uni['csv_path']}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"run": cmd_run, "robots": cmd_robots, "doctor": cmd_doctor,
                "universities": cmd_universities, "summary": cmd_summary}
    try:
        return handlers[args.command](args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
