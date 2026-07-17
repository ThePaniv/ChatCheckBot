# syntax=docker/dockerfile:1

# ---- Builder: export the locked runtime dependencies with uv ----
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

WORKDIR /app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv export --frozen --no-dev --no-emit-project -o requirements.txt


# ---- Final: AWS Lambda Python runtime ----
FROM public.ecr.aws/lambda/python:3.13

COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/chatcheck_bot ${LAMBDA_TASK_ROOT}/chatcheck_bot

# Default handler; the cron Lambda overrides this via image_config.command.
CMD ["chatcheck_bot.bot.webhook_handler"]
