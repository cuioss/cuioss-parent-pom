# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

cui-parent-pom is a Maven parent POM for CUI open-source Java projects. It provides standardized build configuration, dependency management, and development workflows for descendant projects.

## Key Commands

### Building and Installing
```bash
# Standard build and verify
./mvnw -B --no-transfer-progress verify

# Install to local repository
./mvnw clean install

# Run pre-commit tasks (license headers + formatting)
./mvnw -Ppre-commit
```

### Release Process
```bash
# Prepare release
./mvnw -Prelease-pom release:clean release:prepare -DreleaseVersion=X.Y.Z -DdevelopmentVersion=X.Y.Z-SNAPSHOT

# Perform release
./mvnw -Prelease-pom release:perform

# Deploy snapshot
./mvnw -B --no-transfer-progress deploy
```

### Other Useful Commands
```bash
# Generate PlantUML diagrams
./mvnw generate-resources -Pbuild-plantuml

# Generate Maven site
./mvnw site:site site:stage

# Get current version
./mvnw help:evaluate -Dexpression=project.version -q -DforceStdout
```

## Architecture and Structure

### Module Hierarchy
```
cui-parent-pom (root)
├── cui-java-bom (Java dependency management)
│   └── cui-java-parent (Parent POM for simple Java projects)
└── java-ee-bom (Jakarta EE dependency management)
    ├── java-ee-10-bom (Jakarta EE 10 specific)
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
- **release-pom**: Used for releasing POM-only artifacts (no Java code)
- **release**: Used for releasing Java artifacts with sources and javadocs
- **sonar**: Enables SonarCloud analysis with JaCoCo coverage
- **build-plantuml**: Generates PNG images from PlantUML diagrams

### Critical Configuration

1. **Java Version**: Requires Java 21 or higher (enforced by maven-enforcer-plugin)
2. **Maven Version**: Requires Maven 3.8.0+ (3.9.6 via wrapper)
3. **License Headers**: Apache 2.0 license, recently changed from javadoc to regular comment style
4. **Deployment**: Uses new central-publishing-maven-plugin for Sonatype/Maven Central
5. **Reproducible Builds**: Configured with project.build.outputTimestamp

### Development Guidelines

1. **Adding Dependencies**: Define versions as properties in the appropriate BOM module
2. **Plugin Configuration**: Add to pluginManagement in root POM with version property
3. **License Headers**: Run `./mvnw -Ppre-commit` before committing to ensure proper headers
4. **Validating Changes**: Always run `./mvnw verify` to ensure all modules build correctly
5. **Module Structure**: New modules should follow the existing hierarchy pattern

### Recent Changes

The project recently changed license headers from javadoc style (`/** */`) to regular comment style (`/* */`) to avoid IDE warnings. The `license-cleanup` profile will be removed in version 1.2.0.

### CI/CD Integration

- GitHub Actions workflows handle builds, tests, and deployments
- Automatic SNAPSHOT deployments on main branch commits
- Release workflow triggered by changes to project.yml
- Security scanning via GitHub security features and scorecards

## Git Workflow

All cuioss repositories have branch protection on `main`. Direct pushes to `main` are never allowed. Always use this workflow:

1. Create a feature branch: `git checkout -b <branch-name>`
2. Commit changes: `git add <files> && git commit -m "<message>"`
3. Push the branch: `git push -u origin <branch-name>`
4. Create a PR: `gh pr create --repo cuioss/cuioss-parent-pom --head <branch-name> --base main --title "<title>" --body "<body>"`
5. Wait for CI + Gemini review (waits until checks complete): `gh pr checks --watch`
6. **Handle Gemini review comments** — fetch with `gh api repos/cuioss/cuioss-parent-pom/pulls/<pr-number>/comments` and for each:
   - If clearly valid and fixable: fix it, commit, push, then reply explaining the fix and resolve the comment
   - If disagree or out of scope: reply explaining why, then resolve the comment
   - If uncertain (not 100% confident): **ask the user** before acting
   - Every comment MUST get a reply (reason for fix or reason for not fixing) and MUST be resolved
7. Do **NOT** enable auto-merge unless explicitly instructed. Wait for user approval.
8. Return to main: `git checkout main && git pull`