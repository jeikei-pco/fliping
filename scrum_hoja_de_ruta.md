🗺️ JK-Flipping: Hoja de Ruta y Plan Scrum

Nota: Este documento está diseñado para ser directo. Tacha cada tarea (Task) conforme la termines. Las pequeñas victorias mantienen la motivación.

🎯 1. Hoja de Ruta General (Product Backlog)

Esta es la visión a nivel macro de lo que vamos a construir, ordenado por prioridad.

[x] Fundamentos: Servidor base, Base de Datos, Autenticación y Bóveda Segura de APIs.

[x] App Móvil (Esqueleto): Estructura básica de la app, Login y Navegación.

[x] Motor Cripto: Integración CCXT, lectura de saldos, ejecución de orden básica (Arbitraje).

[x] Motor Retail (Scraping Básico): Firecrawl + IA para leer una tienda y detectar un precio.

[x] Motor Inmobiliario/SaaS (Scraping Complejo): Prompts avanzados para evaluar oportunidades con OpenAI/Gemini.

[x] Notificaciones & Dashboard: Mostrar los datos en vivo en la app.

[ ] Pulido y Producción: Estabilidad, manejo de errores y despliegue real.

👤 2. Historias de Usuario (User Stories)

Así es como tú (el usuario) interactuarás con el sistema. Sirven para definir qué programar.

[x] HU-01 (Bóveda): Como usuario, quiero guardar mis API Keys de exchanges e IAs en la app móvil de forma segura, para que el sistema pueda operar por mí.

[x] HU-02 (Control): Como usuario, quiero ver 4 botones (ON/OFF) en mi dashboard, para activar o detener cada motor de flipping individualmente.

[x] HU-03 (Saldos): Como usuario, quiero ver mis saldos actualizados de los exchanges configurados, para saber cuánto capital tengo disponible.

[x] HU-04 (Cripto): Como usuario, quiero que el motor cripto ejecute arbitraje automático cuando detecte un margen favorable, para generar ganancias sin mi intervención.

[x] HU-05 (Alertas Tech): Como usuario, quiero recibir una notificación push cuando el motor tech encuentre una GPU o hardware barato, para comprarlo rápido.

[x] HU-06 (Leads Inmobiliarios/SaaS): Como usuario, quiero leer reportes generados por IA sobre propiedades o software subvaluado en mi app, para decidir si hago una oferta.

🏃‍♂️ 3. Sprints y Tareas (Tasks)

Sprints de 2 semanas. Concéntrate solo en un Sprint a la vez. Olvídate del resto hasta que termines el actual.

✅ SPRINT 1 — COMPLETADO: Los Cimientos y la Bóveda (Días 1-14)

Objetivo: Tener un servidor corriendo, una base de datos conectada y poder guardar API keys de forma segura.

[x] Task 1.1: Inicializar proyecto Backend (Node/Express o Python/FastAPI).
  → Node.js + Express + TypeScript en arquitectura hexagonal (monorepo npm workspaces).

[x] Task 1.2: Configurar Base de Datos PostgreSQL (Local o Docker).
  → PostgreSQL 16-alpine via docker-compose.yml con healthcheck.

[x] Task 1.3: Crear estructura de Arquitectura Hexagonal (Carpetas: Dominio, Casos de Uso, Infraestructura).
  → Carpetas: domain/, application/, infrastructure/, presentation/

[x] Task 1.4: Implementar sistema de encriptación (AES-256) en el backend para las API Keys.
  → AesEncryptionService con MASTER_KEY configurable por .env

[x] Task 1.5: Crear API Endpoint (POST): /api/keys para guardar credenciales.
  → POST /api/keys + GET /api/keys implementados con Prisma + upsert.

[x] Task 1.6: Inicializar proyecto App Móvil (React Native o Flutter).
  → React Native + Expo + TypeScript en apps/mobile/

[x] Task 1.7: Crear pantalla de "Login" básica en la app (Mockup funcional).
  → Login funcional con opción "entrar con backend" y "demo local".

[x] Task 1.8: Crear pantalla de "Configuración" en la app con un formulario para ingresar API keys y enviarlas al backend.
  → Bóveda con formularios para OKX, OpenRouter y Firecrawl.

✅ SPRINT 2 — COMPLETADO: El Motor Cripto y Saldos (Días 15-28)

Objetivo: Conectar CCXT, ver tus saldos reales en la app y crear el "botón" de encendido.

[x] Task 2.1: Instalar e importar librería ccxt en el backend.
  → ccxt ^4.5.64 instalado y funcional.

[x] Task 2.2: Crear servicio backend para desencriptar API keys y usarlas en CCXT.
  → CcxtExchangeService desencripta via vault y conecta al exchange.

[x] Task 2.3: Crear API Endpoint (GET): /api/balances para obtener saldos de un exchange de prueba (ej. Binance testnet).
  → GET /api/balances?exchange=okx&sandbox=true implementado.

[x] Task 2.4: App Móvil: Diseñar Dashboard principal (las 4 tarjetas de los motores).
  → Dashboard con MotorCard para los 4 motores.

[x] Task 2.5: App Móvil: Conectar el Dashboard al endpoint /api/balances y mostrar el dinero disponible.
  → Resumen financiero con totalCapital y detalle por asset.

[x] Task 2.6: Backend: Crear un "Worker" (proceso en bucle) simulado para escanear precios de un par (ej. BTC/USDT).
  → DemoAwareCryptoEngineService con setInterval cada 15s.

[x] Task 2.7: App Móvil: Hacer que el botón (Switch) del "Motor Cripto" envíe una señal al backend para iniciar/detener el Worker.
  → Switch conectado a POST /api/engine/toggle.

✅ SPRINT 3 — COMPLETADO: Los Ojos del Sistema (Scraping Tech) (Días 29-42)

Objetivo: Usar Firecrawl y una IA rápida para detectar oportunidades físicas.

[x] Task 3.1: Obtener API Key de Firecrawl y guardarla en la Bóveda.
  → Formulario de Firecrawl en la app + POST /api/keys listo.

[x] Task 3.2: Obtener API Key de Groq (vía OpenRouter) para IA ultra-rápida.
  → Formulario de OpenRouter en la app + modelo groq/llama-3.3-70b-versatile configurado.

[x] Task 3.3: Backend: Crear worker "Motor Tech" que use Firecrawl para escanear una URL específica (ej. una tienda de hardware).
  → TechEngineService con setInterval cada 5 min, URL configurable (default: Newegg RTX 5090).

[x] Task 3.4: Backend: Enviar el HTML/Texto escrapeado a Groq con un prompt: "Extrae el precio de este producto. ¿Es menor a X?".
  → OpenRouterLLMService + prompt estructurado que retorna JSON con isGoodDeal, lowestPriceUsd, reason.

[x] Task 3.5: Backend: Crear un sistema de Notificaciones básico (ej. guardar un registro en Base de Datos de "Oportunidad Encontrada").
  → PrismaAlertRepository + tabla alerts en Prisma schema. Alert con severity high/medium/low.

[x] Task 3.6: App Móvil: Crear pantalla de "Notificaciones/Alertas" para ver los hallazgos del Motor Tech.
  → Tab "Alertas" con contador de no leídas, cards por severidad, marcar como leída al tocar.

[x] Task 3.7: App Móvil: Conectar el Switch del "Motor Tech" al backend.
  → Switch conectado a POST /api/engines/toggle { motor: "tech", enabled: true/false }.

✅ SPRINT 4 — COMPLETADO: El Cerebro Analítico (Bienes Raíces & SaaS) (Días 43-56)

Objetivo: Análisis profundo con IA pesada para generar "Leads" calificados.

[x] Task 4.1: Asegurar acceso a OpenAI/Gemini vía OpenRouter.
  → OpenRouterLLMService configurado con modelo openai/gpt-4o-mini. Acepta cualquier modelo de OpenRouter.

[x] Task 4.2: Backend: Crear worker "Motor Inmobiliario". Escanear un listado de propiedades de prueba con Firecrawl.
  → RealEstateEngineService con setInterval cada 30 min, URL configurable (default: Realtor.com Detroit).

[x] Task 4.3: Backend: Diseñar Prompt complejo para IA: "Evalúa esta propiedad basada en la regla del 70%. Estima costos de reparación por la descripción. ¿Es buen deal?".
  → Prompt con MAO = (ARV × 0.70) - Reparaciones, keywords de vendedores motivados y dealScore 1-10.

[x] Task 4.4: Backend: Crear worker "Motor SaaS". Escanear marketplace de software (ej. Acquire.com público).
  → SaasEngineService con setInterval cada 1 hora, URL configurable (default: acquire.com/marketplace/saas).

[x] Task 4.5: Backend: Prompt para IA: "Analiza el stack tecnológico y las ganancias de este SaaS. ¿Está subvaluado?".
  → Prompt con múltiplos ARR, techStack, growthPotential y dealScore 1-10. Solo guarda si dealScore >= 6.

[x] Task 4.6: App Móvil: Crear vista de "Reportes Detallados" dentro de la pantalla de Notificaciones para leer los análisis de la IA.
  → Tab "Oportunidades" con cards de dealScore coloreado + Modal de detalle con análisis IA completo.

[x] Task 4.7: App Móvil: Conectar Switches de los Motores Inmobiliario y SaaS.
  → Switches conectados a POST /api/engines/toggle { motor: "real-estate"/"saas", enabled: true/false }.

🚀 SPRINT 5 — PENDIENTE: Orquestación y Pruebas (Días 57-70)

Objetivo: Que todo corra al mismo tiempo sin colapsar y prepararse para el mundo real.

[ ] Task 5.1: Implementar un Gestor de Colas real (ej. Redis/BullMQ en Node o Celery en Python) para que los 4 workers corran en paralelo de forma robusta.

[ ] Task 5.2: Refactorizar manejo de errores (Si falla CCXT, el motor de Firecrawl no debe detenerse).

[ ] Task 5.3: App Móvil: Pruebas de estrés. Encender y apagar los 4 switches repetidamente para verificar estabilidad.

[ ] Task 5.4: Configurar Webhooks o Notificaciones Push reales (ej. Firebase) para que te avise en el móvil sin tener la app abierta.

[ ] Task 5.5: Despliegue del Backend en servidor Cloud (DigitalOcean, AWS, etc.).