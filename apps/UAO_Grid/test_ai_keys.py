import os
import json
import urllib.request
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

def test_api(name, url, key, model, req_type):
    if not key or key.strip() == "":
        print(f"OMITIDO {name}: (No hay API Key configurada)")
        return
        
    print(f"Probando {name} ({model}) en {url}...")
    
    prompt = "Hola, responde solo con la palabra 'OK'."
    
    try:
        if req_type == "openai":
            body = {
                "model": model,
                "messages": [
                    {"role": "user", "content": prompt}
                ],
                "max_tokens": 10
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}"
            }
        else:
            body = {
                "model": model,
                "max_tokens": 10,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "content-type": "application/json", 
                "x-api-key": key, 
                "anthropic-version": "2023-06-01"
            }
            
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            print(f"EXITO {name}: Respuesta HTTP {response.status}")
            
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"FALLO HTTP {name}: {e.code} {e.reason}")
        print(f"   Detalle del error devuelto por la API:")
        try:
            parsed_err = json.loads(error_body)
            print(f"   {json.dumps(parsed_err, indent=2)}")
        except:
            print(f"   {error_body}")
    except urllib.error.URLError as e:
        print(f"FALLO DE CONEXION {name}: ({e.reason})")
    except Exception as e:
        print(f"ERROR INESPERADO {name}: ({str(e)})")
    
    print("-" * 50)

def main():
    print("=" * 50)
    print("TEST DE API KEYS DE IA")
    print("=" * 50)
    
    # 1. Gemini
    for i, key_name in enumerate(["GEMINI_API_KEY", "GEMINI_API_KEY2", "GEMINI_API_KEY3"]):
        test_api(
            name=f"Google Gemini (Key {i+1})", 
            url="https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            key=os.getenv(key_name),
            model="gemini-2.5-flash",
            req_type="openai"
        )
        
    # 2. OpenRouter
    for i, key_name in enumerate(["OPENROUTER_API_KEY", "OPENROUTER_API_KEY2"]):
        test_api(
            name=f"OpenRouter (Key {i+1})", 
            url="https://openrouter.ai/api/v1/chat/completions",
            key=os.getenv(key_name),
            model="google/gemini-2.5-flash",
            req_type="openai"
        )
        
    # 3. OpenAI
    for i, key_name in enumerate(["OPENAI_API_KEY", "OPENAI_API_KEY2"]):
        test_api(
            name=f"OpenAI (Key {i+1})", 
            url="https://api.openai.com/v1/chat/completions",
            key=os.getenv(key_name),
            model="gpt-4o-mini",
            req_type="openai"
        )
        
    # 4. Groq
    test_api(
        name="Groq", 
        url="https://api.groq.com/openai/v1/chat/completions",
        key=os.getenv("GROQ_API_KEY"),
        model="llama3-8b-8192", # Modelo muy ligero para test
        req_type="openai"
    )
    
    # 5. Claude Local Proxy
    test_api(
        name="Claude (Local Proxy)", 
        url=os.getenv("AI_OPTIMIZER_API_URL", "http://127.0.0.1:8082/v1/messages"),
        key=os.getenv("CLAUDE_CODE_PROXY_API_KEY", "freecc"),
        model=os.getenv("CLAUDE_CODE_MODEL", "claude-3-5-haiku-20241022"),
        req_type="anthropic"
    )
    
    print("TEST FINALIZADO.")

if __name__ == "__main__":
    main()
