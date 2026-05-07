"""Integration tests for API endpoints."""

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app, headers={"Authorization": "Bearer cb_dev_key"})

class TestAPIEndpoints:
    """Test suite for API endpoints."""
    
    def test_root_endpoint(self):
        """Test root endpoint serves platform frontend HTML."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
        assert "<!DOCTYPE html>" in response.text or "<html" in response.text
    
    def test_list_blocks(self):
        """Test listing all blocks."""
        response = client.get("/blocks")
        assert response.status_code == 200
        data = response.json()
        assert "blocks" in data
        assert "total" in data
        # count grows as new blocks are added — just ensure minimum
        assert data["total"] >= 23
        
        # Check that vector_search is included
        block_names = [b["name"] for b in data["blocks"]]
        assert "vector_search" in block_names
    
    def test_get_block_info(self):
        """Test getting block info."""
        response = client.get("/blocks/vector_search")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "vector_search"
        assert "config" in data
    
    def test_get_nonexistent_block(self):
        """Test getting non-existent block."""
        response = client.get("/blocks/nonexistent")
        assert response.status_code == 404
    
    def test_health_endpoint(self):
        """Test health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_stats_endpoint(self):
        """Test stats endpoint."""
        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert "blocks" in data


class TestExecuteEndpoint:
    """Tests for execute endpoint."""
    
    def test_execute_chat_mock(self):
        """Test executing chat block with mock."""
        response = client.post("/execute", json={
            "block": "chat",
            "input": "Hello",
            "params": {"provider": "mock"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "chat"
        assert "result" in data
    
    def test_execute_vector_search_list_collections(self):
        """Test executing vector_search block."""
        response = client.post("/execute", json={
            "block": "vector_search",
            "input": {},
            "params": {"operation": "list_collections"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "vector_search"
        assert "collections" in data["result"]
    
    def test_execute_vector_search_embed(self):
        """Test vector_search embed operation."""
        response = client.post("/execute", json={
            "block": "vector_search",
            "input": "test text",
            "params": {"operation": "embed"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "vector_search"
        assert "embeddings" in data["result"]
    
    def test_execute_web_block(self):
        """Test executing web block."""
        response = client.post("/execute", json={
            "block": "web",
            "input": "<html><body>Test</body></html>",
            "params": {"operation": "html_parse"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "web"
    
    def test_execute_nonexistent_block(self):
        """Test executing non-existent block."""
        response = client.post("/execute", json={
            "block": "nonexistent",
            "input": "test",
            "params": {}
        })
        
        assert response.status_code == 404
    
    def test_execute_translate_mock(self):
        """Test executing translate block with mock."""
        response = client.post("/execute", json={
            "block": "translate",
            "input": "Hello",
            "params": {"provider": "mock", "target": "es"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "translate"


class TestChainEndpoint:
    """Tests for chain endpoint."""
    
    def test_chain_execution(self):
        """Test chain execution."""
        response = client.post("/chain", json={
            "steps": [
                {"block": "chat", "params": {"provider": "mock"}},
                {"block": "translate", "params": {"provider": "mock", "target": "es"}}
            ],
            "initial_input": "Hello World"
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "steps_executed" in data
        assert "final_output" in data
    
    def test_empty_chain(self):
        """Test empty chain."""
        response = client.post("/chain", json={
            "steps": [],
            "initial_input": "test"
        })
        
        # Should either succeed or handle gracefully
        assert response.status_code in [200, 422]


class TestDriveEndpoints:
    """Tests for drive-specific endpoints."""
    
    def test_local_drive_list(self):
        """Test local drive via execute endpoint."""
        response = client.post("/execute", json={
            "block": "local_drive",
            "input": "/",
            "params": {"operation": "list"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "local_drive"
    
    def test_google_drive_mock(self):
        """Test Google Drive with mock."""
        response = client.post("/execute", json={
            "block": "google_drive",
            "input": {},
            "params": {"operation": "list"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "google_drive"
    
    def test_onedrive_mock(self):
        """Test OneDrive with mock."""
        response = client.post("/execute", json={
            "block": "onedrive",
            "input": {},
            "params": {"operation": "list"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "onedrive"
    
    def test_android_drive_paths(self):
        """Test Android Drive get_paths."""
        response = client.post("/execute", json={
            "block": "android_drive",
            "input": {},
            "params": {"operation": "get_paths"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "android_drive"
        assert "paths" in data["result"]


class TestVectorSearchEndpoints:
    """Tests for vector search specific endpoints."""
    
    def test_vector_search_count(self):
        """Test vector search count operation."""
        response = client.post("/execute", json={
            "block": "vector_search",
            "input": {},
            "params": {"operation": "count", "collection": "test"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "vector_search"
        assert "count" in data["result"]
    
    def test_vector_search_create_collection(self):
        """Test vector search create collection."""
        response = client.post("/execute", json={
            "block": "vector_search",
            "input": "test_collection",
            "params": {"operation": "create_collection"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "vector_search"
    
    def test_vector_search_add_documents(self):
        """Test vector search add documents."""
        response = client.post("/execute", json={
            "block": "vector_search",
            "input": {
                "documents": [
                    {"text": "Test doc 1", "metadata": {"source": "test"}},
                    {"text": "Test doc 2", "metadata": {"source": "test"}}
                ]
            },
            "params": {"operation": "add", "collection": "test"}
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["block"] == "vector_search"
        assert data["result"]["document_count"] == 2
