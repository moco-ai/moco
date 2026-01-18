# -*- coding: utf-8 -*-
"""コードベース検索ツール"""
import os
import ast
import pickle
import numpy as np
import faiss
import tiktoken
from typing import List, Dict, Any, Optional
from pathlib import Path

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

# 環境変数の確認
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GEMINI_API_KEY = (
    os.environ.get("GENAI_API_KEY") or
    os.environ.get("GEMINI_API_KEY") or
    os.environ.get("GOOGLE_API_KEY")
)
INDEX_DIR = ".code_index"
OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
GEMINI_EMBEDDING_MODEL = "text-embedding-004"
TOKEN_LIMIT = 8000  # 安全のため少し低めに設定

# 利用可能なプロバイダーを判定
def _get_embedding_provider() -> Optional[str]:
    """利用可能な埋め込みプロバイダーを返す。なければ None。"""
    if OPENAI_AVAILABLE and OPENAI_API_KEY:
        return "openai"
    if GENAI_AVAILABLE and GEMINI_API_KEY:
        return "gemini"
    return None

EMBEDDING_PROVIDER = _get_embedding_provider()

# モジュールインポート時にプロバイダーがない場合はエラーを発生させる
# これにより tools/__init__.py の try/except で捕捉される
if EMBEDDING_PROVIDER is None:
    raise ValueError(
        "No embedding provider available for codebase_search. "
        "Set OPENAI_API_KEY or GEMINI_API_KEY environment variable."
    )

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

            # トップレベルのノードのみを処理して重複を避ける
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    self._process_node(node, lines, file_path, chunks)

            # ASTで取れなかったトップレベルのコード（変数定義など）がある場合のケア
            if not chunks and content.strip():
                chunks.extend(self.split_text_by_tokens(content, file_path, 1, "Module"))
        except SyntaxError:
            # 文法エラーがある場合は行ベースにフォールバック
            chunks.extend(self.chunk_generic(content, file_path))

        return chunks

    def _process_node(self, node: ast.AST, lines: List[str], file_path: str, chunks: List[Dict[str, Any]], parent_name: str = ""):
        """ノードを再帰的に処理（クラス内のメソッド抽出など）"""
        start_line = node.lineno
        end_line = getattr(node, 'end_lineno', start_line + 1)
        node_name = getattr(node, 'name', 'unknown')
        full_name = f"{parent_name}.{node_name}" if parent_name else node_name
        
        chunk_content = "\n".join(lines[start_line - 1:end_line])
        
        # トークン制限チェック
        if self.count_tokens(chunk_content) > TOKEN_LIMIT:
            # クラスの場合はメソッド単位で分解を試みる
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._process_node(child, lines, file_path, chunks, parent_name=full_name)
            else:
                # それでも大きい場合は単純分割
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
                "start_line": start_line, # 簡易的に開始行のみ保持
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
    """コードベース検索を管理するクラス"""

    def __init__(self, provider: Optional[str] = None):
        """
        Args:
            provider: 'openai', 'gemini', または None（自動検出）
        """
        self.provider = provider or EMBEDDING_PROVIDER
        if not self.provider:
            raise ValueError(
                "No embedding provider available. "
                "Set OPENAI_API_KEY or GEMINI_API_KEY (GOOGLE_API_KEY) environment variable."
            )

        self._openai_client = None
        self._gemini_client = None

        if self.provider == "openai":
            self._openai_client = OpenAI(api_key=OPENAI_API_KEY)
            self.dimension = 1536  # text-embedding-3-small
        elif self.provider == "gemini":
            self._gemini_client = genai.Client(api_key=GEMINI_API_KEY)
            self.dimension = 768  # text-embedding-004
        else:
            raise ValueError(f"Unknown provider: {self.provider}")

        self.chunker = CodeChunker()
        self.index: Optional[faiss.Index] = None
        self.metadata: List[Dict[str, Any]] = []

    def get_embeddings(self, texts: List[str]) -> np.ndarray:
        """埋め込みを取得し、正規化する（プロバイダーに応じて切り替え）"""
        if self.provider == "openai":
            return self._get_openai_embeddings(texts)
        else:
            return self._get_gemini_embeddings(texts)

    def _get_openai_embeddings(self, texts: List[str]) -> np.ndarray:
        """OpenAI APIを使用して埋め込みを取得"""
        response = self._openai_client.embeddings.create(
            input=texts,
            model=OPENAI_EMBEDDING_MODEL
        )
        embeddings = np.array([data.embedding for data in response.data], dtype=np.float32)
        faiss.normalize_L2(embeddings)  # コサイン類似度のために正規化
        return embeddings

    def _get_gemini_embeddings(self, texts: List[str]) -> np.ndarray:
        """Gemini APIを使用して埋め込みを取得"""
        embeddings_list = []
        for text in texts:
            response = self._gemini_client.models.embed_content(
                model=GEMINI_EMBEDDING_MODEL,
                contents=text
            )
            emb = response.embeddings[0].values
            embeddings_list.append(emb)
        embeddings = np.array(embeddings_list, dtype=np.float32)
        faiss.normalize_L2(embeddings)  # コサイン類似度のために正規化
        return embeddings

    def build_index(self, target_dir: str = ".", extensions: List[str] = None) -> str:
        """インデックスを作成・保存する"""
        if extensions is None:
            extensions = [".py", ".js", ".ts", ".md"]

        all_chunks = []
        for root, dirs, files in os.walk(target_dir):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', '__pycache__', 'venv', '.git')]
            for file in files:
                if any(file.endswith(ext) for ext in extensions):
                    file_path = os.path.join(root, file)
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            content = f.read()
                        
                        if file.endswith(".py"):
                            chunks = self.chunker.chunk_python(content, file_path)
                        else:
                            chunks = self.chunker.chunk_generic(content, file_path)
                        all_chunks.extend(chunks)
                    except Exception as e:
                        print(f"Error processing {file_path}: {e}")

        if not all_chunks:
            return "No code files found to index."

        # 埋め込みの取得 (バッチ処理)
        texts = [c["content"] for c in all_chunks]
        batch_size = 100
        embeddings_list = []
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            embeddings_list.append(self.get_embeddings(batch_texts))

        embeddings = np.vstack(embeddings_list)

        # FAISSインデックスの作成 (Inner Product + L2正規化 = コサイン類似度)
        dimension = embeddings.shape[1]
        self.index = faiss.IndexFlatIP(dimension)
        self.index.add(embeddings)
        self.metadata = all_chunks

        # 保存
        if not os.path.exists(INDEX_DIR):
            os.makedirs(INDEX_DIR)
        faiss.write_index(self.index, os.path.join(INDEX_DIR, "code.index"))
        with open(os.path.join(INDEX_DIR, "metadata.pkl"), "wb") as f:
            pickle.dump(self.metadata, f)

        return f"Successfully indexed {len(all_chunks)} chunks."

    def search(self, query: str, top_k: int = 5) -> str:
        """クエリで検索する"""
        index_path = os.path.join(INDEX_DIR, "code.index")
        metadata_path = os.path.join(INDEX_DIR, "metadata.pkl")

        if self.index is None:
            if not os.path.exists(index_path) or not os.path.exists(metadata_path):
                return "Index not found. Please build index first."
            self.index = faiss.read_index(index_path)
            with open(metadata_path, "rb") as f:
                self.metadata = pickle.load(f)

        query_embedding = self.get_embeddings([query])
        # IndexFlatIP なので scores は内積（正規化済みなのでコサイン類似度）
        scores, indices = self.index.search(query_embedding, top_k)

        results = []
        for i, idx in enumerate(indices[0]):
            if idx < len(self.metadata):
                chunk = self.metadata[idx]
                results.append(
                    f"--- Result {i+1} (Cosine Similarity: {scores[0][i]:.4f}) ---\n"
                    f"File: {chunk['file_path']} (Lines: {chunk['start_line']}-{chunk['end_line']})\n"
                    f"Type: {chunk['type']} ({chunk.get('name', '')})\n"
                    f"```\n{chunk['content']}\n```"
                )

        return "\n\n".join(results) if results else "No relevant code found."


# 検索インスタンスのキャッシュ
_searcher_cache: Optional[CodebaseSearcher] = None


# 互換性のための簡易関数
def codebase_search(query: str, target_dir: str = ".", top_k: int = 5) -> str:
    """コードベースを検索する（インスタンスをキャッシュして高速化）"""
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = CodebaseSearcher()

    index_path = os.path.join(INDEX_DIR, "code.index")
    if not os.path.exists(index_path):
        _searcher_cache.build_index(target_dir)
    return _searcher_cache.search(query, top_k)


def build_code_index(target_dir: str = ".", extensions: List[str] = None) -> str:
    """指定ディレクトリのコードをインデックス化する"""
    global _searcher_cache
    if _searcher_cache is None:
        _searcher_cache = CodebaseSearcher()
    return _searcher_cache.build_index(target_dir, extensions)
