from __future__ import annotations
from collections import defaultdict, deque
from .enums import EdgeType
from .models import KnowledgeEdge

def prerequisite_adjacency(node_ids: list[str], edges: list[KnowledgeEdge]):
    adj={n:[] for n in node_ids}; indegree={n:0 for n in node_ids}
    for e in edges:
        if e.edge_type != EdgeType.PREREQUISITE: continue
        if e.source_node_id in adj and e.target_node_id in adj:
            adj[e.source_node_id].append(e.target_node_id); indegree[e.target_node_id]+=1
    return adj, indegree

def cycle_nodes(node_ids: list[str], edges: list[KnowledgeEdge]) -> list[str]:
    adj, indegree=prerequisite_adjacency(node_ids,edges)
    q=deque(sorted([n for n,d in indegree.items() if d==0])); visited=[]
    while q:
        n=q.popleft(); visited.append(n)
        for m in sorted(adj[n]):
            indegree[m]-=1
            if indegree[m]==0: q.append(m)
    return sorted(set(node_ids)-set(visited))

def roots_and_terminals(node_ids: list[str], edges: list[KnowledgeEdge]):
    adj, indegree=prerequisite_adjacency(node_ids,edges)
    roots=sorted([n for n in node_ids if indegree[n]==0])
    terminals=sorted([n for n in node_ids if not adj[n]])
    return roots, terminals

def graph_depth(node_ids: list[str], edges: list[KnowledgeEdge]) -> int:
    adj, indegree=prerequisite_adjacency(node_ids,edges)
    q=deque(sorted([n for n,d in indegree.items() if d==0])); depth={n:1 for n in q}; seen=0
    while q:
        n=q.popleft(); seen+=1
        for m in adj[n]:
            depth[m]=max(depth.get(m,1),depth[n]+1); indegree[m]-=1
            if indegree[m]==0:q.append(m)
    return max(depth.values(),default=0) if seen==len(node_ids) else 0
