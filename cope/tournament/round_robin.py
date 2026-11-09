from itertools import combinations


def round_robin_pair_counts(participants: list[int], games_per_engine: int) -> dict[tuple[int, int], int]:
    size = len(participants)
    base, extra = divmod(games_per_engine // 2, size - 1)
    counts = {}
    for left, right in combinations(range(size), 2):
        distance = min(right - left, size - (right - left))
        receives_extra = distance <= extra // 2 or (extra % 2 == 1 and distance == size // 2)
        pair = tuple(sorted((participants[left], participants[right])))
        counts[pair] = base + int(receives_extra)
    return counts
