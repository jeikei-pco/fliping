# Implementación de Autenticación JWT y Multi-usuario

Actualmente, el sistema está acoplado al concepto de un usuario por defecto (`demo-user`) o un "usuario más reciente" para facilitar las pruebas locales. El objetivo de este plan es modernizar la arquitectura para que funcione con autenticación real basada en JWT, permitiendo que la API atienda y mantenga los motores de *múltiples usuarios de forma independiente y concurrente*.

## Proposed Changes

---
### API (Backend)

#### [MODIFY] [schema.prisma](file:///d:/proyectos/flipping-jeikei/apps/api/prisma/schema.prisma)
- Añadir el campo `passwordHash String` al modelo `AppUser`.
- Tras este cambio, será necesario ejecutar `npx prisma db push` para actualizar la base de datos.

#### [MODIFY] [package.json](file:///d:/proyectos/flipping-jeikei/apps/api/package.json)
- Instalar dependencias para autenticación: `jsonwebtoken`, `bcryptjs` y sus respectivos tipos (`@types/jsonwebtoken`, `@types/bcryptjs`).

#### [MODIFY] [http-app.ts](file:///d:/proyectos/flipping-jeikei/apps/api/src/presentation/http-app.ts)
- **Middleware de JWT**: Implementar un middleware global que intercepte todas las peticiones (excepto `/api/auth/*` y `/api/health`), verifique el token en el header `Authorization: Bearer <token>`, e inyecte `req.user.id`.
- **Nuevos Endpoints**:
  - `POST /api/auth/register`: Recibirá email, password y displayName. Hasheará la contraseña con `bcrypt` y creará el usuario.
  - `POST /api/auth/login`: Verificará el password y devolverá un token firmado con `jsonwebtoken`.
- **Limpieza de Rutas**: Eliminar cualquier referencia a `defaultUserId` o a recibir el `userId` en el body/query de forma insegura. Ahora todo saldrá exclusivamente de `req.user.id`.

#### [MODIFY] [main.ts](file:///d:/proyectos/flipping-jeikei/apps/api/src/main.ts)
- Eliminar completamente la lógica de `process.env.DEFAULT_USER_ID`, la variable `demo-user` y el fallback del Grid.
- El bootstrap simplemente buscará a *todos* los usuarios con motores activos en la DB y los inicializará, haciendo al sistema verdaderamente agnóstico y multi-usuario.

---
### Mobile (Frontend)

#### [MODIFY] [App.tsx](file:///d:/proyectos/flipping-jeikei/apps/mobile/App.tsx)
- **Gestión del Token**: Añadir estado local `token` (y persistirlo si es posible).
- **Interceptor de API**: Modificar la función `callApi` para que automáticamente añada el header `Authorization: Bearer ${token}` si el usuario ha iniciado sesión.
- **Flujo de Registro/Login**: Modificar la pantalla de login para permitir registrarse (o auto-registrar si el usuario no existe).

## User Review Required

> [!WARNING]
> **Pérdida de Datos Temporales**
> Al requerir el campo `passwordHash`, es probable que los usuarios actuales en tu base de datos local no sean válidos. Se recomienda limpiar la tabla de usuarios local y volver a registrarte desde la app móvil.

## Open Questions

> [!IMPORTANT]
> **¿Cómo prefieres manejar el flujo de registro en la App Móvil?**
> 1. (Recomendado) Añadir un botón que alterne entre "Iniciar Sesión" y "Crear Cuenta" en la pantalla de Login actual.
> 2. Auto-registro: Si el usuario intenta hacer login pero no existe en la base de datos, la API lo registra automáticamente y le devuelve el JWT. (Más rápido para demos).

## Verification Plan

### Automated Tests
- Arrancar la API (`npm run dev:api`).
- Comprobar que los endpoints privados devuelven `401 Unauthorized` si no hay token.

### Manual Verification
- Iniciar la app móvil.
- Registrar un usuario y recibir el JWT.
- Navegar a las vistas y comprobar que las credenciales de OKX, los motores y el dashboard cargan correctamente (enviando el JWT internamente).
- Verificar en la consola de la API que los workers de Python/Scraping se inician con el ID real del nuevo usuario.
