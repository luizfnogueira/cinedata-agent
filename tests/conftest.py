import pytest

from cinedata_agent.config import PROJECT_ROOT
from cinedata_agent.db import Database

DB_PATH = PROJECT_ROOT / "cinerocket.db"

requires_db = pytest.mark.skipif(not DB_PATH.exists(), reason="cinerocket.db não encontrado")


@pytest.fixture(scope="session")
def db():
    # Abrir a conexão leva ~3 s (materialização de vw_filme_pessoa): uma vez por sessão de testes.
    database = Database(DB_PATH, max_rows=50, timeout_seconds=10)
    yield database
    database.close()
