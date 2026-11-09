import pytest
from httpx import ASGITransport, AsyncClient

from app.api import routes
from app.main import create_app


@pytest.fixture
def app():
    # 每个用例前重建 app 与 store，保证从种子状态开始
    routes.reset_store()
    return create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
