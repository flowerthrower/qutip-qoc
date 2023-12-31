"""
Tests GOAT/JOAT and  algorithms to return a proper Result object.
"""

import pytest
import qutip as qt
import numpy as np
import jax
import jax.numpy as jnp
import collections

from qutip_qoc.optimize import optimize_pulses
from qutip_qoc.objective import Objective
from qutip_qoc.time_interval import TimeInterval
from qutip_qoc.result import Result

Case = collections.namedtuple(
    "_Case", [
        "objectives",
        "pulse_options",
        "time_interval",
        "time_options",
        "algorithm_kwargs",
        "optimizer_kwargs",
        "minimizer_kwargs",
        "integrator_kwargs"])

# --------------------------- System and Control ---------------------------


def sin(t, p):
    return p[0] * np.sin(p[1] * t + p[2])


def grad_sin(t, p, idx):
    if idx == 0:
        return np.sin(p[1] * t + p[2])
    if idx == 1:
        return p[0] * np.cos(p[1] * t + p[2]) * t
    if idx == 2:
        return p[0] * np.cos(p[1] * t + p[2])
    if idx == 3:
        return p[0] * np.cos(p[1] * t + p[2]) * p[1]  # w.r.t. time


p_guess = q_guess = [1, 1, 0]
p_bounds = q_bounds = [(-1, 1), (-1, 1), (-np.pi, np.pi)]

H_d = [qt.sigmaz()]
H_c = [[qt.sigmax(), lambda t, p: sin(t, p), {"grad": grad_sin}],
       [qt.sigmay(), lambda t, q: sin(t, q), {"grad": grad_sin}]]

H = H_d + H_c

# ------------------------------- Objective -------------------------------

# state to state transfer
initial = qt.basis(2, 0)
target = qt.basis(2, 1)

state2state_goat = Case(
    objectives=[Objective(initial, H, target)],
    pulse_options={
        "p": {"guess": p_guess, "bounds": p_bounds},
        "q": {"guess": q_guess, "bounds": q_bounds}
    },
    time_interval=TimeInterval(evo_time=10),
    time_options={},
    algorithm_kwargs={
        "alg": "GOAT",
        "fid_err_targ": 0.01,
    },
    optimizer_kwargs={
        "seed": 0,
    },
    minimizer_kwargs={},
    integrator_kwargs={}
)

# ----------------------- JAX ---------------------


def sin_jax(t, p):
    return p[0] * jnp.sin(p[1] * t + p[2])


@jax.jit
def sin_x_jax(t, p, **kwargs): return sin_jax(t, p)


@jax.jit
def sin_y_jax(t, q, **kwargs): return sin_jax(t, q)


Hc_jax = [[qt.sigmax(), sin_x_jax],
          [qt.sigmay(), sin_y_jax]]

H_jax = H_d + Hc_jax

# state to state transfer
state2state_jax = state2state_goat._replace(
    objectives=[Objective(initial, H_jax, target)],
    algorithm_kwargs={"alg": "JOAT", "fid_err_targ": 0.01}
)

# ------------------- discrete CRAB / GRAPE  control ------------------------

n_tslots, evo_time = 100, 10
disc_interval = TimeInterval(n_tslots=n_tslots, evo_time=evo_time)

p_disc = q_disc = np.ones(n_tslots)
p_bound = q_bound = (-10, 10)

Hc_disc = [[qt.sigmax(), p_guess],
           [qt.sigmay(), q_guess]]

H_disc = H_d + Hc_disc


state2state_grape = state2state_goat._replace(
    objectives=[Objective(initial, H_disc, target)],
    pulse_options={
        "p": {"guess": p_disc, "bounds": p_bound},
        "q": {"guess": q_disc, "bounds": q_bound}
    },
    time_interval=disc_interval,
    algorithm_kwargs={"alg": "GRAPE", "fid_err_targ": 0.01}
)


@pytest.fixture(
    params=[
        # GOAT
        pytest.param(state2state_goat, id="State to state (GOAT)"),
        pytest.param(state2state_jax, id="State to state (JAX)"),
        pytest.param(state2state_grape, id="State to state (GRAPE)"),
    ]
)
def tst(request): return request.param


def test_optimize_pulses(tst):
    result = optimize_pulses(
        tst.objectives,
        tst.pulse_options,
        tst.time_interval,
        tst.time_options,
        tst.algorithm_kwargs,
        tst.optimizer_kwargs,
        tst.minimizer_kwargs,
        tst.integrator_kwargs)

    assert isinstance(result, Result)
    assert isinstance(result.objectives, list)
    assert isinstance(result.objectives[0], Objective)
    assert isinstance(result.time_interval, TimeInterval)
    assert isinstance(result.start_local_time, str)
    assert isinstance(result.end_local_time, str)
    assert isinstance(result.total_seconds, float)
    assert isinstance(result.n_iters, int)
    assert isinstance(result.iter_seconds, list)
    assert isinstance(result.iter_seconds[0], float)
    assert isinstance(result.message, str)
    assert isinstance(result.guess_controls, (list, np.ndarray))
    assert isinstance(result.optimized_controls, (list, np.ndarray))
    assert isinstance(result.optimized_objectives, list)
    assert isinstance(result.optimized_objectives[0], Objective)
    assert isinstance(result.final_states, list)
    assert isinstance(result.final_states[0], qt.Qobj)
    assert isinstance(result.guess_params, (list, np.ndarray))
    assert isinstance(result.optimized_params, (list, np.ndarray))
    assert isinstance(result.infidelity, float)
    assert isinstance(result.var_time, bool)
