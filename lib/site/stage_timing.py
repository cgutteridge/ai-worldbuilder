"""Elapsed wall-clock diagnostics for slow apply stages; no operation changes."""
from contextlib import contextmanager
from time import perf_counter


@contextmanager
def timed_stage(name, timings=None):
    """Print before work starts and after it exits, retaining seconds when requested."""
    print(f'timing start: {name}', flush=True)
    start = perf_counter()
    try:
        yield
    finally:
        seconds = perf_counter() - start
        if timings is not None:
            timings[name] = timings.get(name, 0.0) + seconds
        print(f'timing elapsed: {name}: {seconds:.3f}s', flush=True)
