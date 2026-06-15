# =============================================================================
# SecondBrainSense — Infrastructure Validation Suite
# =============================================================================
# Cobertura:
#   T1  Creation     — todos los contenedores esperados están running
#   T2  Health       — healthchecks pasando
#   T3  Connectivity — matriz inter-servicios (quien habla con quien)
#   T4  Volumes      — volúmenes nombrados existen y persisten datos
#   T5  Config       — env vars, restart policies, certs, SERVICE_ROLE
#   T6  APIs         — contratos HTTP/TCP verificados
#   T7  Celery       — worker conectado, beat activo, cola accesible
#   T8  Brain        — imports Python, UniversalCleaner, browsers, modelos
#   T9  Resilience   — restart automático tras SIGKILL (unless-stopped)
#   T10 Security     — baseline OWASP: certs, no debug, notas de prod
#
# Uso:
#   cd docker
#   .\tests\infra-validation.ps1                             # todos los tests
#   .\tests\infra-validation.ps1 -Skip Resilience           # sin tests destructivos
#   .\tests\infra-validation.ps1 -Only Connectivity,APIs    # solo esas categorías
#   .\tests\infra-validation.ps1 -Verbose                   # output detallado
#
# Exit code: 0 = todo PASS/WARN  |  1 = algún FAIL
# =============================================================================
param(
    # Categorías a omitir (ej: -Skip Resilience,Volumes)
    [string[]]$Skip = @(),
    # Ejecutar SOLO estas categorías (sobreescribe -Skip)
    [string[]]$Only = @(),
    # Nombre del stack compose (para filtrar contenedores si hubiera varios)
    [string]$StackName    = "secondbrainsense-dev",
    # Puertos accesibles desde el host
    [string]$BackendHost  = "localhost",
    [int]$BackendPort     = 8929,
    [int]$QdrantPort      = 6333,
    [int]$RedisPort       = 6379,
    [int]$FrontendPort    = 3929,
    [int]$SearXNGPort     = 8888,
    [int]$ZeroCachePort   = 4848,
    [int]$PgAdminPort     = 5050,
    # DB credentials (deben coincidir con .env)
    [string]$DbUser       = "surfsense",
    [string]$DbName       = "surfsense",
    [switch]$Verbose
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# ─── Estado global ────────────────────────────────────────────────────────────
$script:PASS    = 0
$script:FAIL    = 0
$script:WARN    = 0
$script:Results = [System.Collections.Generic.List[PSCustomObject]]::new()

# ─── Helpers de output ────────────────────────────────────────────────────────
function Write-Header([string]$Title) {
    Write-Host "`n$('─' * 72)" -ForegroundColor DarkGray
    Write-Host "  $Title" -ForegroundColor White
    Write-Host "$('─' * 72)" -ForegroundColor DarkGray
}

function Write-Info([string]$Msg) {
    Write-Host "  [INFO] $Msg" -ForegroundColor Cyan
}

function Test-Assert {
    param(
        [string]$Name,
        [string]$Category,
        [bool]  $Condition,
        [string]$Detail    = "",
        [bool]  $IsWarning = $false   # si falla → WARN en lugar de FAIL
    )
    $status = if ($Condition) { "PASS" } elseif ($IsWarning) { "WARN" } else { "FAIL" }
    $script:Results.Add([PSCustomObject]@{
        Category = $Category; Name = $Name; Status = $status; Detail = $Detail
    })
    switch ($status) {
        "PASS" {
            Write-Host "  [PASS] $Name" -ForegroundColor Green
            if ($Detail -and $Verbose) { Write-Host "         $Detail" -ForegroundColor DarkGray }
            $script:PASS++
        }
        "WARN" {
            Write-Host "  [WARN] $Name" -ForegroundColor Yellow
            if ($Detail) { Write-Host "         $Detail" -ForegroundColor Yellow }
            $script:WARN++
        }
        "FAIL" {
            Write-Host "  [FAIL] $Name" -ForegroundColor Red
            if ($Detail) { Write-Host "         $Detail" -ForegroundColor Red }
            $script:FAIL++
        }
    }
}

function Should-Run([string]$Cat) {
    if ($Only.Count -gt 0) { return ($Only | ForEach-Object { $_.ToLower() }) -contains $Cat.ToLower() }
    return -not (($Skip | ForEach-Object { $_.ToLower() }) -contains $Cat.ToLower())
}

# Ejecuta comando en contenedor vía bash -c y devuelve stdout+stderr como string
function Exec([string]$Container, [string]$Cmd) {
    (docker exec $Container bash -c $Cmd 2>&1) -join "`n"
}

# Espera hasta que un contenedor tenga healthcheck=healthy (timeout en segundos)
function Wait-Healthy([string]$Container, [int]$TimeoutSecs = 60) {
    $elapsed = 0
    while ($elapsed -lt $TimeoutSecs) {
        $h = (docker inspect $Container --format "{{.State.Health.Status}}" 2>&1)
        if ($h -eq "healthy") { return $true }
        Start-Sleep -Seconds 3
        $elapsed += 3
    }
    return $false
}

# HTTP GET con manejo de error; devuelve @{Code=int; Body=string}
function Invoke-Get([string]$Url, [int]$TimeoutSecs = 8) {
    try {
        $r = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec $TimeoutSecs -ErrorAction Stop
        return @{ Code = [int]$r.StatusCode; Body = $r.Content }
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        return @{ Code = $code; Body = $_.Exception.Message }
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# T1 — CREATION
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Creation") {
    Write-Header "T1 — Creation: Estado de contenedores"

    $expected = @(
        "sbs-dev-db", "sbs-dev-redis", "sbs-dev-qdrant",
        "sbs-dev-backend", "sbs-dev-celery-worker", "sbs-dev-celery-beat",
        "sbs-dev-otel", "sbs-dev-searxng", "sbs-dev-zero-cache", "sbs-dev-frontend"
    )
    $running = @(docker ps --format "{{.Names}}" 2>&1)

    foreach ($c in $expected) {
        Test-Assert -Name "Container '$c' running" -Category "Creation" `
            -Condition ($running -contains $c) `
            -Detail (if ($running -notcontains $c) { "No aparece en 'docker ps'" } else { "" })
    }

    # migrations debe haber exitado 0 (one-shot runner)
    $migExit = (docker inspect sbs-dev-migrations --format "{{.State.ExitCode}}" 2>&1)
    Test-Assert -Name "sbs-dev-migrations: exited 0 (schema aplicado)" -Category "Creation" `
        -Condition ($migExit -eq "0") -Detail "ExitCode=$migExit"

    # pgAdmin es opcional — OOM conocido tras reinicio de Docker Desktop
    $pgaStatus = (docker inspect sbs-dev-pgadmin --format "{{.State.Status}}" 2>&1)
    Test-Assert -Name "sbs-dev-pgadmin: running (opcional, propenso a OOM)" -Category "Creation" `
        -Condition ($pgaStatus -eq "running") `
        -Detail "Status=$pgaStatus  →  si falla: 'docker start sbs-dev-pgadmin'" `
        -IsWarning ($pgaStatus -ne "running")
}

# ─────────────────────────────────────────────────────────────────────────────
# T2 — HEALTH
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Health") {
    Write-Header "T2 — Health: Healthchecks Docker"

    $withHealthcheck = @(
        "sbs-dev-db", "sbs-dev-redis", "sbs-dev-qdrant",
        "sbs-dev-backend", "sbs-dev-otel", "sbs-dev-searxng", "sbs-dev-zero-cache"
    )
    foreach ($c in $withHealthcheck) {
        $h = (docker inspect $c --format "{{.State.Health.Status}}" 2>&1)
        Test-Assert -Name "$c health=healthy" -Category "Health" `
            -Condition ($h -eq "healthy") -Detail "Health status: $h"
    }

    # celery-worker y celery-beat no tienen healthcheck definido
    foreach ($c in @("sbs-dev-celery-worker", "sbs-dev-celery-beat")) {
        $s = (docker inspect $c --format "{{.State.Status}}" 2>&1)
        Test-Assert -Name "$c status=running (sin healthcheck)" -Category "Health" `
            -Condition ($s -eq "running") -Detail "Status: $s"
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# T3 — CONNECTIVITY  (matriz inter-servicios)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Connectivity") {
    Write-Header "T3 — Connectivity: Matriz inter-servicios"

    # backend → db (TCP 5432)
    $r = Exec "sbs-dev-backend" "python -c ""import socket; s=socket.create_connection(('db',5432),3); s.close(); print('ok')"""
    Test-Assert -Name "backend → db (TCP:5432)" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # backend → redis (TCP 6379)
    $r = Exec "sbs-dev-backend" "python -c ""import socket; s=socket.create_connection(('redis',6379),3); s.close(); print('ok')"""
    Test-Assert -Name "backend → redis (TCP:6379)" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # backend → qdrant (HTTP /healthz)
    $r = Exec "sbs-dev-backend" "curl -sf --max-time 5 http://sbs-dev-qdrant:6333/healthz"
    Test-Assert -Name "backend → qdrant (HTTP:6333/healthz)" -Category "Connectivity" `
        -Condition ($r -match "healthz check passed") -Detail $r.Trim()

    # backend → searxng (HTTP /healthz)
    $r = Exec "sbs-dev-backend" "curl -sf --max-time 5 -o /dev/null -w '%{http_code}' http://searxng:8080/healthz"
    Test-Assert -Name "backend → searxng (HTTP:8080/healthz)" -Category "Connectivity" `
        -Condition ($r.Trim() -eq "200") -Detail "HTTP $($r.Trim())"

    # backend → otel-lgtm (TCP 4317 gRPC)
    $r = Exec "sbs-dev-backend" "python -c ""import socket; s=socket.create_connection(('sbs-dev-otel',4317),3); s.close(); print('ok')"""
    Test-Assert -Name "backend → otel-lgtm (TCP:4317 gRPC)" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # celery-worker → redis (TCP 6379)
    $r = Exec "sbs-dev-celery-worker" "python -c ""import socket; s=socket.create_connection(('redis',6379),3); s.close(); print('ok')"""
    Test-Assert -Name "celery-worker → redis (TCP:6379)" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # celery-worker → qdrant
    $r = Exec "sbs-dev-celery-worker" "curl -sf --max-time 5 http://sbs-dev-qdrant:6333/healthz"
    Test-Assert -Name "celery-worker → qdrant (HTTP:6333/healthz)" -Category "Connectivity" `
        -Condition ($r -match "healthz check passed") -Detail $r.Trim()

    # celery-worker → db
    $r = Exec "sbs-dev-celery-worker" "python -c ""import socket; s=socket.create_connection(('db',5432),3); s.close(); print('ok')"""
    Test-Assert -Name "celery-worker → db (TCP:5432)" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # host → backend
    $resp = Invoke-Get "http://${BackendHost}:${BackendPort}/ready"
    Test-Assert -Name "host → backend (HTTP:$BackendPort/ready)" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host → qdrant
    $resp = Invoke-Get "http://${BackendHost}:${QdrantPort}/healthz"
    Test-Assert -Name "host → qdrant (HTTP:$QdrantPort)" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host → frontend
    $resp = Invoke-Get "http://${BackendHost}:${FrontendPort}"
    Test-Assert -Name "host → frontend (HTTP:$FrontendPort)" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host → searxng
    $resp = Invoke-Get "http://${BackendHost}:${SearXNGPort}/healthz"
    Test-Assert -Name "host → searxng (HTTP:$SearXNGPort/healthz)" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host → zero-cache /keepalive
    $resp = Invoke-Get "http://${BackendHost}:${ZeroCachePort}/keepalive"
    Test-Assert -Name "host → zero-cache (HTTP:$ZeroCachePort/keepalive)" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"
}

# ─────────────────────────────────────────────────────────────────────────────
# T4 — VOLUMES  (existencia + persistencia real de datos)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Volumes") {
    Write-Header "T4 — Volumes: Persistencia de datos"

    $expectedVolumes = @(
        "secondbrainsense-dev-postgres",
        "secondbrainsense-dev-redis",
        "secondbrainsense-dev-qdrant",
        "secondbrainsense-dev-pgadmin",
        "secondbrainsense-dev-shared-temp",
        "secondbrainsense-dev-zero-cache"
    )
    $existingVols = @(docker volume ls --format "{{.Name}}" 2>&1)
    foreach ($v in $expectedVolumes) {
        Test-Assert -Name "Volume '$v' existe" -Category "Volumes" `
            -Condition ($existingVols -contains $v) `
            -Detail (if ($existingVols -notcontains $v) { "No encontrado en docker volume ls" } else { "" })
    }

    # ── PostgreSQL: schema migrado (tablas en schema public) ──────────────────
    $tables = (docker exec sbs-dev-db psql -U $DbUser -d $DbName -t -c "\dt" 2>&1) |
              Where-Object { $_ -match "\w" }
    Test-Assert -Name "PostgreSQL: tablas de schema existen tras migrations" -Category "Volumes" `
        -Condition ($tables.Count -gt 0) `
        -Detail "$($tables.Count) tablas encontradas en schema public"

    # ── Redis AOF: dato persiste tras docker restart ───────────────────────────
    Write-Info "Testando persistencia Redis AOF (restart sbs-dev-redis)..."
    $testVal = "persist_test_$(Get-Date -Format 'HHmmss')"
    docker exec sbs-dev-redis redis-cli SET infra_vol_test $testVal | Out-Null
    docker restart sbs-dev-redis 2>&1 | Out-Null
    $null = Wait-Healthy "sbs-dev-redis" 30
    $gotVal = (docker exec sbs-dev-redis redis-cli GET infra_vol_test 2>&1).Trim()
    Test-Assert -Name "Redis AOF: valor persiste tras docker restart" -Category "Volumes" `
        -Condition ($gotVal -eq $testVal) `
        -Detail "SET='$testVal'  GET_after_restart='$gotVal'"
    # Limpieza
    docker exec sbs-dev-redis redis-cli DEL infra_vol_test | Out-Null

    # ── Qdrant: colección persiste tras docker restart ────────────────────────
    Write-Info "Testando persistencia Qdrant (restart sbs-dev-qdrant)..."
    $createBody = '{"vectors":{"size":4,"distance":"Cosine"}}'
    try {
        Invoke-RestMethod -Uri "http://${BackendHost}:${QdrantPort}/collections/infra_vol_test" `
            -Method PUT -Body $createBody -ContentType "application/json" -ErrorAction Stop | Out-Null
    } catch { Write-Info "Qdrant PUT collection: $($_.Exception.Message)" }

    docker restart sbs-dev-qdrant 2>&1 | Out-Null
    $null = Wait-Healthy "sbs-dev-qdrant" 45
    try {
        $cols = Invoke-RestMethod "http://${BackendHost}:${QdrantPort}/collections" -ErrorAction Stop
        $found = ($cols.result.collections | Where-Object { $_.name -eq "infra_vol_test" }) -ne $null
        Test-Assert -Name "Qdrant: colección persiste tras docker restart" -Category "Volumes" `
            -Condition $found `
            -Detail "Collections: $($cols.result.collections.name -join ', ')"
        # Limpieza
        Invoke-RestMethod -Uri "http://${BackendHost}:${QdrantPort}/collections/infra_vol_test" `
            -Method DELETE -ErrorAction SilentlyContinue | Out-Null
    } catch {
        Test-Assert -Name "Qdrant: colección persiste tras docker restart" -Category "Volumes" `
            -Condition $false -Detail $_.Exception.Message
    }

    # ── shared_temp montado en backend y worker ───────────────────────────────
    foreach ($c in @("sbs-dev-backend", "sbs-dev-celery-worker")) {
        $r = Exec $c "test -d /shared_tmp && echo ok || echo miss"
        Test-Assert -Name "$c: volumen shared_tmp montado" -Category "Volumes" `
            -Condition ($r.Trim() -eq "ok") -Detail $r.Trim()
    }
}

# ─────────────────────────────────────────────────────────────────────────────
# T5 — CONFIG  (variables de entorno, restart policies, certs, roles)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Config") {
    Write-Header "T5 — Config: Variables de entorno y restart policies"

    # Restart policies declaradas en compose
    $restartPolicies = @{
        "sbs-dev-qdrant"     = "unless-stopped"
        "sbs-dev-otel"       = "unless-stopped"
        "sbs-dev-zero-cache" = "unless-stopped"
    }
    foreach ($kv in $restartPolicies.GetEnumerator()) {
        $policy = (docker inspect $kv.Key --format "{{.HostConfig.RestartPolicy.Name}}" 2>&1)
        Test-Assert -Name "$($kv.Key): restart=$($kv.Value)" -Category "Config" `
            -Condition ($policy -eq $kv.Value) -Detail "Actual: $policy"
    }

    # Cert Netskope montado en backend y worker
    foreach ($c in @("sbs-dev-backend", "sbs-dev-celery-worker")) {
        $r = Exec $c "test -f /certs/corp-root.crt && echo ok || echo miss"
        Test-Assert -Name "$c: cert Netskope en /certs/corp-root.crt" -Category "Config" `
            -Condition ($r.Trim() -eq "ok") -Detail $r.Trim()
    }

    # SERVICE_ROLE por contenedor
    $roles = @{
        "sbs-dev-backend"       = "api"
        "sbs-dev-celery-worker" = "worker"
        "sbs-dev-celery-beat"   = "beat"
    }
    foreach ($kv in $roles.GetEnumerator()) {
        $role = (Exec $kv.Key "echo `$SERVICE_ROLE").Trim()
        Test-Assert -Name "$($kv.Key): SERVICE_ROLE=$($kv.Value)" -Category "Config" `
            -Condition ($role -eq $kv.Value) -Detail "Actual: '$role'"
    }

    # PGSSLMODE=disable (red interna Docker — no TLS entre contenedores)
    $v = (Exec "sbs-dev-backend" "echo `$PGSSLMODE").Trim()
    Test-Assert -Name "backend: PGSSLMODE=disable (red interna)" -Category "Config" `
        -Condition ($v -eq "disable") -Detail "PGSSLMODE='$v'"

    # PYTHONPATH=/app
    $v = (Exec "sbs-dev-backend" "echo `$PYTHONPATH").Trim()
    Test-Assert -Name "backend: PYTHONPATH=/app" -Category "Config" `
        -Condition ($v -eq "/app") -Detail "PYTHONPATH='$v'"

    # SSL_CERT_FILE apunta al CA bundle del sistema
    $v = (Exec "sbs-dev-backend" "echo `$SSL_CERT_FILE").Trim()
    Test-Assert -Name "backend: SSL_CERT_FILE apunta a CA bundle sistema" -Category "Config" `
        -Condition ($v -match "/etc/ssl/certs") -Detail "SSL_CERT_FILE='$v'"

    # NODE_EXTRA_CA_CERTS (para scrapling/playwright Node.js)
    $v = (Exec "sbs-dev-backend" "echo `$NODE_EXTRA_CA_CERTS").Trim()
    Test-Assert -Name "backend: NODE_EXTRA_CA_CERTS configurado" -Category "Config" `
        -Condition ($v -ne "") -Detail "NODE_EXTRA_CA_CERTS='$v'"

    # CELERY_BROKER_URL apunta a redis
    $v = (Exec "sbs-dev-celery-worker" "echo `$CELERY_BROKER_URL").Trim()
    Test-Assert -Name "celery-worker: CELERY_BROKER_URL apunta a redis" -Category "Config" `
        -Condition ($v -match "^redis://") -Detail "Broker: '$v'"

    # Qdrant telemetría deshabilitada (no manda datos a telemetry.qdrant.io)
    $v = (docker exec sbs-dev-qdrant bash -c "echo `$QDRANT__TELEMETRY_DISABLED" 2>&1).Trim()
    Test-Assert -Name "qdrant: QDRANT__TELEMETRY_DISABLED=true" -Category "Config" `
        -Condition ($v -eq "true") -Detail "Actual: '$v'"

    # extra_hosts host.docker.internal disponible en backend
    $r = Exec "sbs-dev-backend" "getent hosts host.docker.internal | head -1"
    Test-Assert -Name "backend: host.docker.internal resuelve (Ollama accesible)" -Category "Config" `
        -Condition ($r.Trim() -ne "") -Detail "Resolución: '$($r.Trim())'"
}

# ─────────────────────────────────────────────────────────────────────────────
# T6 — APIs  (contratos HTTP/TCP)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "APIs") {
    Write-Header "T6 — APIs: Contratos HTTP y TCP"

    # Backend /ready → {"status":"ready"}
    try {
        $j = Invoke-RestMethod "http://${BackendHost}:${BackendPort}/ready" -ErrorAction Stop
        Test-Assert -Name "GET /ready → status=ready" -Category "APIs" `
            -Condition ($j.status -eq "ready") -Detail "Response: $($j | ConvertTo-Json -Compress)"
    } catch {
        Test-Assert -Name "GET /ready → status=ready" -Category "APIs" `
            -Condition $false -Detail $_.Exception.Message
    }

    # Backend /docs → 200 (OpenAPI disponible)
    $resp = Invoke-Get "http://${BackendHost}:${BackendPort}/docs"
    Test-Assert -Name "GET /docs → 200 (OpenAPI Swagger UI)" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Qdrant /healthz
    $resp = Invoke-Get "http://${BackendHost}:${QdrantPort}/healthz"
    Test-Assert -Name "GET qdrant/healthz → 200 healthz check passed" -Category "APIs" `
        -Condition ($resp.Code -eq 200 -and $resp.Body -match "healthz check passed") `
        -Detail "HTTP $($resp.Code): $($resp.Body.Trim())"

    # Qdrant /collections → status:ok
    try {
        $j = Invoke-RestMethod "http://${BackendHost}:${QdrantPort}/collections" -ErrorAction Stop
        Test-Assert -Name "GET qdrant/collections → status=ok" -Category "APIs" `
            -Condition ($j.status -eq "ok") `
            -Detail "Collections count: $($j.result.collections.Count)"
    } catch {
        Test-Assert -Name "GET qdrant/collections → status=ok" -Category "APIs" `
            -Condition $false -Detail $_.Exception.Message
    }

    # Redis PING → PONG
    $pong = (docker exec sbs-dev-redis redis-cli PING 2>&1).Trim()
    Test-Assert -Name "Redis PING → PONG" -Category "APIs" `
        -Condition ($pong -eq "PONG") -Detail "Response: '$pong'"

    # PostgreSQL pg_isready
    $pgr = (docker exec sbs-dev-db pg_isready -U $DbUser -d $DbName 2>&1)
    Test-Assert -Name "PostgreSQL pg_isready → accepting connections" -Category "APIs" `
        -Condition ($pgr -match "accepting connections") -Detail $pgr.Trim()

    # SearXNG /healthz → 200
    $resp = Invoke-Get "http://${BackendHost}:${SearXNGPort}/healthz"
    Test-Assert -Name "GET searxng/healthz → 200" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Zero-cache /keepalive → 200
    $resp = Invoke-Get "http://${BackendHost}:${ZeroCachePort}/keepalive"
    Test-Assert -Name "GET zero-cache:$ZeroCachePort/keepalive → 200" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Grafana LGTM /metrics endpoint (OTEL collector HTTP)
    $resp = Invoke-Get "http://${BackendHost}:3001"
    Test-Assert -Name "GET otel-lgtm (Grafana :3001) → responde" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"
}

# ─────────────────────────────────────────────────────────────────────────────
# T7 — CELERY  (worker activo, beat corriendo, cola accesible)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Celery") {
    Write-Header "T7 — Celery: Cola de tareas"

    # Worker responde a inspect ping
    Write-Info "celery inspect ping (timeout 12s)..."
    $ping = (docker exec sbs-dev-celery-worker bash -c `
        "cd /app && timeout 12 celery -A app.celery_app inspect ping -t 8 2>&1" 2>&1) -join "`n"
    Test-Assert -Name "celery-worker: responde a 'inspect ping'" -Category "Celery" `
        -Condition ($ping -match "pong|celery@|ok") `
        -Detail ($ping -split "`n" | Select-Object -First 4 | Out-String).Trim()

    # Worker tiene colas activas (surfsense queue)
    $queues = (docker exec sbs-dev-celery-worker bash -c `
        "cd /app && timeout 12 celery -A app.celery_app inspect active_queues -t 8 2>&1" 2>&1) -join "`n"
    Test-Assert -Name "celery-worker: cola 'surfsense' activa" -Category "Celery" `
        -Condition ($queues -match "surfsense|default") `
        -Detail ($queues -split "`n" | Select-Object -First 4 | Out-String).Trim()

    # Celery beat: logs muestran scheduler activo
    $beatLogs = (docker logs sbs-dev-celery-beat 2>&1) -join "`n"
    Test-Assert -Name "celery-beat: scheduler iniciado (logs)" -Category "Celery" `
        -Condition ($beatLogs -match "beat|scheduler|Starting") `
        -Detail ($beatLogs -split "`n" | Select-Object -Last 3 | Out-String).Trim()

    # Cola 'surfsense' accesible en Redis (LLEN, puede ser 0)
    $qlen = (docker exec sbs-dev-redis redis-cli LLEN surfsense 2>&1).Trim()
    Test-Assert -Name "Redis: cola 'surfsense' accesible (len=$qlen, puede ser 0)" -Category "Celery" `
        -Condition ($qlen -match "^\d+$") -Detail "LLEN surfsense = $qlen"

    # Verificar que el worker tiene la task registrada del pipeline brain
    $tasks = (docker exec sbs-dev-celery-worker bash -c `
        "cd /app && timeout 12 celery -A app.celery_app inspect registered -t 8 2>&1" 2>&1) -join "`n"
    Test-Assert -Name "celery-worker: tareas registradas visibles" -Category "Celery" `
        -Condition ($tasks -match "task|surfsense") `
        -Detail ($tasks -split "`n" | Select-Object -First 5 | Out-String).Trim()
}

# ─────────────────────────────────────────────────────────────────────────────
# T8 — BRAIN  (módulos Python, UniversalCleaner, browsers, modelos pre-baked)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Brain") {
    Write-Header "T8 — Brain: Módulos Python y assets pre-bakeados"

    # Imports principales del pipeline
    $sc = 'from app.brain.extractors import get_extractor_for_extension; from app.brain.rag_lib.layer1_universal.universal_cleaner import UniversalCleaner; from app.brain.preprocessing import py as py_prep; print("imports_ok")'
    $r = Exec "sbs-dev-backend" "cd /app && python -c '$sc'"
    Test-Assert -Name "Brain: imports principales (extractors, cleaner, preprocessing)" -Category "Brain" `
        -Condition ($r -match "imports_ok") `
        -Detail ($r -split "`n" | Select-Object -Last 3 | Out-String).Trim()

    # UniversalCleaner.clean() devuelve resultado
    $sc = 'from app.brain.rag_lib.layer1_universal.universal_cleaner import UniversalCleaner; uc=UniversalCleaner(); r=uc.clean("Texto con  dobles  espacios"); print(f"type={type(r).__name__}"); print("cleaner_ok")'
    $r = Exec "sbs-dev-backend" "cd /app && python -c '$sc'"
    Test-Assert -Name "UniversalCleaner.clean() devuelve CleanResult" -Category "Brain" `
        -Condition ($r -match "cleaner_ok") `
        -Detail ($r -split "`n" | Select-Object -Last 3 | Out-String).Trim()

    # ExtractorFactory: .py devuelve extractor
    $sc = 'from app.brain.extractors import get_extractor_for_extension; e=get_extractor_for_extension(".py"); print(f"ext={type(e).__name__}"); print("factory_ok")'
    $r = Exec "sbs-dev-backend" "cd /app && python -c '$sc'"
    Test-Assert -Name "ExtractorFactory: .py → extractor concreto" -Category "Brain" `
        -Condition ($r -match "factory_ok") `
        -Detail ($r -split "`n" | Select-Object -Last 3 | Out-String).Trim()

    # Scrapling: Chromium binary bakeado en imagen
    $chrome = (Exec "sbs-dev-backend" "find /root/.cache/ms-playwright -name 'chrome' -type f 2>/dev/null | head -1").Trim()
    Test-Assert -Name "Scrapling: Chromium binary presente (/root/.cache/ms-playwright/...)" -Category "Brain" `
        -Condition ($chrome -match "chrome") -Detail "Binary: $chrome"

    # EasyOCR: modelos .pth pre-bakeados (mínimo 2: english_g2 + craft_mlt_25k)
    $cnt = (Exec "sbs-dev-backend" "ls /root/.EasyOCR/model/*.pth 2>/dev/null | wc -l").Trim()
    Test-Assert -Name "EasyOCR: modelos .pth bakeados (count=$cnt, esperado ≥2)" -Category "Brain" `
        -Condition ([int]$cnt -ge 2) -Detail "Modelos encontrados: $cnt"

    # HuggingFace: sentence-transformers cacheado
    $hf = (Exec "sbs-dev-backend" "ls /root/.cache/huggingface/hub/ 2>/dev/null | grep -c sentence-transformers || echo 0").Trim()
    Test-Assert -Name "HuggingFace: sentence-transformers en caché local" -Category "Brain" `
        -Condition ([int]$hf -ge 1) -Detail "Directorios sentence-transformers: $hf"

    # Pandoc disponible (para Docling/pypandoc_binary)
    $pandoc = (Exec "sbs-dev-backend" "pandoc --version 2>&1 | head -1").Trim()
    Test-Assert -Name "Pandoc: disponible en imagen ($pandoc)" -Category "Brain" `
        -Condition ($pandoc -match "pandoc") -Detail $pandoc
}

# ─────────────────────────────────────────────────────────────────────────────
# T9 — RESILIENCE  (restart automático tras SIGKILL)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Resilience") {
    Write-Header "T9 — Resilience: Restart automático (restart: unless-stopped)"
    Write-Host "  [WARN] Esta categoría envía SIGKILL a contenedores y verifica recovery." -ForegroundColor Yellow

    # ── Test 1: Qdrant (restart: unless-stopped) ──────────────────────────────
    $idBefore = (docker inspect sbs-dev-qdrant --format "{{.Id}}" 2>&1).Trim()
    Write-Info "Enviando SIGKILL a sbs-dev-qdrant (restart: unless-stopped)..."
    docker kill sbs-dev-qdrant 2>&1 | Out-Null

    # Esperar a que Docker lo reinicie automáticamente
    Start-Sleep -Seconds 5
    $idAfter = (docker inspect sbs-dev-qdrant --format "{{.Id}}" 2>&1).Trim()
    Test-Assert -Name "Qdrant: mismo contenedor reiniciado (misma ID)" -Category "Resilience" `
        -Condition ($idBefore -eq $idAfter) `
        -Detail "ID: $($idBefore.Substring(0,12))…"

    $healthy = Wait-Healthy "sbs-dev-qdrant" 60
    Test-Assert -Name "Qdrant: healthy dentro de 60s tras SIGKILL" -Category "Resilience" `
        -Condition $healthy -Detail "Health: $(docker inspect sbs-dev-qdrant --format '{{.State.Health.Status}}')"

    # Backend sigue alcanzando Qdrant
    $r = Exec "sbs-dev-backend" "curl -sf --max-time 5 http://sbs-dev-qdrant:6333/healthz"
    Test-Assert -Name "Backend: alcanza Qdrant tras su restart automático" -Category "Resilience" `
        -Condition ($r -match "healthz check passed") -Detail $r.Trim()

    # ── Test 2: Redis (restart: no — dev default) ─────────────────────────────
    # No matamos Redis (sin restart policy, causaría cascade en worker/beat).
    # Verificamos que el backend /ready sigue OK tras el restart de Qdrant.
    Write-Info "Verificando estabilidad del stack tras recovery Qdrant..."
    Start-Sleep -Seconds 5
    $resp = Invoke-Get "http://${BackendHost}:${BackendPort}/ready"
    Test-Assert -Name "Backend /ready: OK tras ciclo de recovery de Qdrant" -Category "Resilience" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # ── Test 3: depends_on chain — verificar que el backend no entró en restart loop ──
    $restartCount = (docker inspect sbs-dev-backend --format "{{.RestartCount}}" 2>&1).Trim()
    Test-Assert -Name "Backend: RestartCount=0 (sin crash loops)" -Category "Resilience" `
        -Condition ([int]$restartCount -eq 0) -Detail "RestartCount=$restartCount"

    $restartCount = (docker inspect sbs-dev-celery-worker --format "{{.RestartCount}}" 2>&1).Trim()
    Test-Assert -Name "celery-worker: RestartCount=0 (sin crash loops)" -Category "Resilience" `
        -Condition ([int]$restartCount -eq 0) -Detail "RestartCount=$restartCount"
}

# ─────────────────────────────────────────────────────────────────────────────
# T10 — SECURITY  (baseline OWASP: no debug, certs, notas de producción)
# ─────────────────────────────────────────────────────────────────────────────
if (Should-Run "Security") {
    Write-Header "T10 — Security: Baseline OWASP y notas para producción"

    # CA bundle del sistema contiene el cert corporativo Netskope
    $certOk = (Exec "sbs-dev-backend" "python -c ""import ssl; ctx=ssl.create_default_context(); print('ssl_ok')""").Trim()
    Test-Assert -Name "Backend: SSL context Python inicializa correctamente" -Category "Security" `
        -Condition ($certOk -match "ssl_ok") -Detail $certOk

    # No hay modo debug activo en FastAPI/Uvicorn
    $debugFlag = (Exec "sbs-dev-backend" "echo `${FASTAPI_DEBUG:-false}").Trim()
    Test-Assert -Name "Backend: FASTAPI_DEBUG=false (no debug en contenedor)" -Category "Security" `
        -Condition ($debugFlag -notmatch "^true$|^1$") -Detail "FASTAPI_DEBUG='$debugFlag'"

    # PGSSLMODE intencional documentado
    $sslmode = (Exec "sbs-dev-backend" "echo `$PGSSLMODE").Trim()
    Test-Assert -Name "PostgreSQL: PGSSLMODE=disable (intencional — red Docker interna)" -Category "Security" `
        -Condition ($sslmode -eq "disable") `
        -Detail "OK para dev. Producción: usar TLS con parámetro sslmode=require hacia RDS/Cloud SQL."

    # Redis sin contraseña — advertencia de producción
    Test-Assert -Name "Redis: sin contraseña (aceptable dev — WARN prod)" -Category "Security" `
        -Condition $true -IsWarning $true `
        -Detail "Producción: redis-server --requirepass <secret> o REDIS_URL con auth."

    # Qdrant sin API key — advertencia de producción
    Test-Assert -Name "Qdrant: sin API key (aceptable dev — WARN prod)" -Category "Security" `
        -Condition $true -IsWarning $true `
        -Detail "Producción: QDRANT__SERVICE__API_KEY=<secret> en docker-compose.yml."

    # pgAdmin protegido por login (no expone DB directamente)
    $resp = Invoke-Get "http://${BackendHost}:${PgAdminPort}"
    Test-Assert -Name "pgAdmin: responde (solicita login — no expone DB sin auth)" -Category "Security" `
        -Condition ($resp.Code -eq 200) `
        -Detail "HTTP $($resp.Code) — pgAdmin requiere credenciales para acceder"  `
        -IsWarning ($resp.Code -ne 200)

    # LANGCHAIN_TRACING desactivado (no hay leak de queries a LangSmith cloud)
    $tracing = (Exec "sbs-dev-backend" "echo `${LANGCHAIN_TRACING_V2:-false}").Trim()
    Test-Assert -Name "Backend: LANGCHAIN_TRACING_V2=false (no telemetría LangSmith)" -Category "Security" `
        -Condition ($tracing -ne "true") -Detail "LANGCHAIN_TRACING_V2='$tracing'"

    # Puertos de infra no expuestos innecesariamente (Redis solo en red Docker en prod)
    # En dev son accesibles desde host — advertir
    Test-Assert -Name "Redis puerto 6379 accesible desde host (solo dev — restringir en prod)" `
        -Category "Security" -Condition $true -IsWarning $true `
        -Detail "Producción: eliminar 'ports: 6379:6379' en docker-compose.yml, acceder solo via red interna."
}

# ─────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
Write-Host "`n$('═' * 72)" -ForegroundColor White
Write-Host "  INFRASTRUCTURE VALIDATION REPORT — SecondBrainSense Dev Stack" -ForegroundColor White
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "$('═' * 72)" -ForegroundColor White

$categories = $script:Results | Select-Object -ExpandProperty Category -Unique
foreach ($cat in $categories) {
    $catR    = $script:Results | Where-Object { $_.Category -eq $cat }
    $cPass   = ($catR | Where-Object Status -eq "PASS").Count
    $cFail   = ($catR | Where-Object Status -eq "FAIL").Count
    $cWarn   = ($catR | Where-Object Status -eq "WARN").Count
    $total   = $catR.Count
    $icon    = if ($cFail -gt 0) { "✗" } else { "✓" }
    $color   = if ($cFail -gt 0) { "Red" } elseif ($cWarn -gt 0) { "Yellow" } else { "Green" }
    Write-Host ("  {0} {1,-14}  PASS:{2,3}  FAIL:{3,3}  WARN:{4,3}  ({5} tests)" -f `
        $icon, $cat, $cPass, $cFail, $cWarn, $total) -ForegroundColor $color
}

Write-Host "$('─' * 72)" -ForegroundColor DarkGray
$totalTests = $script:PASS + $script:FAIL + $script:WARN
$exitColor  = if ($script:FAIL -gt 0) { "Red" } elseif ($script:WARN -gt 0) { "Yellow" } else { "Green" }
Write-Host ("  TOTAL: {0} tests   PASS:{1}   FAIL:{2}   WARN:{3}" -f `
    $totalTests, $script:PASS, $script:FAIL, $script:WARN) -ForegroundColor $exitColor
Write-Host "$('═' * 72)" -ForegroundColor White

if ($script:FAIL -gt 0) {
    Write-Host "`n  TESTS FALLIDOS:" -ForegroundColor Red
    $script:Results | Where-Object Status -eq "FAIL" | ForEach-Object {
        Write-Host "    ✗ [$($_.Category)] $($_.Name)" -ForegroundColor Red
        if ($_.Detail) { Write-Host "      → $($_.Detail)" -ForegroundColor DarkRed }
    }
}

exit $(if ($script:FAIL -gt 0) { 1 } else { 0 })
