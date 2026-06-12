# Build with the tau2-bench checkout as a named context:
#   docker build --build-context tau2=../tau2-bench -t a2a-hackathon-harness .
FROM python:3.12-slim

RUN pip install --no-cache-dir uv

COPY --from=tau2 . /tau2-bench

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Point the sibling-checkout source override at the baked-in copy
RUN sed -i 's#path = "../tau2-bench"#path = "/tau2-bench"#' pyproject.toml \
    && uv venv && uv pip install . /tau2-bench

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8090
CMD ["a2a-hack", "--help"]
