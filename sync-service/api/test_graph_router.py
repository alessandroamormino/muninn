"""Unit tests for api/graph.py — GET /collections and GET /graph/{collection}.

Wave 0 RED tests: written FIRST before api/graph.py exists.
Tests cover:
- test_collections_returns_list: sorted list from configuration/ subdirs
- test_graph_returns_structure: nodes/edges/clusters JSON from mocked Weaviate
- test_graph_empty_collection: 422 when < 10 records
- test_graph_rejects_path_traversal: 422 on path-traversal collection names
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_fake_obj(idx: int, dims: int = 8) -> MagicMock:
    obj = MagicMock()
    obj.uuid = uuid.uuid4()
    obj.vector = {"default": np.random.rand(dims).astype(np.float32).tolist()}
    obj.properties = {"title": f"rec-{idx}"}
    return obj


def _make_test_app():
    from api import graph as graph_mod

    app = FastAPI()
    app.include_router(graph_mod.router)
    return app


# ---------------------------------------------------------------------------
# test_collections_returns_list
# ---------------------------------------------------------------------------

def test_collections_returns_list(tmp_path, monkeypatch):
    from api import graph as graph_mod

    (tmp_path / "Collaboratori").mkdir()
    (tmp_path / "Collaboratori" / "config.yaml").write_text("foo: 1")
    (tmp_path / "Movies").mkdir()
    (tmp_path / "Movies" / "config.yaml").write_text("foo: 1")
    # Directory without config.yaml — should NOT be listed
    (tmp_path / "EmptyDir").mkdir()

    monkeypatch.setattr(graph_mod, "_CONFIG_ROOT", tmp_path)
    app = _make_test_app()
    client = TestClient(app)

    r = client.get("/collections")
    assert r.status_code == 200
    data = r.json()
    assert "collections" in data
    assert data["collections"] == ["Collaboratori", "Movies"]


# ---------------------------------------------------------------------------
# test_graph_returns_structure
# ---------------------------------------------------------------------------

def test_graph_returns_structure(tmp_path, monkeypatch):
    from api import graph as graph_mod

    # Setup config root
    (tmp_path / "Collaboratori").mkdir()
    (tmp_path / "Collaboratori" / "config.yaml").write_text("foo: 1")
    monkeypatch.setattr(graph_mod, "_CONFIG_ROOT", tmp_path)

    n_records = 50
    fake_objs = [_make_fake_obj(i) for i in range(n_records)]

    # Mock Weaviate client
    mock_col = MagicMock()
    mock_col.iterator.return_value = iter(fake_objs)
    mock_client = MagicMock()
    mock_client.collections.get.return_value = mock_col

    # Mock UMAP
    mock_umap_instance = MagicMock()
    coords = np.random.rand(n_records, 2).astype(np.float32)
    mock_umap_instance.fit_transform.return_value = coords
    # Create a fake sparse graph for reducer.graph_
    import scipy.sparse as sp
    knn_matrix = sp.csr_matrix(np.eye(n_records))
    mock_umap_instance.graph_ = knn_matrix
    mock_umap_class = MagicMock(return_value=mock_umap_instance)

    # Mock HDBSCAN
    mock_hdbscan_instance = MagicMock()
    mock_hdbscan_instance.fit_predict.return_value = np.zeros(n_records, dtype=int)
    mock_hdbscan_class = MagicMock(return_value=mock_hdbscan_instance)

    # Mock OllamaLLMClient
    mock_llm_instance = MagicMock()
    mock_llm_instance.generate.return_value = {"name": "Test Cluster"}
    mock_llm_class = MagicMock(return_value=mock_llm_instance)

    with (
        patch.object(graph_mod, "get_client", return_value=mock_client),
        patch.object(graph_mod, "umap") as mock_umap_module,
        patch.object(graph_mod, "HDBSCAN", mock_hdbscan_class),
        patch.object(graph_mod, "OllamaLLMClient", mock_llm_class),
    ):
        mock_umap_module.UMAP = mock_umap_class
        app = _make_test_app()
        client = TestClient(app)

        r = client.get("/graph/Collaboratori")

    assert r.status_code == 200, f"Expected 200 got {r.status_code}: {r.text}"
    data = r.json()
    assert "nodes" in data
    assert "edges" in data
    assert "clusters" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)
    assert isinstance(data["clusters"], list)
    assert len(data["nodes"]) == n_records
    # Each node has required fields
    node = data["nodes"][0]
    for field in ("id", "x", "y", "cluster", "radius", "props"):
        assert field in node, f"Node missing field: {field}"


# ---------------------------------------------------------------------------
# test_graph_empty_collection
# ---------------------------------------------------------------------------

def test_graph_empty_collection(tmp_path, monkeypatch):
    from api import graph as graph_mod

    monkeypatch.setattr(graph_mod, "_CONFIG_ROOT", tmp_path)

    # Return only 5 objects — below minimum of 10
    fake_objs = [_make_fake_obj(i) for i in range(5)]
    mock_col = MagicMock()
    mock_col.iterator.return_value = iter(fake_objs)
    mock_client = MagicMock()
    mock_client.collections.get.return_value = mock_col

    with patch.object(graph_mod, "get_client", return_value=mock_client):
        app = _make_test_app()
        client = TestClient(app)
        r = client.get("/graph/Collaboratori")

    assert r.status_code == 422
    detail = r.json().get("detail", "")
    assert "too few records" in detail.lower() or "minimum" in detail.lower()


# ---------------------------------------------------------------------------
# test_graph_rejects_path_traversal
# ---------------------------------------------------------------------------

TRAVERSAL_NAMES = [
    "..",
    "../etc",
    "foo/bar",
    "foo bar",
    "foo;rm",
    "foo%2Fbar",
]


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_graph_rejects_path_traversal(name, tmp_path, monkeypatch):
    from api import graph as graph_mod

    monkeypatch.setattr(graph_mod, "_CONFIG_ROOT", tmp_path)

    app = _make_test_app()
    # Use requests_mock / TestClient directly; TestClient URL-encodes path params
    client = TestClient(app, raise_server_exceptions=False)
    # Construct URL — percent-encoded names are passed as-is to avoid double encoding
    r = client.get(f"/graph/{name}")
    # Must be 422 (from regex guard) or 404 (HTTP routing rejects the path before it reaches
    # the handler). In either case it must NOT be 200 and must NOT return filesystem content.
    # 422 is the ideal outcome — confirms our regex guard fired BEFORE any I/O.
    # 404 is acceptable — Starlette/FastAPI rejects the URL path before reaching the handler.
    assert r.status_code in (422, 404), (
        f"Expected 422 or 404 for collection name {name!r}, got {r.status_code}: {r.text}"
    )
    # Ensure it's NOT a success that could indicate path traversal went through
    assert r.status_code != 200, f"Path traversal guard failed for {name!r}: got 200"
