"""brain.tools — LangChain tools que exponen el pipeline Brain al agente SurfSense."""

from .brain_search_tool import create_brain_search_tool

__all__ = ["create_brain_search_tool"]
