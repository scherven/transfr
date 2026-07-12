import heapq
import sys
sys.path.insert(0, '.')
from bidirectional_search import reverse_graph

fwd = {
    "S": [("A", 1.0, "sa"), ("C", 2.0, "sc")],
    "A": [("B", 1.0, "ab")],
    "B": [("T", 1.0, "bt")],
    "C": [("T", 2.0, "ct")],
    "T": [],
}
bwd = reverse_graph(fwd)
sources, targets = {"S"}, {"T"}

INF = float("inf")
distF, distB = {}, {}
settledF, settledB = set(), set()
heapF, heapB = [], []
counter = 0
for s in sources:
    distF[s] = 0.0
    heapq.heappush(heapF, (0.0, counter, s)); counter += 1
for t in targets:
    distB[t] = 0.0
    heapq.heappush(heapB, (0.0, counter, t)); counter += 1

mu = INF

def relax(u, dist, heap, graph):
    global counter
    for v, w, _label in graph.get(u, []):
        nd = dist[u] + w
        if v not in dist or nd < dist[v] - 1e-9:
            dist[v] = nd
            heapq.heappush(heap, (nd, counter, v))
            counter += 1
            print(f"    relax {u}->{v} (w={w}): {dist is distF and 'distF' or 'distB'}[{v}]={nd}")

step = 0
while heapF or heapB:
    step += 1
    top_f = heapF[0][0] if heapF else 0.0
    top_b = heapB[0][0] if heapB else 0.0
    print(f"step{step}: heapF={heapF} heapB={heapB} mu={mu} topF={top_f} topB={top_b}")
    if top_f + top_b >= mu:
        print("  TERMINATE (topF+topB>=mu)")
        break
    if heapF and (not heapB or top_f <= top_b):
        _, _, u = heapq.heappop(heapF)
        if u in settledF: 
            print(f"  skip stale F:{u}")
            continue
        settledF.add(u)
        print(f"  settle F:{u} (distF={distF[u]})")
        if u in settledB:
            mu = min(mu, distF[u] + distB[u])
            print(f"    -> mu update via node {u}: mu={mu}")
        relax(u, distF, heapF, fwd)
    else:
        _, _, u = heapq.heappop(heapB)
        if u in settledB:
            print(f"  skip stale B:{u}")
            continue
        settledB.add(u)
        print(f"  settle B:{u} (distB={distB[u]})")
        if u in settledF:
            mu = min(mu, distF[u] + distB[u])
            print(f"    -> mu update via node {u}: mu={mu}")
        relax(u, distB, heapB, bwd)

print("FINAL mu:", mu)
