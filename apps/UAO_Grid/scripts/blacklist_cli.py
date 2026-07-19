import sys
import json
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.database import Database

def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Faltan argumentos"}))
        return
        
    action = sys.argv[1]
    db = Database()
    
    if action == "list":
        try:
            items = db.get_lista_negra()
            print(json.dumps({"success": True, "data": items}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
            
    elif action == "remove":
        if len(sys.argv) < 4:
            print(json.dumps({"error": "Falta symbol o modo"}))
            return
        symbol = sys.argv[2]
        modo = sys.argv[3]
        try:
            db.remover_de_lista_negra(symbol, modo)
            print(json.dumps({"success": True}))
        except Exception as e:
            print(json.dumps({"success": False, "error": str(e)}))
    else:
        print(json.dumps({"error": "Acción inválida"}))

if __name__ == "__main__":
    main()
