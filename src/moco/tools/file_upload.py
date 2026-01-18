# -*- coding: utf-8 -*-
"""ファイルアップロード・解析ツール"""
import base64
import json
import mimetypes
import os
from typing import Any

# オプショナル依存の遅延インポート
_PYPDF_AVAILABLE = None
_PANDAS_AVAILABLE = None


def _check_pypdf() -> bool:
    """pypdfが利用可能か確認"""
    global _PYPDF_AVAILABLE
    if _PYPDF_AVAILABLE is None:
        try:
            import pypdf  # noqa: F401
            _PYPDF_AVAILABLE = True
        except ImportError:
            _PYPDF_AVAILABLE = False
    return _PYPDF_AVAILABLE


def _check_pandas() -> bool:
    """pandasが利用可能か確認"""
    global _PANDAS_AVAILABLE
    if _PANDAS_AVAILABLE is None:
        try:
            import pandas  # noqa: F401
            _PANDAS_AVAILABLE = True
        except ImportError:
            _PANDAS_AVAILABLE = False
    return _PANDAS_AVAILABLE


# 拡張子からMIMEタイプへのマッピング（mimetypesで取得できないもの用）
EXTENSION_MIME_MAP = {
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".ts": "text/typescript",
    ".jsx": "text/jsx",
    ".tsx": "text/tsx",
    ".vue": "text/vue",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".java": "text/x-java",
    ".kt": "text/x-kotlin",
    ".swift": "text/x-swift",
    ".c": "text/x-c",
    ".cpp": "text/x-c++",
    ".h": "text/x-c-header",
    ".hpp": "text/x-c++-header",
    ".cs": "text/x-csharp",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/toml",
    ".ini": "text/ini",
    ".cfg": "text/ini",
    ".sh": "text/x-shellscript",
    ".bash": "text/x-shellscript",
    ".zsh": "text/x-shellscript",
    ".sql": "text/x-sql",
    ".graphql": "text/x-graphql",
    ".dockerfile": "text/x-dockerfile",
}

# テキストとして扱うMIMEタイプのプレフィックス
TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml", "application/javascript")

# ファイルサイズ上限（警告表示用）
MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100MB

# 画像のMIMEタイプ
IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/bmp",
    "image/tiff",
}


def _get_mime_type(file_path: str) -> str:
    """ファイルパスからMIMEタイプを判定"""
    ext = os.path.splitext(file_path)[1].lower()
    
    # カスタムマッピングを優先
    if ext in EXTENSION_MIME_MAP:
        return EXTENSION_MIME_MAP[ext]
    
    # mimetypesモジュールで判定
    mime_type, _ = mimetypes.guess_type(file_path)
    
    return mime_type or "application/octet-stream"


def _is_text_file(mime_type: str) -> bool:
    """テキストファイルかどうか判定"""
    return any(mime_type.startswith(prefix) for prefix in TEXT_MIME_PREFIXES)


def _read_text_file(file_path: str) -> str:
    """テキストファイルを読み込む"""
    encodings = ["utf-8", "utf-8-sig", "cp932", "shift_jis", "euc-jp", "latin-1"]
    
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    
    raise ValueError(f"Unable to decode file with supported encodings: {encodings}")


def _extract_pdf_text(file_path: str) -> str:
    """PDFからテキストを抽出"""
    if not _check_pypdf():
        return "[Error: pypdf is not installed. Install with: pip install pypdf]"
    
    import pypdf
    
    text_parts = []
    with open(file_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        total_pages = len(reader.pages)
        
        for i, page in enumerate(reader.pages):
            page_text = page.extract_text()
            if page_text:
                text_parts.append(f"--- Page {i + 1}/{total_pages} ---\n{page_text}")
    
    if not text_parts:
        return "[No text content found in PDF]"
    
    return "\n\n".join(text_parts)


def _read_csv_with_encoding(file_path: str) -> list[list[str]]:
    """複数エンコーディングでCSVを読み込む"""
    import csv

    encodings = ["utf-8", "utf-8-sig", "cp932", "shift_jis", "euc-jp", "latin-1"]
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding, newline="") as f:
                return list(csv.reader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode CSV with supported encodings: {encodings}")


def _read_csv_with_pandas(file_path: str):
    """pandasで複数エンコーディングを試行してCSVを読み込む"""
    import pandas as pd

    encodings = ["utf-8", "utf-8-sig", "cp932", "shift_jis", "euc-jp", "latin-1"]
    for encoding in encodings:
        try:
            return pd.read_csv(file_path, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode CSV with supported encodings: {encodings}")


def _process_csv(file_path: str) -> dict[str, Any]:
    """CSVを読み込んで要約を返す"""
    if not _check_pandas():
        # pandasがない場合は標準ライブラリで処理
        rows = _read_csv_with_encoding(file_path)

        if not rows:
            return {"summary": "Empty CSV file", "rows": []}

        headers = rows[0] if rows else []
        data_rows = rows[1:11]  # 最初の10行

        return {
            "summary": f"CSV with {len(rows) - 1} rows, {len(headers)} columns",
            "columns": headers,
            "preview_rows": data_rows,
            "total_rows": len(rows) - 1,
        }

    import pandas as pd
    df = _read_csv_with_pandas(file_path)
    
    # 統計情報を取得
    stats = {}
    for col in df.columns:
        col_stats = {"dtype": str(df[col].dtype)}
        if df[col].dtype in ["int64", "float64"]:
            col_stats.update({
                "min": float(df[col].min()) if pd.notna(df[col].min()) else None,
                "max": float(df[col].max()) if pd.notna(df[col].max()) else None,
                "mean": float(df[col].mean()) if pd.notna(df[col].mean()) else None,
            })
        col_stats["null_count"] = int(df[col].isna().sum())
        stats[col] = col_stats
    
    # プレビュー（最初の10行）
    preview = df.head(10).to_dict(orient="records")
    
    return {
        "summary": f"CSV with {len(df)} rows, {len(df.columns)} columns",
        "columns": list(df.columns),
        "column_stats": stats,
        "preview_rows": preview,
        "total_rows": len(df),
    }


def _process_json(file_path: str) -> dict[str, Any]:
    """JSONを読み込んでパース"""
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    # 構造の要約
    def summarize_structure(obj: Any, depth: int = 0, max_depth: int = 3) -> str:
        if depth >= max_depth:
            return "..."
        
        if isinstance(obj, dict):
            if not obj:
                return "{}"
            keys = list(obj.keys())[:5]
            key_summary = ", ".join(f'"{k}"' for k in keys)
            if len(obj) > 5:
                key_summary += f", ... ({len(obj)} keys total)"
            return f"{{{key_summary}}}"
        elif isinstance(obj, list):
            if not obj:
                return "[]"
            return f"[{summarize_structure(obj[0], depth + 1)}] ({len(obj)} items)"
        elif isinstance(obj, str):
            return "string"
        elif isinstance(obj, bool):
            return "boolean"
        elif isinstance(obj, int):
            return "integer"
        elif isinstance(obj, float):
            return "number"
        elif obj is None:
            return "null"
        else:
            return type(obj).__name__
    
    return {
        "structure": summarize_structure(data),
        "data": data,
    }


def _encode_image_base64(file_path: str) -> str:
    """画像をBase64エンコード"""
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def file_upload(file_path: str, extract_text: bool = True) -> dict[str, Any]:
    """
    ファイルを読み込み、内容を解析して返す。

    対応フォーマット:
    - テキスト（.txt, .md, .py, .js 等）: そのまま読み込み
    - PDF: テキスト抽出（pypdfが必要）
    - CSV: 要約表示（pandasがあれば統計情報付き）
    - JSON: パース＆整形表示
    - 画像（.png, .jpg 等）: Base64エンコード（Vision用）

    Args:
        file_path: ファイルパス
        extract_text: Trueの場合テキスト抽出、Falseの場合生データ

    Returns:
        dict: {
            "filename": str,
            "mime_type": str,
            "size_bytes": int,
            "content": str | dict,  # テキストまたは構造化データ
            "base64": str | None,   # 画像の場合のみ
            "error": str | None,    # エラーがある場合
        }

    Examples:
        file_upload("document.pdf")  # PDFテキスト抽出
        file_upload("data.csv")      # CSV要約
        file_upload("image.png")     # Base64エンコード
        file_upload("config.json")   # JSONパース
    """
    # 入力バリデーション
    if not file_path:
        return {"error": "file_path is required"}
    
    if not os.path.exists(file_path):
        return {"error": f"File not found: {file_path}"}
    
    if not os.path.isfile(file_path):
        return {"error": f"Not a file: {file_path}"}
    
    # 基本情報を取得
    filename = os.path.basename(file_path)
    size_bytes = os.path.getsize(file_path)
    mime_type = _get_mime_type(file_path)
    ext = os.path.splitext(file_path)[1].lower()
    
    result: dict[str, Any] = {
        "filename": filename,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "content": None,
        "base64": None,
        "error": None,
        "warning": None,
    }

    # ファイルサイズ警告チェック
    if size_bytes > MAX_FILE_SIZE_BYTES:
        size_mb = size_bytes / (1024 * 1024)
        result["warning"] = f"Large file ({size_mb:.1f} MB). Processing may be slow."
    
    try:
        # PDF処理
        if mime_type == "application/pdf" or ext == ".pdf":
            if extract_text:
                result["content"] = _extract_pdf_text(file_path)
            else:
                result["base64"] = _encode_image_base64(file_path)
            return result
        
        # CSV処理
        if mime_type == "text/csv" or ext == ".csv":
            result["content"] = _process_csv(file_path)
            return result
        
        # JSON処理
        if mime_type == "application/json" or ext == ".json":
            result["content"] = _process_json(file_path)
            return result
        
        # 画像処理
        if mime_type in IMAGE_MIME_TYPES or ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            result["base64"] = _encode_image_base64(file_path)
            result["content"] = f"[Image: {filename}, {size_bytes} bytes]"
            return result
        
        # テキストファイル処理
        if _is_text_file(mime_type) or ext in EXTENSION_MIME_MAP:
            result["content"] = _read_text_file(file_path)
            return result
        
        # 未知のファイルタイプ
        # サイズが小さければテキストとして読み込みを試みる
        if size_bytes < 1024 * 1024:  # 1MB未満
            try:
                result["content"] = _read_text_file(file_path)
                return result
            except (ValueError, UnicodeDecodeError):
                pass
        
        # バイナリとして処理
        result["content"] = f"[Binary file: {filename}, {size_bytes} bytes]"
        if not extract_text:
            result["base64"] = _encode_image_base64(file_path)
        
        return result
    
    except Exception as e:
        result["error"] = f"Error processing file: {e}"
        return result


def file_upload_str(file_path: str, extract_text: bool = True) -> str:
    """
    file_uploadの結果を文字列で返すラッパー。
    ツール呼び出しの戻り値として使用。

    Args:
        file_path: ファイルパス
        extract_text: Trueの場合テキスト抽出

    Returns:
        str: 処理結果の文字列
    """
    result = file_upload(file_path, extract_text)
    
    if result.get("error"):
        return f"Error: {result['error']}"
    
    lines = [
        f"File: {result['filename']}",
        f"Type: {result['mime_type']}",
        f"Size: {result['size_bytes']} bytes",
    ]

    if result.get("warning"):
        lines.append(f"⚠️ Warning: {result['warning']}")
    
    content = result.get("content")
    if content:
        lines.append("")
        if isinstance(content, dict):
            lines.append(json.dumps(content, indent=2, ensure_ascii=False, default=str))
        else:
            lines.append(str(content))
    
    if result.get("base64"):
        # Base64は長いので先頭と末尾のみ表示
        b64 = result["base64"]
        if len(b64) > 100:
            lines.append(f"\nBase64: {b64[:50]}...{b64[-50:]} ({len(b64)} chars)")
        else:
            lines.append(f"\nBase64: {b64}")
    
    return "\n".join(lines)
