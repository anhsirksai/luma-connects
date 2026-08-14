FROM python:3.12-slim

WORKDIR /app

# System deps for beautifulsoup4/lxml-style parsing stay minimal; no compiler
# toolchain is needed since we use the pure-Python html.parser backend.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

ENV PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080

# --workers 1: the pipeline runner and SSE stream both assume a single
# process (in-process asyncio tasks, SQLite as the single writer).
CMD ["python", "-m", "invite_finder.api.app"]
