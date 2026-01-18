"""
チェックポイント管理モジュール

セッション状態の永続化・復元を行う自動チェックポイント機能を提供する。
"""

import json
import logging
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Checkpoint:
    """チェックポイントデータ"""

    checkpoint_id: str
    session_id: str
    timestamp: datetime
    conversation_history: List[Dict[str, Any]]
    context_summary: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """辞書形式に変換"""
        return {
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "conversation_history": self.conversation_history,
            "context_summary": self.context_summary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Checkpoint":
        """辞書からインスタンスを生成"""
        timestamp = data["timestamp"]
        if isinstance(timestamp, str):
            # ISO形式の文字列をdatetimeに変換
            timestamp = datetime.fromisoformat(timestamp)

        return cls(
            checkpoint_id=data["checkpoint_id"],
            session_id=data["session_id"],
            timestamp=timestamp,
            conversation_history=data.get("conversation_history", []),
            context_summary=data.get("context_summary"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class CheckpointConfig:
    """チェックポイント設定"""

    enabled: bool = True
    storage_dir: str = ".moco/checkpoints"
    auto_save_interval: int = 5  # N ターンごとに自動保存
    max_checkpoints_per_session: int = 10  # セッションあたり最大保存数

    def __post_init__(self):
        """設定値のバリデーション"""
        if self.auto_save_interval < 1:
            raise ValueError("auto_save_interval must be >= 1")
        if self.max_checkpoints_per_session < 1:
            raise ValueError("max_checkpoints_per_session must be >= 1")


class CheckpointManager:
    """
    チェックポイント管理クラス

    セッションの会話履歴とコンテキストを定期的に保存し、
    必要に応じて復元する機能を提供する。
    """

    def __init__(self, config: Optional[CheckpointConfig] = None):
        """
        チェックポイントマネージャーを初期化

        Args:
            config: チェックポイント設定（省略時はデフォルト設定）
        """
        self.config = config or CheckpointConfig()
        self._storage_path = Path(self.config.storage_dir)

        # ストレージディレクトリを作成
        if self.config.enabled:
            self._storage_path.mkdir(parents=True, exist_ok=True)

    def _generate_checkpoint_id(self) -> str:
        """ユニークなチェックポイントIDを生成"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        random_suffix = secrets.token_hex(4)
        return f"cp_{timestamp}_{random_suffix}"

    def _sanitize_id(self, id_value: str) -> str:
        """
        IDをサニタイズしてパストラバーサルを防止

        Args:
            id_value: サニタイズするID文字列

        Returns:
            サニタイズされたID文字列

        Raises:
            ValueError: IDが空または無効な場合
        """
        safe_id = "".join(c for c in id_value if c.isalnum() or c in "-_")
        if not safe_id:
            raise ValueError(f"Invalid ID: {id_value}")
        return safe_id

    def _get_session_dir(self, session_id: str) -> Path:
        """セッション用のディレクトリパスを取得"""
        safe_session_id = self._sanitize_id(session_id)
        return self._storage_path / safe_session_id

    def _get_checkpoint_path(self, session_id: str, checkpoint_id: str) -> Path:
        """チェックポイントファイルのパスを取得"""
        safe_checkpoint_id = self._sanitize_id(checkpoint_id)
        return self._get_session_dir(session_id) / f"{safe_checkpoint_id}.json"

    def save(
        self,
        session_id: str,
        conversation_history: List[Dict[str, Any]],
        context_summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Checkpoint]:
        """
        チェックポイントを保存

        Args:
            session_id: セッションID
            conversation_history: 会話履歴
            context_summary: 圧縮済みコンテキストサマリー（オプション）
            metadata: 追加のメタデータ（オプション）

        Returns:
            保存されたチェックポイント、無効な場合はNone
        """
        if not self.config.enabled:
            return None

        try:
            # セッションディレクトリを作成
            session_dir = self._get_session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)

            # チェックポイントを作成
            checkpoint = Checkpoint(
                checkpoint_id=self._generate_checkpoint_id(),
                session_id=session_id,
                timestamp=datetime.now(timezone.utc),
                conversation_history=conversation_history,
                context_summary=context_summary,
                metadata=metadata or {},
            )

            # JSONファイルにアトミックに保存
            checkpoint_path = self._get_checkpoint_path(
                session_id, checkpoint.checkpoint_id
            )

            # 一時ファイル経由で書き込み、os.replace でアトミックに置換
            temp_fd, temp_path = tempfile.mkstemp(
                dir=session_dir, suffix=".json.tmp"
            )
            try:
                with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                    json.dump(checkpoint.to_dict(), f, ensure_ascii=False, indent=2)
                os.replace(temp_path, checkpoint_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise

            logger.debug(f"Checkpoint saved: {checkpoint.checkpoint_id}")

            # 古いチェックポイントをクリーンアップ
            self.cleanup_old(session_id)

            return checkpoint

        except Exception as e:
            logger.error(f"Failed to save checkpoint: {e}")
            return None

    def load_latest(self, session_id: str) -> Optional[Checkpoint]:
        """
        最新のチェックポイントを読み込み

        Args:
            session_id: セッションID

        Returns:
            最新のチェックポイント、存在しない場合はNone
        """
        if not self.config.enabled:
            return None

        checkpoints = self.list_checkpoints(session_id)
        if not checkpoints:
            return None

        # タイムスタンプでソートして最新を取得
        return max(checkpoints, key=lambda cp: cp.timestamp)

    def load(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """
        指定IDのチェックポイントを読み込み

        Args:
            checkpoint_id: チェックポイントID

        Returns:
            チェックポイント、存在しない場合はNone
        """
        if not self.config.enabled:
            return None

        try:
            # checkpoint_id をサニタイズ
            safe_checkpoint_id = self._sanitize_id(checkpoint_id)

            # 全セッションディレクトリを検索
            if not self._storage_path.exists():
                return None

            for session_dir in self._storage_path.iterdir():
                if not session_dir.is_dir():
                    continue

                checkpoint_path = session_dir / f"{safe_checkpoint_id}.json"
                if checkpoint_path.exists():
                    with open(checkpoint_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return Checkpoint.from_dict(data)

            return None

        except ValueError as e:
            logger.warning(f"Invalid checkpoint_id: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to load checkpoint {checkpoint_id}: {e}")
            return None

    def list_checkpoints(self, session_id: str) -> List[Checkpoint]:
        """
        セッションのチェックポイント一覧を取得

        Args:
            session_id: セッションID

        Returns:
            チェックポイントのリスト（タイムスタンプ昇順）
        """
        if not self.config.enabled:
            return []

        session_dir = self._get_session_dir(session_id)
        if not session_dir.exists():
            return []

        checkpoints = []
        try:
            for checkpoint_file in session_dir.glob("cp_*.json"):
                try:
                    with open(checkpoint_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    checkpoints.append(Checkpoint.from_dict(data))
                except Exception as e:
                    logger.warning(
                        f"Failed to load checkpoint {checkpoint_file}: {e}"
                    )
                    continue

            # タイムスタンプでソート
            checkpoints.sort(key=lambda cp: cp.timestamp)
            return checkpoints

        except Exception as e:
            logger.error(f"Failed to list checkpoints for session {session_id}: {e}")
            return []

    def delete(self, checkpoint_id: str) -> bool:
        """
        チェックポイントを削除

        Args:
            checkpoint_id: チェックポイントID

        Returns:
            削除成功時True
        """
        if not self.config.enabled:
            return False

        try:
            # checkpoint_id をサニタイズ
            safe_checkpoint_id = self._sanitize_id(checkpoint_id)

            # 全セッションディレクトリを検索
            if not self._storage_path.exists():
                return False

            for session_dir in self._storage_path.iterdir():
                if not session_dir.is_dir():
                    continue

                checkpoint_path = session_dir / f"{safe_checkpoint_id}.json"
                if checkpoint_path.exists():
                    checkpoint_path.unlink()
                    logger.debug(f"Checkpoint deleted: {checkpoint_id}")
                    return True

            return False

        except ValueError as e:
            logger.warning(f"Invalid checkpoint_id: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete checkpoint {checkpoint_id}: {e}")
            return False

    def cleanup_old(self, session_id: str) -> int:
        """
        古いチェックポイントを削除（max_checkpoints_per_session を超えた分）

        Args:
            session_id: セッションID

        Returns:
            削除されたチェックポイント数
        """
        if not self.config.enabled:
            return 0

        checkpoints = self.list_checkpoints(session_id)
        if len(checkpoints) <= self.config.max_checkpoints_per_session:
            return 0

        # 古い順にソートされているので、先頭から削除
        to_delete = checkpoints[: -self.config.max_checkpoints_per_session]
        deleted_count = 0

        for checkpoint in to_delete:
            if self.delete(checkpoint.checkpoint_id):
                deleted_count += 1

        if deleted_count > 0:
            logger.debug(
                f"Cleaned up {deleted_count} old checkpoints for session {session_id}"
            )

        return deleted_count

    def delete_session_checkpoints(self, session_id: str) -> bool:
        """
        セッションの全チェックポイントを削除

        Args:
            session_id: セッションID

        Returns:
            削除成功時True
        """
        if not self.config.enabled:
            return False

        try:
            session_dir = self._get_session_dir(session_id)
            if session_dir.exists():
                shutil.rmtree(session_dir)
                logger.debug(f"All checkpoints deleted for session {session_id}")
                return True
            return False

        except Exception as e:
            logger.error(
                f"Failed to delete checkpoints for session {session_id}: {e}"
            )
            return False

    def should_auto_save(self, turn_count: int) -> bool:
        """
        自動保存すべきタイミングかどうかを判定

        Args:
            turn_count: 現在のターン数

        Returns:
            自動保存すべき場合True
        """
        if not self.config.enabled:
            return False
        return turn_count > 0 and turn_count % self.config.auto_save_interval == 0
