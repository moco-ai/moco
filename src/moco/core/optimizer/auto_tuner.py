"""
AutoTuner - 自動閾値調整

蓄積データから閾値を自動で最適化する。
品質とコストのバランスを自動的に調整。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

from .quality_tracker import QualityTracker
from .config import OptimizerConfig


@dataclass
class TuningResult:
    """チューニング結果"""
    status: str                    # "updated" | "skipped" | "insufficient_data"
    reason: str                    # 理由
    old_thresholds: Dict[str, int] # 旧閾値
    new_thresholds: Dict[str, int] # 新閾値
    samples_used: int              # 使用サンプル数
    analysis: Dict[str, Any]       # 分析結果


class AutoTuner:
    """蓄積データから閾値を自動調整"""

    MIN_SAMPLES = 20      # 調整に必要な最小サンプル数
    MAX_CHANGE = 5        # 一度に変更できる閾値の最大値
    MIN_SUCCESS_RATE = 0.85  # 許容最低成功率

    def __init__(
        self,
        tracker: QualityTracker,
        config: OptimizerConfig
    ):
        """
        Args:
            tracker: メトリクス記録器
            config: 設定管理
        """
        self.tracker = tracker
        self.config = config

        # 設定から読み込み
        self.min_samples = config.get("tuning.min_samples", self.MIN_SAMPLES)
        self.max_change = config.get("safety.max_threshold_change", self.MAX_CHANGE)
        self.min_success_rate = config.get("safety.min_success_rate", self.MIN_SUCCESS_RATE)

    def should_tune(self) -> Tuple[bool, str]:
        """チューニングすべきか判定"""
        # 自動チューニングが有効か
        if not self.config.get("tuning.enabled", True):
            return False, "自動チューニング無効"

        # 十分なサンプルがあるか
        stats = self.tracker.get_stats(days=7)
        if stats["total_sessions"] < self.min_samples:
            return False, f"サンプル不足 ({stats['total_sessions']}/{self.min_samples})"

        return True, "チューニング可能"

    def tune(self) -> TuningResult:
        """閾値を最適化"""
        should, reason = self.should_tune()

        if not should:
            return TuningResult(
                status="skipped",
                reason=reason,
                old_thresholds=self.config.get_thresholds(),
                new_thresholds=self.config.get_thresholds(),
                samples_used=0,
                analysis={}
            )

        # 過去30日の集約統計を取得（メモリ効率的）
        tuning_stats = self.tracker.get_tuning_stats(days=30)
        total_records = tuning_stats["total_records"]

        if total_records < self.min_samples:
            return TuningResult(
                status="insufficient_data",
                reason=f"データ不足 ({total_records}/{self.min_samples})",
                old_thresholds=self.config.get_thresholds(),
                new_thresholds=self.config.get_thresholds(),
                samples_used=total_records,
                analysis={}
            )

        # 分析実行（集約統計を使用）
        analysis = self._analyze_tuning_stats(tuning_stats)

        # 最適閾値を探索
        new_thresholds = self._find_optimal_thresholds(analysis)
        old_thresholds = self.config.get_thresholds()

        # 安全チェック
        if not self._is_safe(old_thresholds, new_thresholds, analysis):
            return TuningResult(
                status="skipped",
                reason="安全チェック失敗（変更幅が大きすぎる）",
                old_thresholds=old_thresholds,
                new_thresholds=new_thresholds,
                samples_used=total_records,
                analysis=analysis
            )

        # 変更がない場合
        if new_thresholds == old_thresholds:
            return TuningResult(
                status="skipped",
                reason="最適閾値に変更なし",
                old_thresholds=old_thresholds,
                new_thresholds=new_thresholds,
                samples_used=total_records,
                analysis=analysis
            )

        # 閾値を更新
        self.config.update_thresholds(new_thresholds)

        return TuningResult(
            status="updated",
            reason="閾値を更新しました",
            old_thresholds=old_thresholds,
            new_thresholds=new_thresholds,
            samples_used=total_records,
            analysis=analysis
        )

    def _analyze_tuning_stats(self, stats: Dict[str, Any]) -> Dict[str, Any]:
        """集約統計から分析結果を生成（メモリ効率的版）"""
        depth_stats = stats.get("by_depth", {})
        score_buckets = stats.get("by_score_bucket", {})

        weights = self.config.get_weights()
        quality_weight = weights.get("quality", 0.7)
        cost_weight = weights.get("cost", 0.3)

        # スコアバケット別の最適深度を計算
        optimal_by_score = {}
        for bucket, depths in score_buckets.items():
            best_depth = None
            best_score = -1

            for depth, data in depths.items():
                avg_success = data.get("avg_success", 0)
                # コストは深度に応じて推定
                cost_factor = {"flat": 1.0, "light": 0.6, "structured": 0.3}.get(depth, 0.5)

                score = quality_weight * avg_success + cost_weight * cost_factor
                if score > best_score:
                    best_score = score
                    best_depth = depth

            optimal_by_score[bucket] = {
                "optimal_depth": best_depth,
                "score": round(best_score, 3)
            }

        return {
            "by_depth": depth_stats,
            "by_score_bucket": score_buckets,
            "optimal_by_score": optimal_by_score,
            "total_records": stats["total_records"]
        }

    def _find_optimal_thresholds(self, analysis: Dict[str, Any]) -> Dict[str, int]:
        """最適閾値を探索"""
        current = self.config.get_thresholds()
        weights = self.config.get_weights()
        quality_weight = weights.get("quality", 0.7)
        cost_weight = weights.get("cost", 0.3)

        best_thresholds = current.copy()
        best_score = -1

        # グリッドサーチ（現在値±MAX_CHANGE の範囲）
        flat_range = range(
            max(5, current["flat_max"] - self.max_change),
            min(20, current["flat_max"] + self.max_change + 1)
        )
        light_range = range(
            max(15, current["light_max"] - self.max_change),
            min(40, current["light_max"] + self.max_change + 1)
        )

        for flat_max in flat_range:
            for light_max in light_range:
                if light_max <= flat_max + 3:  # 最低3の差を確保
                    continue

                score = self._evaluate_thresholds(
                    analysis, flat_max, light_max,
                    quality_weight, cost_weight
                )

                if score > best_score:
                    best_score = score
                    best_thresholds = {
                        "flat_max": flat_max,
                        "light_max": light_max
                    }

        return best_thresholds

    def _evaluate_thresholds(
        self,
        analysis: Dict[str, Any],
        flat_max: int,
        light_max: int,
        quality_weight: float,
        cost_weight: float
    ) -> float:
        """閾値の評価スコアを計算"""
        score = 0.0
        total_weight = 0.0

        optimal_by_score = analysis.get("optimal_by_score", {})

        for bucket, data in optimal_by_score.items():
            # この閾値だとどの深度になるか
            if bucket <= flat_max:
                assigned_depth = "flat"
            elif bucket <= light_max:
                assigned_depth = "light"
            else:
                assigned_depth = "structured"

            optimal_depth = data.get("optimal_depth")

            # 最適深度と一致していればボーナス
            if assigned_depth == optimal_depth:
                score += 1.0
            elif (assigned_depth == "structured" and optimal_depth in ["light", "structured"]):
                # 安全側にずれているのは許容
                score += 0.5

            total_weight += 1.0

        return score / total_weight if total_weight > 0 else 0

    def _is_safe(
        self,
        old: Dict[str, int],
        new: Dict[str, int],
        analysis: Dict[str, Any]
    ) -> bool:
        """変更が安全か確認"""
        # 変更幅チェック
        for key in new:
            if key in old:
                diff = abs(new[key] - old[key])
                if diff > self.max_change:
                    return False

        # 成功率チェック（新しい閾値で期待される成功率が閾値以上か）
        depth_stats = analysis.get("by_depth", {})
        for depth in ["flat", "light"]:
            if depth in depth_stats:
                if depth_stats[depth].get("avg_success", 1.0) < self.min_success_rate:
                    # この深度の成功率が低い場合、閾値を下げる方向のみ許可
                    if depth == "flat" and new.get("flat_max", 0) > old.get("flat_max", 0):
                        return False
                    if depth == "light" and new.get("light_max", 0) > old.get("light_max", 0):
                        return False

        return True

    def get_recommendations(self) -> List[str]:
        """現在の状態に基づく推奨事項を生成"""
        recommendations = []

        stats = self.tracker.get_stats(days=7)

        # サンプル数のチェック
        if stats["total_sessions"] < self.min_samples:
            recommendations.append(
                f"データ収集中: あと {self.min_samples - stats['total_sessions']} セッション必要"
            )

        # 深度別の成功率チェック
        for depth, data in stats.get("by_depth", {}).items():
            if data.get("avg_success", 1.0) < self.min_success_rate:
                recommendations.append(
                    f"{depth} モードの成功率が低下 ({data['avg_success']:.0%}): "
                    f"閾値を下げることを検討"
                )

        # コスト効率のチェック
        if "structured" in stats.get("by_depth", {}):
            structured = stats["by_depth"]["structured"]
            if structured.get("count", 0) > stats["total_sessions"] * 0.7:
                recommendations.append(
                    "structured モードの使用率が高い (70%+): "
                    "閾値を上げてコスト削減を検討"
                )

        return recommendations
