"""
brain/connectors/base.py
=========================
Contrato base para todos los conectores de API externos.

PROPÓSITO
---------
Un Conector es la capa previa al Extractor para fuentes que no son ficheros
locales sino APIs externas (Confluence, Jira, SharePoint Graph, Notion...).

Su responsabilidad es:
  1. Autenticarse con la API externa
  2. Recuperar el ítem o lote de ítems (página, ticket, issue...)
  3. Serializar el resultado a texto estructurado con formato canónico
  4. Devolver ese texto como si fuera el contenido de un fichero virtual

El texto serializado tiene una extensión virtual (ej. ".confluence",
".jira_ticket") que el ExtractorFactory usa para seleccionar el extractor
correcto — exactamente igual que con ficheros reales.

FLUJO COMPLETO
--------------
  ConectorFactory.get("confluence")
        │
        ▼
  ConfluenceConnector.fetch_item(page_id)
        │  autentica → HTTP GET → JSON → serializa a texto estructurado
        ▼
  texto con formato canónico .confluence
        │
        ▼
  ConfluenceExtractor.extract(texto)    ← mismo BaseExtractor de siempre
        │  parsea el formato canónico → bloques {content, content_type, ...}
        ▼
  UniversalCleaner                      ← limpieza universal
        ▼
  Preprocesador (confluence.py)         ← marcas ##/### semánticas
        ▼
  LLM + Qdrant                          ← síntesis + vectorización

CÓMO AÑADIR UN NUEVO CONECTOR
------------------------------
1. Crea `brain/connectors/nuevo_connector.py` heredando de BaseConnector.
2. Implementa los 4 métodos abstractos:
     - connector_id: str (nombre canónico, ej. "confluence")
     - virtual_extension: str (extensión con punto, ej. ".confluence")
     - validate_connection() -> dict
     - fetch_item(item_id) -> str
     - fetch_batch(source_id, params) -> list[dict]
3. Registra en brain/connectors/factory.py con una línea en _MAP.
4. Crea el extractor en api/extractors/{connector_id}.py (BaseExtractor).
5. Crea el preprocesador en brain/prompts/preprocessing/{connector_id}.py.
6. Añade el TypeSpec en brain/prompts/type_specs.py.
7. Añade las instrucciones en _TYPE_INSTRUCTIONS_FOCUSED en brain/prompts/__init__.py.

Eso es todo. El router, el synthesizer y el ingest_router se adaptan automáticamente
porque solo miran la extensión virtual del fichero.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging

log = logging.getLogger(__name__)


@dataclass
class FetchedItem:
    """
    Resultado de fetch_item(): ítem individual serializado como texto.

    Campos:
      item_id:    identificador original del ítem en la API externa.
                  Para Confluence: page_id. Para Jira: issue_key (TEC-1234).
      slug:       nombre de fichero virtual que se usará como 'source' en Qdrant.
                  Formato: "{connector_id}-{sanitized_title}.{virtual_ext_nopoint}"
                  Ejemplo: "confluence-arquitectura-data-lake.confluence"
      title:      título legible del ítem (nombre de la página, asunto del ticket...)
      text:       texto serializado con formato canónico del conector.
                  Este texto va al Extractor como si fuera el contenido de un fichero.
      metadata:   metadatos adicionales para el frontmatter del pasaporte
                  (url, author, labels, space, status, priority...)
      virtual_ext: extensión virtual con punto (ej. ".confluence", ".jira_ticket")
    """
    item_id: str
    slug: str
    title: str
    text: str
    metadata: dict = field(default_factory=dict)
    virtual_ext: str = ""


@dataclass
class BatchResult:
    """
    Resultado de fetch_batch(): lista de ítems con estadísticas.

    Campos:
      items:     lista de FetchedItem listos para ingestar.
      total:     total de ítems disponibles en la fuente (antes de filtros).
      fetched:   ítems efectivamente recuperados en esta llamada.
      skipped:   ítems omitidos (sin contenido, errores de API, filtrados).
      errors:    lista de errores no fatales durante la recuperación.
    """
    items: list[FetchedItem] = field(default_factory=list)
    total: int = 0
    fetched: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


class BaseConnector(ABC):
    """
    Contrato que deben implementar todos los conectores de API externos.

    Un conector es responsable de:
      - Autenticarse con la API externa (en __init__ o en _build_session())
      - Recuperar contenido por ID de ítem o por lote (space, proyecto, board...)
      - Serializar el contenido al formato canónico de texto de ese conector
      - NO hacer chunking, NO hacer embeddings, NO escribir a disco

    El conector NO llama al Extractor ni al Cleaner. El router (connector.py)
    orquesta la cadena Conector → Extractor → Cleaner → Preprocesador → LLM.
    """

    # ── Identidad del conector ──────────────────────────────────────────────

    @property
    @abstractmethod
    def connector_id(self) -> str:
        """
        Nombre canónico del conector en minúsculas.
        Coincide con la clave en ConectorFactory._MAP.
        Ejemplos: "confluence", "jira", "notion", "github"
        """
        ...

    @property
    @abstractmethod
    def virtual_extension(self) -> str:
        """
        Extensión virtual del fichero serializado, con punto.
        Debe coincidir con la clave en ExtractorFactory._MAP.
        Ejemplos: ".confluence", ".jira_ticket", ".notion_page"
        """
        ...

    # ── Métodos abstractos obligatorios ────────────────────────────────────

    @abstractmethod
    def validate_connection(self) -> dict:
        """
        Verifica que la conexión con la API externa es válida.

        Hace una llamada mínima a la API (ej. GET /myself, GET /serverInfo)
        para comprobar que las credenciales son correctas y la API responde.

        Returns:
            dict con:
              ok (bool):      True si la conexión es válida.
              connector (str): self.connector_id
              detail (str):   mensaje de éxito o descripción del error.
              info (dict):    metadata opcional de la API (versión, usuario...)

        NUNCA lanza excepciones — siempre devuelve un dict con ok=False
        si hay cualquier problema.
        """
        ...

    @abstractmethod
    def fetch_item(self, item_id: str) -> FetchedItem:
        """
        Recupera un ítem individual por su ID y lo serializa a texto.

        Args:
            item_id: identificador del ítem en la API externa.
                     Para Confluence: page_id (numérico o alfanumérico).
                     Para Jira: issue_key (ej. "TEC-1234").

        Returns:
            FetchedItem con el texto serializado en formato canónico.

        Raises:
            ValueError: si el ítem no existe o no tiene contenido extraíble.
            ConnectionError: si la API no responde o devuelve error HTTP.
        """
        ...

    @abstractmethod
    def fetch_batch(
        self,
        source_id: str,
        params: dict | None = None,
    ) -> BatchResult:
        """
        Recupera un lote de ítems de una fuente (space, proyecto, board...).

        Args:
            source_id: identificador de la fuente.
                       Para Confluence: key del space (ej. "TEC", "GOV").
                       Para Jira: key del proyecto (ej. "TEC", "OPS").
            params:    parámetros opcionales de filtrado y paginación:
                       {limit, offset, labels, status, updated_after, ...}

        Returns:
            BatchResult con lista de FetchedItem y estadísticas.

        NUNCA lanza excepciones — registra errores en BatchResult.errors
        y continúa con los ítems recuperados hasta el momento.
        """
        ...

    # ── Utilidades comunes ──────────────────────────────────────────────────

    def _slugify(self, text: str) -> str:
        """
        Normaliza un texto a slug válido como nombre de fichero virtual.
        Elimina caracteres especiales, convierte espacios a guiones, minúsculas.

        Ejemplo: "Arquitectura Data Lake v2" → "arquitectura-data-lake-v2"
        """
        import re
        text = text.lower().strip()
        text = re.sub(r"[áàäâ]", "a", text)
        text = re.sub(r"[éèëê]", "e", text)
        text = re.sub(r"[íìïî]", "i", text)
        text = re.sub(r"[óòöô]", "o", text)
        text = re.sub(r"[úùüû]", "u", text)
        text = re.sub(r"[ñ]", "n", text)
        text = re.sub(r"[^a-z0-9\s-]", "", text)
        text = re.sub(r"[\s_]+", "-", text)
        text = re.sub(r"-+", "-", text)
        return text.strip("-")[:80]

    def _make_slug(self, title: str) -> str:
        """
        Genera el slug canónico del ítem incluyendo el prefijo del conector.
        Formato: "{connector_id}-{slugified_title}"
        Ejemplo: "confluence-arquitectura-data-lake"
        """
        return f"{self.connector_id}-{self._slugify(title)}"

    def _virtual_filename(self, title: str) -> str:
        """
        Genera el nombre de fichero virtual completo (slug + extensión).
        Ejemplo: "confluence-arquitectura-data-lake.confluence"
        """
        ext = self.virtual_extension.lstrip(".")
        return f"{self._make_slug(title)}.{ext}"

    def _check_env_vars(self, *var_names: str) -> tuple[bool, list[str]]:
        """
        Verifica que las variables de entorno requeridas están definidas y no vacías.

        Returns:
            (True, []) si todas están presentes.
            (False, [lista_de_faltantes]) si alguna falta.
        """
        import os
        missing = [v for v in var_names if not os.getenv(v, "").strip()]
        return (len(missing) == 0), missing

    def apply_credentials(self, credentials: dict) -> None:
        """
        Aplica credenciales recibidas por request sobreescribiendo los valores
        cargados desde variables de entorno en __init__.

        Permite que la UI pase credenciales dinámicamente sin necesidad de
        configurar variables de entorno en docker-compose.yml.

        Cada conector concreto sobreescribe este método para mapear los campos
        del dict a sus atributos internos (_base_url, _user, _token...).
        Si no se sobreescribe, las credenciales se ignoran (backward-compat).

        Esquemas por conector:
          Confluence/Jira: {"url": ..., "user": ..., "token": ..., "auth_type": ...}
          GitHub:          {"token": ..., "api_url": ...}
        """
        pass  # sobreescribir en cada conector concreto

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.connector_id})"
