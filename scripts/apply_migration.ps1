$ErrorActionPreference = "Stop"

# Actualiza una base creada con una versión anterior (Corte 1) al esquema actual.
# Una instalación nueva no lo necesita: sql/schema.sql ya crea todo al primer arranque.
$migrations = @(
    ".\sql\migrations\002_human_codes_and_profiles.sql",
    ".\sql\migrations\003_corte2_active_system.sql",
    ".\sql\migrations\004_human_identifiers_for_swagger.sql"
)

Write-Host "Aplicando migraciones 002, 003 y 004 a app-db..." -ForegroundColor Cyan
docker compose up -d app-db
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
foreach ($migration in $migrations) {
    Write-Host "  -> $migration"
    Get-Content -Raw $migration |
      docker compose exec -T app-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "Migraciones aplicadas correctamente." -ForegroundColor Green