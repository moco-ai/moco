"""
Memory Service - 記憶・学習システム API (SQLite版)
既存のLLM Botに差し込んで使用する

使い方:
    from ai_manager.core.memory_service import MemoryService
    
    memory = MemoryService(channel_id="C01234567")
    
    # 1. 会話前: 関連記憶を取得
    memories = memory.recall("経費精算どこ？")
    
    # 2. プロンプトに記憶を追加
    prompt = f"【記憶】{memories}\n\nユーザー: {user_message}"
    response = llm.generate(prompt)
    
    # 3. 会話後: 学習すべきか分析
    result = memory.analyze(user_message, response)
    
    # 4. 必要なら記憶保存

Note: LLM レスポンスの JSON 解析には SmartJSONParser を使用
    if result["should_learn"]:
        memory.learn(result)
"""

import os
import re
import json
from typing import List, Dict, Optional, Tuple, Any, Iterator
from datetime import datetime
from dotenv import load_dotenv

from .db import init_db, get_conn
from .embeddings import build_genai_client, embed_text
from .serialization import serialize_embedding, deserialize_embedding, deserialize_keywords
from .similarity import cos_sim
from ..utils.json_parser import SmartJSONParser

# Lazy import for GraphStore (requires networkx)
GraphStore = None

load_dotenv()

class MemoryService:
    """
    記憶・学習サービス (SQLite版)
    
    機能:
    - recall: 関連記憶を検索（ハイブリッド検索: embedding + キーワード）
    - analyze: 会話を分析して学習すべきか判定
    - learn: 記憶を保存（重複チェック付き）
    
    チャンネル分離:
    - channel_id でフィルタリング
    - 全チャンネルのデータは1つのDBに保存
    """
    
    def __init__(
        self,
        channel_id: Optional[str] = None,
        router_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        db_path: str = "memories/memory.db",
        embedding_model: str = "gemini-embedding-001",
        feedback_threshold: int = 7,
        duplicate_threshold: float = 0.85,
        graph_enabled: bool = True
    ):
        self.channel_id = channel_id
        self.router_id = router_id or ''
        self.worker_id = worker_id or ''
        self.db_path = db_path
        self.embedding_model = embedding_model
        self.feedback_threshold = feedback_threshold
        self.duplicate_threshold = duplicate_threshold
        self.graph_enabled = graph_enabled
        
        # DBディレクトリを作成
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        
        # DB初期化
        self._init_db()
        
        # Embedding client (optional)
        self.genai_client = build_genai_client()

        # Graph Store (optional)
        self.graph = None
        if self.graph_enabled:
            try:
                from .graph import GraphStore as GS
                self.graph = GS(self.db_path)
            except ImportError:
                print("⚠️ GraphStore requires networkx. Install with: pip install networkx")
            except Exception as e:
                print(f"⚠️ Failed to initialize GraphStore: {e}")
    
    def _init_db(self):
        """DBテーブルを初期化"""
        init_db(self.db_path)
    
    def _get_conn(self):
        """DB接続を取得"""
        return get_conn(self.db_path)
    
    def _embed(self, text: str) -> List[float]:
        """テキストをembeddingに変換"""
        return embed_text(self.genai_client, self.embedding_model, text)
    
    def _serialize_embedding(self, emb: List[float]) -> str:
        """embeddingをJSON文字列に変換"""
        return serialize_embedding(emb)
    
    def _deserialize_embedding(self, emb_str: str) -> List[float]:
        """JSON文字列からembeddingを復元"""
        return deserialize_embedding(emb_str)
    
    def _deserialize_keywords(self, kw_str: str) -> List[str]:
        """JSON文字列からキーワードリストを復元"""
        return deserialize_keywords(kw_str)
    
    def _fetch_memories(
        self,
        exclude_types: Optional[List[str]] = None,
        limit: Optional[int] = None,
        since_days: Optional[int] = None,
        keyword_search: Optional[str] = None
    ) -> Iterator[Dict]:
        """
        記憶を条件付きで取得（Generator）
        """
        conn = self._get_conn()
        try:
            cursor = conn.cursor()
            
            query = "SELECT id, content, type, keywords, source, embedding, created_at, channel_id, questions FROM memories WHERE 1=1"
            params = []
            
            # Channel filter
            if self.channel_id:
                query += " AND (channel_id = ? OR channel_id = 'GLOBAL')"
                params.append(self.channel_id)
            
            if self.router_id:
                query += ' AND router_id = ?'
                params.append(self.router_id)
            if self.worker_id:
                query += ' AND worker_id = ?'
                params.append(self.worker_id)
            
            # Type filter
            if exclude_types:
                placeholders = ",".join(["?"] * len(exclude_types))
                query += f" AND type NOT IN ({placeholders})"
                params.extend(exclude_types)
                
            # Time filter
            if since_days:
                query += " AND created_at > datetime('now', ?)"
                params.append(f"-{since_days} days")
                
            # Keyword search (Rough filter via LIKE)
            if keyword_search:
                # クエリをトークンに分割して検索
                tokens = re.findall(r'[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]+|[\u3040-\u309f]{2,}|[\u30a0-\u30ff]{2,}', keyword_search)
                if tokens:
                    token_queries = []
                    for t in tokens[:5]: # 最大5トークンまで
                        token_queries.append("(content LIKE ? OR keywords LIKE ? OR questions LIKE ?)")
                        params.append(f"%{t}%")
                        params.append(f"%{t}%")
                        params.append(f"%{t}%")
                    query += " AND (" + " OR ".join(token_queries) + ")"


            # Order by created_at DESC
            query += " ORDER BY created_at DESC"
            
            if limit:
                query += " LIMIT ?"
                params.append(limit)
                
            cursor.execute(query, tuple(params))
            
            while True:
                row = cursor.fetchone()
                if not row:
                    break
                yield {
                    "id": row[0],
                    "content": row[1],
                    "type": row[2],
                    "keywords": self._deserialize_keywords(row[3]),
                    "source": row[4],
                    "emb": self._deserialize_embedding(row[5]),
                    "created_at": row[6],
                    "channel_id": row[7],
                    "questions": json.loads(row[8] or "[]") if isinstance(row[8], str) else (row[8] or [])
                }
        finally:
            conn.close()


    def _get_all_memories(self) -> List[Dict]:
        """
        全記憶を取得 (互換性維持のため)
        """
        return list(self._fetch_memories())
    
    def _is_duplicate(self, text: str) -> Tuple[bool, Optional[str]]:
        """重複チェック"""
        # 直近100件程度で重複チェックすれば十分 (パフォーマンスのため)
        memory_gen = self._fetch_memories(limit=100)
        
        new_emb = self._embed(text)
        if not new_emb:
            return False, None
        
        for m in memory_gen:
            if m['emb']:
                sim = cos_sim(new_emb, m['emb'])
                if sim >= self.duplicate_threshold:
                    return True, m['content']
        return False, None
    
    def recall(
        self,
        query: str,
        top_k: int = 10,
        exclude_types: Optional[List[str]] = None,
        since_days: Optional[int] = None
    ) -> List[Dict]:
        """
        関連記憶を検索（ハイブリッド検索）
        
        Args:
            query: 検索クエリ
            top_k: 返す記憶の数 (Default: 10)
            exclude_types: 除外するメモリタイプのリスト
            since_days: 過去何日分の記憶を対象にするか (Noneの場合は全期間、ただし最大1000件)
        
        Returns:
            関連記憶のリスト
        """
        if exclude_types is None:
            exclude_types = []
        
        # SQLレベルでフィルタリング
        # 1. 最近の記憶を取得 (ベースライン)
        memory_gen_recent = self._fetch_memories(
            exclude_types=exclude_types,
            limit=500,
            since_days=since_days
        )
        
        # 2. キーワードにマッチする記憶を広めに取得 (キーワードマッチ用)
        memory_gen_kw = self._fetch_memories(
            exclude_types=exclude_types,
            limit=500,
            keyword_search=query,
            since_days=since_days
        )
        
        # Generatorを統合して重複排除
        seen_ids = set()
        def combined_gen():
            for m in memory_gen_recent:
                if m['id'] not in seen_ids:
                    seen_ids.add(m['id'])
                    yield m
            for m in memory_gen_kw:
                if m['id'] not in seen_ids:
                    seen_ids.add(m['id'])
                    yield m

        query_emb = self._embed(query)
        # 検索クエリのn-gram集合を事前作成 (2-gram)
        query_ngrams = set()
        for i in range(len(query) - 1):
            query_ngrams.add(query[i:i+2])
        
        # グラフ検索 (エンティティベース)
        graph_boosts = {}
        if self.graph:
            # クエリから単語を抽出（簡易的）
            words = re.findall(r'[a-zA-Z0-9]{2,}|[\u4e00-\u9fff]+', query)
            for word in words:
                related = self.graph.get_related(word, max_hops=1)
                for rel in related:
                    mid = rel.get("memory_id")
                    if mid:
                        graph_boosts[mid] = graph_boosts.get(mid, 0) + 0.2

        results = []
        now = datetime.now()
        
        for m in combined_gen():
            # 除外タイプはスキップ (念のため再度チェック)
            mem_type = m.get('type', 'unknown')
            if mem_type in exclude_types:
                continue
            
            # Embeddingスコア
            emb_score = 0
            if query_emb and m['emb']:
                emb_score = max(0, cos_sim(query_emb, m['emb']))
            
            # キーワードマッチスコア（高速化版）
            kw_list = m.get('keywords', [])
            qs_list = m.get('questions', [])
            all_match_targets = kw_list + qs_list
            
            matched = 0
            for kw in all_match_targets:
                hit = False
                # キーワードの2文字がクエリに含まれるかチェック
                for i in range(len(kw) - 1):
                    if kw[i:i+2] in query_ngrams:
                        hit = True
                        break
                if not hit:
                    # クエリの2文字がキーワードに含まれるかチェック
                    for i in range(len(query) - 1):
                        if query[i:i+2] in kw:
                            hit = True
                            break
                if hit:
                    matched += 1
            kw_score = matched / len(all_match_targets) if all_match_targets else 0

            
            # Recency Score (直近性) - 高速化版
            recency_score = 0
            created_at_val = m.get("created_at")
            if created_at_val:
                try:
                    # SQLite timestamp format: YYYY-MM-DD HH:MM:SS
                    # 文字列の先頭10文字(YYYY-MM-DD)だけで判定
                    created_date_str = str(created_at_val)[:10]
                    created_date = datetime.strptime(created_date_str, "%Y-%m-%d")
                    days_diff = (now - created_date).days
                    if days_diff < 30:
                        recency_score = 0.15 * (1 - (max(0, days_diff) / 30))
                except Exception:
                    pass

            # Success Bonus (成功体験ボーナス)
            success_bonus = 0
            if "Success: True" in m['content']:
                success_bonus = 0.1
            
            # ハイブリッドスコア + 補正
            hybrid_score = (emb_score * 0.5) + (kw_score * 0.4) + recency_score + success_bonus
            
            # グラフブースト
            if m['id'] in graph_boosts:
                hybrid_score += graph_boosts[m['id']]

            if hybrid_score > 0.1:  # 最低しきい値
                results.append({
                    "id": m['id'],
                    "content": m['content'],
                    "type": m.get('type', 'unknown'),
                    "score": hybrid_score
                })
        
        # スコア順でソート
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]
    
    def analyze(
        self,
        user_message: str,
        bot_response: Optional[str] = None,
        llm_client = None
    ) -> Dict:
        """
        会話を分析して学習すべきか判定 (LLM使用)
        
        Args:
            user_message: ユーザーのメッセージ
            bot_response: Botの返答（オプション）
            llm_client: LLMクライアント（分析用）※指定がない場合はself.genai_clientを使用
        
        Returns:
            分析結果
        """
        # クライアントの確保
        client = llm_client or self.genai_client
        if not client:
            # クライアントがない場合は簡易判定
            info_keywords = ['は', 'にある', 'です', 'だよ', 'だから', 'ルール']
            has_info = any(kw in user_message for kw in info_keywords)
            return {
                "should_learn": has_info,
                "score": 5 if has_info else 0,
                "type": "knowledge" if has_info else None,
                "content": user_message if has_info else None,
                "keywords": []
            }
        
        # LLMによる分析 (ai_vs_ai_unified.py と同じプロンプト)
        prompt = f"""以下の会話を分析し、AIが覚えておくべき情報やルールがあるか判定してください。

【AIの発言】
{bot_response if bot_response else "(なし)"}

【ユーザーの発言】
{user_message}

以下のJSON形式で出力:
{{
  "score": 0-10,
  "rule": "行動ルール（20字以内）またはnull",
  "rule_kw": ["キーワード1", "キーワード2"],
  "info": "覚えておくべき情報（場所、手順、ルール等）またはnull",
  "info_kw": ["キーワード1", "キーワード2"],
  "relations": [
    {{"subject": "田中", "predicate": "member_of", "object": "開発チーム"}}
  ],
  "questions": ["この情報を引き出すための想定質問1", "想定質問2"],
  "shared": true/false
}}

score判定基準（重要度）:
- 0-2: 普通の雑談、挨拶
- 3-5: 軽い確認、質問
- 6-7: 指摘、注意、不満、場所・手順の教授
- 8-10: 明確な業務命令、強い叱責、重要な社内ルール

shared判定基準（長期記憶 vs 短期記憶）:
- true: 長期的に覚えておくべき情報（例: ユーザーの名前、好み、設定、ルール、重要な決定事項、プロジェクト情報、発見したファイルパスやディレクトリ構造）
- false: このセッション/タスク限定の一時的な情報（例: 一時的な確認事項、すぐに忘れてよい文脈）

重要:
- ユーザーが「覚えておいて」と言った情報は必ず shared=true にする。
- ファイルパスやディレクトリ構造は必ずフルパスで記録し、shared=true にする。後続の作業で必要になるため。

relations (関係性抽出):
エンティティ間の重要な関係性を抽出し、SPO組（主語・述語・目的語）のリストを作成してください。特に「誰がどこに所属している」「何がどこにある」「AはBの一部である」などの事実を重視してください。


ルール作成:
scoreが{self.feedback_threshold}以上の時のみruleを作成。それ以外はnull。

情報記録:
ユーザーが場所・手順・社内ルールを教えた場合は必ずinfoに記録。

想定質問 (Reverse HyDE):
保存する情報(rule/info)に対して、ユーザーが将来検索するであろう「質問文」を2-3個生成してquestionsに含めてください。
検索精度の向上のため、キーワードだけでなく自然言語の質問形式も予測して保存します。"""

        try:
            # Gemini (google-genai) の場合
            if hasattr(client, "models"):
                from google.genai import types
                r = client.models.generate_content(
                    model="models/gemini-3-flash-preview",
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                    contents=[{"role":"user", "parts":[{"text":prompt}]}]
                )
                # .text を使うと警告が出るため、直接 parts からテキストを取得
                response_text = ""
                if r.candidates and r.candidates[0].content and r.candidates[0].content.parts:
                    for part in r.candidates[0].content.parts:
                        if hasattr(part, 'text') and part.text:
                            response_text += part.text
                result = SmartJSONParser.parse(response_text, default={})
            
            # OpenAI の場合
            elif hasattr(client, "chat"):
                r = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"}
                )
                result = SmartJSONParser.parse(r.choices[0].message.content, default={})
            
            else:
                raise ValueError("Unsupported LLM client")

            # result がリストの場合は最初の要素を使用、辞書でない場合は空辞書
            if isinstance(result, list):
                result = result[0] if result else {}
            if not isinstance(result, dict):
                result = {}

            score = result.get("score", 0)
            rule = result.get("rule")
            rule_kw = result.get("rule_kw", [])
            info = result.get("info")
            info_kw = result.get("info_kw", [])
            questions = result.get("questions", [])
            shared = result.get("shared", False)
            relations = result.get("relations", [])
            
            # 結果を統合
            if info:
                return {
                    "should_learn": True,
                    "score": score,
                    "type": "knowledge",
                    "content": info,
                    "keywords": info_kw,
                    "questions": questions,
                    "shared": shared,
                    "relations": relations
                }
            elif rule and score >= self.feedback_threshold:
                return {
                    "should_learn": True,
                    "score": score,
                    "type": "behavior",
                    "content": rule,
                    "keywords": rule_kw,
                    "questions": questions,
                    "shared": shared,
                    "relations": relations
                }
            else:
                return {
                    "should_learn": False,
                    "score": score,
                    "type": None,
                    "content": None,
                    "keywords": [],
                    "questions": [],
                    "shared": False,
                    "relations": []
                }

        except Exception as e:
            print(f"[MemoryService] Analyze error: {e}")
            return {
                "should_learn": False,
                "score": 0,
                "type": None,
                "content": None,
                "keywords": [],
                "questions": [],
                "shared": False,
                "relations": []
            }
    
    def _resolve_conflict(self, content: str, memory_type: str) -> List[int]:
        """矛盾する古い記憶を特定する"""
        if not self.genai_client or memory_type == "action_log":
            return []
            
        # 関連記憶を検索
        related_memories = self.recall(content, top_k=5)
        if not related_memories:
            return []
            
        # 候補をリスト化
        candidates = []


        for m in related_memories:
            # スコアが低すぎるものは除外
            if m['score'] < 0.25:
                continue
            candidates.append(f"ID:{m['id']} Content:{m['content']}")
            
        if not candidates:
            return []
            
        candidates_text = "\n".join(candidates)
        
        prompt = f"""新しい記憶を保存しようとしています。既存の記憶と矛盾し、置換（削除）すべき古い記憶があればIDを指定してください。

【新しい記憶】
{content}

【既存の記憶候補】
{candidates_text}

以下の条件に当てはまる場合のみ、そのIDを出力してください。
1. 新しい記憶が、既存の記憶の内容を**明確に否定・更新**している（例:「AはBだ」→「いや、AはCだ」）。
2. 単なる追加情報の場合は削除しないこと（例:「AはBだ」→「AはDでもある」）。

出力形式(JSON):
{{
  "delete_ids": [ID1, ID2]
}}
削除すべきものがない場合は空リスト [] を返す。"""

        try:
            if hasattr(self.genai_client, "models"):
                from google.genai import types
                r = self.genai_client.models.generate_content(
                    model="models/gemini-3-flash-preview",
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                    contents=[{"role":"user", "parts":[{"text":prompt}]}]
                )
                # .text を使うと警告が出るため、直接 parts からテキストを取得
                response_text = ""
                if r.candidates and r.candidates[0].content and r.candidates[0].content.parts:
                    for part in r.candidates[0].content.parts:
                        if hasattr(part, 'text') and part.text:
                            response_text += part.text
                result = SmartJSONParser.parse(response_text, default={})
                return result.get("delete_ids", [])
            else:
                # OpenAI fallback (omitted for brevity, assuming Gemini)
                return []
        except Exception as e:
            print(f"[MemoryService] Conflict resolution failed: {e}")
            return []

    def learn(
        self,
        content: str,
        memory_type: str = "knowledge",
        keywords: Optional[List[str]] = None,
        source: Optional[str] = None,
        run_id: Optional[str] = None,
        check_conflict: bool = True,
        shared: bool = False,
        questions: Optional[List[str]] = None,
        relations: Optional[List[Dict]] = None
    ) -> Dict:
        """
        記憶を保存
        
        Args:
            content: 記憶する内容
            memory_type: "knowledge" or "behavior"
            keywords: 検索用キーワード
            source: 記憶の出典
            check_conflict: 矛盾チェックを行うか (Default: True)
            shared: Trueなら全チャンネル共有(GLOBAL)として保存 (Default: False)
            questions: 想定質問リスト (Reverse HyDE用)
            relations: エンティティ間の関係性リスト
        """
        # 重複チェック (完全一致に近いもの)
        # NOTE: For task-level summaries and action logs we want to keep multiple runs.
        if memory_type not in ["action_log", "task_run_summary", "task_run_event"]:
            is_dup, existing = self._is_duplicate(content)
            if is_dup:
                return {
                    "success": False,
                    "is_duplicate": True,
                    "existing": existing
                }
        
        # 矛盾解決 (Conflict Resolution)
        deleted_ids = []
        if check_conflict and memory_type in ["knowledge", "behavior"]:
            deleted_ids = self._resolve_conflict(content, memory_type)
            for del_id in deleted_ids:
                print(f"♻️ Resolving conflict: Deleting old memory ID {del_id}")
                self.delete(del_id)
        
        # Embedding生成 (Reverse HyDE: Content + Keywords + Questions)
        kw_text = ", ".join(keywords) if keywords else ""
        qs_text = "\n".join(questions) if questions else ""
        # 構造化タグ付き
        emb_text = f"[Questions]:\n{qs_text}\n\n[Content]:\n{content}\n\n[Keywords]:\n{kw_text}".strip()
        emb = self._embed(emb_text)
        
        # 保存先チャンネル決定
        target_channel_id = "GLOBAL" if shared else self.channel_id

        # DBに保存
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO memories (channel_id, router_id, worker_id, run_id, content, type, keywords, questions, source, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            target_channel_id,
            self.router_id,
            self.worker_id,
            run_id,
            content,
            memory_type,
            json.dumps(keywords or [], ensure_ascii=False),
            json.dumps(questions or [], ensure_ascii=False),
            source,
            self._serialize_embedding(emb)
        ))
        memory_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        # グラフに関係性を保存
        if self.graph and relations and memory_id is not None:
            for rel in relations:
                try:
                    self.graph.add_relation(
                        subject=rel["subject"],
                        predicate=rel["predicate"],
                        object=rel["object"],
                        memory_id=memory_id
                    )
                except Exception as e:
                    print(f"⚠️ Failed to add relation to graph: {e}")

        return {
            "success": True,
            "is_duplicate": False,
            "memory_id": memory_id,
            "deleted_ids": deleted_ids
        }
    
    def delete(self, memory_id: int) -> bool:
        """記憶を削除"""
        # グラフから関係性を削除
        if self.graph:
            try:
                self.graph.delete_relations(memory_id)
            except Exception as e:
                print(f"⚠️ Failed to delete relations from graph: {e}")

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM memories WHERE id = ?', (memory_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    def list_all(self) -> List[Dict]:
        """全記憶を取得（デバッグ用）"""
        return self._get_all_memories()
    
    def count(self) -> int:
        """記憶数を取得"""
        conn = self._get_conn()
        cursor = conn.cursor()
        if self.channel_id:
            cursor.execute("SELECT COUNT(*) FROM memories WHERE channel_id = ? OR channel_id = 'GLOBAL'", (self.channel_id,))
        else:
            cursor.execute('SELECT COUNT(*) FROM memories')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    
    def get_context_prompt(self, query: str) -> str:
        """
        プロンプトに追加する記憶コンテキストを生成
        
        Args:
            query: ユーザーのメッセージ
        
        Returns:
            プロンプトに追加する文字列
        """
        memories = self.recall(query)
        if not memories:
            return ""
        
        lines = ["【記憶している情報】"]
        for m in memories:
            lines.append(f"- {m['content']}")
        
        return "\n".join(lines)

    def record_action_result(self, action: str, params: Dict, result: str, success: bool, feedback: int = 0):
        """
        アクションの実行結果を記録する (Agent Runtimeから呼ばれる)
        
        NOTE: 失敗したアクションのみ記録する（成功は冗長なため）
        - 失敗を記録することで、同じ失敗を繰り返さないよう学習に活用
        - 成功パターンは task_run_summary でカバー
        """
        # Success handling:
        # We don't store success action logs (to reduce noise), but we DO use success as a signal
        # that older failures for the same tool may have become stale (system updates, auth fixes, etc.).
        # To prevent stale failures from being recalled, we proactively delete old failure memories
        # for the same action in the same channel scope.
        if success:
            try:
                deleted = self._cleanup_action_log_failures(action=action, max_delete=50)
                if deleted:
                    print(f"🧠 [Memory] Cleaned up {deleted} stale action_log failures for action={action}")
            except Exception:
                pass
            return
        
        try:
            content = f"Action: {action}, Params: {json.dumps(params, ensure_ascii=False)}, Success: {success}, Result: {result[:200]}"
            self.learn(
                content=content,
                memory_type="action_log",
                keywords=[action, "failure", "error"],
                source="system_runtime"
            )
            print(f"🧠 [Memory] Recorded FAILED Action: {action}")
        except Exception as e:
            print(f"⚠️ Failed to record action result: {e}")

    def _cleanup_action_log_failures(self, action: str, max_delete: int = 50) -> int:
        """
        Delete older failure memories for a given action (tool name).
        This keeps action_log from poisoning future runs after the system/tool behavior changes.

        Scope:
        - Only deletes type='action_log'
        - Only within this MemoryService scope (channel_id); GLOBAL is not targeted.
        - Matches by content prefix "Action: {action},"
        """
        if not action:
            return 0
        if not self.channel_id:
            # Without a channel scope, be conservative and do not delete.
            return 0

        conn = self._get_conn()
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id
                FROM memories
                WHERE channel_id = ?
                  AND type = 'action_log'
                  AND content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.channel_id, f"Action: {action},%", int(max_delete)),
            )
            rows = cursor.fetchall()
            ids = [r[0] for r in rows if r and r[0] is not None]
            if not ids:
                return 0
            cursor.executemany("DELETE FROM memories WHERE id = ?", [(i,) for i in ids])
            deleted = cursor.rowcount if cursor.rowcount is not None else len(ids)
            conn.commit()
            return int(deleted)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Task Run Correlation APIs
    # ------------------------------------------------------------------
    def record_task_run_event(
        self,
        run_id: str,
        tool_name: str,
        params: Dict[str, Any],
        result: Dict[str, Any],
        success: bool,
        error_type: Optional[str] = None,
        recovery_hint: Optional[str] = None,
        channel_id: Optional[str] = None,
        router_id: Optional[str] = None,
        skill_id: Optional[str] = None,
    ) -> None:
        """
        Store a tool execution event tied to a specific task_run_id.
        This is used to build TaskRunSummary across multiple workers.
        """
        if not run_id:
            return
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            params_json = json.dumps(params or {}, ensure_ascii=False)
            # Keep result preview compact (avoid secrets / huge blobs)
            result_preview = ""
            try:
                result_preview = json.dumps(result, ensure_ascii=False)[:2000]
            except Exception:
                result_preview = str(result)[:2000]
            cursor.execute(
                """
                INSERT INTO task_run_events
                (run_id, channel_id, router_id, skill_id, tool_name, params_json, result_preview,
                 success, error_type, recovery_hint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    channel_id or self.channel_id,
                    router_id,
                    skill_id,
                    tool_name,
                    params_json,
                    result_preview,
                    1 if success else 0,
                    error_type,
                    recovery_hint,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ Failed to record task run event: {e}")

    def list_task_run_events(self, run_id: str, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """List all tool events for a given run_id (ordered)."""
        if not run_id:
            return []
        try:
            conn = self._get_conn()
            cursor = conn.cursor()
            
            # Use provided channel_id or the service's default
            target_channel = channel_id or self.channel_id
            
            query = """
                SELECT tool_name, params_json, result_preview, success, error_type, recovery_hint, skill_id, router_id, created_at
                FROM task_run_events
                WHERE run_id = ?
            """
            params = [str(run_id)]
            
            if target_channel:
                query += " AND channel_id = ?"
                params.append(target_channel)
                
            query += " ORDER BY id ASC"
            
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            conn.close()
            out = []
            for r in rows:
                out.append(
                    {
                        "tool_name": r[0],
                        "params_json": r[1],
                        "result_preview": r[2],
                        "success": bool(r[3]),
                        "error_type": r[4],
                        "recovery_hint": r[5],
                        "skill_id": r[6],
                        "router_id": r[7],
                        "created_at": r[8],
                    }
                )
            return out
        except Exception as e:
            print(f"⚠️ Failed to list task run events: {e}")
            return []
