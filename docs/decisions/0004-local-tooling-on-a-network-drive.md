# 0004 - Local tooling that works from a network drive: build, don't mount

**Status:** Accepted, 2026-09-30
**Applies to:** `docker-compose.yml`, `Dockerfile`, `.env.example`, local development

## Context

The development machine keeps the repository on `Z:`, a mapped network drive (an SMB share), and runs containers with Rancher Desktop, not Docker Desktop. That combination breaks several tools that assume a local disk:

- **Docker bind mounts from `Z:` are refused** by Rancher Desktop's VM engine ("invalid volume specification"). Mounts from `C:` work.
- **File-system watching fails**, so hot reload (such as `uvicorn --reload`) can't see changes.
- **Writing to some paths fails.** pytest can't always write `.pytest_cache`, and `pre-commit autoupdate` couldn't rewrite its own config file.
- **Imports are slow.** A virtualenv on the share takes about 3 seconds just to import the migration command's modules. The same command inside the container finished in 0.3 seconds.
- **`localhost` hangs.** Under Rancher Desktop a connection to IPv6 `::1` hangs instead of being refused, so anything connecting to `localhost` waits for a timeout before falling back to IPv4.

## Decision

- **No bind mounts, anywhere.** The source and any configuration a container needs are built into the image. After a code change, `docker compose up -d --build` rebuilds it. The image's dependency layer is cached, so a source-only rebuild takes seconds.
- **The same image runs the migration step and the API**, so what is tested locally is what would be deployed.
- **Local URLs use `127.0.0.1`, not `localhost`** (`.env.example`), and every database connection, including Alembic's, has a 5-second connect timeout, so an address that hangs fails fast.
- **The build context is an allowlist** (`.dockerignore`), so nothing from the working copy, `.env` in particular, reaches an image unless it's named.

## Alternatives

**Bind-mount the source for hot reload.** This is the usual Python development setup, but it simply doesn't work from `Z:` under Rancher Desktop, and hot reload would fail anyway without file watching.

**Move the repository to a local disk.** This would fix every item in Context at once, and is worth doing if the network drive becomes a daily obstacle. It's a choice about the owner's machine rather than the project, so the project is built to work either way. Nothing here depends on the network drive, and a clone on a local disk works unchanged.

**Docker Desktop instead of Rancher Desktop.** It handles some of these cases differently, but it brings a commercial licensing question the owner chose to avoid.

## Consequences

- There's no hot reload in containers. The development loop is either the API run on the host (`python -m mtg_deck_advisor.api`, against the Compose database) or a rebuild.
- The workflow is identical on any machine: nothing assumes a network drive, and nothing breaks on a local disk.
- Every configuration change needs a rebuild to take effect in a container. `docker compose up -d --build` is the one command to remember.
