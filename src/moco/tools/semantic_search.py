# -*- coding: utf-8 -*-
"""意味検索ツール（ドキュメント・設定ファイル向け）

codebase_search がコード中心なのに対し、semantic_search は
README, 仕様書, 設定ファイル (.md/.yaml/.json/.txt/.toml など) を
意味ベースで検索するためのツールです。
"""
import os
import pickle
import hashlib
import logging
import numpy as np
import faiss
import tiktoken
from typing import List, Dict, Any, Optional, Set
from pathlib import Path
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


# ドキュメント専用インデックス（codebase_search の .code_index とは別）
INDEX_DIR = ".doc_index"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
GEMINI_EMBEDDING_MODEL = "text-embedding-004"
TOKEN_LIMIT = 8000

# ドキュメント・設定ファイル中心の拡張子（コードは含めない）
DEFAULT_EXTENSIONS = [
    ".md",
    ".txt",
    ".rst",
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".env.example",
    ".gitignore",
]


def _get_openai_api_key() -> Optional[str]:
    _ensure_dotenv_loaded()
    return os.environ.get("OPENAI_API_KEY")


def _get_gemini_api_key() -> Optional[str]:
    _ensure_dotenv_loaded()
    return (
        os.environ.get("GENAI_API_KEY") or
        os.environ.get("GEMINI_API_KEY") or
        os.environ.get("GOOGLE_API_KEY")
    )


def _get_embedding_provider() -> Optional[str]:
    if OPENAI_AVAILABLE and _get_openai_api_key():
        return "openai"
    if GENAI_AVAILABLE and _get_gemini_api_key():
        return "gemini"
    return None


class DocChunker:
    """ドキュメントをチャンクに分割するクラス"""

    def __init__(self, model_name: str = OPENAI_EMBEDDING_MODEL):
        try:
            self.encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def count_tokens(self, text: str) -> int:
        return len(self.encoding.encode(text))

    def chunk_by_sections(self, content: str, file_path: str) -> List[Dict[str, Any]]:
        """Markdown のセクション (## / ###) やパラグラフで分割"""
        chunks = []
        lines = content.splitlines()
        
        current_chunk_lines = []
        current_start = 1
        
        for i, line in enumerate(lines, 1):
            # 見出し行でチャンクを区切る
            if line.startswith("#") and current_chunk_lines:
                chunk_content = "\n".join(current_chunk_lines)
                if chunk_content.strip():
                    chunks.extend(self._maybe_split(chunk_content, file_path, current_start))
                current_chunk_lines = [line]
                current_start = i
            else:
                current_chunk_lines.append(line)
        
        # 残りを処理
        if current_chunk_lines:
            chunk_content = "\n".join(current_chunk_lines)
            if chunk_content.strip():
                chunks.extend(self._maybe_split(chunk_content, file_path, current_start))
        
        # チャンクが空なら全体を1チャンクに
        if not chunks and content.strip():
            chunks.extend(self._maybe_split(content, file_path, 1))
        
        return chunks

    def _maybe_split(self, content: str, file_path: str, start_line: int) -> List[Dict[str, Any]]:
        """トークン制限を超える場合は分割"""
        if self.count_tokens(content) <= TOKEN_LIMIT:
            return [{
                "content": content,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": start_line + content.count("\n"),
                "type": "Section",
            }]
        
        # 分割が必要
        tokens = self.encoding.encode(content)
        chunks = []
        for i in range(0, len(tokens), TOKEN_LIMIT):
            chunk_tokens = tokens[i:i + TOKEN_LIMIT]
            chunk_content = self.encoding.decode(chunk_tokens)
            chunks.append({
                "content": chunk_content,
                "file_path": file_path,
                "start_line": start_line,
                "end_line": start_line,
                "type": "Section_split",
            })
        return chunks

    def chunk_generic(self, content: str, file_path: str, chunk_size: int = 50) -> List[Dict[str, Any]]:
        """汎用的な行ベースの分割"""
        chunks = []
        lines = content.splitlines()
        for i in range(0, len(lines), chunk_size):
            chunk_lines = lines[i:i + chunk_size]
            chunk_content = "\n".join(chunk_lines)
            chunks.extend(self._maybe_split(chunk_content, file_path, i + 1))
        return chunks


class SemanticSearcher:
    """ドキュメント向け意味検索を管理するクラス"""

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

        self.chunker = DocChunker()
        self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict[str, Any]] = []
        self.file_cache: Dict[str, Dict[str, Any]] = {}
        self._next_id = 0

    def _get_file_info(self, file_path: str) -> Dict[str, Any]:
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
        if file_path not in self.file_cache:
            return True
        cached = self.file_cache[file_path]
        return (cached.get("mtime") != file_info["mtime"] or 
                cached.get("content_hash") != file_info["content_hash"])

    def get_embeddings(self, texts: List[str]) -> np.ndarray:
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
        if not os.path.exists(INDEX_DIR):
            os.makedirs(INDEX_DIR)
        
        faiss.write_index(self.index, os.path.join(INDEX_DIR, "doc.index"))
        with open(os.path.join(INDEX_DIR, "metadata.pkl"), "wb") as f:
            pickle.dump({
                "chunks": self.metadata,
                "file_cache": self.file_cache,
                "next_id": self._next_id
            }, f)

    def _load_index(self) -> bool:
        index_path = os.path.join(INDEX_DIR, "doc.index")
        metadata_path = os.path.join(INDEX_DIR, "metadata.pkl")
        
        if not os.path.exists(index_path) or not os.path.exists(metadata_path):
            return False
        
        self.index = faiss.read_index(index_path)
        with open(metadata_path, "rb") as f:
            data = pickle.load(f)
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
        if extensions is None:
            extensions = DEFAULT_EXTENSIONS

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
                        
                        if file.endswith(".md"):
                            chunks = self.chunker.chunk_by_sections(content, file_path)
                        else:
                            chunks = self.chunker.chunk_generic(content, file_path)
                        
                        chunk_ids = []
                        for chunk in chunks:
                            chunk["id"] = self._next_id
                            chunk_ids.append(self._next_id)
                            self._next_id += 1
                        all_chunks.extend(chunks)
                        
                        self.file_cache[file_path] = {
                            "mtime": file_info["mtime"],
                            "content_hash": file_info["content_hash"],
                            "chunk_ids": chunk_ids
                        }
                    except Exception as e:
                        logger.warning(f"Error processing {file_path}: {e}")

        if not all_chunks:
            return "No document files found to index."

        texts = [c["content"] for c in all_chunks]
        batch_size = 100
        embeddings_list = []
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            embeddings_list.append(self.get_embeddings(batch_texts))

        embeddings = np.vstack(embeddings_list)

        dimension = embeddings.shape[1]
        base_index = faiss.IndexFlatIP(dimension)
        self.index = faiss.IndexIDMap2(base_index)
        
        ids = np.array([c["id"] for c in all_chunks], dtype=np.int64)
        self.index.add_with_ids(embeddings, ids)
        self.metadata = all_chunks

        self._save_index()
        return f"Successfully indexed {len(all_chunks)} chunks from {len(self.file_cache)} files."

    def incremental_update(self, target_dir: str = ".", extensions: List[str] = None) -> str:
        if extensions is None:
            extensions = DEFAULT_EXTENSIONS
        
        if self.index is None:
            if not self._load_index():
                return self.build_index(target_dir, extensions)
        
        if not hasattr(self.index, 'remove_ids'):
            logger.info("Rebuilding index for incremental update support...")
            return self.build_index(target_dir, extensions)

        current_files: Set[str] = set()
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv', '.git')]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    current_files.add(os.path.join(root, file))

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

        if not added_files and not modified_files and not deleted_files:
            return "Index is up to date. No changes detected."

        ids_to_remove = []
        for file_path in deleted_files + [f[0] for f in modified_files]:
            if file_path in self.file_cache:
                ids_to_remove.extend(self.file_cache[file_path].get("chunk_ids", []))
                del self.file_cache[file_path]

        if ids_to_remove:
            self.index.remove_ids(np.array(ids_to_remove, dtype=np.int64))
            self.metadata = [c for c in self.metadata if c["id"] not in ids_to_remove]

        new_chunks = []
        for file_path, file_info in added_files + modified_files:
            try:
                content = file_info["content"]
                
                if file_path.endswith(".md"):
                    chunks = self.chunker.chunk_by_sections(content, file_path)
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
        if self.index is None:
            if not self._load_index():
                return "Index not found. Please build index first with build_doc_index() or update_doc_index()."

        query_embedding = self.get_embeddings([query])
        scores, indices = self.index.search(query_embedding, top_k)

        id_to_chunk = {c["id"]: c for c in self.metadata}
        
        results = []
        for i, idx in enumerate(indices[0]):
            if idx >= 0:
                chunk = id_to_chunk.get(idx)
                if chunk:
                    results.append(
                        f"--- Result {i+1} (Similarity: {scores[0][i]:.4f}) ---\n"
                        f"File: {chunk['file_path']} (Lines: {chunk['start_line']}-{chunk['end_line']})\n"
                        f"```\n{chunk['content']}\n```"
                    )

        return "\n\n".join(results) if results else "No relevant documents found."


# シングルトンキャッシュ
_searcher_cache: Optional[SemanticSearcher] = None


def semantic_search(query: str, target_dir: str = ".", top_k: int = 5) -> str:
    """
    ドキュメント・設定ファイルを意味ベースで検索します。

    コードではなく、README, 仕様書, 設定ファイル (.md, .yaml, .json, .txt, .toml 等) を
    「意味が近い」順に探したいときに使います。

    使い分け:
    - grep / ripgrep: 文字列が分かっているとき（完全一致・正規表現）
    - codebase_search: コード (.py, .js, .ts 等) を意味検索したいとき
    - semantic_search: ドキュメント・設定を意味検索したいとき（シンボル名が分からない場合に有効）

    Args:
        query: 検索クエリ（自然言語で OK、例: "認証の設定方法", "API キーはどこで設定？"）
        target_dir: 検索対象ディレクトリ（デフォルト: カレント）
        top_k: 返す結果数（デフォルト: 5）

    Returns:
        類似度順にマッチしたドキュメントのチャンク
    """
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = SemanticSearcher()

    index_path = os.path.join(INDEX_DIR, "doc.index")
    if not os.path.exists(index_path):
        _searcher_cache.build_index(target_dir)
    else:
        _searcher_cache.incremental_update(target_dir)
    
    return _searcher_cache.search(query, top_k)


def build_doc_index(target_dir: str = ".", extensions: List[str] = None) -> str:
    """
    ドキュメント・設定ファイルのインデックスを構築します（全再構築）。

    Args:
        target_dir: インデックス対象ディレクトリ
        extensions: 対象拡張子のリスト（省略時はデフォルト）

    Returns:
        構築結果のメッセージ
    """
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = SemanticSearcher()
    return _searcher_cache.build_index(target_dir, extensions)


def update_doc_index(target_dir: str = ".", extensions: List[str] = None) -> str:
    """
    ドキュメント・設定ファイルのインデックスをインクリメンタル更新します。

    Args:
        target_dir: インデックス対象ディレクトリ
        extensions: 対象拡張子のリスト（省略時はデフォルト）

    Returns:
        更新結果のメッセージ
    """
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = SemanticSearcher()
    return _searcher_cache.incremental_update(target_dir, extensions)
