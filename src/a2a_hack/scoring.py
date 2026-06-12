"""Combine the three pairing runs into final hackathon scores.

final = 0.5 * own-pair + 0.25 * (team personal x held-out CS)
      + 0.25 * (held-out personal x team CS)

Each pairing's score is the mean reward over a task grid; a task with no
simulation or an INFRASTRUCTURE_ERROR counts as 0. `score_pairings` reports
the means over the union grid (one team's standalone score) and also the set
of tasks each pairing actually completed, so cross-team marking can re-score
every team over the *common* completed set (see combine_per_pairing)."""

from pathlib import Path

from loguru import logger
from tau2.data_model.simulation import Results, TerminationReason

PAIRING_WEIGHTS = {"a": 0.5, "b": 0.25, "c": 0.25}


def load_pairing_rewards(path: Path) -> tuple[dict[str, float], set[str], set[str]]:
    """Load per-task rewards, the task grid, and the completed set for one pairing.

    A missing/empty dir (a pairing that never ran) scores 0 on every task.

    Returns:
        (rewards by task id, task ids in the run's task set, task ids that
        completed — i.e. produced a non-INFRASTRUCTURE_ERROR simulation).
    """
    if not (Path(path) / "results.json").exists():
        logger.warning(f"Pairing dir {path} has no results.json; scoring 0")
        return {}, set(), set()
    metadata = Results.load_metadata(path)
    task_ids = {t.id for t in metadata.tasks}
    rewards: dict[str, float] = {}
    completed: set[str] = set()
    for sim in Results.iter_simulations(path):
        if sim.termination_reason == TerminationReason.INFRASTRUCTURE_ERROR:
            reward = 0.0
        else:
            reward = sim.reward_info.reward if sim.reward_info else 0.0
            completed.add(str(sim.task_id))
        rewards[str(sim.task_id)] = reward
    return rewards, task_ids, completed


def combine_per_pairing(
    per_task: dict[str, dict[str, float]], task_ids_by_pairing: dict[str, list[str]]
) -> dict:
    """Weighted score from a per-task reward table over a chosen task set.

    Each pairing is averaged over its own list of task ids (a missing task
    counts as 0); the final is the 50/25/25 weighting. This is the single
    source of truth for both a team's standalone score (its own completed
    tasks) and the cross-team score (the tasks all teams completed).
    """
    means = {}
    for name in PAIRING_WEIGHTS:
        ids = task_ids_by_pairing.get(name) or []
        means[name] = (
            sum(per_task.get(tid, {}).get(name, 0.0) for tid in ids) / len(ids)
            if ids
            else 0.0
        )
    final = sum(PAIRING_WEIGHTS[name] * means[name] for name in PAIRING_WEIGHTS)
    return {**means, "final": final}


def score_pairings(a_dir: Path, b_dir: Path, c_dir: Path) -> dict:
    """Combine three pairing runs into the 50/25/25 score for one team.

    The reported means cover the union of the three runs' task sets, so a
    pairing that silently dropped tasks still scores 0 on them. `per_task`
    is the reward table and `completed` is the per-pairing set of tasks that
    actually finished — cross-team marking intersects those across teams and
    re-scores everyone with combine_per_pairing.
    """
    pairings: dict[str, dict[str, float]] = {}
    completed: dict[str, set[str]] = {}
    grid: set[str] = set()
    for name, path in (("a", a_dir), ("b", b_dir), ("c", c_dir)):
        rewards, task_ids, done = load_pairing_rewards(Path(path))
        pairings[name] = rewards
        completed[name] = done
        grid |= task_ids
        missing = task_ids - set(rewards)
        if missing:
            logger.warning(
                f"Pairing {name}: {len(missing)} task(s) without a simulation "
                f"(scored 0): {sorted(missing)}"
            )

    per_task = {
        task_id: {name: pairings[name].get(task_id, 0.0) for name in PAIRING_WEIGHTS}
        for task_id in sorted(grid)
    }
    combined = combine_per_pairing(
        per_task, {name: sorted(grid) for name in PAIRING_WEIGHTS}
    )
    return {
        **combined,
        "per_task": per_task,
        "completed": {name: sorted(completed[name]) for name in PAIRING_WEIGHTS},
    }
