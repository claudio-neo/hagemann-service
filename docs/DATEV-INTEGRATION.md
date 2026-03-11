# DATEV Integration — Hagemann

**Versión:** 1.0  
**Última actualización:** 2026-03-11  
**Estado:** Implementado (modo sandbox activo — credenciales pendientes)

---

## Índice

1. [Resumen](#1-resumen)
2. [Flujo OAuth 2.0 con DATEVconnect](#2-flujo-oauth-20-con-datevconnect)
3. [URLs de la API DATEV](#3-urls-de-la-api-datev)
4. [Mapeo de Campos](#4-mapeo-de-campos)
5. [Formato CSV Alternativo](#5-formato-csv-alternativo)
6. [Modo Sandbox](#6-modo-sandbox)
7. [Pasos para Activar en Producción](#7-pasos-para-activar-en-producción)
8. [Diagrama de Secuencia — Flujo de Exportación](#8-diagrama-de-secuencia--flujo-de-exportación)
9. [Endpoints Disponibles](#9-endpoints-disponibles)
10. [Configuración: Beraternummer y Mandantennummer](#10-configuración-beraternummer-y-mandantennummer)
11. [Códigos de Error DATEV Comunes](#11-códigos-de-error-datev-comunes)
12. [Variables de Entorno](#12-variables-de-entorno)

---

## 1. Resumen

La integración DATEV permite exportar los datos de horas y nómina de Hagemann
al software **DATEV Lohn & Gehalt** que usa el asesor fiscal.

### Dos modos de exportación

| Modo | Descripción | Requiere credenciales |
|------|-------------|----------------------|
| **API (DATEVconnect)** | Envío directo via REST + OAuth 2.0 | Sí |
| **CSV offline** | Descarga local, importación manual en DATEV | No |

### Estado actual

- ✅ Estructura completa implementada
- ✅ Modo sandbox activo (`DATEV_SANDBOX=true`)
- ✅ CSV offline disponible (sin credenciales)
- ⏳ Credenciales OAuth pendientes de recibir del asesor fiscal

---

## 2. Flujo OAuth 2.0 con DATEVconnect

DATEV usa **OAuth 2.0 Authorization Code Flow** con PKCE.

### Registro de la aplicación

1. El asesor fiscal crea una aplicación en el portal [developer.datev.de](https://developer.datev.de)
2. Obtiene: `client_id` y `client_secret`
3. Registra el `redirect_uri` (callback de nuestra app)
4. Nos facilita los datos via `POST /api/v1/datev/config`

### Flujo de autorización paso a paso

```
1. Admin visita GET /api/v1/datev/oauth/authorize
   → Recibe la authorization_url de DATEV

2. Admin abre la URL en su navegador
   → Login en portal DATEV con sus credenciales

3. DATEV redirige a nuestro callback:
   GET /api/v1/datev/oauth/callback?code=XXXX&state=YYYY

4. El backend intercambia el código por tokens:
   POST https://login.datev.de/openiddict/token
   → Recibe: access_token (1h), refresh_token (larga duración)

5. Los tokens se guardan en datev_config
   → Futuras exportaciones usan el access_token automáticamente

6. El access_token se renueva automáticamente cuando queda < 5 min
   → Usando el refresh_token sin intervención del usuario
```

### Scopes requeridos

```
openid profile datev:payroll:read datev:payroll:write
```

---

## 3. URLs de la API DATEV

| Endpoint | URL |
|----------|-----|
| **Authorization** | `https://login.datev.de/openiddict/authorize` |
| **Token** | `https://login.datev.de/openiddict/token` |
| **API Base** | `https://api.datev.de/marketplace` |
| **Payroll endpoint** | `https://api.datev.de/marketplace/v1/payroll/lohngehalt` |
| **Developer portal** | `https://developer.datev.de` |
| **Documentación oficial** | `https://apps.datev.de/help-center/documents/1080181` |

---

## 4. Mapeo de Campos

### Nuestro modelo → DATEV Lohn & Gehalt

| Campo DATEV | Fuente en Hagemann | Notas |
|-------------|-------------------|-------|
| `PersonalnummerArbeitnehmer` | `empleados.id_nummer` | Número de personal (Personalnummer) |
| `NachnameMitarbeiter` | `empleados.apellido` | Apellido del empleado |
| `VornameMitarbeiter` | `empleados.nombre` | Nombre del empleado |
| `Abrechnungszeitraum` | `{year}{month:02d}` | Periodo contable en formato YYYYMM |
| `Normalstunden` | `saldo_horas_mensual.horas_reales` | Horas efectivamente trabajadas |
| `Überstunden` | `max(0, horas_reales - horas_planificadas)` | Horas extra sobre el contrato |
| `Krankheitstage` | `sum(solicitudes_vacaciones WHERE tipo=BAJA_MEDICA)` | Días de baja médica en el mes |
| `Urlaubstage` | `sum(solicitudes_vacaciones WHERE tipo=VACACIONES)` | Días de vacaciones tomados |
| `Zeitkonto_Saldo` | `saldo_horas_mensual.saldo_final` | Saldo acumulado de horas (puede ser negativo) |
| `Kostenstelle` | `centros_coste.codigo` (más usado por segmentos) | Centro de coste principal del empleado |
| `BeraternummerDatev` | `datev_config.consultant_number` | Beraternummer del asesor DATEV |
| `MandantennummerDatev` | `datev_config.client_number` | Mandantennummer de la empresa |

### Ejemplo de payload completo

```json
{
  "BeraternummerDatev": "1234567890",
  "MandantennummerDatev": "12345",
  "Unternehmensname": "Hagemann GmbH",
  "Abrechnungszeitraum": "202603",
  "Arbeitnehmer": [
    {
      "PersonalnummerArbeitnehmer": "101",
      "NachnameMitarbeiter": "Müller",
      "VornameMitarbeiter": "Klaus",
      "Abrechnungszeitraum": "202603",
      "Normalstunden": 168.50,
      "Überstunden": 8.50,
      "Krankheitstage": 2,
      "Urlaubstage": 0,
      "Zeitkonto_Saldo": 8.50,
      "Kostenstelle": "4100"
    }
  ],
  "metadata": {
    "total_empleados": 1,
    "generado_en": "2026-03-11T09:00:00",
    "payroll_type": "Lohn"
  }
}
```

---

## 5. Formato CSV Alternativo

El CSV sigue el formato **DTVF (DATEV-Format)** para importación manual.

### Especificaciones técnicas

| Parámetro | Valor |
|-----------|-------|
| Separador de campos | `;` (punto y coma) |
| Separador decimal | `,` (coma, convención alemana) |
| Fin de línea | `\r\n` (CRLF) |
| Codificación | `UTF-8-BOM` (requerido para caracteres alemanes: Ü, ö, ß) |
| Primera fila | Cabeceras |

### Cabeceras del CSV

```
PersonalnummerArbeitnehmer;NachnameMitarbeiter;VornameMitarbeiter;
Abrechnungszeitraum;Normalstunden;Überstunden;Krankheitstage;
Urlaubstage;Zeitkonto_Saldo;Kostenstelle;BeraternummerDatev;MandantennummerDatev
```

### Ejemplo

```csv
PersonalnummerArbeitnehmer;NachnameMitarbeiter;VornameMitarbeiter;Abrechnungszeitraum;Normalstunden;Überstunden;Krankheitstage;Urlaubstage;Zeitkonto_Saldo;Kostenstelle;BeraternummerDatev;MandantennummerDatev
101;Müller;Klaus;202603;168,50;8,50;2;0;8,50;4100;1234567890;12345
102;Schmidt;Anna;202603;160,00;0,00;0;5;-5,00;4200;1234567890;12345
```

### Importación manual en DATEV

1. En DATEV Lohn & Gehalt: `Extras → Datenaustausch → DATEV-Format importieren`
2. Seleccionar el archivo `.csv` descargado
3. Revisar el mapeo de columnas
4. Confirmar importación

---

## 6. Modo Sandbox

Mientras no estén disponibles las credenciales reales, toda la integración opera en **modo sandbox**.

### Comportamiento en sandbox

| Operación | Comportamiento sandbox |
|-----------|----------------------|
| `GET /datev/status` | `sandbox: true`, `status: "ready"` |
| `POST /datev/config` | Guarda los datos (sin validación contra DATEV) |
| `GET /datev/oauth/authorize` | Genera URL real de DATEV (sin usar client_id real) |
| `GET /datev/oauth/callback` | Genera tokens ficticios `sandbox_access_XXXXX` |
| `POST /datev/export` dry_run=true | Devuelve payload real (sin enviar) |
| `POST /datev/export` dry_run=false | Simula envío, guarda log con `import_id: "SANDBOX-XXXX"` |
| `POST /datev/export/csv` | **Funciona normalmente** — genera CSV real |

### Activar/desactivar sandbox

```bash
# Activar sandbox (por defecto)
DATEV_SANDBOX=true

# Desactivar sandbox (producción con credenciales reales)
DATEV_SANDBOX=false
```

El sandbox también se activa automáticamente si no hay `client_id` configurado,
independientemente de la variable de entorno.

---

## 7. Pasos para Activar en Producción

Cuando lleguen las credenciales del asesor fiscal:

### Paso 1: Configurar credenciales

```bash
curl -X POST http://localhost:8013/api/v1/datev/config \
  -H "Content-Type: application/json" \
  -d '{
    "consultant_number": "1234567890",
    "client_number": "12345",
    "company_name": "Hagemann GmbH",
    "fiscal_year_start": "2026-01-01",
    "client_id": "TU_CLIENT_ID_DATEV",
    "client_secret": "TU_CLIENT_SECRET_DATEV",
    "payroll_type": "Lohn"
  }'
```

### Paso 2: Desactivar sandbox

En `docker-compose.yml` o `.env`:

```yaml
environment:
  - DATEV_SANDBOX=false
  - DATEV_REDIRECT_URI=https://tu-dominio.com/api/v1/datev/oauth/callback
```

### Paso 3: Autorizar la conexión OAuth

```bash
# Obtener la URL de autorización
curl http://localhost:8013/api/v1/datev/oauth/authorize

# → Visitar la authorization_url en el navegador
# → Login con credenciales DATEV del asesor
# → DATEV redirige al callback automáticamente
```

### Paso 4: Verificar estado

```bash
curl http://localhost:8013/api/v1/datev/status
# → "status": "ready", "token_valid": true
```

### Paso 5: Primera exportación (dry_run)

```bash
curl -X POST http://localhost:8013/api/v1/datev/export \
  -H "Content-Type: application/json" \
  -d '{"year": 2026, "month": 3, "dry_run": true}'
# → Revisar el payload antes de enviar
```

### Paso 6: Exportación real

```bash
curl -X POST http://localhost:8013/api/v1/datev/export \
  -H "Content-Type: application/json" \
  -d '{"year": 2026, "month": 3, "dry_run": false, "exported_by": "admin"}'
```

---

## 8. Diagrama de Secuencia — Flujo de Exportación

### Flujo OAuth (una sola vez)

```
Admin                    Hagemann API              DATEV
  │                           │                      │
  │── GET /datev/oauth/authorize ──>│                  │
  │<── {authorization_url} ────│                      │
  │                           │                      │
  │── [abre URL en navegador] ──────────────────────>│
  │                           │                      │
  │                           │<── redirect?code=XXX ─│
  │                           │                      │
  │                           │── POST /token ───────>│
  │                           │<── {access_token,     │
  │                           │     refresh_token}    │
  │                           │                      │
  │                           │[guarda tokens en DB]  │
  │<── {status: "ok"} ────────│                      │
```

### Flujo de exportación mensual

```
Admin/Sistema            Hagemann API              DATEV API
  │                           │                      │
  │── POST /datev/export ─────>│                      │
  │   {year, month,           │                      │
  │    dry_run: false}        │                      │
  │                           │                      │
  │                    [verificar token]              │
  │                    [si expira < 5min → refresh]   │
  │                           │                      │
  │                    [consultar saldos_horas]       │
  │                    [mapear a formato DATEV]       │
  │                           │                      │
  │                           │── POST /lohngehalt ──>│
  │                           │<── {importId: "XXX"} ─│
  │                           │                      │
  │                    [guardar en datev_export_log]  │
  │                           │                      │
  │<── {status: "success",    │                      │
  │     import_id: "XXX",     │                      │
  │     records_sent: N} ─────│                      │
```

### Flujo de exportación CSV (offline)

```
Admin                    Hagemann API
  │                           │
  │── POST /datev/export/csv ─>│
  │   {year, month}           │
  │                           │
  │                    [consultar saldos_horas]
  │                    [generar CSV UTF-8-BOM]
  │                           │
  │<── [descarga CSV] ─────────│
  │                           │
  [importar manualmente en DATEV Lohn & Gehalt]
```

---

## 9. Endpoints Disponibles

| Método | Endpoint | Descripción |
|--------|----------|-------------|
| `GET` | `/api/v1/datev/status` | Estado de la integración |
| `GET` | `/api/v1/datev/config` | Ver configuración (sin secret) |
| `POST` | `/api/v1/datev/config` | Crear/actualizar configuración |
| `GET` | `/api/v1/datev/oauth/authorize` | Obtener URL OAuth |
| `GET` | `/api/v1/datev/oauth/callback` | Callback OAuth (intercambiar código) |
| `POST` | `/api/v1/datev/export` | Exportar (dry_run o real) |
| `GET` | `/api/v1/datev/export/history` | Historial de exportaciones |
| `POST` | `/api/v1/datev/export/csv` | Descargar CSV offline |

Ver documentación interactiva completa en: `http://localhost:8013/docs#/DATEV`

---

## 10. Configuración: Beraternummer y Mandantennummer

### Beraternummer (Número de Asesor)

- **Qué es:** Identificador único del asesor fiscal/contable en DATEV
- **Formato:** 10 dígitos numéricos (ej: `1234567890`)
- **Quién lo tiene:** El asesor fiscal (Steuerberater) de Hagemann
- **Dónde se usa:** En cada exportación para identificar al asesor destinatario

### Mandantennummer (Número de Mandante)

- **Qué es:** Identificador de la empresa cliente dentro del sistema del asesor
- **Formato:** Número de 1 a 99999 (ej: `12345`)
- **Quién lo asigna:** El asesor fiscal lo asigna a cada cliente suyo
- **Dónde se usa:** Para que DATEV identifique a qué empresa pertenecen los datos

### Obtención de los datos

Solicitar al asesor fiscal:
1. Su `Beraternummer` (número de asesor DATEV)
2. El `Mandantennummer` asignado a Hagemann
3. El `client_id` y `client_secret` de la App DATEV (requiere que el asesor cree la App en developer.datev.de)

---

## 11. Códigos de Error DATEV Comunes

| Código HTTP | Código DATEV | Descripción | Solución |
|-------------|-------------|-------------|----------|
| `401` | `UNAUTHORIZED` | Token expirado o inválido | Re-autorizar via OAuth |
| `403` | `FORBIDDEN` | Sin permisos suficientes | Verificar scopes de la App DATEV |
| `400` | `INVALID_MANDANT` | Mandantennummer incorrecto | Verificar con el asesor |
| `400` | `INVALID_BERATER` | Beraternummer incorrecto | Verificar con el asesor |
| `400` | `DUPLICATE_IMPORT` | Ya se importó este periodo | Usar la opción de reimportación DATEV |
| `422` | `INVALID_PERSONALNUMMER` | Personalnummer no encontrado en DATEV | Sincronizar empleados primero |
| `429` | `RATE_LIMITED` | Demasiadas peticiones | Esperar y reintentar |
| `500` | `DATEV_INTERNAL` | Error interno DATEV | Reintentar en 30 min |

### Errores de la integración local

| Situación | Mensaje | Acción |
|-----------|---------|--------|
| Sin configuración | `"No hay configuración DATEV"` | `POST /datev/config` |
| Sin credenciales OAuth | `"client_id no configurado"` | Añadir credenciales |
| Token expirado | `"Token expirado"` | `GET /datev/oauth/authorize` |
| Sin datos del mes | Payload con `Arbeitnehmer: []` | Verificar que existan saldos mensuales |

---

## 12. Variables de Entorno

| Variable | Valor por defecto | Descripción |
|----------|------------------|-------------|
| `DATEV_SANDBOX` | `true` | `true` = modo sandbox, no envía datos reales |
| `DATEV_REDIRECT_URI` | `http://localhost:8013/api/v1/datev/oauth/callback` | URI de callback OAuth (cambiar en producción) |

### Configuración en docker-compose.yml

```yaml
hagemann-service:
  environment:
    # Sandbox (cambiar a false en producción)
    - DATEV_SANDBOX=true
    # Redirect URI para OAuth (ajustar al dominio real en producción)
    - DATEV_REDIRECT_URI=https://hagemann.tu-dominio.com/api/v1/datev/oauth/callback
```

---

## Notas de Implementación

### Seguridad

- `client_secret` se almacena en texto plano en la base de datos actual.  
  **Para producción:** encriptar con `cryptography.fernet` o usar un vault de secretos.
- Los tokens OAuth se almacenan en la tabla `datev_config`.  
  Acceso restringido a admins.
- El endpoint `GET /datev/config` **nunca devuelve** `client_secret`.

### Renovación automática de tokens

El servicio verifica la expiración del token en cada exportación. Si queda
menos de 5 minutos para expirar, lo renueva automáticamente usando el
`refresh_token` sin intervención del usuario.

### Backup local

Cada exportación exitosa puede guardar el CSV localmente (`file_path` en el log).
Recomendado para auditoría. Implementar limpieza periódica de archivos antiguos.
