# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cui-parent-pom is a Maven parent POM for CUI open-source Java projects. It provides standardized build configuration, dependency management, and development workflows for descendant projects. It is a POM-and-BOM aggregator — there is no Java source in this repository.

## Key Commands

### Building and Installing
```bash
# Standard build and verify
./mvnw -B --no-transfer-progress verify

# Install to local repository
./mvnw clean install

# Run pre-commit tasks (license headers + formatting)
./mvnw -Ppre-commit verify
```

### Releasing
Releases are **fully automated by GitHub Actions**. Merging a pull request that changes
`.github/project.yml` triggers `.github/workflows/release.yml` (the reusable
`cuioss-organization` maven-release workflow), which runs the Maven release goals, tags,
deploys to Maven Central, creates the GitHub release, and — because `pages.deploy-at-release`
is set — deploys the documentation site.

Use the **`/release` skill** (`.claude/skills/release/`) to cut a release: it determines the
version, opens and merges the version-bump PR, waits for the release workflow, and reformats
the generated release notes. Do **not** hand-run `release:prepare`/`release:perform`.

### Other Useful Commands
```bash
# Generate PlantUML diagrams (add sources under doc/plantuml/*.puml first)
./mvnw generate-resources -Pbuild-plantuml

# Generate Maven site
./mvnw site:site site:stage

# Get current version
./mvnw help:evaluate -Dexpression=project.version -q -DforceStdout

# Deploy a SNAPSHOT (normally done automatically on main by CI)
./mvnw -B --no-transfer-progress deploy
```

## Architecture and Structure

### Module Hierarchy
```
cui-parent-pom (root)
├── cui-java-bom (Java dependency management)
│   └── cui-java-parent (Parent POM for simple Java projects)
└── java-ee-bom (Jakarta EE dependency management)
    ├── java-ee-10-bom (Jakarta EE 10 module; forward-adopts some EE 11 APIs)
    │   └── quarkus-bom (Quarkus framework)
    └── java-ee-orthogonal (Cross-cutting EE concerns)
```

### Key Design Patterns

1. **Hierarchical POM Structure**: Each module inherits from its parent, providing layered configuration
2. **Bill of Materials (BOM) Pattern**: Separate BOMs manage dependencies for different technology stacks
3. **Plugin Management**: All plugins are centrally configured in the root POM's pluginManagement section
4. **Profile-Based Workflows**: Different profiles enable specific build behaviors (pre-commit, release, sonar, etc.)

### Important Profiles

- **pre-commit**: Applies license headers and OpenRewrite formatting
- **coverage**: Local JaCoCo coverage analysis with thresholds
- **release-pom**: Used for releasing POM-only artifacts (no Java code); the release workflow uses this profile (`maven-profiles-release` in `project.yml`)
- **release** / **release-snapshot**: Java-artifact release / snapshot profiles
- **javadoc**: Javadoc generation
- **sonar**: Enables SonarCloud analysis with JaCoCo coverage
- **build-plantuml**: Generates PNG images from PlantUML `.puml` sources under `doc/plantuml/`

### Critical Configuration

1. **Java Version**: Requires Java 21 or higher (enforced by maven-enforcer-plugin)
2. **Maven Version**: Requires Maven 3.8.0+ (3.9.6 via the `maven-wrapper-plugin`, `distributionType=only-script`)
3. **License Headers**: Apache 2.0 license in regular comment style (`/* */`), stamped by the mycila `license-maven-plugin` as an open range, `${project.inceptionYear}-present`. Two rules go with this:
   - Do not remove the explicit `<year>` override. The plugin binds `${year}` to `project.inceptionYear`, *not* to the current year, so dropping it silently restamps every descendant's headers with the inception year alone.
   - **Every descendant project must declare its own `<inceptionYear>`.** It is an inherited POM element, so a project that omits it reports this parent's inception year and stamps a copyright year predating its own first commit.
4. **Deployment**: Uses the `central-publishing-maven-plugin` for Sonatype/Maven Central
5. **Reproducible Builds**: Configured with `project.build.outputTimestamp`
6. **Parent version property**: `version.cui.parent` (root `pom.xml`) holds the released parent version; the release plugin bumps it automatically via `preparationGoals`. Consumers import BOMs with `${version.cui.parent}`.

### Development Guidelines

1. **Adding Dependencies**: Define versions as properties in the appropriate BOM module
2. **Plugin Configuration**: Add to pluginManagement in root POM with version property
3. **License Headers**: Run `./mvnw -Ppre-commit verify` before committing to ensure proper headers
4. **Validating Changes**: Always run `./mvnw verify` to ensure all modules build correctly
5. **Module Structure**: New modules should follow the existing hierarchy pattern

### CI/CD Integration

- GitHub Actions workflows are **thin callers** to the reusable workflows in
  `cuioss/cuioss-organization` (pinned by SHA, currently `v0.7.0`). Per-repo configuration
  lives in `.github/project.yml`.
  - `maven.yml` → `reusable-maven-build.yml` (build + Sonar + snapshot deploy; path filtering handled inside the reusable workflow)
  - `release.yml` → `reusable-maven-release.yml` (triggered by a merged `project.yml` change)
  - `dependency-review.yml`, `dependabot-auto-merge.yml`, `scorecards.yml` → their reusable counterparts
- `quarkus-alignment.yml` is the exception: a repo-local workflow, not a thin caller. It runs
  `.github/scripts/check-quarkus-alignment.py`, which asserts that
  `version.microprofile.config.impl.smallrye` (java-ee-10-bom) equals the smallrye-config
  release `io.quarkus:quarkus-bom:${version.quarkus}` manages — a coupling nothing in the
  Maven build can see, because this repo resolves no dependencies. It runs on every PR and on
  the merge queue, and `release.yml` calls it as a `needs:` guard so the release job cannot
  start on a misaligned pair. The check fails closed: "cannot determine" blocks.
- Automatic SNAPSHOT deployments on `main` commits.
- Dependabot updates (maven + github-actions) with a tiered `cooldown`.
- Supply-chain hardening via OpenSSF Scorecard; all actions pinned by commit SHA.

## Git Workflow

All cuioss repositories have branch protection on `main`. Direct pushes to `main` are never allowed. Always use this workflow:

1. Create a feature branch: `git checkout -b <branch-name>` (use a CI-recognized prefix: `feature/`, `fix/`, `chore/`, `release/`)
2. Commit changes: `git add <files> && git commit -m "<message>"`
3. Push the branch: `git push -u origin <branch-name>`
4. Create a PR: `gh pr create --repo cuioss/cuioss-parent-pom --head <branch-name> --base main --title "<title>" --body "<body>"`
5. Wait for CI + automated review (waits until checks complete): `gh pr checks --watch`
6. **Handle review comments** — fetch with `gh api repos/cuioss/cuioss-parent-pom/pulls/<pr-number>/comments` and for each:
   - If clearly valid and fixable: fix it, commit, push, then reply explaining the fix and resolve the comment
   - If disagree or out of scope: reply explaining why, then resolve the comment
   - If uncertain (not 100% confident): **ask the user** before acting
   - Every comment MUST get a reply (reason for fix or reason for not fixing) and MUST be resolved
7. Do **NOT** enable auto-merge unless explicitly instructed. Wait for user approval.
8. Return to main: `git checkout main && git pull`

## Temporary Files

Use `.plan/temp/` for ALL temporary and generated files (covered by `Write(.plan/**)` permission — avoids permission prompts).

## Tool Usage

- Use proper tools (Edit, Read, Write) instead of shell commands (echo, cat)
- Never use Bash for file operations (find, grep, cat, ls) — use Glob, Read, Grep tools instead
