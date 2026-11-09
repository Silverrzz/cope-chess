from __future__ import annotations

from collections import defaultdict, deque
from math import log, sqrt


EMPTY_SCORE_COUNT = 0.001

def paired_error_margins(games, ratings: dict[int, float], anchor_engine_id: int | None) -> dict[int, float | None]:
    import numpy as np

    engine_ids = sorted(ratings)
    indices = {engine_id: index for index, engine_id in enumerate(engine_ids)}
    margins = dict.fromkeys(engine_ids)
    if not engine_ids:
        return margins
    size = len(engine_ids)
    information = np.zeros((size, size))
    noise = np.zeros((size, size))
    adjacency = [set() for _ in engine_ids]
    pending = defaultdict(lambda: (deque(), deque()))
    frequencies = defaultdict(lambda: [0] * 5)

    for game in sorted(games, key=lambda game: game.id):
        if game.white_engine_id not in indices or game.black_engine_id not in indices:
            continue
        first, second = sorted((indices[game.white_engine_id], indices[game.black_engine_id]))
        if first == second:
            continue
        white_first = indices[game.white_engine_id] == first
        score = 1.0 if game.result == '1-0' else 0.0 if game.result == '0-1' else 0.5
        first_score = score if white_first else 1.0 - score
        expected = 1.0 / (1.0 + 10.0 ** (max(-4000.0, min(4000.0, ratings[engine_ids[second]] - ratings[engine_ids[first]])) / 400.0))
        weight = expected * (1.0 - expected)
        information[first, first] += weight
        information[second, second] += weight
        information[first, second] -= weight
        information[second, first] -= weight
        adjacency[first].add(second)
        adjacency[second].add(first)
        source_tournament_id = game.source_tournament_id or game.tournament_id
        if game.opening_id is None:
            frequencies[(source_tournament_id, first, second, 1, white_first)][round(first_score * 2)] += 1
            continue
        key = (source_tournament_id, game.opening_id, first, second)
        queues = pending[key]
        side = int(white_first)
        if queues[1 - side]:
            total = first_score + queues[1 - side].popleft()
            frequencies[(source_tournament_id, first, second, 2, None)][round(total * 2)] += 1
            if not queues[0] and not queues[1]:
                del pending[key]
        else:
            queues[side].append(first_score)

    for (tournament_id, _, first, second), queues in pending.items():
        for side, scores in enumerate(queues):
            for score in scores:
                frequencies[(tournament_id, first, second, 1, bool(side))][round(score * 2)] += 1

    for (_, first, second, length, _), counts in frequencies.items():
        counts = counts[:length * 2 + 1]
        count = sum(counts)
        expected = 1.0 / (1.0 + 10.0 ** (max(-4000.0, min(4000.0, ratings[engine_ids[second]] - ratings[engine_ids[first]])) / 400.0))
        if count == 1:
            variance = length * expected * (1.0 - expected)
        else:
            observed = [frequency if frequency else EMPTY_SCORE_COUNT for frequency in counts]
            total = sum(observed)
            mean = sum((index / 2) * frequency for index, frequency in enumerate(observed)) / total
            variance = sum((index / 2 - mean) ** 2 * frequency for index, frequency in enumerate(observed)) / total
        weight = count * variance
        noise[first, first] += weight
        noise[second, second] += weight
        noise[first, second] -= weight
        noise[second, first] -= weight

    fixed = indices.get(anchor_engine_id, 0)
    component = {fixed}
    frontier = [fixed]
    while frontier:
        node = frontier.pop()
        for neighbor in adjacency[node] - component:
            component.add(neighbor)
            frontier.append(neighbor)
    anchored = anchor_engine_id in indices
    if not anchored and len(component) != size:
        return margins
    members = sorted(component)
    adjustable = [index for index in members if index != fixed]
    covariance = np.zeros((len(members), len(members)))
    if adjustable:
        reduced_information = information[np.ix_(adjustable, adjustable)]
        try:
            inverse = np.linalg.solve(reduced_information, np.eye(len(adjustable)))
        except np.linalg.LinAlgError:
            return margins
        reduced_covariance = inverse @ noise[np.ix_(adjustable, adjustable)] @ inverse.T
        positions = [members.index(index) for index in adjustable]
        covariance[np.ix_(positions, positions)] = reduced_covariance
    if not anchored:
        center = np.eye(size) - np.ones((size, size)) / size
        covariance = center @ covariance @ center.T
    factor = 1.96 * 400 / log(10)
    for position, index in enumerate(members):
        variance = float(covariance[position, position])
        if np.isfinite(variance):
            margins[engine_ids[index]] = round(factor * sqrt(max(0.0, variance)), 6)
    return margins
