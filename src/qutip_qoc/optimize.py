import numpy as np

import qutip_qtrl.logging_utils as logging
import qutip_qtrl.pulseoptim as cpo

from qutip_qoc.analytical_control import global_local_optimization

__all__ = ["optimize_pulses"]


def optimize_pulses(
    objectives,
    pulse_options,
    time_interval,
    time_options=None,
    algorithm_kwargs=None,
    optimizer_kwargs=None,
    minimizer_kwargs=None,
    integrator_kwargs=None,
):
    """
    Run GOAT, JOPT, GRAPE or CRAB optimization.

    Parameters
    ----------
    objectives : list of :class:`qutip_qoc.Objective`
        List of objectives to be optimized.

    pulse_options : dict
        Dictionary of options for the control pulse optimization.
        For each control function it must specify:

            control_id : dict
                - guess: ndarray, shape (n,)
                    Initial guess. Array of real elements of size (n,),
                    where ``n`` is the number of independent variables.

                - bounds : sequence, optional
                    Sequence of ``(min, max)`` pairs for each element in
                    `guess`. None is used to specify no bound.

        GRAPE and CRAB have only one control function with n_tslots parameters.
        The bounds are only one pair of ``(min, max)`` limiting the amplitude of all tslots equally.

    time_interval : :class:`qutip_qoc.TimeInterval`
        Pulse duration time interval.
        GRAPE and CRAB require n_tslots attribute for discretization.

    time_options : dict, optional
        Only supported by GOAT and JOPT.
        Dictionary of options for the time interval optimization.
        It must specify both:

            - guess: ndarray, shape (n,)
                Initial guess. Array of real elements of size (n,),
                where ``n`` is the number of independent variables.

            - bounds : sequence, optional
                Sequence of ``(min, max)`` pairs for each element in `guess`.
                None is used to specify no bound.

    algorithm_kwargs : dict, optional
        Dictionary of options for the optimization algorithm.

            - alg : str
                Algorithm to use for the optimization.
                Supported are: "GRAPE", "CRAB", "GOAT", "JOPT".

            - fid_err_targ : float, optional
                Fidelity error target for the optimization.

            - max_iter : int, optional
                Maximum number of iterations to perform.
                Referes to local minimizer steps.
                Global steps default to 0 (no global optimization).
                Can be overridden by specifying in minimizer_kwargs.

        Algorithm specific keywords for GRAPE,CRAB can be found in
        :func:`qutip_qtrl.pulseoptim.optimize_pulse`.

    optimizer_kwargs : dict, optional
        Dictionary of options for the global optimizer.
        Only supported by GOAT and JOPT.

            - method : str, optional
                Algorithm to use for the global optimization.
                Supported are: "basinhopping", "dual_annealing"

            - max_iter : int, optional
                Maximum number of iterations to perform.

        Full list of options can be found in
        :func:`scipy.optimize.basinhopping`
        and :func:`scipy.optimize.dual_annealing`.

    minimizer_kwargs : dict, optional
        Dictionary of options for the local minimizer.

            - method : str, optional
                Algorithm to use for the local optimization.
                Gradient driven methods are supported.

        Full list of options and methods can be found in
        :func:`scipy.optimize.minimize`.

    integrator_kwargs : dict, optional
        Dictionary of options for the integrator.
        Only supported by GOAT and JOPT.
        Options for the solver, see :obj:`MESolver.options` and
        `Integrator <./classes.html#classes-ode>`_ for a list of all options.

    Returns
    -------
    result : :class:`qutip_qoc.Result`
        Optimization result.
    """
    if time_options is None:
        time_options = {}
    if algorithm_kwargs is None:
        algorithm_kwargs = {}
    if optimizer_kwargs is None:
        optimizer_kwargs = {}
    if minimizer_kwargs is None:
        minimizer_kwargs = {}
    if integrator_kwargs is None:
        integrator_kwargs = {}

    alg = algorithm_kwargs.get("alg", "GRAPE")  # works with most input types

    Hd_lst, Hc_lst = [], []
    if not isinstance(objectives, list):
        objectives = [objectives]
    for objective in objectives:
        # extract drift and control Hamiltonians from the objective
        Hd_lst.append(objective.H[0])
        Hc_lst.append([H[0] if isinstance(H, list) else H for H in objective.H[1:]])

    # extract initial and target states/operators from the objective
    init = objective.initial
    targ = objective.target

    # extract guess and bounds for the control pulses
    x0, bounds = [], []
    for key in pulse_options.keys():
        x0.append(pulse_options[key].get("guess"))
        bounds.append(pulse_options[key].get("bounds"))
    try:  # GRAPE, CRAB format
        lbound = [b[0][0] for b in bounds]
        ubound = [b[0][1] for b in bounds]
    except Exception:
        lbound = [b[0] for b in bounds]
        ubound = [b[1] for b in bounds]

    # default "log_level" if not specified
    if algorithm_kwargs.get("disp", False):
        log_level = logging.INFO
    else:
        log_level = logging.WARN

    if "options" in minimizer_kwargs:
        minimizer_kwargs["options"].setdefault(
            "maxiter", algorithm_kwargs.get("max_iter", 1000)
        )
        minimizer_kwargs["options"].setdefault(
            "gtol", algorithm_kwargs.get("min_grad", 0.0 if alg == "CRAB" else 1e-10)
        )
    else:
        minimizer_kwargs["options"] = {
            "maxiter": algorithm_kwargs.get("max_iter", 1000),
            "gtol": algorithm_kwargs.get("min_grad", 0.0 if alg == "CRAB" else 1e-10),
        }

    # prepare qtrl optimizers
    qtrl_optimizers = []
    if alg == "CRAB" or alg == "GRAPE":
        if alg == "GRAPE":  # algorithm specific kwargs
            # only scalar bounds
            lbound = lbound[0]
            ubound = ubound[0]
            use_as_amps = True
            alg_params = algorithm_kwargs.get("alg_params", {})

        elif alg == "CRAB":
            minimizer_kwargs.setdefault("method", "Nelder-Mead")  # no gradient
            # Check wether guess referes to amplitudes (or parameters for CRAB)
            use_as_amps = len(x0[0]) == time_interval.n_tslots
            num_coeffs = algorithm_kwargs.get("num_coeffs", None)
            fix_frequency = algorithm_kwargs.get("fix_frequency", False)

            if num_coeffs is None:
                if use_as_amps:
                    num_coeffs = (
                        2  # default only two sets of fourier expansion coefficients
                    )
                else:  # depending on the number of parameters given
                    num_coeffs = len(x0[0]) // 2 if fix_frequency else len(x0[0]) // 3

            alg_params = {
                "num_coeffs": num_coeffs,
                "init_coeff_scaling": algorithm_kwargs.get("init_coeff_scaling"),
                "crab_pulse_params": algorithm_kwargs.get("crab_pulse_params"),
                "fix_frequency": fix_frequency,
            }

        # one optimizer for each objective
        for i, objective in enumerate(objectives):
            params = {
                "drift": Hd_lst[i],
                "ctrls": Hc_lst[i],
                "initial": init,
                "target": targ,
                "num_tslots": time_interval.n_tslots,
                "evo_time": time_interval.evo_time,
                "tau": None,  # implicitly derived from tslots
                "amp_lbound": lbound,
                "amp_ubound": ubound,
                "fid_err_targ": algorithm_kwargs.get("fid_err_targ", 1e-10),
                "min_grad": minimizer_kwargs["options"]["gtol"],
                "max_iter": minimizer_kwargs["options"]["maxiter"],
                "max_wall_time": algorithm_kwargs.get("max_wall_time", 180),
                "alg": alg,
                "optim_method": algorithm_kwargs.get("optim_method", None),
                "method_params": minimizer_kwargs,
                "optim_alg": None,  # deprecated
                "max_metric_corr": None,  # deprecated
                "accuracy_factor": None,  # deprecated
                "alg_params": alg_params,
                "optim_params": algorithm_kwargs.get("optim_params", None),
                "dyn_type": algorithm_kwargs.get("dyn_type", "GEN_MAT"),
                "dyn_params": algorithm_kwargs.get("dyn_params", None),
                "prop_type": algorithm_kwargs.get(
                    "prop_type", "DEF"
                ),  # check other defaults
                "prop_params": algorithm_kwargs.get("prop_params", None),
                "fid_type": algorithm_kwargs.get("fid_type", "DEF"),
                "fid_params": algorithm_kwargs.get("fid_params", None),
                "phase_option": None,  # deprecated
                "fid_err_scale_factor": None,  # deprecated
                "tslot_type": algorithm_kwargs.get("tslot_type", "DEF"),
                "tslot_params": algorithm_kwargs.get("tslot_params", None),
                "amp_update_mode": None,  # deprecated
                "init_pulse_type": algorithm_kwargs.get("init_pulse_type", "DEF"),
                "init_pulse_params": algorithm_kwargs.get(
                    "init_pulse_params", None
                ),  # wavelength, frequency, phase etc.
                "pulse_scaling": algorithm_kwargs.get("pulse_scaling", 1.0),
                "pulse_offset": algorithm_kwargs.get("pulse_offset", 0.0),
                "ramping_pulse_type": algorithm_kwargs.get("ramping_pulse_type", None),
                "ramping_pulse_params": algorithm_kwargs.get(
                    "ramping_pulse_params", None
                ),
                "log_level": algorithm_kwargs.get("log_level", log_level),
                "gen_stats": algorithm_kwargs.get("gen_stats", False),
            }

            qtrl_optimizer = cpo.create_pulse_optimizer(
                drift=params["drift"],
                ctrls=params["ctrls"],
                initial=params["initial"],
                target=params["target"],
                num_tslots=params["num_tslots"],
                evo_time=params["evo_time"],
                tau=params["tau"],
                amp_lbound=params["amp_lbound"],
                amp_ubound=params["amp_ubound"],
                fid_err_targ=params["fid_err_targ"],
                min_grad=params["min_grad"],
                max_iter=params["max_iter"],
                max_wall_time=params["max_wall_time"],
                alg=params["alg"],
                optim_method=params["optim_method"],
                method_params=params["method_params"],
                optim_alg=params["optim_alg"],
                max_metric_corr=params["max_metric_corr"],
                accuracy_factor=params["accuracy_factor"],
                alg_params=params["alg_params"],
                optim_params=params["optim_params"],
                dyn_type=params["dyn_type"],
                dyn_params=params["dyn_params"],
                prop_type=params["prop_type"],
                prop_params=params["prop_params"],
                fid_type=params["fid_type"],
                fid_params=params["fid_params"],
                phase_option=params["phase_option"],
                fid_err_scale_factor=params["fid_err_scale_factor"],
                tslot_type=params["tslot_type"],
                tslot_params=params["tslot_params"],
                amp_update_mode=params["amp_update_mode"],
                init_pulse_type=params["init_pulse_type"],
                init_pulse_params=params["init_pulse_params"],
                pulse_scaling=params["pulse_scaling"],
                pulse_offset=params["pulse_offset"],
                ramping_pulse_type=params["ramping_pulse_type"],
                ramping_pulse_params=params["ramping_pulse_params"],
                log_level=params["log_level"],
                gen_stats=params["gen_stats"],
            )
            dyn = qtrl_optimizer.dynamics
            dyn.init_timeslots()

            # Generate initial pulses for each control through generator
            init_amps = np.zeros([dyn.num_tslots, dyn.num_ctrls])

            for j in range(dyn.num_ctrls):
                if isinstance(qtrl_optimizer.pulse_generator, list):
                    # pulse generator for each control
                    pgen = qtrl_optimizer.pulse_generator[j]
                else:
                    pgen = qtrl_optimizer.pulse_generator

                if use_as_amps:
                    if alg == "CRAB":
                        pgen.guess_pulse = x0[j]
                        pgen.init_pulse()
                    init_amps[:, j] = x0[j]

                else:
                    # Set the initial parameters
                    pgen.set_optim_var_vals(np.array(x0[j]))
                    # pgen._pulse_initialised = True
                    init_amps[:, j] = pgen.gen_pulse()
                    # dyn.prop_computer.grad_exact = False

            # Initialise the starting amplitudes
            # NOTE: any initial CRAB guess pulse is only used
            # to modulate or offset the initial amplitudes
            # depending on init_pulse_params: pulse_action
            dyn.initialize_controls(init_amps)
            # And store the (random) initial parameters
            init_params = qtrl_optimizer._get_optim_var_vals()

            if use_as_amps:  # For the global optimizer
                num_params = len(init_params) // len(pulse_options)
                for i, key in enumerate(pulse_options.keys()):
                    pulse_options[key]["guess"] = init_params[
                        i * num_params : (i + 1) * num_params
                    ]  # amplitude bounds are taken care of by pulse generator
                    pulse_options[key]["bounds"] = None

            qtrl_optimizers.append(qtrl_optimizer)

    return global_local_optimization(
        objectives,
        pulse_options,
        time_interval,
        time_options,
        algorithm_kwargs,
        optimizer_kwargs,
        minimizer_kwargs,
        integrator_kwargs,
        qtrl_optimizers,
    )
