"""Consulta da cota diária de modelos gratuitos no OpenRouter.

GET /api/v1/key não é uma requisição de modelo: não consome a cota.
Atenção: o contador do OpenRouter tem atraso de alguns minutos.
"""

import json
import urllib.request
from dataclasses import dataclass

KEY_INFO_URL = "https://openrouter.ai/api/v1/key"


@dataclass(frozen=True)
class QuotaStatus:
    used: int
    limit: int
    remaining: int


def parse_quota(payload: dict) -> QuotaStatus:
    daily = payload["data"]["free_model_daily_requests"]
    return QuotaStatus(used=daily["used"], limit=daily["limit"], remaining=daily["remaining"])


def fetch_quota(api_key: str, timeout_seconds: float = 10.0) -> QuotaStatus:
    request = urllib.request.Request(KEY_INFO_URL, headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return parse_quota(json.load(response))
