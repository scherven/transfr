import heapq, sys
sys.path.insert(0, '.')
from bidirectional_search import bidirectional_shortest_path, reverse_graph
from dijkstra import shortest_path

# Added a direct S->T distractor edge (weight 3.5) alongside the S-C-T path.
fwd = {
    "S": [("A", 1.0, "sa"), ("C", 2.0, "sc"), ("T", 3.5, "direct")],
    "A": [("B", 1.0, "ab")],
    "B": [("T", 1.0, "bt")],
    "C": [("T", 2.0, "ct")],
    "T": [],
}
bwd = reverse_graph(fwd)

print("ground truth:", shortest_path(fwd, {"S"}, {"T"}))
print("correct bidirectional:", bidirectional_shortest_path(fwd, bwd, {"S"}, {"T"}))

# naive (settled-only) with the FIXED loop condition
INF = float("inf")
distF, distB = {}, {}
settledF, settledB = set(), set()
heapF, heapB = [], []
counter = 0
for s in {"S"}:
    distF[s] = 0.0; heapq.heappush(heapF, (0.0, counter, s)); counter += 1
for t in {"T"}:
    distB[t] = 0.0; heapq.heappush(heapB, (0.0, counter, t)); counter += 1
mu = INF
def relax(u, dist, heap, graph):
    global counter
    for v, w, _l in graph.get(u, []):
        nd = dist[u] + w
        if v not in dist or nd < dist[v] - 1e-9:
            dist[v] = nd
            heapq.heappush(heap, (nd, counter, v)); counter += 1
while heapF or heapB:
    top_f = heapF[0][0] if heapF else 0.0
    top_b = heapB[0][0] if heapB else 0.0
    if top_f + top_b >= mu:
        break
    if heapF and (not heapB or top_f <= top_b):
        _, _, u = heapq.heappop(heapF)
        if u in settledF: continue
        settledF.add(u)
        if u in settledB: mu = min(mu, distF[u] + distB[u])
        relax(u, distF, heapF, fwd)
    else:
        _, _, u = heapq.heappop(heapB)
        if u in settledB: continue
        settledB.add(u)
        if u in settledF: mu = min(mu, distF[u] + distB[u])
        relax(u, distB, heapB, bwd)
print("naive (node-overlap only):", None if mu == INF else mu)
