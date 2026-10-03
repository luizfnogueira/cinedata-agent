"""Agente Text-to-SQL sobre a camada Gold da CineData Analytics."""

import os

# O PydanticAI imprime um banner promocional na primeira execução; poluiria a CLI.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
