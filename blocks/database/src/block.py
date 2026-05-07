"""Database Block - SQLite/PostgreSQL persistence"""
from blocks.base import LegoBlock
from typing import Dict, Any, List, Optional
import json

class DatabaseBlock(LegoBlock):
    """SQL Database - SQLite (local) or PostgreSQL (cloud)"""
    name = "database"
    version = "1.0.0"
    requires = ["config"]
    layer = 0  # Infrastructure layer
    tags = ["infrastructure", "database", "storage"]
    default_config = {
        "backend": "sqlite",
        "connection_string": "sqlite:///data/cerebrum.db"
    }
    
    def __init__(self, hal_block, config: Dict[str, Any]):
        super().__init__(hal_block, config)
        self.backend = config.get("backend", "sqlite")
        self.connection_string = config.get("connection_string", "sqlite:///data/cerebrum.db")
        self._connection = None
        
    async def initialize(self):
        """Initialize database connection"""
        print(f"🗄️  Database Block initialized")
        print(f"   Backend: {self.backend}")
        print(f"   Connection: {self.connection_string}")
        
        if self.backend == "sqlite":
            import sqlite3
            import os
            # Ensure directory exists
            db_path = self.connection_string.replace("sqlite:///", "")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            
            self._connection = sqlite3.connect(db_path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
        
        elif self.backend == "postgresql":
            try:
                import psycopg2
                self._connection = psycopg2.connect(self.connection_string)
            except ImportError:
                print("   Warning: psycopg2 not installed, using sqlite fallback")
                self.backend = "sqlite"
                import sqlite3
                self._connection = sqlite3.connect("data/cerebrum.db", check_same_thread=False)
        
        return True
    
    async def execute(self, input_data: Dict) -> Dict:
        action = input_data.get("action")
        if action == "query":
            return await self._query(input_data)
        elif action == "insert":
            return await self._insert(input_data)
        elif action == "update":
            return await self._update(input_data)
        elif action == "delete":
            return await self._delete(input_data)
        elif action == "create_table":
            return await self._create_table(input_data)
        elif action == "list_tables":
            return await self._list_tables()
        return {"error": "Unknown action"}
    
    async def _query(self, data: Dict) -> Dict:
        """Execute SELECT query"""
        sql = data.get("sql")
        params = data.get("params", ())
        
        try:
            cursor = self._connection.cursor()
            cursor.execute(sql, params)
            
            rows = cursor.fetchall()
            columns = [description[0] for description in cursor.description] if cursor.description else []
            
            # Convert to dict
            results = []
            for row in rows:
                results.append(dict(zip(columns, row)))
            
            return {
                "rows": results,
                "count": len(results),
                "columns": columns
            }
            
        except Exception as e:
            return {"error": f"Query failed: {str(e)}", "sql": sql}
    
    async def _insert(self, data: Dict) -> Dict:
        """Insert data"""
        table = data.get("table")
        values = data.get("values", {})
        
        columns = ", ".join(values.keys())
        placeholders = ", ".join(["?" if self.backend == "sqlite" else "%s"] * len(values))
        sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        
        try:
            cursor = self._connection.cursor()
            cursor.execute(sql, tuple(values.values()))
            self._connection.commit()
            
            # Get last insert id
            last_id = cursor.lastrowid if self.backend == "sqlite" else None
            
            return {
                "inserted": True,
                "id": last_id,
                "rows_affected": cursor.rowcount
            }
            
        except Exception as e:
            return {"error": f"Insert failed: {str(e)}"}
    
    async def _update(self, data: Dict) -> Dict:
        """Update data"""
        table = data.get("table")
        values = data.get("values", {})
        where = data.get("where", "")
        where_params = data.get("where_params", ())
        
        set_clause = ", ".join([f"{k} = ?" if self.backend == "sqlite" else f"{k} = %s" for k in values.keys()])
        sql = f"UPDATE {table} SET {set_clause} WHERE {where}"
        
        try:
            cursor = self._connection.cursor()
            cursor.execute(sql, tuple(values.values()) + tuple(where_params))
            self._connection.commit()
            
            return {
                "updated": True,
                "rows_affected": cursor.rowcount
            }
            
        except Exception as e:
            return {"error": f"Update failed: {str(e)}"}
    
    async def _delete(self, data: Dict) -> Dict:
        """Delete data"""
        table = data.get("table")
        where = data.get("where", "")
        where_params = data.get("where_params", ())
        
        sql = f"DELETE FROM {table} WHERE {where}"
        
        try:
            cursor = self._connection.cursor()
            cursor.execute(sql, where_params)
            self._connection.commit()
            
            return {
                "deleted": True,
                "rows_affected": cursor.rowcount
            }
            
        except Exception as e:
            return {"error": f"Delete failed: {str(e)}"}
    
    async def _create_table(self, data: Dict) -> Dict:
        """Create table"""
        table = data.get("table")
        schema = data.get("schema", {})  # {column: type}
        
        columns_def = ", ".join([f"{col} {dtype}" for col, dtype in schema.items()])
        sql = f"CREATE TABLE IF NOT EXISTS {table} ({columns_def})"
        
        try:
            cursor = self._connection.cursor()
            cursor.execute(sql)
            self._connection.commit()
            
            return {"created": True, "table": table}
            
        except Exception as e:
            return {"error": f"Create table failed: {str(e)}"}
    
    async def _list_tables(self) -> Dict:
        """List all tables"""
        try:
            if self.backend == "sqlite":
                cursor = self._connection.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
            else:
                cursor = self._connection.cursor()
                cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                tables = [row[0] for row in cursor.fetchall()]
            
            return {"tables": tables, "count": len(tables)}
            
        except Exception as e:
            return {"error": f"List tables failed: {str(e)}"}
    
    def health(self) -> Dict:
        h = super().health()
        h["backend"] = self.backend
        h["connected"] = self._connection is not None
        return h
