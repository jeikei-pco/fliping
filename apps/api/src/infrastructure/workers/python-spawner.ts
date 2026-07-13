import { spawn, ChildProcess } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
import fs from "fs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

let pythonProcess: ChildProcess | null = null;

export const spawnPythonWorker = () => {
  if (process.env.DISABLE_LOCAL_PYTHON_WORKER === "true") {
    console.log("[Python Spawner] Worker local desactivado por DISABLE_LOCAL_PYTHON_WORKER=true.");
    return;
  }

  // Ruta al script de python basada en process.cwd() (que típicamente es apps/api)
  const apiRoot = process.cwd();
  const repoRoot = path.resolve(apiRoot, "../../");
  const pythonWorkerPath = path.join(repoRoot, "apps", "python-workers", "grid_worker");
  const mainScript = path.join(pythonWorkerPath, "main.py");
  
  // Buscar el ejecutable de python en el entorno virtual
  let pythonExecutable = path.join(pythonWorkerPath, ".venv", "Scripts", "python.exe");
  
  // Soporte para linux/mac en caso de que WSL se use nativamente luego
  if (!fs.existsSync(pythonExecutable)) {
    pythonExecutable = path.join(pythonWorkerPath, ".venv", "bin", "python");
  }

  if (!fs.existsSync(pythonExecutable)) {
    console.warn(`[Python Spawner] No se encontró el entorno virtual en ${pythonExecutable}. El worker de python no se iniciará.`);
    return;
  }

  console.log(`[Python Spawner] Iniciando worker de python: ${pythonExecutable} ${mainScript}`);

  pythonProcess = spawn(pythonExecutable, [mainScript], {
    cwd: pythonWorkerPath,
    env: {
      ...process.env,
      // Forzar stdout unbuffered para que los logs lleguen de inmediato
      PYTHONUNBUFFERED: "1" 
    }
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
    console.log(`[Python Spawner] El worker de python se cerró con código ${code}.`);
    pythonProcess = null;
  });
};

export const stopPythonWorker = () => {
  if (pythonProcess) {
    console.log("[Python Spawner] Deteniendo worker de python...");
    pythonProcess.kill("SIGINT");
    pythonProcess = null;
  }
};
