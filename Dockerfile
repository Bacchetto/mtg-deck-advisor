# The application image: the API server, and the migration command run by the
# one-shot `migrate` service. Two stages: the first installs everything into a
# virtual environment, and the second copies only that environment into a
# clean image, so pip's caches and the build tooling never reach it.
#
#   docker build -t mtg-deck-advisor .
#
# PINNING: the base image is pinned to an exact Python release and Debian
# base (3.12.14 on trixie), so a rebuild months from now runs the Python the
# tests ran on. A tag can still be re-pointed by its publisher; pinning by
# digest (python:3.12.14-slim-trixie@sha256:...) rules that out, at the cost
# of needing a tool such as Dependabot to pick up security fixes.

# ---------------------------------------------------------------------------
# Stage 1: build
# ---------------------------------------------------------------------------
FROM python:3.12.14-slim-trixie AS build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Dependencies first, the application second. Docker reruns a step only when
# its inputs change, so an edit to the source reuses the installed
# dependencies instead of downloading them again.
#
# pip can only install a project's dependencies by installing the project, so
# this step installs pyproject.toml against a placeholder package (an empty
# __init__.py and README). --mount=type=cache keeps pip's download cache
# between builds, outside the image.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p src/mtg_deck_advisor \
 && touch src/mtg_deck_advisor/__init__.py README.md \
 && pip install .

# Now the real package, replacing the placeholder. --no-deps: the dependencies
# are already installed by the step above.
COPY README.md ./
COPY src src
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps --force-reinstall .

# Nothing installs packages at runtime, so pip leaves the environment before
# it is copied: 13 MB less, and one less tool for an attacker inside the
# container.
RUN pip uninstall --yes pip

# ---------------------------------------------------------------------------
# Stage 2: run
# ---------------------------------------------------------------------------
FROM python:3.12.14-slim-trixie

# A dedicated, unprivileged user. Root inside a container is not root on the
# host, but if the process were ever compromised, root in the container is a
# far better starting point for an attacker.
RUN groupadd --system app && useradd --system --gid app --no-create-home app

COPY --from=build /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH" \
    # Logs reach `docker logs` immediately rather than sitting in a buffer.
    PYTHONUNBUFFERED=1 \
    # The app cannot write to its own install directory, and should not try.
    PYTHONDONTWRITEBYTECODE=1 \
    # Inside a container, listen on every interface: Docker's port mapping
    # decides what is actually reachable from outside.
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    LOG_FORMAT=json \
    ENVIRONMENT=production

USER app
EXPOSE 8000

# Docker's own view of whether the app is healthy, from inside the container.
# The slim image has no curl, so Python's standard library does the request;
# a non-200 answer (503 when the database is down) raises and fails the check.
# The timeout is longer than the app's 5-second database connect timeout, so a
# slow database reports "unhealthy" rather than a timed-out check.
HEALTHCHECK --interval=15s --timeout=10s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)"]

# The launcher configures logging before uvicorn starts (see api/__main__.py).
CMD ["python", "-m", "mtg_deck_advisor.api"]
