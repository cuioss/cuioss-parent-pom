#!/usr/bin/env python3
"""Assert the smallrye-config version matches the release Quarkus was built against.

Why this exists
---------------
``java-ee-10-bom`` pins ``io.smallrye.config:*``, and ``quarkus-bom`` inherits
``java-ee-10-bom`` as its *parent*, so those pins beat the ``io.quarkus:quarkus-bom``
it imports. The version therefore has to equal the smallrye-config release Quarkus
itself was built against. A *newer* one is not safe: Quarkus' deployment classes are
compiled against one specific release, so even an internally coherent newer stack
fails augmentation with

    failed to access io.smallrye.config.ConfigMappingLoader$ConfigMappingImplementation
    from io.quarkus.deployment.configuration.ConfigMappingUtils

The ``requireSameVersions`` enforcer guard cannot catch this: nothing is split, so it
is correctly silent. Only this check does.

Two shapes are supported:

* **declared** -- read both versions from the effective POM. The pin may live in a reactor
  POM (the BOM repo itself, and consumers that pin their own Quarkus) *or* be inherited
  from a real ``<parent>`` such as ``cui-quarkus-parent``. Maven still does not propagate
  properties from *imported* BOMs, and ``quarkus-maven-plugin`` needs the value as a build
  extension, so an imported BOM alone can never supply it -- but a parent can.
* **resolved** (``--check-resolved``) -- additionally run ``dependency:list`` and assert
  every ``io.smallrye.config`` artifact actually resolves to the expected version. This
  catches a split family as well as a wrong one, and is what a consumer repo wants.

Where this runs
---------------
In *this* repo it is a CI gate, not a habit: ``.github/workflows/quarkus-alignment.yml``
runs it on every pull request and on the merge queue, and ``.github/workflows/release.yml``
runs it again as a ``needs:`` guard on the release job -- so a misaligned pair cannot be
released even by a manual ``workflow_dispatch``. The release skill runs the same file
by hand during its pre-release gate.

Exit codes
----------
0 aligned; 1 misaligned; 2 could not determine (treat as blocking, never as a pass).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{http://maven.apache.org/POM/4.0.0}"
QUARKUS_BOM_URL = (
    "https://repo1.maven.org/maven2/io/quarkus/quarkus-bom/{v}/quarkus-bom-{v}.pom"
)
QUARKUS_PROP = "version.quarkus"
SMALLRYE_PROP = "version.microprofile.config.impl.smallrye"
SMALLRYE_GROUP = "io.smallrye.config"
QUARKUS_CORE = ("io.quarkus", "quarkus-core")


class Undetermined(Exception):
    """The check could not run. Never report this as a pass."""


def _properties(root: ET.Element) -> dict[str, str]:
    node = root.find(NS + "properties")
    return {} if node is None else {e.tag.replace(NS, ""): (e.text or "").strip() for e in node}


def _deref(value: str, props: dict[str, str]) -> str:
    """Resolve a single ${...} indirection; BOMs express versions either way."""
    seen: set[str] = set()
    while value.startswith("${") and value.endswith("}"):
        key = value[2:-1]
        if key in seen or key not in props:
            break
        seen.add(key)
        value = props[key].strip()
    return value


def _outside_reactor(repo: Path, pom: Path) -> bool:
    """True for a pom.xml that is on disk under ``repo`` but is not part of its reactor.

    Build output (``target``) and vendored trees (``node_modules``) are the obvious cases.
    The subtle one is a *nested checkout*: agent tooling parks git worktrees inside the
    repository (``.plan/local/worktrees/<branch>/``), each a full copy of the project at
    some other revision. Their poms declare the same properties at whatever value that
    branch pinned, so scanning them turns every open worktree into a phantom
    "declared inconsistently" conflict and the check exits 2 -- which the release gate
    treats as blocking. A stale sibling branch must never be able to veto a release.

    Two rules, because either alone leaves a hole: dot-directories (no reactor module
    lives in one) and any directory carrying its own ``.git`` (a worktree has it as a
    file, a clone as a directory) placed somewhere not hidden.
    """
    if "target" in pom.parts or "node_modules" in pom.parts:
        return True
    for parent in pom.parents:
        if parent == repo or repo not in parent.parents:
            break
        if parent.name.startswith(".") or (parent / ".git").exists():
            return True
    return False


def find_property(repo: Path, name: str) -> tuple[str, Path]:
    """Find a property across the reactor. Later declarations do not override earlier
    ones silently -- a genuine conflict is an error, not a coin toss."""
    hits: list[tuple[str, Path]] = []
    for pom in sorted(repo.rglob("pom.xml")):
        if _outside_reactor(repo, pom):
            continue
        try:
            root = ET.parse(pom).getroot()
        except ET.ParseError:
            continue
        props = _properties(root)
        if name in props:
            hits.append((_deref(props[name], props), pom))
    if not hits:
        raise Undetermined(f"property {name} not declared anywhere under {repo}")
    values = {v for v, _ in hits}
    if len(values) > 1:
        detail = ", ".join(f"{v} ({p.relative_to(repo)})" for v, p in hits)
        raise Undetermined(f"property {name} declared inconsistently: {detail}")
    return hits[0]


def evaluate_property(repo: Path, name: str) -> str | None:
    """Read a property from the *effective* POM, so inherited values are visible too.

    Returns None when the property is not defined anywhere in the parent chain; Maven
    prints a ``null object or invalid expression`` marker for that case rather than
    failing, so an absent property must not be confused with a broken build.
    """
    mvnw = repo / "mvnw"
    cmd = [str(mvnw) if mvnw.exists() else "mvn", "-B", "-q", "help:evaluate",
           f"-Dexpression={name}", "-DforceStdout", "-N"]
    try:
        out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=900)
    except Exception as exc:
        raise Undetermined(f"help:evaluate {name} failed to run: {exc}") from exc
    if out.returncode != 0:
        raise Undetermined(f"help:evaluate {name} exited {out.returncode}:\n{out.stderr[-2000:]}")
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    value = lines[-1]
    return None if "null object or invalid expression" in value else value


def resolve_property(repo: Path, name: str) -> tuple[str, str]:
    """The property as the build actually sees it: declared here, or inherited.

    Scanning reactor POM text alone reports an inherited pin as "not declared anywhere"
    and exits 2. That is a release blocker by design -- but a gate that *cannot run* is
    the one people wave through, which is precisely the outage it exists to prevent.
    TokenSheriff hit this the moment it adopted ``cui-quarkus-parent`` and stopped
    pinning ``version.quarkus`` itself, so the text scan gets an effective-POM fallback.

    A genuine *conflict* between reactor declarations still fails: that is a real
    inconsistency, not a missing value, and resolving it to one silent winner would hide
    the very split this check hunts for.
    """
    try:
        value, pom = find_property(repo, name)
        return value, str(pom.relative_to(repo))
    except Undetermined as exc:
        if "not declared anywhere" not in str(exc):
            raise
        value = evaluate_property(repo, name)
        if value is None:
            raise Undetermined(
                f"property {name} is neither declared under {repo} nor defined in the "
                f"effective POM") from exc
        return value, "inherited from the parent chain"


def quarkus_smallrye_version(quarkus_version: str) -> str:
    """The smallrye-config release io.quarkus:quarkus-bom:<version> manages."""
    url = QUARKUS_BOM_URL.format(v=quarkus_version)
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            root = ET.fromstring(resp.read())
    except Exception as exc:  # network, 404, malformed - all are "cannot determine"
        raise Undetermined(f"could not fetch {url}: {exc}") from exc
    props = _properties(root)
    for dep in root.iter(NS + "dependency"):
        if (
            dep.findtext(NS + "groupId") == SMALLRYE_GROUP
            and dep.findtext(NS + "artifactId") == "smallrye-config"
        ):
            return _deref((dep.findtext(NS + "version") or "").strip(), props)
    raise Undetermined(f"quarkus-bom {quarkus_version} does not manage {SMALLRYE_GROUP}:smallrye-config")


def resolved_versions(repo: Path) -> tuple[dict[str, set[str]], set[str]]:
    """What this project actually resolves: every io.smallrye.config artifact, and the
    io.quarkus:quarkus-core version(s)."""
    mvnw = repo / "mvnw"
    cmd = [str(mvnw) if mvnw.exists() else "mvn", "-B", "-q", "dependency:list",
           "-DincludeScope=test", "-DoutputFile=/dev/stdout", "-DappendOutput=true"]
    try:
        out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        raise Undetermined(f"dependency:list failed to run: {exc}") from exc
    if out.returncode != 0:
        raise Undetermined(f"dependency:list exited {out.returncode}:\n{out.stderr[-2000:]}")
    found: dict[str, set[str]] = {}
    core: set[str] = set()
    for line in out.stdout.splitlines():
        m = re.search(rf"{re.escape(SMALLRYE_GROUP)}:([\w.-]+):jar:([\w.-]+):", line)
        if m:
            found.setdefault(m.group(1), set()).add(m.group(2))
        c = re.search(rf"{re.escape(QUARKUS_CORE[0])}:{re.escape(QUARKUS_CORE[1])}:jar:([\w.-]+):", line)
        if c:
            core.add(c.group(1))
    return found, core


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=Path.cwd(),
                    help="repository root to check (default: cwd)")
    ap.add_argument("--check-resolved", action="store_true",
                    help="also assert every resolved io.smallrye.config artifact matches "
                         "(runs dependency:list; use in consumer repos)")
    args = ap.parse_args()
    repo: Path = args.repo.resolve()

    try:
        quarkus, quarkus_src = resolve_property(repo, QUARKUS_PROP)
        expected = quarkus_smallrye_version(quarkus)
        print(f"quarkus            {quarkus}   ({quarkus_src})")
        print(f"quarkus expects    {SMALLRYE_GROUP} {expected}")

        problems: list[str] = []

        # The declared pin is only relevant where the repo actually pins one.
        try:
            ours, ours_src = resolve_property(repo, SMALLRYE_PROP)
            print(f"declared           {ours}   ({ours_src})")
            if ours != expected:
                problems.append(f"declared {SMALLRYE_PROP}={ours}, Quarkus {quarkus} expects {expected}")
        except Undetermined as exc:
            if not args.check_resolved:
                raise
            print(f"declared           (none: {exc})")

        if args.check_resolved:
            resolved, core = resolved_versions(repo)

            # version.quarkus drives quarkus-maven-plugin (a build extension), while the
            # Quarkus *artifacts* come from whichever BOM is imported. Those are separate
            # inputs and can drift apart: cui-reference-documentation once ran plugin
            # 3.38.0 against BOM-supplied 3.39.0, which is how the original outage began.
            # The smallrye check cannot see this - both sides may still agree on smallrye.
            if core:
                shown = ", ".join(sorted(core))
                print(f"resolved           quarkus-core {shown}")
                if core != {quarkus}:
                    problems.append(
                        f"quarkus-maven-plugin uses version.quarkus={quarkus} but "
                        f"io.quarkus:quarkus-core resolves to {shown} - the plugin and the "
                        f"imported BOM have drifted apart")
            if not resolved:
                print("resolved           (no io.smallrye.config artifacts on the classpath)")
            for artifact, versions in sorted(resolved.items()):
                shown = ", ".join(sorted(versions))
                print(f"resolved           {artifact} {shown}")
                if versions != {expected}:
                    problems.append(f"{artifact} resolves to {shown}, expected {expected}")

        if problems:
            print("\nMISALIGNED - fix before releasing:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            print("\nSet the version to what Quarkus manages, or move version.quarkus with it.\n"
                  "Never release 'and fix it after': the fan-out reaches consumers within minutes.",
                  file=sys.stderr)
            return 1

        print("\nALIGNED")
        return 0

    except Undetermined as exc:
        print(f"CANNOT DETERMINE: {exc}", file=sys.stderr)
        print("Treat this as blocking - an unresolvable check is not a pass.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
