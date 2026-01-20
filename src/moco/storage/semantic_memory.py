import os
import sqlite3
import json
from ..utils.json_parser import SmartJSONParser
import logging
import numpy as np
import faiss
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Gemini client for embeddings
try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False

DEFAULT_EMBEDDING_MODEL = "text-embedding-004"

class SemanticMemory:
    """
    Semantic Memory using FAISS and Gemini Embeddings.
    Stores documents and their embeddings for similarity search.
    """

    def __init__(self, db_path: str, embedding_model: str = DEFAULT_EMBEDDING_MODEL):
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.dimension = 768  # text-embedding-004 dimension
        self.index = faiss.IndexFlatL2(self.dimension)
        self.doc_ids = []  # List to map index position to doc_id
        
        # Initialize DB and load existing data
        self._init_db()
        self._load_all_documents()

    def _get_client(self):
        api_key = (
            os.environ.get("GENAI_API_KEY") or
            os.environ.get("GEMINI_API_KEY") or
            os.environ.get("GOOGLE_API_KEY")
        )
        if not api_key:
            raise ValueError("Gemini API key not found in environment variables.")
        return genai.Client(api_key=api_key)

    def _init_db(self):
        """Initialize SQLite table for documents."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS semantic_documents (
                doc_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                metadata TEXT,
                embedding BLOB,
                created_at TIMESTAMP NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _load_all_documents(self):
        """Load all documents from DB into FAISS index."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT doc_id, embedding FROM semantic_documents WHERE embedding IS NOT NULL")
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return

            embeddings = []
            for doc_id, emb_blob in rows:
                emb = np.frombuffer(emb_blob, dtype=np.float32)
                if emb.shape[0] == self.dimension:
                    embeddings.append(emb)
                    self.doc_ids.append(doc_id)
                else:
                    logger.warning(f"Embedding dimension mismatch for {doc_id}: {emb.shape[0]}")

            if embeddings:
                self.index.add(np.array(embeddings).astype('float32'))
                logger.info(f"Loaded {len(embeddings)} documents into semantic memory.")
        except Exception as e:
            logger.error(f"Failed to load documents into semantic memory: {e}")

    def _get_embedding(self, text: str) -> np.ndarray:
        """Get embedding for text using Gemini API."""
        if not GENAI_AVAILABLE:
            raise RuntimeError("google-genai library is not installed.")
        
        client = self._get_client()
        response = client.models.embed_content(
            model=self.embedding_model,
            contents=text
        )
        # Handle both single and batch responses
        embedding = response.embeddings[0].values
        return np.array(embedding).astype('float32')

    def add_document(self, doc_id: str, content: str, metadata: Optional[Dict[str, Any]] = None):
        """Add a document to semantic memory."""
        try:
            embedding = self._get_embedding(content)
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            now = datetime.now().isoformat()
            metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
            emb_blob = embedding.tobytes()
            
            cursor.execute("""
                INSERT OR REPLACE INTO semantic_documents (doc_id, content, metadata, embedding, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (doc_id, content, metadata_json, emb_blob, now))
            
            conn.commit()
            conn.close()
            
            # Update in-memory index
            self.index.add(np.array([embedding]))
            self.doc_ids.append(doc_id)
            
            logger.info(f"Added document {doc_id} to semantic memory.")
        except Exception as e:
            logger.error(f"Failed to add document {doc_id}: {e}")
            raise e

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search for similar documents."""
        if self.index.ntotal == 0:
            return []
            
        try:
            query_embedding = self._get_embedding(query)
            
            # Search FAISS
            distances, indices = self.index.search(np.array([query_embedding]), top_k)
            
            results = []
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            for i, idx in enumerate(indices[0]):
                if idx == -1: continue
                
                doc_id = self.doc_ids[idx]
                cursor.execute("SELECT doc_id, content, metadata FROM semantic_documents WHERE doc_id = ?", (doc_id,))
                row = cursor.fetchone()
                
                if row:
                    res = dict(row)
                    res['score'] = float(distances[0][i])
                    try:
                        res['metadata'] = SmartJSONParser.parse(res['metadata']) or {}
                    except Exception:
                        res['metadata'] = {}
                    results.append(res)
            
            conn.close()
            return results
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    def clear(self):
        """Clear all documents."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM semantic_documents")
            conn.commit()
            conn.close()
            
            self.index = faiss.IndexFlatL2(self.dimension)
            self.doc_ids = []
            logger.info("Cleared semantic memory.")
        except Exception as e:
            logger.error(f"Clear failed: {e}")
