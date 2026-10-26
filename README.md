# 🤖 Asistente Personal Inteligente

Asistente personal conversacional desarrollado en Python, accesible a través de Telegram y diseñado para centralizar tareas cotidianas en una única herramienta. Integra un modelo de lenguaje con herramientas especializadas, persistencia de datos y confirmaciones explícitas para operaciones sensibles.

El proyecto combina **IA local, automatización, gestión personal y finanzas**, con una arquitectura modular preparada para ampliar funcionalidades y canales de comunicación.

## ✨ Funcionalidades

### 💬 Conversación e inteligencia artificial
- Conversación mediante un modelo de lenguaje con uso de herramientas (*function calling*).
- Arquitectura desacoplada del proveedor de IA.
- Integración con Telegram y soporte para ejecución con modelos locales mediante Ollama.
- Contexto conversacional e historial persistente.
- Memoria basada en reglas que requieren confirmación antes de activarse.

### 📅 Agenda y organización personal
- Gestión de eventos, citas y fechas importantes.
- Creación y seguimiento de tareas con vencimiento.
- Recordatorios persistentes y envío automático por Telegram.
- Interpretación de fechas en lenguaje natural.
- Eventos y tareas recurrentes.

### 💰 Gestión financiera
- Cuentas bancarias, efectivo y tarjetas de crédito.
- Registro de gastos e ingresos con clasificación por categorías.
- Transferencias entre cuentas.
- Cálculo de patrimonio, liquidez y deuda de tarjetas.
- Resúmenes de gastos por categoría y comercio.
- Presupuestos mensuales con estados de seguimiento.
- Objetivos de ahorro y registro de contribuciones.
- Recurrencias financieras, pagos planificados y suscripciones.

### 🔐 Seguridad y consistencia
- Acceso al bot de Telegram restringido mediante una lista de usuarios autorizados.
- Separación entre lógica de conversación, dominio y persistencia.
- Aislamiento de los datos por usuario.
- Validación de entradas mediante Pydantic.
- Confirmación explícita para operaciones sensibles.
- Migraciones de base de datos con Alembic.
- Registro estructurado con redacción de información sensible.

## 🛠️ Tecnologías

- **Python 3.12+**
- **PostgreSQL 16**
- **SQLAlchemy async y Alembic**
- **Pydantic y pydantic-settings**
- **Telegram Bot API**
- **Ollama** para ejecutar modelos de lenguaje localmente
- **Docker y Docker Compose**
- **pytest, Ruff y mypy**

## 🏗️ Arquitectura

El proyecto está organizado por capas para separar responsabilidades y facilitar su mantenimiento.

```text
src/asistente/
├── adapters/       # Integraciones con canales
├── ai/             # Cliente de IA y prompts
├── api/            # API web y endpoints
├── application/    # Orquestación y casos de uso
├── domain/         # Reglas de negocio
├── persistence/    # Base de datos y repositorios
├── scheduler/      # Recordatorios y tareas programadas
├── shared/         # Utilidades comunes
└── tools/          # Herramientas disponibles para la IA

migrations/         # Migraciones de base de datos
tests/              # Pruebas unitarias e integración
```

La capa de IA no accede directamente a la base de datos: las operaciones se realizan mediante herramientas, servicios de dominio y repositorios.

## 🚀 Puesta en marcha

### Requisitos

- Python 3.12 o superior.
- Docker Desktop con Docker Compose.
- Un bot de Telegram creado mediante [@BotFather](https://t.me/BotFather).
- Ollama y un modelo compatible, si se desea ejecutar la IA localmente.
- Las credenciales y variables de entorno requeridas por la configuración elegida.

### 1. Clonar el repositorio

```bash
git clone https://github.com/Jesus-0046/chatbot-asistente-personal.git
cd chatbot-asistente-personal
```

### 2. Configurar las variables de entorno

Crea tu archivo `.env` a partir de `.env.example` y completa los valores necesarios para tu entorno.

Configura, entre otros, el token de Telegram, los identificadores de los usuarios autorizados, la conexión a PostgreSQL y el proveedor de IA.

**No publiques el archivo `.env` ni incluyas credenciales reales en el repositorio.**

Consulta `.env.example` para conocer los nombres y valores esperados por la aplicación.

### 3. Iniciar la base de datos

```bash
docker compose up -d db
docker compose ps
```

Comprueba que PostgreSQL esté disponible antes de continuar.

### 4. Instalar las dependencias

En Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

### 5. Aplicar las migraciones

```powershell
alembic upgrade head
```

### 6. Iniciar el asistente

```powershell
python -m asistente.main
```

Abre Telegram, accede al bot y envía `/start`.

Los nombres de las variables de entorno y los detalles adicionales de configuración se encuentran en `.env.example`, `DEPLOYMENT.md` y `OPERATIONS.md`.

## 🧪 Pruebas y calidad

El repositorio incluye pruebas unitarias, pruebas de integración y comprobaciones de tipos y estilo.

```bash
pytest
pytest -m db
ruff check .
ruff format --check .
mypy src
```

Las pruebas de integración que dependen de PostgreSQL requieren una base de datos de prueba configurada mediante `TEST_DATABASE_URL`.

La suite y las comprobaciones de calidad deben ejecutarse en el entorno actual antes de considerar todas las verificaciones superadas.

## 🗺️ Estado del proyecto

El proyecto incluye funcionalidades de conversación, agenda, recordatorios, gestión financiera, presupuestos, ahorro, recurrencias y suscripciones.

Entre las áreas de desarrollo y mejora se encuentran las pruebas de integración en distintos entornos, el endurecimiento continuo de la calidad del código y la ampliación de funcionalidades.

Consulta `PLAN_TECNICO.md` para conocer el detalle técnico y el roadmap.

## 🔒 Seguridad

- Los secretos se configuran mediante variables de entorno.
- Los archivos locales de configuración y las copias de seguridad se excluyen de Git.
- Las operaciones sensibles requieren confirmación.
- Los servicios aplican aislamiento por usuario.
- Las transferencias entre cuentas no se contabilizan como gastos; los saldos derivados se calculan a partir de los movimientos registrados.

## 📄 Documentación adicional

- `DEPLOYMENT.md`: despliegue.
- `OPERATIONS.md`: operación y mantenimiento.
- `BACKUP_RESTORE.md`: copias de seguridad y recuperación.
- `PLAN_TECNICO.md`: arquitectura y planificación técnica.

---

**Proyecto personal de desarrollo de software**, centrado en la integración de modelos de lenguaje, automatización, persistencia asíncrona y diseño modular de aplicaciones Python.
