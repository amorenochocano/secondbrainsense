"""
json_ext.py
-----------
Extractor para ficheros JSON (.json).
Formatea el JSON de forma legible para su indexación y síntesis.
"""
import json
from .base import BaseExtractor


class JsonExtractor(BaseExtractor):
    def extract(self, source: str) -> list[dict]:
        """
        Lee un fichero .json y lo convierte a texto formateado legible.
        Si es muy grande, se trunca para no exceder límites.
        """
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Intentar formatear bonito para mejor legibilidad
        try:
            data = json.loads(content)
            formatted = json.dumps(data, indent=2, ensure_ascii=False)
        except json.JSONDecodeError:
            formatted = content

        return [{"page": 1, "text": formatted, "content": formatted, "content_type": "text"}]
