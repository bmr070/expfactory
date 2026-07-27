# Python 3.11 is the floor the package declares. Matching CI's lower bound here
# means a container run and a CI run disagree for real reasons rather than
# because they were built on different interpreters.
FROM mcr.microsoft.com/devcontainers/python:3.11-bookworm

# Nothing here is a runtime dependency of the verifier core — pyproject keeps
# that at zero deliberately. These are for the *workload* and for git operations
# from inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# The GitHub CLI, so PR work does not have to hop back to the host.
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# torch is deliberately NOT installed. It is a workload dependency, not a
# substrate one — the substrate launches processes and never imports it — and
# baking 2.5 GB into the image for hypotheses that may not need it (H2 and H3 do
# not) is a cost with no current return. Install it in the container when a
# hypothesis actually calls for it:
#
#   pip install torch --index-url https://download.pytorch.org/whl/cu124
#
# Note that URL is not on the egress allowlist in src/expfactory/egress.py. That
# is correct: the allowlist governs what the *factory* fetches during a run, and
# widening it for a one-off developer install would be the exact loosening GH#7
# refused. Install it by hand, deliberately, outside the run path.

WORKDIR /workspaces/expfactory
