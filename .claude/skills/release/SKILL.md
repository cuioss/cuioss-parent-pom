---
name: release
description: Cut a cui-parent-pom release — bump .github/project.yml version and the README sample version, open and merge the release PR, wait for the automated Release workflow, verify the release landed, then reformat the generated GitHub release notes
user-invocable: true
allowed-tools: Bash, Read, Edit, AskUserQuestion
---

# Release Skill

Cuts a new `cui-parent-pom` (`de.cuioss:cui-parent-pom`) release end-to-end: determine the
version, open the version-bump PR that triggers the release, merge it, wait for the automated
Release workflow, verify the release landed, and reformat the auto-generated GitHub release
notes.

## How the release is wired (read first)

The release is **fully automated by GitHub Actions**. `.github/workflows/release.yml` triggers
on a **merged pull request that changes `.github/project.yml`**:

```yaml
on:
  pull_request:
    types: [closed]
    paths:
      - '.github/project.yml'
```

So this skill never runs Maven release goals by hand. Its job is to produce and merge the
correct `project.yml` change; the reusable `cuioss-organization` release workflow
(`reusable-maven-release.yml`, run with the `release-pom` profile) does the Maven release
(`-DreleaseVersion=<current-version> -DdevelopmentVersion=<next-version>`), tagging, Maven
Central deploy, GitHub release creation, and — because `pages.deploy-at-release: true` — the
documentation pages deploy.

This is a **POM-and-BOM aggregator** — there is no Java source, no tests, and no
integration/e2e suites. Two PR gating checks run: the **Maven Build** matrix (Java 21) and
**Quarkus Alignment** (the smallrye-config/Quarkus pairing from Step 2b, run again by CI).

Observed timings:
- PR gating checks (**Maven Build**, **Quarkus Alignment**): typically **~1–3 min** (validate
  + enforcer only, no tests; the alignment check is one POM fetch).
- Release workflow: **~5 min**, but Maven Central propagation, the GitHub release publish, and
  the pages deploy can lag → allow **up to ~30 min** before treating it as stuck.

## Version scheme (differs from library repos)

`.github/project.yml` is the single source of truth for both versions — read it, never
assume:

```bash
grep -E 'current-version|next-version' .github/project.yml
```

- `release.current-version` — the last released version.
- `release.next-version` — the rolling development version, held at the `X.Y-SNAPSHOT`
  minor floor (the pom `<version>` carries it).

**Default rule — patch bump.** Unlike the library repos, `next-version` does **not** move
between releases. To cut the next release, **increment the third segment of
`current-version`** and **leave `next-version` unchanged**. Do **not** strip `-SNAPSHOT`
from `next-version` — that would publish a version lower than the one just released.

**Ask the user** (AskUserQuestion) only if in doubt — e.g. a new minor line or a major bump
is intended, or `current-version` and the tag history look inconsistent. Otherwise state the
determined version and proceed.

## Workflow

### Step 1 — Determine the version number

Apply the patch-bump rule from [Version scheme](#version-scheme-differs-from-library-repos)
above, reading both numbers out of `.github/project.yml`.

### Step 2 — Determine current status (clean to release?)
```bash
gh pr list --repo cuioss/cuioss-parent-pom --state open --json number,title,isDraft
```
- **No open PRs** → proceed.
- **Open PRs exist** → surface the list and **ask the user** whether to proceed or wait. Do
  not silently ignore them.

Confirm the working tree is clean (`git status --porcelain`) before branching.

### Step 2b — Pre-release regression gate (do NOT skip)

This BOM is consumed by every cuioss repo, so a bad version reaches all of them at once.
Regressions have shipped this way; all were cheap to catch here and expensive to catch
downstream. Run all three checks **before** branching, from the BOM checkout root.

#### What can go wrong (three distinct modes)

This repo has **no Java source and resolves no dependencies of its own**, so its build is
green for every one of these. None is caught by `./mvnw verify` here.

| # | mode | symptom downstream | caught by |
|---|------|--------------------|-----------|
| 1 | **Coherent but wrong** — a family pinned to a version Quarkus was not built against | augmentation fails (`failed to access ...ConfigMappingLoader`) | (a) only — the enforcer is correctly silent, nothing is split |
| 2 | **Split family** — part of a family pinned, the rest floating | `requireSameVersions` fires, and it is *right* | (b), or the consumer's own build |
| 3 | **Over-broad family block** — a `requireSameVersions` groupId that is not one product | `requireSameVersions` fires and is **wrong**; no pin can satisfy it | (c) only |

Mode 3 is the nastiest: the rule itself is the defect, every consumer is blocked at
`validate`, and the fix must be released from here before anyone can build. It shipped
twice — `org.jboss.resteasy:*` (`resteasy-tracing-api` is from the separately released
`resteasy-extensions`, max `2.0.2.Final`) and `org.bouncycastle:*` (FIPS, `lts8on` and the
legacy JDK lines version independently, and `quarkus-bom` manages `bc-fips` alongside the
`jdk18on` line).

**Do not rely on the canary for mode 3.** Coverage there is incidental: when this was
investigated, `cui-reference-documentation` resolved RESTEasy on the **7.x** line — where
`resteasy-jackson2-provider` still marks `resteasy-tracing-api` `<optional>true</optional>`,
so the break was invisible — and resolved **zero** `org.bouncycastle` artifacts. Check (c)
is metadata-based and needs no consumer, which is why it is the reliable one.

#### (a) Quarkus alignment — smallrye-config

`java-ee-10-bom` pins `io.smallrye.config:*`, and `quarkus-bom` inherits `java-ee-10-bom` as
its **parent**, so those pins win over the `io.quarkus:quarkus-bom` it imports. The version
must therefore equal the release Quarkus itself was built against. A *newer* one is not
safe: Quarkus' deployment classes are compiled against one specific release, so even an
internally coherent newer stack fails augmentation with

```
failed to access io.smallrye.config.ConfigMappingLoader$ConfigMappingImplementation
from io.quarkus.deployment.configuration.ConfigMappingUtils
```

The `requireSameVersions` enforcer guard does **not** catch this — nothing is split, so it is
correctly silent. Only this check does.

This is now also enforced by CI — `.github/workflows/quarkus-alignment.yml` runs the check
on every PR and on the merge queue, and `.github/workflows/release.yml` runs it again as a
`needs:` guard on the release job, so a misaligned pair cannot be released even by a manual
`workflow_dispatch`. Run it here anyway: catching it before the release branch exists is
cheaper than a red release PR, and the same script covers the consumer repos (below), which
CI here cannot see.

The script lives under `.github/scripts/` rather than with this skill, because CI owns it —
the skill is one of its callers, not its home. It **exits non-zero** on a mismatch:

```bash
python3 .github/scripts/check-quarkus-alignment.py --repo .
```

```
quarkus            3.39.3   (cui-java-bom/cui-java-parent/cui-quarkus-parent/pom.xml)
quarkus expects    io.smallrye.config 3.17.2
declared           3.17.2   (java-ee-bom/java-ee-10-bom/pom.xml)

ALIGNED
```

| exit | meaning |
|------|---------|
| 0 | aligned — proceed |
| 1 | **misaligned** — set the property to what Quarkus manages, land that fix, then restart |
| 2 | **could not determine** — also blocking. An unresolvable check is never a pass. |

The script resolves both versions through the POMs (following `${...}` indirection, since
either side may express the version as a property reference) and reads the expected value
out of `io.quarkus:quarkus-bom:<version.quarkus>` on Maven Central. It refuses to guess: a
property declared inconsistently across the reactor is exit 2, not a coin toss.

**In consumer repos** add `--check-resolved`. That additionally runs `dependency:list` and
asserts every `io.smallrye.config` artifact actually resolves to the expected version, which
catches a *split* family as well as a wrong one:

```bash
python3 .github/scripts/check-quarkus-alignment.py \
  --repo ~/git/cui-reference-documentation --check-resolved
```

Consumers cannot inherit `version.quarkus` — Maven does not propagate properties from
*imported* BOMs, and `quarkus-maven-plugin` needs the value as a build extension — so each
Quarkus consumer declares it locally and is checked against **its own** Quarkus, not this
repo's. Removing that local property is not an option; it fails with
`Unresolveable build extension`.

`io.smallrye.config:*` is in `dependabot ignore` precisely because no automated bump can
reason about this coupling — it must move together with `version.quarkus`. If you see a
Dependabot PR raising it anyway, that ignore entry has been lost; restore it.

#### (b) Downstream smoke build — a real Quarkus consumer

The alignment check covers the one artifact that has burned us twice. It does **not** cover
the other ~24 entries `java-ee-10-bom` and `quarkus-bom` both manage. Build a real Quarkus
consumer against the candidate.

`cui-reference-documentation` is the right canary: it is Quarkus-based and imports both
`java-ee-10-bom` and `quarkus-bom`, so it exercises the overlap directly.

Set the paths and the candidate version once, then reuse them — the cleanup **must** run
against the BOM checkout, not the consumer you last `cd`-ed into:

```bash
BOM_DIR=$(pwd)                       # run from the BOM checkout root
CANARY=~/git/cui-reference-documentation
RC=<version>-RC                      # e.g. 1.5.8-RC
DEV=$(./mvnw -q -N help:evaluate -Dexpression=project.version -DforceStdout)   # e.g. 1.5-SNAPSHOT

# Every reactor artifactId, derived from the POMs rather than listed by hand.
MODULES=($(for p in $(git ls-files '*pom.xml'); do
  sed '/<parent>/,/<\/parent>/d' "$p" | grep -m1 -o '<artifactId>[^<]*' | cut -d'>' -f2
done))
VSET=(-B -q versions:set -DprocessAllModules=true -DgenerateBackupPoms=false
      -DupdateBuildOutputTimestampPolicy=never)

./mvnw "${VSET[@]}" -DnewVersion="$RC"
./mvnw -B -q install                 # the whole reactor, however many modules it has

# Point the canary's <parent> version at "$RC" (it inherits cui-quarkus-parent), then:
(cd "$CANARY" && ./mvnw -B clean verify -Dversion.cui.parent="$RC")   # must be BUILD SUCCESS
```

Three details in there are load-bearing — each replaces a step that went wrong in practice:

- **`-Dversion.cui.parent="$RC"` is not optional.** The canary *inherits* `version.cui.parent`
  rather than declaring it, and imports `java-ee-10-bom`, `quarkus-bom` and
  `java-ee-orthogonal` with it. Editing only `<parent>` leaves it at the last *released*
  value, so the canary imports the released BOMs — the ones carrying the smallrye pin —
  instead of the candidate, and passes while testing the wrong thing. Confirm with
  `./mvnw -q -N help:evaluate -Dexpression=version.cui.parent -DforceStdout` in the canary:
  without the flag it prints the previous release.
- **The module list is derived, not typed.** A hand-kept list drifted twice: it omitted
  `cui-quarkus-parent` (the canary's actual parent — the build could not resolve it), and it
  named the root `cuioss-parent-pom` when its artifactId is `cui-parent-pom`, so the purge
  never removed the root candidate. The repo name is not the artifactId.
- **`-DupdateBuildOutputTimestampPolicy=never`.** `versions:set` otherwise rewrites
  `project.build.outputTimestamp` to *now* on every run, which setting the version back
  cannot undo — the tree stays dirty and the clean-tree check below fails for a reason
  that has nothing to do with the release.

Arrays (`"${VSET[@]}"`) rather than plain variables, because zsh does not word-split an
unquoted `$VAR`: `./mvnw $VSET` passes one argument there and Maven looks for a plugin
named ` -q versions`. The array form behaves the same in bash and zsh.

Afterwards restore the development version and purge the candidate — note the explicit
`cd "$BOM_DIR"` and that `$DEV` was captured above rather than hard-coded, so this cannot
stamp the wrong version across every POM:

```bash
cd "$BOM_DIR"
./mvnw "${VSET[@]}" -DnewVersion="$DEV"
for a in "${MODULES[@]}"; do
  rm -rf "$HOME/.m2/repository/de/cuioss/$a/$RC"
done
git status --porcelain      # must be empty before continuing
(cd "$CANARY" && git checkout -- pom.xml)
```

A red canary is a **blocker**, not a warning.

#### (c) requireSameVersions family blocks — still satisfiable?

Each family block in the root pom asserts that a set of artifacts always resolves to one
version. That holds only while they really are one release train. When an upstream groupId
turns out to carry two independently versioned lines, the block becomes **impossible to
satisfy** — and this repo cannot notice, because it resolves none of those artifacts.

The check is pure Maven Central metadata: intersect the published version sets of each
block's artifacts. An empty intersection means no `dependencyManagement` pin anywhere can
make the rule pass.

```bash
python3 .claude/skills/release/check-family-blocks.py --repo .
```

```
[OK] org.bouncycastle:* (8 artifacts enumerated)
  8 artifacts
  newest common version: 1.85
  family newest is 1.85.2; 7 artifact(s) never published it, capping the common version
    bcjmail-jdk18on (1.85), bcmail-jdk18on (1.85), ... +2 more
```

| exit | meaning |
|------|---------|
| 0 | every block satisfiable — proceed |
| 1 | **a block is unsatisfiable** — narrow it before releasing (see below) |
| 2 | **could not determine** — also blocking |

Run against the pre-fix pom it reports `[BROKEN] org.jboss.resteasy:*` naming
`resteasy-tracing-api → 2.0.2.Final`, and `[BROKEN] org.bouncycastle:*` naming the FIPS and
`lts8on` outliers — i.e. it catches both shipped regressions.

**When a block comes back BROKEN**, the fix is always to narrow it, never to delete it:

1. Find the real family boundary — the upstream BOM is the authority (`resteasy-bom` never
   listed `resteasy-tracing-api`). Confirm against `maven-metadata.xml` that the outlier
   genuinely has no version in common; if it does, this is mode 2 and a pin is the fix.
2. Replace the `groupId:*` wildcard with the artifacts that genuinely share a version.
   There is **no exclusion syntax**: `requireSameVersions` (owned by maven-enforcer-plugin,
   *not* extra-enforcer-rules) exposes only `dependencies`, `plugins`, `buildPlugins`,
   `reportPlugins`, `uniqueVersions`, `sameModuleVersions`, and its pattern translation
   rewrites `?` to `.`, so a negative lookahead is not expressible either.
3. Record the carve-out in the comment above the blocks, in the style of the `io.smallrye`
   note, so the next reader does not re-widen it.
4. Verify with a negative control that the narrowed block still fails on a genuinely split
   stack — narrowing must not neuter the rule.

Informational lines are not failures. `N artifact(s) never published [the family newest]`
means either an out-of-band module patch (`bcprov-jdk18on 1.85.2`) or a shrinking family
(RESTEasy dropped its netty/vertx modules in 7.x). Worth reading, not worth blocking.

The `WAIVERS` map at the top of the script holds blocks left broad on purpose, with the
reason. `io.smallrye.config:*` is waived today (`smallrye-config-jasypt` 3.17.2 and the
discontinued `smallrye-config-converter-json` 3.8.3 lag the 3.18.2 core; neither is on the
Quarkus default path). A waiver is a decision — if a waived artifact becomes reachable,
narrow the block instead of extending the waiver.

Report all three results before proceeding.

### Step 3 — Pull current main
```bash
git checkout main && git pull --ff-only origin main
```

### Step 4 — Create the release branch
Branch prefix **must** be a CI-accepted prefix (`main`, `feature/*`, `fix/*`, `chore/*`,
`release/*`, `dependabot/**`) or the Maven Build check skips and auto-merge is blocked:
```bash
git checkout -b chore/release_<version>
```

### Step 5 — Update `.github/project.yml` and the README sample version
1. Edit the `release` block in `.github/project.yml`:
   - `current-version:` → the release version from Step 1
   - `next-version:` → **unchanged**
2. Update the parent-version sample in **`README.adoc`** (the `<version>…</version>` under the
   `cui-java-parent` `<parent>` in the "Usage for Standard java-modules" snippet) to the new
   release version, so the published docs show the released parent version. Then check for any
   other stale references to the previous version in docs:
   ```bash
   grep -rn "<previous-version>" --include="*.adoc" .   # e.g. 1.4.5 — update sample references
   ```
   Do **not** touch `version.cui.parent` in `pom.xml` — the release plugin bumps it
   automatically via `preparationGoals`.

### Step 6 — Commit, push, open PR
```bash
git add .github/project.yml README.adoc
git commit -m "chore(release): prepare release <version>"
git push -u origin chore/release_<version>
gh pr create --repo cuioss/cuioss-parent-pom --base main \
  --title "chore(release): prepare release <version>" \
  --label "skip-bot-review" \
  --body "Bump current-version to <version> (next-version stays <next-version>) and the README parent sample. Triggers the automated Release workflow on merge."
```
Apply the **`skip-bot-review`** label (via `--label "skip-bot-review"` on `gh pr create`, or
`gh pr edit <pr#> --add-label "skip-bot-review"` if the PR already exists). A mechanical
release-prep PR only changes the `project.yml` version and the README sample — no bot review is
necessary, and the label suppresses the automated reviewers (CodeRabbit / Sourcery).

### Step 7 — Wait for PR checks (~1–3 min)
```bash
gh pr checks <pr#> --repo cuioss/cuioss-parent-pom --watch
```

### Step 8 — Handle PR comments / failures (if any)
- If a check fails, read the failing run's log (`gh run view <id> --log-failed`), fix on the
  branch, push, re-wait. **Never** merge a red PR.
- If the automated reviewer or a human leaves comments (`gh pr view <pr#> --comments`), address
  them per the PR-comment protocol in `CLAUDE.md`: reply to and resolve every comment; ask the
  user when uncertain.

### Step 9 — Merge → release starts automatically
`main` is governed by the org-managed **merge queue** (`main-merge-queue`), so the merge is
**enqueued, not immediate**, and `--delete-branch` is **rejected** (the queue auto-deletes the
head branch). Do **not** pass `--delete-branch`:
```bash
gh pr merge <pr#> --repo cuioss/cuioss-parent-pom --squash
```
This only *queues* the PR. Poll until it actually lands on `main` — the release fires on the
merge commit, not on enqueue (the queue can take a few minutes):
```bash
gh pr view <pr#> --repo cuioss/cuioss-parent-pom --json state --jq .state   # wait for MERGED
```
Merging this PR (it touches `.github/project.yml`) fires `release.yml` automatically — do
**not** dispatch the release manually unless the auto-trigger demonstrably did not fire.

> The release workflow itself is unaffected by the queue: `cuioss-release-bot` is a bypass
> actor on `main-merge-queue`, so its direct push of the release commit + tag succeeds.

### Step 10 — Wait for the Release workflow (~30 min)
```bash
gh run list --repo cuioss/cuioss-parent-pom --workflow "Release" --limit 3 \
  --json status,conclusion,displayTitle,databaseId
gh run watch <databaseId> --repo cuioss/cuioss-parent-pom
```

### Step 11 — Verify the release landed
```bash
gh release view <version> --repo cuioss/cuioss-parent-pom --json tagName,name,createdAt,body
git fetch --tags && git tag --list <version>
```
Confirm the tag exists and a GitHub release for `<version>` was created. If not, inspect the
Release workflow run log before proceeding.

### Step 12 — Reformat the generated release notes
The Release workflow creates the GitHub release with **auto-generated** notes (a flat
`## What's Changed` list). Rewrite them in place using the house format below, then push:
```bash
mkdir -p .plan/temp
gh release view <version> --repo cuioss/cuioss-parent-pom --json body --jq .body > .plan/temp/release-<version>-orig.md
# ...build the reformatted body in .plan/temp/release-<version>.md...
gh release edit <version> --repo cuioss/cuioss-parent-pom --notes-file .plan/temp/release-<version>.md
```

#### House format rules (apply exactly)
1. **Two top-level groups:** `## Features & Enhancements` and `## Dependency Updates`.
2. **Features & Enhancements** — for a POM/BOM aggregator these are rare; group functional PRs
   by theme with `###` subheadings when present, e.g. `### Build & Configuration`,
   `### CI/CD & Workflows`, `### Documentation`. Omit empty sections.
3. **Dependency Updates** — group by type with `###` subheadings:
   - `### Java` — managed library version bumps in the BOMs (e.g. quarkus, myfaces, jakarta,
     microprofile, cui-java-tools, cui-http)
   - `### Infra` — build/CI: build-plugin bumps, `cuioss-organization` workflow bumps,
     GitHub Actions bumps
4. **Collapse by library identity — one line per library, spanning the full range.**
   The unit of collapsing is the *library*, not the PR title. Merge into a single line
   whenever the PRs concern the same library, in all three shapes that occur:
   - **Version chains** — several bumps of one artifact (`A → B → C`) collapse to one line
     spanning `A → C`, carrying the latest PR's author.
   - **The same library in several places** — one library bumped in more than one module or
     directory is **one** line naming them all, not one line each. Those titles differ only
     by that suffix, so do not wait for identical titles before merging.
   - **One upstream release landing as several coordinates** — when a single upstream bump
     arrives as separate PRs against different coordinates (e.g. a version property *and*
     a BOM or parent), that is **one** bump: one line naming the coordinates in parentheses.

   Carry every merged PR's URL onto the surviving line, comma-separated.
5. **Recover versions the title omits.** Dependabot truncates a title to
   `bump <lib> in /<dir>`, with no versions, when several dependencies must move together.
   Never publish a dependency line without a version range: read the PR body, which states
   ``Updates `<lib>` from X to Y``, and use those versions when computing the range:

   ```bash
   gh pr view <n> --repo cuioss/cuioss-parent-pom --json body --jq .body | head -6
   ```
6. **Remove all OpenRewrite bumps and friends** — drop every `rewrite-maven-plugin`,
   `rewrite-migrate-java`, `rewrite-testing-frameworks`, and related OpenRewrite PR.
7. **Remove internal tooling churn** — drop PRs that only touch dev/build orchestration with no
   user-facing effect, and the mechanical version-bump PR itself.
8. **Preserve each kept PR line** in its original
   `* <title> by @author in <url>` shape. Rules 4 and 5 **override** verbatimness where
   they conflict: rewrite the title's version range to span the collapsed chain, and name
   the several modules or coordinates on the surviving line.
9. Keep the trailing `**Full Changelog**: ...compare/<prev>...<version>` line.

#### Verify before publishing (mandatory)

These rules are easy to under-apply: a duplicate survives whenever two PRs touch the same
library under differing titles. After building the notes file and **before**
`gh release edit`, assert that every library appears exactly once:

```bash
grep -oE '(bump|update) [^ ]+ (from|in)' .plan/temp/release-<version>.md \
  | sort | uniq -c | sort -rn | head
```

Every count must be `1`. Any count `>1` is an unmerged duplicate — collapse it per rule
4 and re-run. Also confirm that no dependency line is missing a version range
(rule 5).


### Step 13 — Done
Report: released version, release URL, the PR number, and how many dependency PRs were
collapsed/removed during note reformatting.

## Critical rules
- The release is triggered by **merging a `.github/project.yml` change** — never hand-run Maven
  release goals.
- **Patch-bump `current-version`; leave `next-version` unchanged** on its `X.Y-SNAPSHOT`
  minor floor.
- Always update the **README parent sample version** in the same PR; never touch
  `version.cui.parent` (auto-bumped by the release plugin).
- Branch prefix **must** be `chore/` (or another CI-accepted prefix) or the build check skips
  and auto-merge is blocked.
- Never merge a red PR; fix and re-wait.
- **Run all three Step 2b checks every time.** A bad BOM version fans out to every cuioss
  repo within minutes of the release, and this repo's own build is green for all three
  failure modes because it resolves no dependencies. The enforcer guard cannot catch a
  coherent-but-wrong Quarkus-managed version (only the alignment check can), and the
  canary's coverage of the enforcer families is incidental (only the family-block check is
  reliable).
- **Never delete a `requireSameVersions` block to make a build pass** — narrow it to the
  artifacts that genuinely share a version, and keep a negative control proving it still
  fails on a real split.
- Temporary files go under `.plan/temp/`.
