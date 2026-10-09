"""commands_github — GitHub account farm and Student Pack verification for kancahub."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


def map_github_farm_args(a: Any, choice: Any = None) -> list[str]:
    """Map kancahub github farm CLI arguments to scripts/github_farm.py flags.

    Pure string work, no network: the caller resolves the egress once via
    `_choose_egress(...)` and passes it in as `choice`. Legacy default values
    (proxy=None/auto) mean "leave the child's built-in egress alone", which is
    what keeps offline callers (tests, other tools) side-effect free.
    """
    import kancahub
    cmd: list[str] = []
    if getattr(a, "index", None) is not None:
        cmd += ["--index", str(a.index)]
    # Translate --domain to github_farm.py's --email-domain
    domain = getattr(a, "domain", None)
    if domain:
        cmd += ["--email-domain", domain]
    inbox = getattr(a, "inbox", None)
    if inbox:
        cmd += ["--inbox", inbox]
    max_acc = getattr(a, "max_accounts", None)
    if max_acc is not None:
        cmd += ["--max-accounts", str(max_acc)]
    # Pacing: explicit --delay-min/--delay-max ALWAYS win; otherwise apply the
    # --pace preset (default 'normal' = 60-90s, ~1-1.5 min/account).
    pace = getattr(a, "pace", None)
    delay_min = getattr(a, "delay_min", None)
    delay_max = getattr(a, "delay_max", None)
    if delay_min is None and delay_max is None and pace in kancahub.PACE_PRESETS:
        delay_min, delay_max = kancahub.PACE_PRESETS[pace]
    if delay_min is not None:
        cmd += ["--delay-min", str(delay_min)]
    if delay_max is not None:
        cmd += ["--delay-max", str(delay_max)]
    retries = getattr(a, "retries", None)
    if retries:
        cmd += ["--retries", str(retries)]
    if choice is None:
        mode = getattr(a, "proxy", None)
        if getattr(a, "no_proxy", False):
            choice = kancahub.EgressChoice(None, "none", direct=True)
        elif mode and mode.lower() not in (kancahub.PROXY_AUTO, kancahub.PROXY_NONE, "direct"):
            # An explicit proxy URL is pure data — forward it without probing.
            choice = kancahub.EgressChoice(mode, "explicit")
        elif mode and mode.lower() == kancahub.PROXY_NONE:
            choice = kancahub.EgressChoice(None, "none", direct=True)
        # else: legacy call (no resolved choice) -> do not touch the child's egress
    # An explicit hop must win over the child's pool, or github_farm.py would
    # silently pick a different (pool) egress than the one we just verified.
    if choice is not None and choice.proxy and getattr(a, "pool", None):
        print(kancahub.col("yellow", "• --pool ignored: an explicit/auto proxy already selects the egress"))
    elif getattr(a, "pool", None):
        cmd += ["--pool", a.pool]
    if choice is not None:
        cmd += kancahub._proxy_flags(choice, supports_no_proxy=True)
    if getattr(a, "headless", False):
        cmd.append("--headless")
    if getattr(a, "dry_run", False):
        cmd.append("--dry-run")
    return cmd


def build_github_parser(sub: Any = None) -> argparse.ArgumentParser:
    """Build the argument parser for 'kancahub github' and its subcommands."""
    import kancahub
    if sub is None:
        ghp = argparse.ArgumentParser(
            prog="kancahub github",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="GitHub account farm (our domain / BINUS) + Student Pack SheerID verification",
        )
    elif hasattr(sub, "add_parser"):
        ghp = sub.add_parser(
            "github",
            help="GitHub account farm (our domain / BINUS) + Student Pack SheerID verification",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=(
                "GitHub account farm & Student Pack verification:\n"
                "\n"
                "  1. farm      Create GitHub account(s) using our domain (kancalabs.biz.id / kancalabs.my.id)\n"
                "               via KancaHub mail relay OR the BINUS school mailbox.\n"
                "  2. verify    Automate SheerID / GitHub Student Pack verification from a direct URL or\n"
                "               by extracting the verification link from the school M365 inbox.\n"
                "  3. edu       One-shot flow: farm -> verify -> human pause (camera/student-ID stays human).\n"
                "  4. check     Verify environment dependencies, Camoufox, and mailbox/relay credentials.\n"
                "\n"
                "Examples:\n"
                "  kancahub github farm --domain bizid --max-accounts 3\n"
                "  kancahub github farm --domain binus --index 1 --headless\n"
                "  kancahub github verify --from-mail\n"
                "  kancahub github verify --url 'https://services.sheerid.com/verify/<id>/'\n"
                "  kancahub github edu --interactive\n"
                "  kancahub github check"
            ),
        )
    else:
        ghp = sub

    ghs = ghp.add_subparsers(dest="github_cmd")

    # ---- farm ----
    gf = ghs.add_parser(
        "farm",
        help="sign up GitHub account(s) using our domain or BINUS mailbox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Create GitHub account(s) with automated form fill & OTP handling.\n"
            "\n"
            "Domain choices:\n"
            "  --domain bizid   gh<rand>@kancalabs.biz.id (via KancaHub mail relay, default relay)\n"
            "  --domain myid    gh<rand>@kancalabs.my.id  (via KancaHub mail relay)\n"
            "  --domain binus   raymondi+gh<N>@binus.ac.id (school mailbox, default)\n"
            "\n"
            "Rate limiting policy: 20-45s random delay, concurrency 1, max 5 accounts/run by default.\n"
            "Arkose / CAPTCHA puzzles remain manual if triggered by anti-bot.\n"
            "\n"
            "Examples:\n"
            "  kancahub github farm --domain bizid --max-accounts 3 --headless\n"
            "  kancahub github farm --domain binus --index 2 --dry-run\n"
            "  kancahub github farm --domain myid --proxy http://127.0.0.1:8888"
        ),
    )
    gf.add_argument("--index", type=int, default=1, help="starting N for account address (default: 1)")
    gf.add_argument(
        "--domain",
        choices=["binus", "bizid", "myid"],
        default="binus",
        help="email domain choice: binus (default, school mailbox), bizid (kancalabs.biz.id), myid (kancalabs.my.id)",
    )
    gf.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="binus",
        help="inbox source: binus (default, Outlook school mailbox) or relay (KancaHub mail relay)",
    )
    gf.add_argument("--headless", action="store_true", help="run browser headless (virtual display / Xvfb)")
    gf.add_argument("--dry-run", action="store_true", help="walk the signup flow, screenshot, do not submit/create")
    gf.add_argument("--proxy", default="auto", metavar="PROXY", help=kancahub.PROXY_HELP)
    gf.add_argument("--pool", default=None, help="proxy list file for rotation")
    gf.add_argument("--retries", type=int, default=0, metavar="N", help="retries on access_restricted block (default: 0)")
    gf.add_argument("--no-proxy", action="store_true", help="alias for --proxy none: force direct connection")
    gf.add_argument("--delay-min", type=float, default=None, metavar="SEC", help="min delay between accounts (overrides --pace)")
    gf.add_argument("--delay-max", type=float, default=None, metavar="SEC", help="max delay between accounts (overrides --pace)")
    gf.add_argument("--pace", choices=sorted(kancahub.PACE_PRESETS), default="normal",
                    help="per-account pacing: fast=20-45s, normal=60-90s (~1-1.5 min/account; avoids the "
                         "'take a breath' throttle), safe=120-180s (default: normal)")
    gf.add_argument("--max-accounts", type=int, default=5, metavar="N", help="max accounts to process in this run (default: 5)")
    gf.add_argument("--mobile-rotate", action="store_true", help="rotate tethered phone carrier IP before run & retry on block")

    # ---- verify ----
    gv = ghs.add_parser(
        "verify",
        help="automate SheerID / GitHub Student Pack verification (direct URL or from mailbox)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Automate SheerID / GitHub Student Pack verification.\n"
            "\n"
            "Verification sources:\n"
            "  --from-mail     Read newest SheerID verification link from the school M365 mailbox\n"
            "                  using Camoufox (unwraps SafeLinks and extracts the verify URL).\n"
            "  --url <URL>     Direct SheerID verification link (https://services.sheerid.com/verify/...).\n"
            "\n"
            "HONEST DISCLOSURE:\n"
            "  GitHub Student Developer Pack uses SheerID for select academic partner verifications.\n"
            "  The underlying automated verifier was originally developed for K-12 teacher verification.\n"
            "  If GitHub requests a student program or requires student ID upload / camera capture,\n"
            "  the solver may report a program mismatch and require manual document upload.\n"
            "\n"
            "Examples:\n"
            "  kancahub github verify --from-mail\n"
            "  kancahub github verify --url 'https://services.sheerid.com/verify/<id>/'\n"
            "  kancahub github verify --from-mail --gateway --headless"
        ),
    )
    gv.add_argument("--url", default=None, help="direct SheerID verification URL")
    gv.add_argument("--from-mail", action="store_true", help="find and extract SheerID link from school M365 mailbox")
    gv.add_argument("--timeout", type=int, default=180, help="mailbox polling timeout in seconds (default: 180)")
    gv.add_argument("--proxy", default=None, help="proxy URL (e.g. http://127.0.0.1:8888)")
    gv.add_argument("--gateway", action="store_true", help="use local gateway 127.0.0.1:8888")
    gv.add_argument("--headless", action="store_true", help="run browser headless when inspecting mailbox")
    gv.add_argument("--open", action="store_true", help="open verification link in browser and inspect resulting page")
    gv.add_argument("--debug", action="store_true", help="enable debug mode for SheerID verifier")
    gv.add_argument("--email", default=None, help="manual email to supply to SheerID verifier")

    # ---- edu (one-shot: farm -> verify -> human pause) ----
    ge = ghs.add_parser(
        "edu",
        help="one-shot GitHub Education flow: farm -> SheerID verify -> human pause",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "One-shot GitHub Education (Student Pack) flow.\n"
            "\n"
            "Stages (stops + reports at the first failure):\n"
            "  1. farm          Create ONE GitHub account (github_farm.py) with the chosen\n"
            "                   --domain/--inbox, through --proxy auto egress.\n"
            "  2. verify        SheerID verification: uses --url, else extracts the link from\n"
            "                   the school mailbox (--from-mail). Skipped with --no-verify.\n"
            "  3. human pause   If verification needs a real camera/student-ID/phone upload:\n"
            "                   with --interactive, print instructions and WAIT for you;\n"
            "                   otherwise stop and tell you a human is required.\n"
            "\n"
            "HONEST: camera / student-ID / phone steps cannot be automated — this flow is\n"
            "human-assisted by design. No fake documents are ever generated here.\n"
            "\n"
            "Examples:\n"
            "  kancahub github edu --interactive\n"
            "  kancahub github edu --domain binus --inbox binus --url 'https://services.sheerid.com/verify/<id>/'\n"
            "  kancahub github edu --no-verify            # account only"
        ),
    )
    ge.add_argument(
        "--domain",
        choices=["binus", "bizid", "myid"],
        default="bizid",
        help="email domain for the new account: bizid (default, kancalabs.biz.id), "
             "myid (kancalabs.my.id), binus (school mailbox)",
    )
    ge.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="relay",
        help="inbox source: relay (default, KancaHub mail relay) or binus (Outlook school mailbox)",
    )
    ge.add_argument("--proxy", default="auto", metavar="PROXY", help=kancahub.PROXY_HELP)
    ge.add_argument("--interactive", action="store_true",
                    help="pause and wait for the human at the camera/student-ID/phone step")
    ge.add_argument("--no-verify", action="store_true",
                    help="skip the SheerID verify stage (account creation only)")
    ge.add_argument("--url", default=None,
                    help="direct SheerID verification URL (skips mailbox extraction)")
    ge.add_argument("--max-accounts", type=int, default=1, metavar="N",
                    help="max accounts for the farm stage (default: 1)")

    # ---- check ----
    gc = ghs.add_parser(
        "check",
        help="check dependencies, browser setup, and mailbox/relay credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Check dependencies and configuration for GitHub farming and verification.\n"
            "\n"
            "Examples:\n"
            "  kancahub github check"
        ),
    )
    gc.add_argument(
        "--domain",
        choices=["binus", "bizid", "myid"],
        default="binus",
        help="domain to check: binus (school mailbox) or bizid/myid (mail relay)",
    )
    gc.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="binus",
        help="inbox to check: binus (Outlook) or relay (KancaHub mail relay)",
    )

    return ghp


def build_edu_steps(no_verify: bool = False) -> list[str]:
    """Ordered stage list for the one-shot github edu flow (pure, testable).

    Returns ['farm'] with --no-verify, else ['farm', 'verify', 'human_pause'].
    """
    if no_verify:
        return ["farm"]
    return ["farm", "verify", "human_pause"]


# --- markers in the SheerID verifier output that mean "a human is required" ---
# (camera capture / student-ID photo / phone verification cannot be automated)
_EDU_HUMAN_MARKERS = (
    "camera", "selfie", "student id", "student-id", "photo of", "upload a photo",
    "upload photo", "document upload", "docupload", "phone verification",
    "verify your phone", "id upload",
)


def edu_needs_human(output: str) -> bool:
    """True when verifier output indicates a physical human step is required."""
    low = (output or "").lower()
    return any(m in low for m in _EDU_HUMAN_MARKERS)


# --- markers that the verifier reached the document upload / auto-pass stage ---
_EDU_DOC_MARKERS = ("langkah 4", "docupload", "dokumen diupload", "dokumen guru",
                    "auto-pass", "auto_pass", "upload selesai")


def edu_doc_upload_attempted(output: str) -> bool:
    """True when verifier output shows the docUpload step was attempted (or auto-passed)."""
    low = (output or "").lower()
    return any(m in low for m in _EDU_DOC_MARKERS)


def _github_accounts_count() -> int:
    """Number of accounts recorded in github_accounts.json (0 on any error)."""
    import kancahub
    try:
        data = json.loads((kancahub.AUTO_FREECF / "github_accounts.json").read_text())
    except Exception:  # noqa: BLE001 - missing/corrupt file just means "no account"
        return 0
    accounts = data.get("accounts") if isinstance(data, dict) else data
    return len(accounts or [])


def _sheerid_find_url(py_camo: str, a: Any, proxy_override: str | None = None) -> str | None:
    """Extract the SheerID verification link from the school mailbox.

    Prints its own errors (same messages as the historical inline code).
    Returns the URL, or None on failure. `proxy_override` (a concrete URL from
    an already-resolved egress choice) wins over a.proxy when given.
    """
    import kancahub
    finder = kancahub.AUTO_FREECF / "scripts" / "sheerid_link_finder.py"
    if not finder.exists():
        print(kancahub.col("red", f"✗ sheerid_link_finder.py not found at {finder}"))
        return None
    print(kancahub.col("cyan", "Searching school mailbox for SheerID verification link via Camoufox…"))
    finder_cmd = [
        py_camo,
        str(finder),
        "--json",
        "--timeout",
        str(getattr(a, "timeout", 180)),
    ]
    if getattr(a, "headless", False):
        finder_cmd.append("--headless")
    proxy = proxy_override or getattr(a, "proxy", None) or (
        "127.0.0.1:8888" if getattr(a, "gateway", False) else None)
    # The finder takes a concrete proxy URL; never forward mode keywords.
    if proxy and proxy.strip().lower() in ("auto", "none", "direct", "warp", "off"):
        proxy = None
    if proxy:
        finder_cmd += ["--proxy", proxy]
    if getattr(a, "open", False):
        finder_cmd.append("--open")

    try:
        proc = subprocess.run(
            finder_cmd,
            capture_output=True,
            text=True,
            cwd=str(kancahub.AUTO_FREECF),
        )
    except Exception as e:  # noqa: BLE001
        print(kancahub.col("red", f"✗ Failed to execute sheerid_link_finder.py: {e}"))
        return None

    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except Exception:
        data = {}

    if proc.returncode != 0 or not data.get("success"):
        err_msg = data.get("error") if isinstance(data, dict) else None
        if not err_msg:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        print(kancahub.col("red", f"✗ SheerID link finder failed: {err_msg}"))
        return None

    url = data.get("url")
    print(kancahub.col("green", f"✓ Found SheerID verification URL: {url}"))
    if getattr(a, "open", False):
        print(kancahub.col("green", "Verification link inspected in browser."))
    return url


def _sheerid_run(py: str, url: str, *, proxy: str | None = None,
                 debug: bool = False, email: str | None = None,
                 capture: bool = False) -> tuple[int, str]:
    """Run the K-12 SheerID verifier against `url`. Returns (exit_code, output).

    capture=False streams output live (plain `verify`); capture=True captures it
    (used by `edu` to detect camera/ID/phone steps in the output).
    """
    import kancahub
    script = kancahub.K12_DIR / "script.py"
    if not script.exists():
        print(kancahub.col("red", f"✗ SheerID verifier not found at {script}"))
        print(kancahub.col("yellow", f"  Extracted verification URL is: {url}"))
        print(kancahub.col("yellow", "  You can open and verify this URL manually in a browser."))
        return 1, ""

    print(kancahub.col("bold", "\n  GitHub SheerID Verification Handoff"))
    print(kancahub.col("yellow", "  ⚠️  NOTE: GitHub Student Pack uses SheerID for select academic partner verifications."))
    print(kancahub.col("yellow", "     The underlying solver (script.py) was built for K-12 Teacher verification."))
    print(kancahub.col("yellow", "     If GitHub requires a student-specific program or student ID document upload,"))
    print(kancahub.col("yellow", "     the automated solver may report a program mismatch and require manual upload.\n"))

    vcmd = [py, str(script), url]
    if proxy:
        vcmd += ["--proxy", proxy]
    if debug:
        vcmd.append("--debug")
    if email:
        vcmd += ["--email", email]

    if capture:
        try:
            proc = subprocess.run(vcmd, cwd=str(kancahub.K12_DIR), capture_output=True, text=True)
        except Exception as e:  # noqa: BLE001
            print(kancahub.col("red", f"✗ Failed to execute SheerID verifier: {e}"))
            return 1, ""
        out = (proc.stdout or "") + (proc.stderr or "")
        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        return proc.returncode, out
    return kancahub.run(vcmd, cwd=kancahub.K12_DIR), ""


def _print_edu_summary(summary: dict) -> None:
    """Final honest report for the github edu flow."""
    import kancahub
    print(kancahub.col("bold", "\n  GitHub Edu flow summary"))
    print(f"    account created : {'yes' if summary.get('account') else 'no'}")
    print(f"    sheerid reached : {'yes' if summary.get('sheerid') else 'no'}")
    print(f"    docUpload       : {'attempted' if summary.get('doc_upload') else 'not reached'}")
    pending = summary.get("human_pending") or []
    if pending:
        print(f"    human steps     : {', '.join(pending)}")
    else:
        print("    human steps     : none detected (GitHub's manual review may still apply)")


def cmd_github(a) -> int:
    """GitHub account farm & Student Pack verification."""
    import kancahub
    py = kancahub.pick_python()
    py_camo = kancahub.pick_python(camoufox=True)
    sub = getattr(a, "github_cmd", None)
    if not sub:
        print(kancahub.col("yellow", "• no github subcommand specified — run 'kancahub github --help' for usage"))
        return 1

    farm = kancahub.AUTO_FREECF / "scripts" / "github_farm.py"

    if sub == "check":
        if not farm.exists():
            print(kancahub.col("red", f"✗ github_farm.py not found at {farm}"))
            return 1
        cmd = [py_camo, str(farm), "--check"]
        if getattr(a, "domain", None):
            cmd += ["--email-domain", a.domain]
        if getattr(a, "inbox", None):
            cmd += ["--inbox", a.inbox]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "farm":
        if not farm.exists():
            print(kancahub.col("red", f"✗ github_farm.py not found at {farm}"))
            return 1
        # Resolve the egress once, at run time (never at parse/import time).
        mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = kancahub.PROXY_NONE
        choice = kancahub._choose_egress(mode, kancahub.EGRESS_TARGETS["github"])
        cmd = [py_camo, str(farm)] + kancahub.map_github_farm_args(a, choice)
        print(kancahub.col("yellow", "Note: Anti-bot CAPTCHA puzzles and Education attestation remain manual steps if encountered."))
        try:
            return kancahub.run_with_mobile_retry(cmd, cwd=kancahub.AUTO_FREECF,
                                                 mobile_rotate=getattr(a, "mobile_rotate", False),
                                                 account=f"github:{getattr(a, 'index', 1)}")
        finally:
            kancahub._stop_auto_gateways()

    if sub == "verify":
        url = getattr(a, "url", None)
        if getattr(a, "from_mail", False):
            url = kancahub._sheerid_find_url(py_camo, a)
            if not url:
                return 1

        if not url:
            print(kancahub.col("red", "✗ SheerID verification URL required."))
            print(kancahub.col("yellow", "  Specify --url <sheerid-url> OR use --from-mail to extract it from the school inbox."))
            print(kancahub.col("yellow", "  Example: kancahub github verify --from-mail"))
            print(kancahub.col("yellow", "  Example: kancahub github verify --url https://services.sheerid.com/verify/..."))
            return 1

        proxy = getattr(a, "proxy", None) or ("127.0.0.1:8888" if getattr(a, "gateway", False) else None)
        rc, _ = kancahub._sheerid_run(py, url, proxy=proxy,
                                      debug=getattr(a, "debug", False),
                                      email=getattr(a, "email", None))
        return rc

    if sub == "edu":
        steps = kancahub.build_edu_steps(no_verify=getattr(a, "no_verify", False))
        print(kancahub.col("bold", f"\n  GitHub Edu one-shot flow — stages: {' -> '.join(steps)}"))
        summary = {
            "account": False,
            "sheerid": False,
            "doc_upload": False,
            "human_pending": [],
        }
        # Check the farm tool BEFORE resolving egress, so a missing tool is a
        # clear, offline error (no gateway spawned, no network touched).
        if "farm" in steps and not farm.exists():
            print(kancahub.col("red", f"✗ github_farm.py not found at {farm}"))
            print(kancahub.col("yellow", f"  Expected at: {farm}"))
            print(kancahub.col("yellow", "  Install/restore the Auto-FreeCF repo, then re-run."))
            return 1

        # Resolve the egress ONCE; the same concrete proxy serves every stage
        # (farm, mailbox finder, SheerID verifier). The gateway stays alive for
        # the whole flow and is torn down in the finally below.
        mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
        choice = kancahub._choose_egress(mode, kancahub.EGRESS_TARGETS["github"])
        resolved_proxy = choice.proxy if choice is not None else None
        try:
            # ── [1] farm: create ONE account ────────────────────────────
            if "farm" in steps:
                print(kancahub.col("bold", "\n  [1/3] farm: creating a GitHub account…"))
                before = kancahub._github_accounts_count()
                cmd = [py_camo, str(farm)] + kancahub.map_github_farm_args(a, choice)
                rc = kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)
                if rc != 0:
                    print(kancahub.col("red", f"\n✗ STOP: farm stage failed (exit {rc}). Nothing else was attempted."))
                    print(kancahub.col("yellow", "  Anti-bot/CAPTCHA or a blocked egress IP are the usual causes — see the log above."))
                    kancahub._print_edu_summary(summary)
                    return rc
                after = kancahub._github_accounts_count()
                summary["account"] = after > before
                if not summary["account"]:
                    print(kancahub.col("yellow", "\n⚠ farm exited 0 but no NEW account landed in github_accounts.json."))

            # ── [2] verify: SheerID ─────────────────────────────────────
            if "verify" in steps:
                print(kancahub.col("bold", "\n  [2/3] verify: SheerID Student Pack verification…"))
                url = getattr(a, "url", None)
                if not url:
                    url = kancahub._sheerid_find_url(py_camo, a, proxy_override=resolved_proxy)
                    if not url:
                        print(kancahub.col("red", "\n✗ STOP: could not obtain a SheerID verification URL."))
                        print(kancahub.col("yellow", "  Pass --url <sheerid-url>, or fix the school-mailbox extraction."))
                        kancahub._print_edu_summary(summary)
                        return 1
                summary["sheerid"] = True
                # A missing verifier is a tool problem, not a human step: clear
                # error + exit 1 (no traceback, nothing else attempted).
                if not (kancahub.K12_DIR / "script.py").exists():
                    print(kancahub.col("red", f"✗ SheerID verifier not found at {kancahub.K12_DIR / 'script.py'}"))
                    print(kancahub.col("yellow", f"  Extracted verification URL: {url}"))
                    print(kancahub.col("yellow", "  Restore the K-12 tool or open that URL manually in a browser."))
                    kancahub._print_edu_summary(summary)
                    return 1
                rc, out = kancahub._sheerid_run(py, url, proxy=resolved_proxy, capture=True)
                summary["doc_upload"] = kancahub.edu_doc_upload_attempted(out)
                needs_human = kancahub.edu_needs_human(out) or (rc != 0 and not summary["doc_upload"])
                if needs_human:
                    summary["human_pending"].append("camera / student-ID / phone step (SheerID)")
                if rc != 0 and not needs_human:
                    print(kancahub.col("red", f"\n✗ STOP: SheerID verifier failed (exit {rc})."))
                    kancahub._print_edu_summary(summary)
                    return rc

            # ── [3] human pause ─────────────────────────────────────────
            if "human_pause" in steps and summary["human_pending"]:
                print(kancahub.col("bold", "\n  [3/3] human pause: a physical step is required"))
                print(kancahub.col("yellow", "  SheerID/GitHub needs something only a human can provide:"))
                print(kancahub.col("yellow", "    • a live camera capture / selfie, or"))
                print(kancahub.col("yellow", "    • a photo of a real student ID, or"))
                print(kancahub.col("yellow", "    • phone/SMS verification."))
                print(kancahub.col("yellow", "  No script can do this — and we never fake documents."))
                if getattr(a, "interactive", False):
                    print(kancahub.col("cyan", "\n  Complete the physical step now (in the open browser / on your phone)."))
                    try:
                        input("  Press Enter when done (or Ctrl+C to stop): ")
                    except (EOFError, KeyboardInterrupt):
                        print(kancahub.col("yellow", "\n  No input received — stopping; the human step remains pending."))
                        kancahub._print_edu_summary(summary)
                        return 1
                    print(kancahub.col("green", "  ✓ Continuing after human step."))
                else:
                    print(kancahub.col("yellow", "  Re-run with --interactive to be walked through it, or finish it manually."))
        finally:
            kancahub._stop_auto_gateways()

        kancahub._print_edu_summary(summary)
        if summary["human_pending"] and not getattr(a, "interactive", False):
            print(kancahub.col("yellow", "  Result: flow reached the human step; finish it manually to complete verification."))
            return 0 if summary["account"] else 1
        return 0 if summary["account"] or summary["sheerid"] else 1

    print(kancahub.col("red", f"✗ unknown github command: {sub}"))
    return 1
