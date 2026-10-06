"""Signature tables: which imports, dependencies and files indicate a framework, a capability or a
package manager (docs/RUBRICS.md §2). Matching is by module/package prefix, case-insensitive.
"""

import re

# Capability → package/module prefixes (Python modules, npm/NuGet/Maven/Go package names).
HTTP_FRAMEWORKS: dict[str, tuple[str, ...]] = {
    "fastapi": ("fastapi",),
    "flask": ("flask",),
    "django": ("django",),
    "starlette": ("starlette",),
    "aiohttp-server": ("aiohttp.web",),
    "express": ("express",),
    "nestjs": ("@nestjs/core", "@nestjs/common"),
    "koa": ("koa",),
    "fastify": ("fastify",),
    "nextjs": ("next",),
    "aspnetcore": ("microsoft.aspnetcore",),
    "spring": ("org.springframework", "spring-boot-starter-web", "spring-web"),
    "gin": ("github.com/gin-gonic/gin",),
    "echo": ("github.com/labstack/echo",),
    "fiber": ("github.com/gofiber/fiber",),
}
DATA_LIBRARIES: dict[str, tuple[str, ...]] = {
    "sqlalchemy": ("sqlalchemy", "sqlmodel"),
    "django-orm": ("django.db",),
    "psycopg": ("psycopg", "psycopg2", "asyncpg"),
    "mysql": ("pymysql", "mysqlclient", "mysql.connector", "mysql2"),
    "sqlite": ("sqlite3", "aiosqlite"),
    "mongodb": ("pymongo", "motor", "mongoengine", "mongoose", "mongodb"),
    "peewee": ("peewee", "tortoise"),
    "prisma": ("@prisma/client", "prisma"),
    "typeorm": ("typeorm",),
    "sequelize": ("sequelize",),
    "knex": ("knex", "drizzle-orm", "pg"),
    "efcore": ("microsoft.entityframeworkcore", "dapper", "npgsql"),
    "jpa": ("spring-boot-starter-data-jpa", "org.hibernate", "jakarta.persistence", "java.sql"),
    "go-sql": ("database/sql", "gorm.io/gorm", "github.com/jmoiron/sqlx", "github.com/jackc/pgx"),
}
MESSAGING: dict[str, tuple[str, ...]] = {
    "celery": ("celery",),
    "kafka": ("kafka", "confluent_kafka", "aiokafka", "kafkajs", "confluent.kafka", "spring-kafka"),
    "rabbitmq": ("pika", "aio_pika", "kombu", "amqplib", "rabbitmq.client", "spring-rabbit"),
    "azure-servicebus": ("azure.servicebus", "@azure/service-bus", "azure.messaging.servicebus"),
    "nats": ("nats",),
    "bullmq": ("bullmq", "bull"),
    "masstransit": ("masstransit", "nservicebus"),
}
OUTBOUND_HTTP: tuple[str, ...] = (
    "requests", "httpx", "aiohttp", "urllib3", "axios", "node-fetch", "got", "undici", "ky",
    "system.net.http", "okhttp3", "org.springframework.web.client", "net/http",
)  # fmt: skip
# Plain-text signals for clients that need no import (global fetch, HttpClient).
OUTBOUND_TEXT: dict[str, re.Pattern[str]] = {
    "javascript": re.compile(r"\bfetch\(\s*[`'\"h]"),
    "typescript": re.compile(r"\bfetch\(\s*[`'\"h]"),
    "csharp": re.compile(r"\bnew\s+HttpClient\b|IHttpClientFactory"),
}

# Package managers by manifest/lockfile name.
PACKAGE_MANAGERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("uv", re.compile(r"(^|/)uv\.lock$")),
    ("poetry", re.compile(r"(^|/)poetry\.lock$")),
    ("pdm", re.compile(r"(^|/)pdm\.lock$")),
    ("pipenv", re.compile(r"(^|/)Pipfile(\.lock)?$")),
    ("pip", re.compile(r"(^|/)requirements[^/]*\.txt$")),
    ("npm", re.compile(r"(^|/)package-lock\.json$")),
    ("yarn", re.compile(r"(^|/)yarn\.lock$")),
    ("pnpm", re.compile(r"(^|/)pnpm-lock\.yaml$")),
    ("bun", re.compile(r"(^|/)bun\.lockb?$")),
    ("go-modules", re.compile(r"(^|/)go\.mod$")),
    ("cargo", re.compile(r"(^|/)Cargo\.toml$")),
    ("maven", re.compile(r"(^|/)pom\.xml$")),
    ("gradle", re.compile(r"(^|/)build\.gradle(\.kts)?$")),
    ("nuget", re.compile(r"\.(cs|fs|vb)proj$")),
    ("bundler", re.compile(r"(^|/)Gemfile$")),
    ("composer", re.compile(r"(^|/)composer\.json$")),
)

IAC_FILES = re.compile(
    r"\.tf$|\.tf\.json$|\.bicep$|(^|/)azuredeploy[^/]*\.json$|(^|/)Pulumi\.ya?ml$"
    r"|(^|/)(cloudformation|cfn)/[^/]+\.(ya?ml|json)$|\.cfn\.(ya?ml|json)$|(^|/)template\.ya?ml$"
)
K8S_MARKERS = re.compile(r"(^|/)(Chart\.yaml|kustomization\.ya?ml|helmfile\.ya?ml)$")
ENTRYPOINTS = re.compile(
    r"^(src/)?(\w+/)?(main|app|server|wsgi|asgi|manage)\.py$"
    r"|^(src/)?(index|server|main|app)\.[jt]s$"
    r"|(^|/)Program\.cs$|(^|/)main\.go$|Application\.(java|kt)$"
)

# Language families for the lang_* flags (RUBRICS.md §2: ≥ 10% of LOC).
LANGUAGE_FLAGS: dict[str, frozenset[str]] = {
    "lang_python": frozenset({"python"}),
    "lang_js_ts": frozenset({"javascript", "typescript"}),
    "lang_dotnet": frozenset({"csharp"}),
    "lang_java": frozenset({"java", "kotlin", "scala"}),
    "lang_go": frozenset({"go"}),
}
LANGUAGE_SHARE = 0.10


def matches(name: str, prefixes: tuple[str, ...]) -> bool:
    """True if `name` equals a prefix or continues it with `.`, `/` or `-`."""
    lowered = name.lower()
    return any(lowered == p or lowered.startswith((p + ".", p + "/", p + "-")) for p in prefixes)
