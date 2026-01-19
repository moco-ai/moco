# -*- coding: utf-8 -*-
"""コードベース検索ツール（インクリメンタル更新対応）"""
import os
import ast
import pickle
import json
import hashlib
import logging
import numpy as np
import faiss
import tiktoken
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv, find_dotenv

logger = logging.getLogger(__name__)
_DOTENV_LOADED = False


def _ensure_dotenv_loaded() -> None:
    """必要に応じて .env を読み込む（多重読み込みを避ける）。"""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    env_path = find_dotenv(usecwd=True) or (Path(__file__).parent.parent.parent / ".env")
    if env_path:
        load_dotenv(env_path)
    _DOTENV_LOADED = True

# OpenAI client (optional)
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None
    OPENAI_AVAILABLE = False

# Gemini client (optional, fallback)
try:
    from google import genai
    GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GENAI_AVAILABLE = False

INDEX_DIR = ".code_index"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
GEMINI_EMBEDDING_MODEL = "text-embedding-004"
TOKEN_LIMIT = 8000  # 安全のため少し低めに設定

def _get_openai_api_key() -> Optional[str]:
    """OpenAI API キーを環境変数から取得する。"""
    _ensure_dotenv_loaded()
    return os.environ.get("OPENAI_API_KEY")


def _get_gemini_api_key() -> Optional[str]:
    """Gemini API キーを環境変数から取得する。"""
    _ensure_dotenv_loaded()
    return (
        os.environ.get("GENAI_API_KEY") or
        os.environ.get("GEMINI_API_KEY") or
        os.environ.get("GOOGLE_API_KEY")
    )


def _get_embedding_provider() -> Optional[str]:
    """利用可能な埋め込みプロバイダーを返す。なければ None。"""
    if OPENAI_AVAILABLE and _get_openai_api_key():
        return "openai"
    if GENAI_AVAILABLE and _get_gemini_api_key():
        return "gemini"
    return None


class CodeChunker:
    """コードをチャンクに分割するクラス"""

    def __init__(self, model_name: str = OPENAI_EMBEDDING_MODEL):
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))

    def chunk_python(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """PythonファイルをASTを使ってクラス/関数単位で分割する"""
        chunks = []
        try:
            tree = ast.parse(content)
            lines = content.splitlines()

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self._process_node(node, lines, file_path, chunks)

            if not chunks and content.strip():
                chunks.extend(self.split_text_by_tokens(content, file_path, 1, "Module"))
        except SyntaxError:
            chunks.extend(self.chunk_generic(content, file_path))

        return chunks

    def _process_node(self, node: ast.AST, lines: List[str], file_path: str, chunks: List[Dict[str, Any]], parent_name: str = ""):
        """ノードを再帰的に処理"""
        start_line = node.lineno
        end_line = getattr(node, 'end_lineno', start_line + 1)
        node_name = getattr(node, 'name', 'unknown')
        full_name = f"{parent_name}.{node_name}" if parent_name else node_name
        
        chunk_content = "\n".join(lines[start_line - 1:end_line])
        
        if self.count_tokens(chunk_content) > TOKEN_LIMIT:
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._process_node(child, lines, file_path, chunks, parent_name=full_name)
            else:
                chunks.extend(self.split_text_by_tokens(chunk_content, file_path, start_line, type(node).__name__))
        else:
            chunks.append({
                "content": chunk_content,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "type": type(node).__name__,
                "name": full_name
            })

    def split_text_by_tokens(self, text: str, file_path: str, start_line: int, node_type: str) -> List[Dict[str, Any]]:
        """トークン制限に基づいてテキストを分割する"""
        tokens = self.encoding.encode(text)
        chunks = []
        for i in range(0, len(tokens), TOKEN_LIMIT):
            chunk_tokens = tokens[i:i + TOKEN_LIMIT]
            chunk_content = self.encoding.decode(chunk_tokens)
            chunks.append({
                "content": chunk_content,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": start_line,
                "type": f"{node_type}_split"
            })
        return chunks

    def chunk_generic(self, content: str, file_path: str, chunk_size: int = 50) -> List[Dict[str, Any]]:
        """汎用的な行ベースの分割"""
        chunks = []
        lines = content.splitlines()
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i:i + chunk_size]
            chunk_content = "\n".join(chunk_lines)
            
            if self.count_tokens(chunk_content) > TOKEN_LIMIT:
                chunks.extend(self.split_text_by_tokens(chunk_content, file_path, i + 1, "Generic"))
            else:
                chunks.append({
                    "content": chunk_content,
                    "file_path": file_path,
                    "start_line": i + 1,
                    "end_line": min(i + chunk_size, len(lines)),
                    "type": "Generic"
                })
        return chunks


class CodebaseSearcher:
    """コードベース検索を管理するクラス（インクリメンタル更新対応）"""

    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or _get_embedding_provider()
        if not self.provider:
            raise ValueError(
                "No embedding provider available. "
                "Set OPENAI_API_KEY or GEMINI_API_KEY environment variable."
            )

        self._openai_client = None
        self._gemini_client = None

        if self.provider == "openai":
            self._openai_client = OpenAI(api_key=_get_openai_api_key())
            self.dimension = 1536
        elif self.provider == "gemini":
            self._gemini_client = genai.Client(api_key=_get_gemini_api_key())
            self.dimension = 768
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        self.chunker = CodeChunker()
        self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict[str, Any]] = []
        self.file_cache: Dict[str, Dict[str, Any]] = {}  # {file_path: {mtime, content_hash, chunk_ids}}
        self._next_id = 0

    def _get_file_info(self, file_path: str) -> Dict[str, Any]:
        """ファイルのmtimeとコンテンツハッシュを取得"""
        stat = os.stat(file_path)
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        return {
            "mtime": stat.st_mtime,
            "content_hash": content_hash,
            "content": content
        }

    def _is_file_changed(self, file_path: str, file_info: Dict[str, Any]) -> bool:
        """ファイルが変更されたかどうかを判定"""
        if file_path not in self.file_cache:
            return True
        cached = self.file_cache[file_path]
        # mtimeとハッシュの両方をチェック（mtimeは精度が低い場合があるため）
        return (cached.get("mtime") != file_info["mtime"] or 
                cached.get("content_hash") != file_info["content_hash"])

    def get_embeddings(self, texts: List[str]) -> np.ndarray:
        """埋め込みを取得"""
        if self.provider == "openai":
            return self._get_openai_embeddings(texts)
        else:
            return self._get_gemini_embeddings(texts)

    def _get_openai_embeddings(self, texts: List[str]) -> np.ndarray:
        response = self._openai_client.embeddings.create(
            input=texts,
            model=OPENAI_EMBEDDING_MODEL
        )
        embeddings = np.array([data.embedding for data in response.data], dtype=np.float32)
        faiss.normalize_L2(embeddings)
        return embeddings

    def _get_gemini_embeddings(self, texts: List[str]) -> np.ndarray:
        embeddings_list = []
        for text in texts:
            response = self._gemini_client.models.embed_content(
                model=GEMINI_EMBEDDING_MODEL,
                contents=text
            )
            emb = response.embeddings[0].values
            embeddings_list.append(emb)
        embeddings = np.array(embeddings_list, dtype=np.float32)
        faiss.normalize_L2(embeddings)
        return embeddings

    def _save_index(self):
        """インデックスとメタデータを保存"""
        if not os.path.exists(INDEX_DIR):
            os.makedirs(INDEX_DIR)
        
        faiss.write_index(self.index, os.path.join(INDEX_DIR, "code.index"))
        with open(os.path.join(INDEX_DIR, "metadata.pkl"), "wb") as f:
            pickle.dump({
                "chunks": self.metadata,
                "file_cache": self.file_cache,
                "next_id": self._next_id
            }, f)

    def _load_index(self) -> bool:
        """インデックスとメタデータをロード"""
        index_path = os.path.join(INDEX_DIR, "code.index")
        metadata_path = os.path.join(INDEX_DIR, "metadata.pkl")
        
        if not os.path.exists(index_path) or not os.path.exists(metadata_path):
            return False
        
        self.index = faiss.read_index(index_path)
        with open(metadata_path, "rb") as f:
            data = pickle.load(f)
            # 後方互換性: 古い形式のメタデータもサポート
            if isinstance(data, list):
                self.metadata = data
                self.file_cache = {}
                self._next_id = len(data)
            else:
                self.metadata = data.get("chunks", [])
                self.file_cache = data.get("file_cache", {})
                self._next_id = data.get("next_id", len(self.metadata))
        return True

    def build_index(self, target_dir: str = ".", extensions: List[str] = None) -> str:
        """インデックスを作成・保存する（全再構築）"""
        if extensions is None:
            extensions = [".py", ".js", ".ts", ".md"]

        all_chunks = []
        self.file_cache = {}
        
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv', '.git')]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    file_path = os.path.join(root, file)
                    try:
                        file_info = self._get_file_info(file_path)
                        content = file_info["content"]
                        
                        if file.endswith(".py"):
                            chunks = self.chunker.chunk_python(content, file_path)
                        else:
                            chunks = self.chunker.chunk_generic(content, file_path)
                        
                        # チャンクにIDを付与
                        chunk_ids = []
                        for chunk in chunks:
                            chunk["id"] = self._next_id
                            chunk_ids.append(self._next_id)
                            self._next_id += 1
                        all_chunks.extend(chunks)
                        
                        # ファイルキャッシュを更新
                        self.file_cache[file_path] = {
                            "mtime": file_info["mtime"],
                            "content_hash": file_info["content_hash"],
                            "chunk_ids": chunk_ids
                        }
                    except Exception as e:
                        logger.warning(f"Error processing {file_path}: {e}")

        if not all_chunks:
            return "No code files found to index."

        # 埋め込みの取得
        texts = [c["content"] for c in all_chunks]
        batch_size = 100
        embeddings_list = []
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            embeddings_list.append(self.get_embeddings(batch_texts))

        embeddings = np.vstack(embeddings_list)

        # FAISSインデックスの作成（IDMap2で削除対応）
        dimension = embeddings.shape[1]
        base_index = faiss.IndexFlatIP(dimension)
        self.index = faiss.IndexIDMap2(base_index)
        
        ids = np.array([c["id"] for c in all_chunks], dtype=np.int64)
        self.index.add_with_ids(embeddings, ids)
        self.metadata = all_chunks

        self._save_index()
        return f"Successfully indexed {len(all_chunks)} chunks from {len(self.file_cache)} files."

    def incremental_update(self, target_dir: str = ".", extensions: List[str] = None) -> str:
        """インクリメンタル更新（変更ファイルのみ処理）"""
        if extensions is None:
            extensions = [".py", ".js", ".ts", ".md"]
        
        # 既存インデックスをロード
        if self.index is None:
            if not self._load_index():
                return self.build_index(target_dir, extensions)
        
        # IndexIDMap2でない場合は再構築が必要
        if not hasattr(self.index, 'remove_ids'):
            logger.info("Rebuilding index for incremental update support...")
            return self.build_index(target_dir, extensions)

        # 現在のファイル一覧を取得
        current_files: Set[str] = set()
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv', '.git')]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    current_files.add(os.path.join(root, file))

        # 変更・追加・削除ファイルを検出
        added_files = []
        modified_files = []
        deleted_files = []

        for file_path in current_files:
            try:
                file_info = self._get_file_info(file_path)
                if file_path not in self.file_cache:
                    added_files.append((file_path, file_info))
                elif self._is_file_changed(file_path, file_info):
                    modified_files.append((file_path, file_info))
            except Exception as e:
                logger.warning(f"Error checking {file_path}: {e}")

        for cached_path in list(self.file_cache.keys()):
            if cached_path not in current_files:
                deleted_files.append(cached_path)

        # 変更がなければ終了
        if not added_files and not modified_files and not deleted_files:
            return "Index is up to date. No changes detected."

        # 削除されたファイルと変更されたファイルのチャンクを削除
        ids_to_remove = []
        for file_path in deleted_files + [f[0] for f in modified_files]:
            if file_path in self.file_cache:
                ids_to_remove.extend(self.file_cache[file_path].get("chunk_ids", []))
                del self.file_cache[file_path]

        if ids_to_remove:
            self.index.remove_ids(np.array(ids_to_remove, dtype=np.int64))
            self.metadata = [c for c in self.metadata if c["id"] not in ids_to_remove]

        # 追加・変更ファイルの新しいチャンクを作成
        new_chunks = []
        for file_path, file_info in added_files + modified_files:
            try:
                content = file_info["content"]
                
                if file_path.endswith(".py"):
                    chunks = self.chunker.chunk_python(content, file_path)
                else:
                    chunks = self.chunker.chunk_generic(content, file_path)
                
                chunk_ids = []
                for chunk in chunks:
                    chunk["id"] = self._next_id
                    chunk_ids.append(self._next_id)
                    self._next_id += 1
                new_chunks.extend(chunks)
                
                self.file_cache[file_path] = {
                    "mtime": file_info["mtime"],
                    "content_hash": file_info["content_hash"],
                    "chunk_ids": chunk_ids
                }
            except Exception as e:
                logger.warning(f"Error processing {file_path}: {e}")

        # 新しいチャンクの埋め込みを取得してインデックスに追加
        if new_chunks:
            texts = [c["content"] for c in new_chunks]
            batch_size = 100
            embeddings_list = []
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i + batch_size]
                embeddings_list.append(self.get_embeddings(batch_texts))

            embeddings = np.vstack(embeddings_list)
            ids = np.array([c["id"] for c in new_chunks], dtype=np.int64)
            self.index.add_with_ids(embeddings, ids)
            self.metadata.extend(new_chunks)

        self._save_index()
        
        return (f"Incremental update complete: "
                f"+{len(added_files)} added, "
                f"~{len(modified_files)} modified, "
                f"-{len(deleted_files)} deleted, "
                f"{len(new_chunks)} new chunks indexed.")

    def search(self, query: str, top_k: int = 5) -> str:
        """クエリで検索する"""
        if self.index is None:
            if not self._load_index():
                return "Index not found. Please build index first with build_code_index() or update_code_index()."

        query_embedding = self.get_embeddings([query])
        scores, indices = self.index.search(query_embedding, top_k)

        # IDMap2の場合、indicesはIDを返す
        id_to_chunk = {c["id"]: c for c in self.metadata}
        
        results = []
        for i, idx in enumerate(indices[0]):
            if idx >= 0:  # -1 は見つからなかった場合
                chunk = id_to_chunk.get(idx)
                if chunk:
                    results.append(
                        f"--- Result {i+1} (Cosine Similarity: {scores[0][i]:.4f}) ---\n"
                        f"File: {chunk['file_path']} (Lines: {chunk['start_line']}-{chunk['end_line']})\n"
                        f"Type: {chunk['type']} ({chunk.get('name', '')})\n"
                        f"```\n{chunk['content']}\n```"
                    )

        return "\n\n".join(results) if results else "No relevant code found."

    def get_stats(self) -> Dict[str, Any]:
        """インデックスの統計情報を取得"""
        if self.index is None:
            self._load_index()
        
        return {
            "total_chunks": len(self.metadata) if self.metadata else 0,
            "total_files": len(self.file_cache),
            "index_size": self.index.ntotal if self.index else 0,
            "files": list(self.file_cache.keys())[:10]  # 最初の10ファイルのみ
        }


# 検索インスタンスのキャッシュ
_searcher_cache: Optional[CodebaseSearcher] = None


def codebase_search(query: str, target_dir: str = ".", top_k: int = 5) -> str:
    """コードベースを検索する（自動的にインクリメンタル更新）"""
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = CodebaseSearcher()

    index_path = os.path.join(INDEX_DIR, "code.index")
    if not os.path.exists(index_path):
        _searcher_cache.build_index(target_dir)
    else:
        # インデックスが存在する場合はインクリメンタル更新
        _searcher_cache.incremental_update(target_dir)
    
    return _searcher_cache.search(query, top_k)


def build_code_index(target_dir: str = ".", extensions: List[str] = None) -> str:
    """指定ディレクトリのコードをインデックス化する（全再構築）"""
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = CodebaseSearcher()
    return _searcher_cache.build_index(target_dir, extensions)


def update_code_index(target_dir: str = ".", extensions: List[str] = None) -> str:
    """指定ディレクトリのコードをインクリメンタル更新する"""
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = CodebaseSearcher()
    return _searcher_cache.incremental_update(target_dir, extensions)


def get_index_stats() -> Dict[str, Any]:
    """インデックスの統計情報を取得する"""
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = CodebaseSearcher()
    return _searcher_cache.get_stats()
