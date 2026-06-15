# M5 — Backups y Mantenimiento
> Nivel: Admin · Tiempo estimado: 2h  
> Objetivo: proteger los datos y mantener el sistema saludable

---

## 5.1 Qué hay que hacer backup

```
✅ Lo que DEBES guardar:
├── PostgreSQL (documentos, chunks, embeddings, usuarios, chats)
├── Fichero .env (configuración y secrets)
└── Documentos subidos manualmente (si los hay fuera de conectores)

❌ Lo que NO necesitas guardar:
├── La imagen Docker (se descarga de nuevo)
└── Caché de Redis (se regenera sola)
```

Los documentos indexados desde conectores (GitHub, Jira, etc.) se pueden re-indexar si se pierden, pero perderías el historial de chats y la configuración. **El backup de PostgreSQL es lo crítico.**

---

## 5.2 Script de backup diario

Guarda este script como `backup-surfsense.sh` y dale permisos de ejecución:

```bash
#!/bin/bash
# backup-surfsense.sh
# Backup diario de SurfSense — PostgreSQL + configuración

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/surfsense"
CONTAINER="surfsense"
DB_USER="surfsense"
DB_NAME="surfsense"

# Crear directorio de backup si no existe
mkdir -p "$BACKUP_DIR"

echo "[$DATE] Iniciando backup de SurfSense..."

# 1. Backup de PostgreSQL
echo "  → Exportando base de datos..."
docker exec "$CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME" \
  | gzip > "$BACKUP_DIR/db_$DATE.sql.gz"

if [ $? -eq 0 ]; then
  echo "  ✅ DB backup: $BACKUP_DIR/db_$DATE.sql.gz"
else
  echo "  ❌ ERROR en backup de DB"
  exit 1
fi

# 2. Backup del .env
echo "  → Copiando configuración..."
cp .env "$BACKUP_DIR/env_$DATE.bak"
echo "  ✅ Config backup: $BACKUP_DIR/env_$DATE.bak"

# 3. Tamaño del backup
SIZE=$(du -sh "$BACKUP_DIR/db_$DATE.sql.gz" | cut -f1)
echo "  📦 Tamaño: $SIZE"

# 4. Rotar backups (conservar últimos 7 días)
echo "  → Limpiando backups antiguos (> 7 días)..."
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete
find "$BACKUP_DIR" -name "*.bak" -mtime +7 -delete

echo "[$DATE] Backup completado ✅"
```

```bash
# Dar permisos de ejecución
chmod +x backup-surfsense.sh

# Probar manualmente
./backup-surfsense.sh

# Automatizar con cron (ejecutar cada día a las 2:00 AM)
crontab -e
# Añadir esta línea:
# 0 2 * * * /ruta/completa/backup-surfsense.sh >> /var/log/surfsense-backup.log 2>&1
```

---

## 5.3 Restaurar desde backup

```bash
# Parar el contenedor para restaurar con seguridad
docker stop surfsense

# Descomprimir el backup
gunzip /backups/surfsense/db_20260612_020000.sql.gz

# Restaurar la base de datos
docker start surfsense
sleep 30  # esperar que PostgreSQL arranque

docker exec -i surfsense psql -U surfsense -d surfsense \
  < /backups/surfsense/db_20260612_020000.sql

echo "Restauración completada"

# Verificar que los datos están bien
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT count(*) as documentos FROM document;"
```

---

## 5.4 Actualizar SurfSense sin perder datos

```bash
# PASO 1: Hacer backup antes de actualizar (siempre)
./backup-surfsense.sh

# PASO 2: Ver versión actual
docker inspect surfsense | grep "Image"

# PASO 3: Descargar nueva imagen
docker pull ghcr.io/modsetter/surfsense:latest

# PASO 4: Parar y eliminar el contenedor (los datos están en el volumen)
docker stop surfsense
docker rm surfsense

# PASO 5: Arrancar con la nueva imagen apuntando al mismo volumen
docker run -d \
  -p 3000:3000 \
  -p 8000:8000 \
  -p 5133:5133 \
  -v surfsense-data:/data \
  --env-file .env \
  --name surfsense \
  --restart unless-stopped \
  ghcr.io/modsetter/surfsense:latest

# PASO 6: Verificar arranque correcto
sleep 60
curl http://localhost:8000/health
docker exec surfsense supervisorctl status
```

> Las migraciones de base de datos se aplican automáticamente al arrancar. Si hay algún problema, los logs lo mostrarán: `docker logs -f surfsense | head -100`

---

## 5.5 Monitorización sin infraestructura extra

Script de health check simple para ejecutar cada 5 minutos:

```bash
#!/bin/bash
# healthcheck-surfsense.sh

LOG="/var/log/surfsense-health.log"
DATE=$(date '+%Y-%m-%d %H:%M:%S')

# 1. Verificar que el contenedor está corriendo
if ! docker ps | grep -q surfsense; then
  echo "[$DATE] ❌ CONTENEDOR CAÍDO — reiniciando..." >> $LOG
  docker start surfsense
  exit 1
fi

# 2. Verificar que la API responde
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 10 http://localhost:8000/health)

if [ "$HTTP_CODE" != "200" ]; then
  echo "[$DATE] ❌ API NO RESPONDE (HTTP $HTTP_CODE) — reiniciando contenedor..." >> $LOG
  docker restart surfsense
  exit 1
fi

# 3. Verificar uso de disco
DISK=$(df -h /var/lib/docker | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK" -gt 85 ]; then
  echo "[$DATE] ⚠️ DISCO AL ${DISK}% — limpiando imágenes Docker..." >> $LOG
  docker image prune -f >> $LOG 2>&1
fi

# 4. Verificar uso de memoria del contenedor
MEM=$(docker stats surfsense --no-stream --format "{{.MemPerc}}" | tr -d '%')
if [ "${MEM%.*}" -gt 90 ]; then
  echo "[$DATE] ⚠️ MEMORIA AL ${MEM}%" >> $LOG
fi

echo "[$DATE] ✅ OK — API: HTTP $HTTP_CODE, Disco: ${DISK}%, Mem: ${MEM}%" >> $LOG
```

```bash
chmod +x healthcheck-surfsense.sh

# Ejecutar cada 5 minutos
crontab -e
# */5 * * * * /ruta/healthcheck-surfsense.sh
```

---

## 5.6 Limpieza periódica

```bash
# Limpiar documentos huérfanos (sin conector asignado y sin uso)
# Primero revisar qué hay antes de borrar
docker exec surfsense psql -U surfsense -d surfsense \
  -c "SELECT count(*) as huerfanos
      FROM document
      WHERE connector_id IS NULL
        AND created_at < NOW() - INTERVAL '30 days';"

# Limpiar logs de indexación antiguos (> 30 días)
docker exec surfsense psql -U surfsense -d surfsense \
  -c "DELETE FROM indexing_jobs
      WHERE created_at < NOW() - INTERVAL '30 days';"

# Limpiar imágenes Docker no usadas
docker image prune -f

# Ver tamaño total del volumen de datos
docker system df -v | grep surfsense-data
```

---

## Checklist del módulo

- [ ] Script de backup creado y probado manualmente
- [ ] Cron de backup diario configurado
- [ ] Script de health check configurado (cron cada 5 min)
- [ ] Proceso de actualización documentado y probado
- [ ] Directorio de backups con espacio suficiente

---

**Anterior:** [M4 — Tuning del RAG](./M4-tuning-rag.md)  
**Siguiente:** [M6 — Mejoras de Bajo Calado](./M6-mejoras-bajo-calado.md)
