import pytest

from cinedata_agent.config import PROJECT_ROOT
from cinedata_agent.db import Database

DB_PATH = PROJECT_ROOT / "cinerocket.db"


@pytest.fixture(scope="session")
def db():
    """Conexão compartilhada pelos testes; pula o teste se o banco não foi baixado.

    Abrir a conexão leva ~3 s (materialização de vw_filme_pessoa): uma vez por sessão.
    """
    if not DB_PATH.exists():
        pytest.skip("cinerocket.db não encontrado")
    database = Database(DB_PATH, max_rows=50, timeout_seconds=10)
    yield database
    database.close()
