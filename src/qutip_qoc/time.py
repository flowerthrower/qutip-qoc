import numpy as np

__all__ = ["TimeInterval"]


class TimeInterval:
    """
    Class for storing a time interval and deriving its attributes.

    Attributes
    ----------
    tslots : array_like, optional
        List of time slots at which the control pulse is evaluated.
        The last element of tslots is the total evolution time.
        Can be unevenly spaced.

    evo_time : float, optional
        Total evolution time.
        If given together with n_tslots, tslots is derived from evo_time
        and assumed to be evenly spaced.

    n_tslots : int, optional
        Number of time slots. Length of tslots is n_tslots.

    """

    def __init__(self, tslots=None, evo_time=None, n_tslots=100):
        self._tslots = tslots
        self._evo_time = evo_time
        self._n_tslots = n_tslots

    def __call__(self):
        return self.tslots

    @property
    def tslots(self):
        if self._tslots is None:
            n_tslots = self.n_tslots
            if self._evo_time:  # derive from evo_time
                self._tslots = np.linspace(0.0, self._evo_time, n_tslots)
        return self._tslots

    @property
    def evo_time(self):
        if self._evo_time is None:
            tslots = self.tslots
            self._evo_time = tslots[-1]
        return self._evo_time

    @property
    def n_tslots(self):
        if self._n_tslots is None:
            if self._tslots:
                self._n_tslots = len(self._tslots)
            else:
                raise ValueError(
                    "Either tslots or evo_time + n_tslots must be specified."
                )
        return self._n_tslots
