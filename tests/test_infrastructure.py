from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_compose_defines_isolated_postgres_service():
    compose = (ROOT / "docker-compose.yml").read_text()

    assert "image: postgres:16" in compose
    assert "container_name: stocktool-postgres" in compose
    assert "${POSTGRES_PORT:-15433}:5432" in compose
    assert "stocktool_pgdata:/var/lib/postgresql/data" in compose
    assert "pg_isready" in compose


def test_env_example_contains_local_database_settings():
    env_example = (ROOT / ".env.example").read_text()

    for key in (
        "POSTGRES_HOST=",
        "POSTGRES_PORT=15433",
        "POSTGRES_DB=stocktool",
        "POSTGRES_USER=stocktool",
        "POSTGRES_PASSWORD=",
        "DATABASE_URI=",
    ):
        assert key in env_example


def test_requirements_cover_runtime_and_test_stack():
    requirements = (ROOT / "requirements.txt").read_text().lower()

    for package in (
        "pandas",
        "psycopg",
        "fastapi",
        "uvicorn",
        "streamlit",
        "akshare",
        "python-dotenv",
        "pytest",
    ):
        assert package in requirements


def test_init_db_is_idempotent_and_creates_documented_partitions():
    script = (ROOT / "scripts/init_db.sh").read_text()

    assert "set -euo pipefail" in script
    assert "ON_ERROR_STOP=1" in script
    for table in (
        "stock_basic",
        "daily_price",
        "sector_daily",
        "stock_extra",
        "signals",
        "backtest_runs",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in script
    assert "daily_price_%s" in script
    assert "stock_extra_%s" in script
    assert "2019..2026" in script


def test_ci_workflow_defines_python_test_pipeline():
    ci_path = ROOT / ".github/workflows/ci.yml"
    assert ci_path.exists(), "CI workflow file .github/workflows/ci.yml does not exist"

    ci_content = ci_path.read_text()
    assert "branches: [ main ]" in ci_content or "branches:\n      - main" in ci_content or "- main" in ci_content
    assert "runs-on: ubuntu-latest" in ci_content
    assert 'python-version: "3.12"' in ci_content
    assert "pip install -r requirements.txt" in ci_content
    assert "python -m compileall data engine api dashboard" in ci_content
    assert "python -m pytest" in ci_content

