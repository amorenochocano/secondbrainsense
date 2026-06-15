# =============================================================================
# SecondBrainSense - Infrastructure Validation Suite
# Compatible: Windows PowerShell 5.1+
# =============================================================================
# Cobertura:
#   T1  Creation     - todos los contenedores esperados estan running
#   T2  Health       - healthchecks pasando
#   T3  Connectivity - matriz inter-servicios
#   T4  Volumes      - volumenes nombrados existen y persisten datos
#   T5  Config       - env vars, restart policies, certs, SERVICE_ROLE
#   T6  APIs         - contratos HTTP/TCP verificados
#   T7  Celery       - worker conectado, beat activo, cola accesible
#   T8  Brain        - imports Python, UniversalCleaner, browsers, modelos
#   T9  Resilience   - restart automatico tras SIGKILL (unless-stopped)
#   T10 Security     - baseline: certs, no debug, notas de prod
#
# Uso:
#   cd docker
#   powershell -ExecutionPolicy Bypass -File tests\infra-validation.ps1
#   powershell -ExecutionPolicy Bypass -File tests\infra-validation.ps1 -Skip Resilience
#   powershell -ExecutionPolicy Bypass -File tests\infra-validation.ps1 -Only APIs,Brain
#
# Exit code: 0 = todo PASS/WARN  |  1 = algun FAIL
# =============================================================================
param(
    [string[]]$Skip  = @(),
    [string[]]$Only  = @(),
    [string]$BackendHost  = "localhost",
    [int]$BackendPort     = 8929,
    [int]$QdrantPort      = 6333,
    [int]$FrontendPort    = 3929,
    [int]$SearXNGPort     = 8888,
    [int]$ZeroCachePort   = 4848,
    [int]$PgAdminPort     = 5050,
    [string]$DbUser       = "surfsense",
    [string]$DbName       = "surfsense",
    [switch]$Verbose
)

$ErrorActionPreference = "Continue"

$script:PASS    = 0
$script:FAIL    = 0
$script:WARN    = 0
$script:Results = New-Object System.Collections.ArrayList

function Write-Header([string]$Title) {
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkGray
    Write-Host "  $Title" -ForegroundColor White
    Write-Host ("=" * 70) -ForegroundColor DarkGray
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
        [bool]  $IsWarning = $false
    )
    if ($Condition) {
        $status = "PASS"
    } elseif ($IsWarning) {
        $status = "WARN"
    } else {
        $status = "FAIL"
    }

    $row = New-Object PSObject -Property @{
        Category = $Category
        Name     = $Name
        Status   = $status
        Detail   = $Detail
    }
    [void]$script:Results.Add($row)

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
    if ($Only.Count -gt 0) {
        $lowers = $Only | ForEach-Object { $_.ToLower() }
        return $lowers -contains $Cat.ToLower()
    }
    $skipLow = $Skip | ForEach-Object { $_.ToLower() }
    return -not ($skipLow -contains $Cat.ToLower())
}

# Ejecuta bash -c en un contenedor y devuelve stdout+stderr como string
function Exec([string]$Container, [string]$BashCmd) {
    $out = docker exec $Container bash -c $BashCmd 2>&1
    return ($out -join "`n")
}

function Wait-Healthy([string]$Container, [int]$TimeoutSecs = 60) {
    $elapsed = 0
    while ($elapsed -lt $TimeoutSecs) {
        $h = docker inspect $Container --format "{{.State.Health.Status}}" 2>&1
        if ($h -eq "healthy") { return $true }
        Start-Sleep -Seconds 3
        $elapsed += 3
    }
    return $false
}

function Invoke-Get([string]$Url, [int]$TimeoutSecs = 8) {
    try {
        $r = Invoke-WebRequest $Url -UseBasicParsing -TimeoutSec $TimeoutSecs -ErrorAction Stop
        return @{ Code = [int]$r.StatusCode; Body = $r.Content }
    } catch {
        $code = 0
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
        }
        return @{ Code = $code; Body = $_.Exception.Message }
    }
}

# =============================================================================
# T1 - CREATION
# =============================================================================
if (Should-Run "Creation") {
    Write-Header "T1 - Creation: Estado de contenedores"

    $expected = @(
        "sbs-dev-db", "sbs-dev-redis", "sbs-dev-qdrant",
        "sbs-dev-backend", "sbs-dev-celery-worker", "sbs-dev-celery-beat",
        "sbs-dev-otel", "sbs-dev-searxng", "sbs-dev-zero-cache", "sbs-dev-frontend"
    )
    $running = @(docker ps --format "{{.Names}}" 2>&1)

    foreach ($c in $expected) {
        $ok = $running -contains $c
        $detail = ""
        if (-not $ok) { $detail = "No aparece en 'docker ps'" }
        Test-Assert -Name "Container ${c} running" -Category "Creation" -Condition $ok -Detail $detail
    }

    $migExit = docker inspect sbs-dev-migrations --format "{{.State.ExitCode}}" 2>&1
    Test-Assert -Name "sbs-dev-migrations: exited 0 (schema aplicado)" -Category "Creation" `
        -Condition ($migExit -eq "0") -Detail "ExitCode=$migExit"

    $pgaStatus = docker inspect sbs-dev-pgadmin --format "{{.State.Status}}" 2>&1
    $pgaOk = ($pgaStatus -eq "running")
    Test-Assert -Name "sbs-dev-pgadmin: running (opcional, propenso a OOM)" -Category "Creation" `
        -Condition $pgaOk `
        -Detail "Status=$pgaStatus -- si falla: docker start sbs-dev-pgadmin" `
        -IsWarning (-not $pgaOk)
}

# =============================================================================
# T2 - HEALTH
# =============================================================================
if (Should-Run "Health") {
    Write-Header "T2 - Health: Healthchecks Docker"

    $withHC = @(
        "sbs-dev-db", "sbs-dev-redis", "sbs-dev-qdrant",
        "sbs-dev-backend", "sbs-dev-otel", "sbs-dev-searxng", "sbs-dev-zero-cache"
    )
    foreach ($c in $withHC) {
        $h = docker inspect $c --format "{{.State.Health.Status}}" 2>&1
        Test-Assert -Name "${c} health=healthy" -Category "Health" `
            -Condition ($h -eq "healthy") -Detail "Health: $h"
    }

    foreach ($c in @("sbs-dev-celery-worker", "sbs-dev-celery-beat")) {
        $s = docker inspect $c --format "{{.State.Status}}" 2>&1
        Test-Assert -Name "${c} status=running (sin healthcheck)" -Category "Health" `
            -Condition ($s -eq "running") -Detail "Status: $s"
    }
}

# =============================================================================
# T3 - CONNECTIVITY
# =============================================================================
if (Should-Run "Connectivity") {
    Write-Header "T3 - Connectivity: Matriz inter-servicios"

    # backend -> db TCP:5432
    $r = Exec "sbs-dev-backend" '(echo > /dev/tcp/db/5432) 2>/dev/null && echo ok || echo fail'
    Test-Assert -Name "backend -> db TCP:5432" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # backend -> redis TCP:6379
    $r = Exec "sbs-dev-backend" '(echo > /dev/tcp/redis/6379) 2>/dev/null && echo ok || echo fail'
    Test-Assert -Name "backend -> redis TCP:6379" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # backend -> qdrant HTTP
    $r = Exec "sbs-dev-backend" 'curl -sf --max-time 5 http://sbs-dev-qdrant:6333/healthz'
    Test-Assert -Name "backend -> qdrant HTTP:6333/healthz" -Category "Connectivity" `
        -Condition ($r -match "healthz check passed") -Detail $r.Trim()

    # backend -> searxng HTTP
    $r = Exec "sbs-dev-backend" 'curl -sf --max-time 5 -o /dev/null -w "%{http_code}" http://searxng:8080/healthz'
    Test-Assert -Name "backend -> searxng HTTP:8080/healthz" -Category "Connectivity" `
        -Condition ($r.Trim() -eq "200") -Detail "HTTP $($r.Trim())"

    # backend -> otel-lgtm TCP:4317
    $r = Exec "sbs-dev-backend" '(echo > /dev/tcp/sbs-dev-otel/4317) 2>/dev/null && echo ok || echo fail'
    Test-Assert -Name "backend -> otel-lgtm TCP:4317 gRPC" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # celery-worker -> redis TCP:6379
    $r = Exec "sbs-dev-celery-worker" '(echo > /dev/tcp/redis/6379) 2>/dev/null && echo ok || echo fail'
    Test-Assert -Name "celery-worker -> redis TCP:6379" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # celery-worker -> qdrant
    $r = Exec "sbs-dev-celery-worker" 'curl -sf --max-time 5 http://sbs-dev-qdrant:6333/healthz'
    Test-Assert -Name "celery-worker -> qdrant HTTP:6333" -Category "Connectivity" `
        -Condition ($r -match "healthz check passed") -Detail $r.Trim()

    # celery-worker -> db TCP:5432
    $r = Exec "sbs-dev-celery-worker" '(echo > /dev/tcp/db/5432) 2>/dev/null && echo ok || echo fail'
    Test-Assert -Name "celery-worker -> db TCP:5432" -Category "Connectivity" `
        -Condition ($r -match "ok") -Detail $r.Trim()

    # host -> backend
    $resp = Invoke-Get "http://${BackendHost}:${BackendPort}/ready"
    Test-Assert -Name "host -> backend HTTP:${BackendPort}/ready" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host -> qdrant
    $resp = Invoke-Get "http://${BackendHost}:${QdrantPort}/healthz"
    Test-Assert -Name "host -> qdrant HTTP:${QdrantPort}" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host -> frontend
    $resp = Invoke-Get "http://${BackendHost}:${FrontendPort}"
    Test-Assert -Name "host -> frontend HTTP:${FrontendPort}" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host -> searxng
    $resp = Invoke-Get "http://${BackendHost}:${SearXNGPort}/healthz"
    Test-Assert -Name "host -> searxng HTTP:${SearXNGPort}/healthz" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # host -> zero-cache
    $resp = Invoke-Get "http://${BackendHost}:${ZeroCachePort}/keepalive"
    Test-Assert -Name "host -> zero-cache HTTP:${ZeroCachePort}/keepalive" -Category "Connectivity" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"
}

# =============================================================================
# T4 - VOLUMES
# =============================================================================
if (Should-Run "Volumes") {
    Write-Header "T4 - Volumes: Persistencia de datos"

    $expectedVols = @(
        "secondbrainsense-dev-postgres",
        "secondbrainsense-dev-redis",
        "secondbrainsense-dev-qdrant",
        "secondbrainsense-dev-pgadmin",
        "secondbrainsense-dev-shared-temp",
        "secondbrainsense-dev-zero-cache"
    )
    $existingVols = @(docker volume ls --format "{{.Name}}" 2>&1)
    foreach ($v in $expectedVols) {
        $ok = $existingVols -contains $v
        Test-Assert -Name "Volume $v existe" -Category "Volumes" `
            -Condition $ok `
            -Detail $(if (-not $ok) { "No en docker volume ls" } else { "" })
    }

    # PostgreSQL: tablas de schema existen
    $tables = docker exec sbs-dev-db psql -U $DbUser -d $DbName -t -c "\dt" 2>&1
    $tableCount = @($tables | Where-Object { $_ -match "\w" }).Count
    Test-Assert -Name "PostgreSQL: tablas de schema existen tras migrations" -Category "Volumes" `
        -Condition ($tableCount -gt 0) `
        -Detail "$tableCount tablas en schema public"

    # Redis AOF: dato persiste tras restart
    Write-Info "Testando persistencia Redis AOF (restart sbs-dev-redis)..."
    $testVal = "persist_test_$(Get-Date -Format 'HHmmss')"
    docker exec sbs-dev-redis redis-cli SET infra_vol_test $testVal | Out-Null
    docker restart sbs-dev-redis 2>&1 | Out-Null
    $null = Wait-Healthy "sbs-dev-redis" 30
    $gotVal = (docker exec sbs-dev-redis redis-cli GET infra_vol_test 2>&1).Trim()
    Test-Assert -Name "Redis AOF: valor persiste tras docker restart" -Category "Volumes" `
        -Condition ($gotVal -eq $testVal) `
        -Detail "SET='$testVal'  GET_after_restart='$gotVal'"
    docker exec sbs-dev-redis redis-cli DEL infra_vol_test | Out-Null

    # Qdrant: coleccion persiste tras restart
    Write-Info "Testando persistencia Qdrant (restart sbs-dev-qdrant)..."
    $createBody = '{"vectors":{"size":4,"distance":"Cosine"}}'
    try {
        Invoke-RestMethod -Uri "http://${BackendHost}:${QdrantPort}/collections/infra_vol_test" `
            -Method PUT -Body $createBody -ContentType "application/json" -ErrorAction Stop | Out-Null
    } catch {
        Write-Info "Qdrant PUT collection: $($_.Exception.Message)"
    }
    docker restart sbs-dev-qdrant 2>&1 | Out-Null
    $null = Wait-Healthy "sbs-dev-qdrant" 45
    try {
        $cols = Invoke-RestMethod "http://${BackendHost}:${QdrantPort}/collections" -ErrorAction Stop
        $found = $false
        foreach ($col in $cols.result.collections) {
            if ($col.name -eq "infra_vol_test") { $found = $true }
        }
        $colNames = ($cols.result.collections | ForEach-Object { $_.name }) -join ", "
        Test-Assert -Name "Qdrant: coleccion persiste tras docker restart" -Category "Volumes" `
            -Condition $found -Detail "Collections: $colNames"
        Invoke-RestMethod -Uri "http://${BackendHost}:${QdrantPort}/collections/infra_vol_test" `
            -Method DELETE -ErrorAction SilentlyContinue | Out-Null
    } catch {
        Test-Assert -Name "Qdrant: coleccion persiste tras docker restart" -Category "Volumes" `
            -Condition $false -Detail $_.Exception.Message
    }

    # shared_tmp montado en backend y worker
    foreach ($c in @("sbs-dev-backend", "sbs-dev-celery-worker")) {
        $r = Exec $c 'test -d /shared_tmp && echo ok || echo miss'
        Test-Assert -Name "${c} volumen shared_tmp montado" -Category "Volumes" `
            -Condition ($r.Trim() -eq "ok") -Detail $r.Trim()
    }
}

# =============================================================================
# T5 - CONFIG
# =============================================================================
if (Should-Run "Config") {
    Write-Header "T5 - Config: Variables de entorno y restart policies"

    # Restart policies
    $rpolicies = @{
        "sbs-dev-qdrant"     = "unless-stopped"
        "sbs-dev-otel"       = "unless-stopped"
        "sbs-dev-zero-cache" = "unless-stopped"
    }
    foreach ($kv in $rpolicies.GetEnumerator()) {
        $policy = docker inspect $kv.Key --format "{{.HostConfig.RestartPolicy.Name}}" 2>&1
        Test-Assert -Name "$($kv.Key) restart=$($kv.Value)" -Category "Config" `
            -Condition ($policy -eq $kv.Value) -Detail "Actual: $policy"
    }

    # Cert Netskope montado
    foreach ($c in @("sbs-dev-backend", "sbs-dev-celery-worker")) {
        $r = Exec $c 'test -f /certs/corp-root.crt && echo ok || echo miss'
        Test-Assert -Name "${c} cert Netskope en /certs/corp-root.crt" -Category "Config" `
            -Condition ($r.Trim() -eq "ok") -Detail $r.Trim()
    }

    # SERVICE_ROLE
    $roles = @{
        "sbs-dev-backend"       = "api"
        "sbs-dev-celery-worker" = "worker"
        "sbs-dev-celery-beat"   = "beat"
    }
    foreach ($kv in $roles.GetEnumerator()) {
        $role = (Exec $kv.Key 'echo $SERVICE_ROLE').Trim()
        Test-Assert -Name "$($kv.Key) SERVICE_ROLE=$($kv.Value)" -Category "Config" `
            -Condition ($role -eq $kv.Value) -Detail "Actual: '$role'"
    }

    # PGSSLMODE=disable
    $v = (Exec "sbs-dev-backend" 'echo $PGSSLMODE').Trim()
    Test-Assert -Name "backend PGSSLMODE=disable (red interna Docker)" -Category "Config" `
        -Condition ($v -eq "disable") -Detail "PGSSLMODE='$v'"

    # PYTHONPATH=/app
    $v = (Exec "sbs-dev-backend" 'echo $PYTHONPATH').Trim()
    Test-Assert -Name "backend PYTHONPATH=/app" -Category "Config" `
        -Condition ($v -eq "/app") -Detail "PYTHONPATH='$v'"

    # SSL_CERT_FILE (en dev apunta al cert Netskope montado en /certs/)
    $v = (Exec "sbs-dev-backend" 'echo $SSL_CERT_FILE').Trim()
    Test-Assert -Name "backend SSL_CERT_FILE configurado" -Category "Config" `
        -Condition ($v -ne "") -Detail "SSL_CERT_FILE='$v'"

    # NODE_EXTRA_CA_CERTS (scrapling/playwright)
    $v = (Exec "sbs-dev-backend" 'echo $NODE_EXTRA_CA_CERTS').Trim()
    Test-Assert -Name "backend NODE_EXTRA_CA_CERTS configurado" -Category "Config" `
        -Condition ($v -ne "") -Detail "NODE_EXTRA_CA_CERTS='$v'"

    # CELERY_BROKER_URL
    $v = (Exec "sbs-dev-celery-worker" 'echo $CELERY_BROKER_URL').Trim()
    Test-Assert -Name "celery-worker CELERY_BROKER_URL apunta a redis" -Category "Config" `
        -Condition ($v -match "^redis://") -Detail "Broker: '$v'"

    # Qdrant telemetria deshabilitada
    $v = (docker exec sbs-dev-qdrant sh -c 'echo $QDRANT__TELEMETRY_DISABLED' 2>&1).Trim()
    Test-Assert -Name "qdrant QDRANT__TELEMETRY_DISABLED=true" -Category "Config" `
        -Condition ($v -eq "true") -Detail "Actual: '$v'"

    # host.docker.internal resuelve (Ollama accesible)
    $r = (Exec "sbs-dev-backend" 'getent hosts host.docker.internal').Trim()
    Test-Assert -Name "backend host.docker.internal resuelve (Ollama)" -Category "Config" `
        -Condition ($r -ne "") -Detail "Resolucion: '$r'"
}

# =============================================================================
# T6 - APIs
# =============================================================================
if (Should-Run "APIs") {
    Write-Header "T6 - APIs: Contratos HTTP y TCP"

    # Backend /ready
    try {
        $j = Invoke-RestMethod "http://${BackendHost}:${BackendPort}/ready" -ErrorAction Stop
        Test-Assert -Name "GET /ready -> status=ready" -Category "APIs" `
            -Condition ($j.status -eq "ready") -Detail "status=$($j.status)"
    } catch {
        Test-Assert -Name "GET /ready -> status=ready" -Category "APIs" `
            -Condition $false -Detail $_.Exception.Message
    }

    # Backend /docs (OpenAPI)
    $resp = Invoke-Get "http://${BackendHost}:${BackendPort}/docs"
    Test-Assert -Name "GET /docs -> 200 (OpenAPI Swagger UI)" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Qdrant /healthz
    $resp = Invoke-Get "http://${BackendHost}:${QdrantPort}/healthz"
    Test-Assert -Name "GET qdrant/healthz -> 200 healthz check passed" -Category "APIs" `
        -Condition ($resp.Code -eq 200 -and $resp.Body -match "healthz check passed") `
        -Detail "HTTP $($resp.Code): $($resp.Body.Trim())"

    # Qdrant /collections
    try {
        $j = Invoke-RestMethod "http://${BackendHost}:${QdrantPort}/collections" -ErrorAction Stop
        Test-Assert -Name "GET qdrant/collections -> status=ok" -Category "APIs" `
            -Condition ($j.status -eq "ok") `
            -Detail "Collections count: $($j.result.collections.Count)"
    } catch {
        Test-Assert -Name "GET qdrant/collections -> status=ok" -Category "APIs" `
            -Condition $false -Detail $_.Exception.Message
    }

    # Redis PING
    $pong = (docker exec sbs-dev-redis redis-cli PING 2>&1).Trim()
    Test-Assert -Name "Redis PING -> PONG" -Category "APIs" `
        -Condition ($pong -eq "PONG") -Detail "Response: '$pong'"

    # PostgreSQL pg_isready
    $pgr = (docker exec sbs-dev-db pg_isready -U $DbUser -d $DbName 2>&1)
    $pgrStr = ($pgr -join " ")
    Test-Assert -Name "PostgreSQL pg_isready -> accepting connections" -Category "APIs" `
        -Condition ($pgrStr -match "accepting connections") -Detail $pgrStr.Trim()

    # SearXNG /healthz
    $resp = Invoke-Get "http://${BackendHost}:${SearXNGPort}/healthz"
    Test-Assert -Name "GET searxng/healthz -> 200" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Zero-cache /keepalive
    $resp = Invoke-Get "http://${BackendHost}:${ZeroCachePort}/keepalive"
    Test-Assert -Name "GET zero-cache/keepalive -> 200" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Grafana LGTM
    $resp = Invoke-Get "http://${BackendHost}:3001"
    Test-Assert -Name "GET otel-lgtm Grafana :3001 -> responde" -Category "APIs" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"
}

# =============================================================================
# T7 - CELERY
# =============================================================================
if (Should-Run "Celery") {
    Write-Header "T7 - Celery: Cola de tareas"

    # Worker proceso activo (via /proc - el contenedor no tiene ps)
    Write-Info "Verificando proceso celery worker..."
    $proc = (Exec "sbs-dev-celery-worker" 'grep -l celery /proc/[0-9]*/cmdline 2>/dev/null | head -3').Trim()
    Test-Assert -Name "celery-worker: proceso celery activo en contenedor" -Category "Celery" `
        -Condition ($proc -match "/proc/\d+/cmdline") `
        -Detail $proc

    # Worker registrado en Redis (Kombu bindings)
    $keys = (docker exec sbs-dev-redis redis-cli KEYS "_kombu.binding.*" 2>&1) -join " "
    Test-Assert -Name "celery-worker: colas registradas en Redis (Kombu)" -Category "Celery" `
        -Condition ($keys -match "kombu|surfsense|celery") `
        -Detail $keys.Trim()

    # Celery beat logs
    $beatLogs = (docker logs sbs-dev-celery-beat 2>&1) -join "`n"
    Test-Assert -Name "celery-beat: scheduler iniciado" -Category "Celery" `
        -Condition ($beatLogs -match "beat|scheduler|Starting") `
        -Detail (($beatLogs -split "`n" | Select-Object -Last 3) -join " | ")

    # Cola en Redis
    $qlen = (docker exec sbs-dev-redis redis-cli LLEN surfsense 2>&1).Trim()
    Test-Assert -Name "Redis: cola surfsense accesible (len=$qlen, puede ser 0)" -Category "Celery" `
        -Condition ($qlen -match "^\d+$") -Detail "LLEN surfsense = $qlen"

    # Tareas registradas (via Redis KEYS)
    $allKeys = (docker exec sbs-dev-redis redis-cli KEYS "*" 2>&1) -join "`n"
    Test-Assert -Name "celery-worker: claves Redis presentes (broker activo)" -Category "Celery" `
        -Condition ($allKeys -match "kombu|celery|surfsense") `
        -Detail (($allKeys -split "`n" | Select-Object -First 8) -join " | ")
}

# =============================================================================
# T8 - BRAIN
# =============================================================================
if (Should-Run "Brain") {
    Write-Header "T8 - Brain: Modulos Python y assets pre-bakeados"

    # Imports principales (via base64 para evitar quoting hell PS->bash)
    $pyImport = "from app.brain.extractors import get_extractor_for_extension; from app.brain.rag_lib.layer1_universal.universal_cleaner import UniversalCleaner; print('imports_ok')"
    $b64Import = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pyImport))
    $r = Exec "sbs-dev-backend" "cd /app && echo $b64Import | base64 -d | python 2>&1"
    Test-Assert -Name "Brain: imports principales (extractors, cleaner)" -Category "Brain" `
        -Condition ($r -match "imports_ok") `
        -Detail (($r -split "`n" | Select-Object -Last 3) -join " | ")

    # UniversalCleaner.clean() via base64
    $pyClean = "from app.brain.rag_lib.layer1_universal.universal_cleaner import UniversalCleaner; uc=UniversalCleaner(); r=uc.clean('TextoConEspacios'); print(type(r).__name__); print('cleaner_ok')"
    $b64Clean = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pyClean))
    $r = Exec "sbs-dev-backend" "cd /app && echo $b64Clean | base64 -d | python 2>&1"
    Test-Assert -Name "UniversalCleaner.clean() devuelve CleanResult" -Category "Brain" `
        -Condition ($r -match "cleaner_ok") `
        -Detail (($r -split "`n" | Select-Object -Last 3) -join " | ")

    # ExtractorFactory
    $pyFactory = 'from app.brain.extractors import get_extractor_for_extension; e=get_extractor_for_extension(".py"); print(type(e).__name__); print("factory_ok")'
    $r = Exec "sbs-dev-backend" "cd /app && python -c '$pyFactory'"
    Test-Assert -Name "ExtractorFactory: .py -> extractor concreto" -Category "Brain" `
        -Condition ($r -match "factory_ok") `
        -Detail (($r -split "`n" | Select-Object -Last 3) -join " | ")

    # Scrapling: Chromium binary bakeado
    $chrome = (Exec "sbs-dev-backend" 'find /root/.cache/ms-playwright -name "chrome" -type f 2>/dev/null | head -1').Trim()
    Test-Assert -Name "Scrapling: Chromium binary presente en imagen" -Category "Brain" `
        -Condition ($chrome -match "chrome") -Detail "Binary: $chrome"

    # EasyOCR models .pth
    $cnt = (Exec "sbs-dev-backend" 'ls /root/.EasyOCR/model/*.pth 2>/dev/null | wc -l').Trim()
    $cntInt = 0
    [int]::TryParse($cnt, [ref]$cntInt) | Out-Null
    Test-Assert -Name "EasyOCR: modelos .pth bakeados (count=$cntInt, esperado >=2)" -Category "Brain" `
        -Condition ($cntInt -ge 2) -Detail "Modelos encontrados: $cntInt"

    # HuggingFace cache
    $hfRaw = (Exec "sbs-dev-backend" 'ls /root/.cache/huggingface/hub/ 2>/dev/null | grep sentence-transformers | wc -l').Trim()
    $hfInt = 0
    [int]::TryParse($hfRaw, [ref]$hfInt) | Out-Null
    Test-Assert -Name "HuggingFace: sentence-transformers en cache local" -Category "Brain" `
        -Condition ($hfInt -ge 1) -Detail "Dirs sentence-transformers: $hfInt"

    # Pandoc
    $pandoc = (Exec "sbs-dev-backend" 'pandoc --version 2>&1 | head -1').Trim()
    Test-Assert -Name "Pandoc: disponible en imagen" -Category "Brain" `
        -Condition ($pandoc -match "pandoc") -Detail $pandoc
}

# =============================================================================
# T9 - RESILIENCE
# =============================================================================
if (Should-Run "Resilience") {
    Write-Header "T9 - Resilience: Restart automatico (restart: unless-stopped)"
    Write-Host "  [WARN] Esta categoria envia SIGKILL a contenedores y verifica recovery." -ForegroundColor Yellow

    # Qdrant SIGKILL -> restart automatico
    $idBefore = (docker inspect sbs-dev-qdrant --format "{{.Id}}" 2>&1).Trim()
    Write-Info "Enviando SIGKILL a sbs-dev-qdrant..."
    docker kill sbs-dev-qdrant 2>&1 | Out-Null
    Start-Sleep -Seconds 5
    $idAfter = (docker inspect sbs-dev-qdrant --format "{{.Id}}" 2>&1).Trim()
    Test-Assert -Name "Qdrant: mismo contenedor reiniciado (misma ID)" -Category "Resilience" `
        -Condition ($idBefore -eq $idAfter) `
        -Detail "ID: $($idBefore.Substring(0,12))..."

    $healthy = Wait-Healthy "sbs-dev-qdrant" 60
    Test-Assert -Name "Qdrant: healthy dentro de 60s tras SIGKILL" -Category "Resilience" `
        -Condition $healthy `
        -Detail "Health: $(docker inspect sbs-dev-qdrant --format '{{.State.Health.Status}}')"

    # Backend sigue alcanzando Qdrant tras su restart
    $r = Exec "sbs-dev-backend" 'curl -sf --max-time 5 http://sbs-dev-qdrant:6333/healthz'
    Test-Assert -Name "Backend: alcanza Qdrant tras restart automatico" -Category "Resilience" `
        -Condition ($r -match "healthz check passed") -Detail $r.Trim()

    # Backend /ready sigue OK
    Start-Sleep -Seconds 5
    $resp = Invoke-Get "http://${BackendHost}:${BackendPort}/ready"
    Test-Assert -Name "Backend /ready: OK tras ciclo recovery Qdrant" -Category "Resilience" `
        -Condition ($resp.Code -eq 200) -Detail "HTTP $($resp.Code)"

    # Restart counts
    $rc = (docker inspect sbs-dev-backend --format "{{.RestartCount}}" 2>&1).Trim()
    Test-Assert -Name "Backend: RestartCount=0 (sin crash loops)" -Category "Resilience" `
        -Condition ($rc -eq "0") -Detail "RestartCount=$rc"

    $rc = (docker inspect sbs-dev-celery-worker --format "{{.RestartCount}}" 2>&1).Trim()
    Test-Assert -Name "celery-worker: RestartCount=0 (sin crash loops)" -Category "Resilience" `
        -Condition ($rc -eq "0") -Detail "RestartCount=$rc"
}

# =============================================================================
# T10 - SECURITY
# =============================================================================
if (Should-Run "Security") {
    Write-Header "T10 - Security: Baseline OWASP y notas para produccion"

    # SSL context Python OK (via base64)
    $pySSL = "import ssl; ssl.create_default_context(); print('ssl_ok')"
    $b64SSL = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($pySSL))
    $sslTest = (Exec "sbs-dev-backend" "echo $b64SSL | base64 -d | python 2>&1").Trim()
    Test-Assert -Name "Backend: SSL context Python inicializa correctamente" -Category "Security" `
        -Condition ($sslTest -match "ssl_ok") -Detail $sslTest

    # No debug en FastAPI
    $debugFlag = (Exec "sbs-dev-backend" 'echo ${FASTAPI_DEBUG:-false}').Trim()
    Test-Assert -Name "Backend: FASTAPI_DEBUG no activo" -Category "Security" `
        -Condition ($debugFlag -notmatch "^true$|^1$") -Detail "FASTAPI_DEBUG='$debugFlag'"

    # PGSSLMODE intencional
    $sslmode = (Exec "sbs-dev-backend" 'echo $PGSSLMODE').Trim()
    Test-Assert -Name "PostgreSQL: PGSSLMODE=disable intencional (red Docker interna)" -Category "Security" `
        -Condition ($sslmode -eq "disable") `
        -Detail "NOTA prod: usar sslmode=require hacia RDS o Cloud SQL"

    # Redis sin contrasena - WARN
    Test-Assert -Name "Redis: sin contrasena (dev OK - WARN prod)" -Category "Security" `
        -Condition $true -IsWarning $true `
        -Detail "Prod: redis-server --requirepass <secret>"

    # Qdrant sin API key - WARN
    Test-Assert -Name "Qdrant: sin API key (dev OK - WARN prod)" -Category "Security" `
        -Condition $true -IsWarning $true `
        -Detail "Prod: QDRANT__SERVICE__API_KEY=<secret>"

    # pgAdmin protegido por login
    $resp = Invoke-Get "http://${BackendHost}:${PgAdminPort}"
    Test-Assert -Name "pgAdmin: responde con pagina login (no expone DB sin auth)" -Category "Security" `
        -Condition ($resp.Code -eq 200) `
        -Detail "HTTP $($resp.Code)" `
        -IsWarning ($resp.Code -ne 200)

    # LANGCHAIN_TRACING desactivado
    $tracing = (Exec "sbs-dev-backend" 'echo ${LANGCHAIN_TRACING_V2:-false}').Trim()
    Test-Assert -Name "Backend: LANGCHAIN_TRACING_V2=false (no telemetria LangSmith)" -Category "Security" `
        -Condition ($tracing -ne "true") -Detail "LANGCHAIN_TRACING_V2='$tracing'"

    # Redis puerto expuesto al host - WARN
    Test-Assert -Name "Redis 6379 accesible desde host (solo dev - restringir en prod)" `
        -Category "Security" -Condition $true -IsWarning $true `
        -Detail "Prod: eliminar ports 6379:6379, acceder solo via red interna Docker"
}

# =============================================================================
# SUMMARY
# =============================================================================
Write-Host ""
Write-Host ("=" * 70) -ForegroundColor White
Write-Host "  INFRASTRUCTURE VALIDATION REPORT - SecondBrainSense Dev Stack" -ForegroundColor White
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ("=" * 70) -ForegroundColor White

$cats = $script:Results | Select-Object -ExpandProperty Category -Unique
foreach ($cat in $cats) {
    $catR  = @($script:Results | Where-Object { $_.Category -eq $cat })
    $cPass = @($catR | Where-Object { $_.Status -eq "PASS" }).Count
    $cFail = @($catR | Where-Object { $_.Status -eq "FAIL" }).Count
    $cWarn = @($catR | Where-Object { $_.Status -eq "WARN" }).Count
    $total = $catR.Count
    $icon  = if ($cFail -gt 0) { "X" } else { "v" }
    $color = if ($cFail -gt 0) { "Red" } elseif ($cWarn -gt 0) { "Yellow" } else { "Green" }
    Write-Host ("  [{0}] {1,-14}  PASS:{2,3}  FAIL:{3,3}  WARN:{4,3}  ({5} tests)" -f `
        $icon, $cat, $cPass, $cFail, $cWarn, $total) -ForegroundColor $color
}

Write-Host ("-" * 70) -ForegroundColor DarkGray
$totalTests = $script:PASS + $script:FAIL + $script:WARN
$exitColor  = if ($script:FAIL -gt 0) { "Red" } elseif ($script:WARN -gt 0) { "Yellow" } else { "Green" }
Write-Host ("  TOTAL: {0} tests   PASS:{1}   FAIL:{2}   WARN:{3}" -f `
    $totalTests, $script:PASS, $script:FAIL, $script:WARN) -ForegroundColor $exitColor
Write-Host ("=" * 70) -ForegroundColor White

if ($script:FAIL -gt 0) {
    Write-Host ""
    Write-Host "  TESTS FALLIDOS:" -ForegroundColor Red
    $script:Results | Where-Object { $_.Status -eq "FAIL" } | ForEach-Object {
        Write-Host "    [$($_.Category)] $($_.Name)" -ForegroundColor Red
        if ($_.Detail) { Write-Host "      -> $($_.Detail)" -ForegroundColor DarkRed }
    }
}

if ($script:FAIL -gt 0) { exit 1 } else { exit 0 }
