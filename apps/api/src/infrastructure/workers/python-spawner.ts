import { spawn, ChildProcess } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let pythonProcess: ChildProcess | null = null;
let shouldRestart = true;
let restartTimer: NodeJS.Timeout | null = null;

function startProcess(
  pythonExecutable: string,
  mainScript: string,
  pythonWorkerPath: string
) {
  if (!shouldRestart) return;

  console.log(
    `[Python Spawner] Iniciando worker de python: ${pythonExecutable} ${mainScript}`
  );

  pythonProcess = spawn(pythonExecutable, [mainScript], {
    cwd: pythonWorkerPath,
    env: {
      ...process.env,
      // Forzar stdout unbuffered para que los logs lleguen de inmediato
      PYTHONUNBUFFERED: "1",
    },
  });

  pythonProcess.stdout?.on("data", (data) => {
    const lines = data.toString().trim().split("\n");
    for (const line of lines) {
      if (line) console.log(`[Python Worker] ${line}`);
    }
  });

  pythonProcess.stderr?.on("data", (data) => {
    const lines = data.toString().trim().split("\n");
    for (const line of lines) {
      if (!line) continue;
      if (line.includes(" - INFO - ") || line.includes(" - DEBUG - ")) {
        console.log(`[Python Worker] ${line}`);
      } else if (line.includes(" - WARNING - ")) {
        console.warn(`[Python Worker WARN] ${line}`);
      } else {
        console.error(`[Python Worker ERR] ${line}`);
      }
    }
  });

  pythonProcess.on("close", (code) => {
    pythonProcess = null;
    if (!shouldRestart) {
      console.log(
        `[Python Spawner] El worker de python se cerró con código ${code}. Apagado solicitado.`
      );
      return;
    }
    const isNormalExit = code === 0 || code === null;
    if (isNormalExit) {
      console.log(
        `[Python Spawner] El worker de python terminó limpiamente (código ${code}).`
      );
    } else {
      console.warn(
        `[Python Spawner] Worker cerrado inesperadamente (código ${code}). Reiniciando en 5s...`
      );
      restartTimer = setTimeout(() => {
        startProcess(pythonExecutable, mainScript, pythonWorkerPath);
      }, 5000);
    }
  });
}

export const spawnPythonWorker = () => {
  if (process.env.DISABLE_LOCAL_PYTHON_WORKER === "true") {
    console.log(
      "[Python Spawner] Worker local desactivado por DISABLE_LOCAL_PYTHON_WORKER=true."
    );
    return;
  }

  shouldRestart = true;

  // Ruta al script de python basada en process.cwd() (que típicamente es apps/api)
  const apiRoot = process.cwd();
  const repoRoot = path.resolve(apiRoot, "../../");
  const pythonWorkerPath = path.join(
    repoRoot,
    "apps",
    "python-workers",
    "grid_worker"
  );
  const mainScript = path.join(pythonWorkerPath, "main.py");

  // Buscar el ejecutable de python en el entorno virtual
  let pythonExecutable = path.join(
    pythonWorkerPath,
    ".venv",
    "Scripts",
    "python.exe"
  );

  // Soporte para linux/mac en caso de que WSL se use nativamente luego
  if (!fs.existsSync(pythonExecutable)) {
    pythonExecutable = path.join(
      pythonWorkerPath,
      ".venv",
      "bin",
      "python"
    );
  }

  if (!fs.existsSync(pythonExecutable)) {
    console.warn(
      `[Python Spawner] No se encontró el entorno virtual en ${pythonExecutable}. El worker de python no se iniciará.`
    );
    return;
  }

  startProcess(pythonExecutable, mainScript, pythonWorkerPath);
};

export const stopPythonWorker = () => {
  shouldRestart = false;
  if (restartTimer) {
    clearTimeout(restartTimer);
    restartTimer = null;
  }
  if (pythonProcess) {
    console.log("[Python Spawner] Deteniendo worker de python...");
    pythonProcess.kill("SIGINT");
    pythonProcess = null;
  }
};
