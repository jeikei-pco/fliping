Cambios en la Base de Datos (schema.prisma)
Actualmente, el modelo Credential tiene una restricción que impide guardar una credencial real y una de sandbox para el mismo exchange al mismo tiempo: @@unique([userId, provider]).

Soporte Real y Sandbox: Debemos actualizar la restricción en schema.prisma a @@unique([userId, provider, sandbox]) para permitir que un usuario guarde, por ejemplo, las claves de "Binance Live" (sandbox: false) y "Binance Testnet" (sandbox: true) simultáneamente.

Guardar el Exchange Activo: El modelo EngineStatus ya cuenta con un campo config String? @db.Text. Utilizaremos este campo para almacenar un JSON con la configuración activa del motor (ej. {"activeExchange": "binance", "useSandbox": true}).

2. Cambios en el Backend Node.js (http-app.ts)
Debemos centralizar toda la lógica para que la API sea la única fuente de verdad, tanto para la app móvil como para los workers en Python.

Endpoint de Configuración Activa: Crear endpoints (GET /api/engine/config y POST /api/engine/config) que interactúen con el campo config del modelo EngineStatus. Esto guardará en la base de datos qué exchange está usando el usuario actualmente.

Endpoint para el Worker de Python: Crear un endpoint interno (ej. GET /api/internal/worker-config) exclusivo para que el Grid Worker de Python consulte qué exchange debe instanciar y obtenga las credenciales desencriptadas. El worker ya no debe conectarse directamente a la base de datos ni depender de variables estáticas.

Modificar /api/balances: Actualizar este endpoint para que, si la app móvil no envía el parámetro exchange, el backend consulte automáticamente el campo config en EngineStatus para saber cuál es el exchange activo del usuario y devuelva los balances correctos.

3. Cambios en el Frontend Móvil (App.tsx)
Carga Inicial de Configuración: Al entrar al dashboard, la app debe llamar a /api/engine/config para saber qué exchange está activo y si está en modo sandbox, en lugar de depender del estado local estático.

Selector Global de Exchange: Implementar un menú desplegable en la Bóveda y en el Dashboard. Al cambiar el exchange en el Dashboard, se hará un POST a /api/engine/config para actualizar la base de datos. Inmediatamente después, se llamará a /api/balances para refrescar la vista.

Gestión de Credenciales: En la vista de configuración (Bóveda), permitir guardar explícitamente credenciales marcando o desmarcando el switch de Sandbox, haciendo un POST a /api/keys que ahora soportará múltiples entornos por exchange gracias al cambio en Prisma.

¿Responde esto a tus dudas abiertas?
Exchanges en el desplegable: Puedes incluir Binance, Bybit y OKX. Como la API ahora le dirá al worker qué exchange usar, el diseño es completamente escalable.

Campos dinámicos de API: En la app móvil (App.tsx), los campos (apiKey, secret, passphrase) deben mostrarse condicionalmente según el exchange seleccionado en el Picker (ej. ocultar passphrase si se selecciona Binance, ya que no lo requiere).