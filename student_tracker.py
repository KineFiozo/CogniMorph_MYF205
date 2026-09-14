import json
import os

class StudentTracker:
    def __init__(self, storage_file="progreso_estudiante.json"):
        self.storage_file = storage_file
        self.data = self._load_data()

    def _load_data(self):
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            "temas_estudiados": [],
            "sesiones_chat": 0,
            "quizzes_realizados": 0,
            "puntuacion_promedio": 0.0,
            "areas_a_reforzar": []
        }

    def save_data(self):
        with open(self.storage_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4, ensure_ascii=False)

    def registrar_sesion(self, tema):
        if tema and tema not in self.data["temas_estudiados"]:
            self.data["temas_estudiados"].append(tema)
        self.data["sesiones_chat"] += 1
        self.save_data()