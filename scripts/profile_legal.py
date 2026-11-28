"""Measure Python allocations for the local preview build."""
import tracemalloc
from legal import ROOT, build

tracemalloc.start()
build(ROOT / '.cache/profile-site')
current, peak = tracemalloc.get_traced_memory()
print(f'current_bytes={current} peak_bytes={peak}')
