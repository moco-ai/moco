from typing import List, Dict, Optional
import sqlite3
import json

try:
    import networkx as nx
    NX_AVAILABLE = True
except ImportError:
    nx = None
    NX_AVAILABLE = False

class GraphStore:
    """
    NetworkXベースのグラフストア (SQLite永続化)
    """
    def __init__(self, db_path: str):
        if not NX_AVAILABLE:
            raise ImportError("networkx is required for GraphStore. Install with: pip install networkx")
        self.db_path = db_path
        self.graph = nx.MultiDiGraph()
        self._load_from_db()

    def _load_from_db(self):
        """SQLiteから関係性をロードしてNetworkXに反映"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT subject, predicate, object, memory_id FROM relations")
            for sub, pred, obj, mem_id in cursor.fetchall():
                self.graph.add_edge(sub, obj, predicate=pred, memory_id=mem_id)
        except sqlite3.OperationalError:
            # テーブルが存在しない場合はスキップ
            pass
        finally:
            conn.close()

    def add_relation(self, subject: str, predicate: str, object: str, memory_id: int):
        """関係性を追加（メモリと同期）"""
        # MemoryStore側のDBへの書き込みはMemoryService側で行われる想定だが、
        # ここではグラフへの反映と、一応DBへの反映も担当する設計にするか？
        # 実装方針に従い、ここはNetworkXへの反映とDB保存をセットで行う
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO relations (memory_id, subject, predicate, object) VALUES (?, ?, ?, ?)",
                (memory_id, subject, predicate, object)
            )
            conn.commit()
            self.graph.add_edge(subject, object, predicate=predicate, memory_id=memory_id)
        finally:
            conn.close()

    def get_related(self, entity: str, max_hops: int = 2) -> List[Dict]:
        """指定したエンティティに関連する情報を取得"""
        if not self.graph.has_node(entity):
            return []

        results = []
        # 指定したノードからmax_hops以内のノードを探索
        visited = {entity}
        queue = [(entity, 0)]
        seen_edges = set()
        
        while queue:
            curr_node, dist = queue.pop(0)
            if dist >= max_hops:
                continue
            
            # 出るエッジ
            if self.graph.has_node(curr_node):
                # out_edgesを使用して明示的に
                for _, neighbor, data in self.graph.out_edges(curr_node, data=True):
                    edge_id = data.get("memory_id")
                    if edge_id not in seen_edges:
                        results.append({
                            "subject": curr_node,
                            "predicate": data["predicate"],
                            "object": neighbor,
                            "memory_id": edge_id
                        })
                        if edge_id:
                            seen_edges.add(edge_id)
                    
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, dist + 1))
                
                # 入るエッジ (無向グラフ的に扱いたい場合)
                for neighbor, _, data in self.graph.in_edges(curr_node, data=True): # type: ignore
                    edge_id = data.get("memory_id")
                    if edge_id not in seen_edges:
                        results.append({
                            "subject": neighbor,
                            "predicate": data["predicate"],
                            "object": curr_node,
                            "memory_id": edge_id
                        })
                        if edge_id:
                            seen_edges.add(edge_id)
                    
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append((neighbor, dist + 1))
                    
        return results

    def delete_relations(self, memory_id: int):
        """特定のmemory_idに紐づく関係性を削除"""
        # 1. DBから削除
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM relations WHERE memory_id = ?", (memory_id,))
            conn.commit()
        finally:
            conn.close()

        # 2. NetworkXから削除 (メモリ上のエッジを全走査して削除)
        edges_to_remove = []
        for u, v, k, data in self.graph.edges(keys=True, data=True): # type: ignore
            if data.get("memory_id") == memory_id:
                edges_to_remove.append((u, v, k))
        
        for u, v, k in edges_to_remove:
            self.graph.remove_edge(u, v, key=k)
            
        # 孤立したノードの削除 (オプション)
        nodes_to_remove = [n for n, d in self.graph.degree if d == 0] # type: ignore
        for n in nodes_to_remove:
            self.graph.remove_node(n)

